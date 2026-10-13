"""High level state holder for one AAT multiroom amplifier.

Wraps AatMultiroomClient with:
  * a cache of per-zone state (input/volume/mute/standby, plus the
    bass/treble/balance/pre-amp adjustments)
  * optimistic local updates so the UI reacts instantly to a tap, before the
    device even answers
  * a dispatcher signal per zone (plus one device-wide signal for the master
    power switch and connectivity changes), fired on every state change -
    from our own commands, from unsolicited device messages, or from
    periodic re-sync - so only the entities that actually need to re-render
    do
  * automatic reconnection with backoff, including detection of a "hung"
    connection (socket still open, device stopped answering)
  * background tasks (periodic refresh, reconnect loop, failure resync)
    that never die from an unexpected exception, and are properly awaited
    on shutdown
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import AatCommandError, AatConnectionError, AatMultiroomClient
from .const import (
    BALANCE_CENTER,
    BALANCE_MAX,
    CONF_MODEL,
    DEFAULT_ERROR_TRANSLATION_KEY,
    DEFAULT_PORT,
    DOMAIN,
    ERROR_CODE_TRANSLATION_KEYS,
    MAX_VOLUME,
    OUTPUT_MODES,
    PREAMP_MAX,
    REFRESH_INTERVAL,
    TONE_FLAT,
    TONE_MAX,
    VOLUME_LIMIT_MIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT

_LOGGER = logging.getLogger(__name__)

_RECONNECT_MIN_DELAY = 3
_RECONNECT_MAX_DELAY = 60


@dataclass
class ZoneState:
    """Cached state of a single zone.

    `bass`/`treble`/`balance`/`preamp` hold the device's own raw values
    (see the ranges in const.py) - converting to dB is the entities' job."""

    input: int = 1
    volume: int = 0
    mute: bool = False
    standby: bool = True
    bass: int = TONE_FLAT
    treble: int = TONE_FLAT
    balance: int = BALANCE_CENTER
    preamp: int = 0
    # VOLLIMIT ceiling (firmware >= 3.08); MAX_VOLUME means "no limit".
    volume_limit: int = MAX_VOLUME


def _adjust_handler(attr: str) -> Callable[["AatMultiroomDevice", list[str]], int]:
    """Handler for any reply/push of the form `<CMD> <zone> <value>` that
    just stores `value` on one ZoneState attribute - shared by every
    bass/treble/balance/preamp command (+, -, GET, SET all answer alike)."""

    def handler(self: "AatMultiroomDevice", args: list[str]) -> int:
        zone = int(args[0])
        setattr(self._get_zone(zone), attr, int(args[1]))
        return zone

    return handler


class AatMultiroomDevice:
    """Owns the connection and cached state for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self.model: str = entry.data.get(CONF_MODEL, "")
        self.zone_count = 0
        self.zones: dict[int, ZoneState] = {}
        self.power: bool = True
        # Set by __init__.py right after registering the hub device, before
        # any entity is constructed - lets zone entities put via_device_id
        # in their DeviceInfo alongside the older via_device tuple.
        self.hub_device_id: str | None = None
        # From GETALL (manual 1.13.1): firmware version, the second
        # (configurable) TCP port, and the connection timeout in seconds
        # (0 = disabled).
        self.firmware: str | None = None
        self.tcp_port2: int | None = None
        self.tcp_timeout: int | None = None
        # None = not probed yet; False = firmware doesn't know VOLLIMIT
        # (answered "unknown command"), so don't ask again.
        self.vollimit_supported: bool | None = None

        self.client = AatMultiroomClient(self.host, self.port)
        self.client.add_listener(self._on_message)
        self.client.on_disconnected = self._schedule_reconnect

        self._refresh_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._resync_task: asyncio.Task | None = None
        self._closing = False

    @property
    def signal(self) -> str:
        """Device-wide signal: master power, and anything (like a
        connectivity change) that can affect every entity at once."""
        return f"{DOMAIN}_{self.entry.entry_id}_update"

    def zone_signal(self, zone_num: int) -> str:
        """Per-zone signal, so a change in one zone doesn't re-render every
        entity of every other zone too."""
        return f"{DOMAIN}_{self.entry.entry_id}_zone_{zone_num}_update"

    @property
    def connected(self) -> bool:
        return self.client.connected

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    def _notify_zone(self, zone_num: int) -> None:
        async_dispatcher_send(self.hass, self.zone_signal(zone_num))

    def _notify_all(self) -> None:
        """Device-wide signal plus every zone's own signal - used for
        connectivity changes and full resyncs, where potentially everything
        just changed."""
        self._notify()
        for zone_num in self.zones:
            self._notify_zone(zone_num)

    def _get_zone(self, zone_num: int) -> ZoneState:
        if zone_num not in self.zones:
            self.zones[zone_num] = ZoneState()
        return self.zones[zone_num]

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        await self.client.async_connect()
        await self.async_refresh_full_state()
        self._refresh_task = self.hass.async_create_background_task(
            self._periodic_refresh(), name=f"aat_multiroom_periodic_refresh_{self.host}"
        )

    async def async_close(self) -> None:
        self._closing = True
        tasks = [t for t in (self._refresh_task, self._reconnect_task, self._resync_task) if t]
        for task in tasks:
            task.cancel()
        if tasks:
            # Actually wait for cancellation to land, instead of firing it
            # and moving on - avoids "Task was destroyed but it is pending"
            # warnings on unload/reload.
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.async_disconnect()

    def _refresh_interval(self) -> float:
        """Seconds between safety-net resyncs.

        If the device has a TCP timeout configured (manual 1.18), keep our
        own traffic comfortably inside it so an idle-looking connection is
        never the thing that gets reset."""
        timeout = self.tcp_timeout
        if timeout and timeout > 0 and timeout < 2 * REFRESH_INTERVAL:
            return max(5.0, timeout / 2)
        return float(REFRESH_INTERVAL)

    async def _periodic_refresh(self) -> None:
        while not self._closing:
            await asyncio.sleep(self._refresh_interval())
            if not self.client.connected:
                continue
            try:
                await self.async_refresh_full_state()
            except AatConnectionError as err:
                _LOGGER.debug("Periodic refresh failed for %s: %s", self.host, err)
            except Exception:  # noqa: BLE001
                # Whatever this was, it must not kill the loop - without it,
                # nothing keeps zone state in sync ever again until Home
                # Assistant itself restarts.
                _LOGGER.exception(
                    "Unexpected error during periodic refresh for %s; will retry in %ss",
                    self.host,
                    REFRESH_INTERVAL,
                )

    def _schedule_reconnect(self) -> None:
        # Connectivity affects every entity's `available` - not just one
        # zone's - so this needs the broad notify, not a single zone's.
        self._notify_all()
        if self._closing:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.hass.async_create_background_task(
                self._reconnect_loop(), name=f"aat_multiroom_reconnect_{self.host}"
            )

    async def _reconnect_loop(self) -> None:
        delay = _RECONNECT_MIN_DELAY
        while not self._closing and not self.client.connected:
            await asyncio.sleep(delay)
            if self._closing:
                return

            try:
                await self.client.async_connect()
            except AatConnectionError as err:
                _LOGGER.debug(
                    "Reconnect attempt to %s:%s failed (retrying in %ss): %s",
                    self.host,
                    self.port,
                    delay,
                    err,
                )
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                continue
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Unexpected error reconnecting to %s:%s (retrying in %ss)",
                    self.host,
                    self.port,
                    delay,
                )
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                continue

            # Transport-connected now; entities can already flip back to
            # available even if the state refresh below hits a snag - the
            # periodic refresh (or the next push message) will catch up.
            _LOGGER.info("Reconnected to AAT multiroom %s:%s", self.host, self.port)
            try:
                await self.async_refresh_full_state()  # calls _notify_all() itself
            except AatConnectionError as err:
                _LOGGER.debug("Initial refresh after reconnect failed for %s: %s", self.host, err)
                self._notify_all()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error refreshing state after reconnect to %s", self.host)
                self._notify_all()
            return

    # ------------------------------------------------------------------
    # state sync
    # ------------------------------------------------------------------

    async def async_refresh_full_state(self) -> None:
        if self._resync_task is not None and not self._resync_task.done():
            # A resync is already in flight (e.g. triggered by a failed
            # command moments ago) - piggyback on it instead of hammering
            # the device with a second overlapping GETALL/ZSTDBYGET pass.
            await self._resync_task
            return
        self._resync_task = self.hass.async_create_task(self._do_refresh_full_state())
        await self._resync_task

    async def _do_refresh_full_state(self) -> None:
        args = await self.client.async_send_command("GETALL")
        self._parse_getall(args)
        for zone_num in list(self.zones):
            try:
                zargs = await self.client.async_send_command("ZSTDBYGET", zone_num)
                if len(zargs) >= 2:
                    self.zones[zone_num].standby = zargs[1].upper() == "ON"
            except AatConnectionError:
                break
        await self._probe_volume_limits()
        self._notify_all()

    async def _probe_volume_limits(self) -> None:
        """Read each zone's VOLLIMIT, which GETALL doesn't carry.

        Firmware older than 3.08 answers "unknown command" (error 7); that
        is remembered so those units aren't asked again every refresh. The
        values themselves land through the normal reply handlers."""
        if self.vollimit_supported is False:
            return
        for zone_num in list(self.zones):
            try:
                await self.client.async_send_command("VOLLIMIT", zone_num)
            except AatCommandError as err:
                if err.code == "7":
                    self.vollimit_supported = False
                    return
                _LOGGER.debug("VOLLIMIT %s rejected by %s: code %s", zone_num, self.host, err.code)
            except AatConnectionError:
                return
            else:
                self.vollimit_supported = True

    def _parse_getall(self, args: list[str]) -> None:
        if len(args) < 5:
            return
        self.model = args[0]
        self.firmware = args[1]
        self.power = args[2].upper() == "ON"
        for index, attr in ((3, "tcp_port2"), (4, "tcp_timeout")):
            try:
                setattr(self, attr, int(args[index]))
            except ValueError:
                continue
        zone_data = args[5:]
        zone_count = len(zone_data) // 7
        for i in range(zone_count):
            chunk = zone_data[i * 7 : (i + 1) * 7]
            zone = self._get_zone(i + 1)
            try:
                zone.input = int(chunk[0])
                zone.volume = int(chunk[1])
                zone.mute = chunk[2].upper() == "ON"
            except (ValueError, IndexError):
                continue
            # Chunk order per the manual: input, volume, mute, bass, treble,
            # balance, preamp. Parsed one by one so a single odd value
            # can't throw away the others.
            for index, attr in ((3, "bass"), (4, "treble"), (5, "balance"), (6, "preamp")):
                try:
                    setattr(zone, attr, int(chunk[index]))
                except (ValueError, IndexError):
                    continue
        self.zone_count = zone_count

    # ------------------------------------------------------------------
    # push updates (our own replies AND unsolicited device messages)
    #
    # Each handler returns the zone number it touched, or None for a
    # device-wide change (power) - _on_message uses that to fire only the
    # signal that actually needs to update.
    # ------------------------------------------------------------------

    def _on_message(self, cmd: str, args: list[str]) -> None:
        handler = self._HANDLERS.get(cmd)
        if handler is None:
            return
        try:
            zone_num = handler(self, args)
        except (ValueError, IndexError):
            _LOGGER.debug("Could not parse %s %s", cmd, args)
            return
        if zone_num is None:
            self._notify()
        else:
            self._notify_zone(zone_num)

    def _h_zstdbyon(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).standby = True
        return zone

    def _h_zstdbyoff(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).standby = False
        return zone

    def _h_zstdbytog(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).standby = args[1].upper() == "ON"
        return zone

    def _h_zstdbyget(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).standby = args[1].upper() == "ON"
        return zone

    def _h_muteon(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).mute = True
        return zone

    def _h_muteoff(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).mute = False
        return zone

    def _h_mutetog(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).mute = args[1].upper() == "ON"
        return zone

    def _h_muteget(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).mute = args[1].upper() == "ON"
        return zone

    def _h_volchange(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).volume = int(args[1])
        return zone

    def _h_inpchange(self, args: list[str]) -> int:
        zone = int(args[0])
        self._get_zone(zone).input = int(args[1])
        return zone

    _h_bass = _adjust_handler("bass")
    _h_treble = _adjust_handler("treble")
    _h_balance = _adjust_handler("balance")
    _h_preamp = _adjust_handler("preamp")
    _h_vollimit = _adjust_handler("volume_limit")

    def _h_pwron(self, args: list[str]) -> None:
        self.power = True
        return None

    def _h_pwroff(self, args: list[str]) -> None:
        self.power = False
        return None

    def _h_pwrtog(self, args: list[str]) -> None:
        self.power = args[0].upper() == "ON"
        return None

    def _h_pwrget(self, args: list[str]) -> None:
        self.power = args[0].upper() == "ON"
        return None

    _HANDLERS: dict[str, Callable[["AatMultiroomDevice", list[str]], int | None]] = {
        "ZSTDBYON": _h_zstdbyon,
        "ZSTDBYOFF": _h_zstdbyoff,
        "ZSTDBYTOG": _h_zstdbytog,
        "ZSTDBYGET": _h_zstdbyget,
        "MUTEON": _h_muteon,
        "MUTEOFF": _h_muteoff,
        "MUTETOG": _h_mutetog,
        "MUTEGET": _h_muteget,
        "VOL+": _h_volchange,
        "VOL-": _h_volchange,
        "VOLGET": _h_volchange,
        "VOLSET": _h_volchange,
        "INPSET": _h_inpchange,
        "INPGET": _h_inpchange,
        "BASS+": _h_bass,
        "BASS-": _h_bass,
        "BASSGET": _h_bass,
        "BASSSET": _h_bass,
        "TREBLE+": _h_treble,
        "TREBLE-": _h_treble,
        "TREBLEGET": _h_treble,
        "TREBLESET": _h_treble,
        "LEFT+": _h_balance,
        "RIGHT+": _h_balance,
        "BALGET": _h_balance,
        "BALSET": _h_balance,
        "PREAMP+": _h_preamp,
        "PREAMP-": _h_preamp,
        "PREAMPGET": _h_preamp,
        "PREAMPSET": _h_preamp,
        "VOLLIMIT": _h_vollimit,
        # The VOLX* family (firmware >= 3.08) answers `<zone> <volume> <flag>`
        # - same leading fields as VOL*, plus a "limit reached" flag.
        "VOLX+": _h_volchange,
        "VOLX-": _h_volchange,
        "VOLGETX": _h_volchange,
        "VOLSETX": _h_volchange,
        "PWRON": _h_pwron,
        "PWROFF": _h_pwroff,
        "PWRTOG": _h_pwrtog,
        "PWRGET": _h_pwrget,
    }

    # ------------------------------------------------------------------
    # commands (optimistic: update cache immediately, then talk to device)
    # ------------------------------------------------------------------

    async def _run_command(
        self,
        apply_optimistic: Callable[[], None],
        cmd: str,
        *args: object,
        zone: int | None = None,
        all_zones: bool = False,
    ) -> None:
        apply_optimistic()
        if all_zones:
            self._notify_all()
        elif zone is None:
            self._notify()
        else:
            self._notify_zone(zone)
        try:
            await self.client.async_send_command(cmd, *args)
        except AatCommandError as err:
            # The device rejected the command outright (manual section
            # 1.3.8) - our optimistic guess was wrong, resync it.
            _LOGGER.debug("%s %s rejected by %s: code %s", cmd, args, self.host, err.code)
            self.hass.async_create_task(self.async_refresh_full_state())
            translation_key = ERROR_CODE_TRANSLATION_KEYS.get(
                err.code, DEFAULT_ERROR_TRANSLATION_KEY
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders={"code": err.code, "command": cmd},
            ) from err
        except AatConnectionError as err:
            # Connection dropped mid-command; local state may now be wrong.
            _LOGGER.debug("%s %s failed for %s: %s", cmd, args, self.host, err)
            self.hass.async_create_task(self.async_refresh_full_state())
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="connection_error",
                translation_placeholders={"command": cmd, "error": str(err)},
            ) from err

    async def async_zone_power(self, zone: int, on: bool) -> None:
        def _apply() -> None:
            self._get_zone(zone).standby = not on

        await self._run_command(_apply, "ZSTDBYOFF" if on else "ZSTDBYON", zone, zone=zone)

    async def async_set_volume(self, zone: int, volume: int) -> None:
        volume = max(0, min(MAX_VOLUME, volume))

        def _apply() -> None:
            self._get_zone(zone).volume = volume

        await self._run_command(_apply, "VOLSET", zone, volume, zone=zone)

    async def async_volume_step(self, zone: int, up: bool) -> None:
        def _apply() -> None:
            zone_state = self._get_zone(zone)
            if up:
                zone_state.volume = min(MAX_VOLUME, zone_state.volume + 1)
            else:
                zone_state.volume = max(0, zone_state.volume - 1)

        await self._run_command(_apply, "VOL+" if up else "VOL-", zone, zone=zone)

    async def async_set_mute(self, zone: int, mute: bool) -> None:
        def _apply() -> None:
            self._get_zone(zone).mute = mute

        await self._run_command(_apply, "MUTEON" if mute else "MUTEOFF", zone, zone=zone)

    async def async_select_input(self, zone: int, input_num: int) -> None:
        def _apply() -> None:
            self._get_zone(zone).input = input_num

        await self._run_command(_apply, "INPSET", zone, input_num, zone=zone)

    async def _async_set_adjust(
        self, zone: int, attr: str, command: str, value: int, maximum: int, minimum: int = 0
    ) -> None:
        value = max(minimum, min(maximum, int(value)))

        def _apply() -> None:
            setattr(self._get_zone(zone), attr, value)

        await self._run_command(_apply, command, zone, value, zone=zone)

    async def async_set_bass(self, zone: int, value: int) -> None:
        await self._async_set_adjust(zone, "bass", "BASSSET", value, TONE_MAX)

    async def async_set_treble(self, zone: int, value: int) -> None:
        await self._async_set_adjust(zone, "treble", "TREBLESET", value, TONE_MAX)

    async def async_set_balance(self, zone: int, value: int) -> None:
        await self._async_set_adjust(zone, "balance", "BALSET", value, BALANCE_MAX)

    async def async_set_preamp(self, zone: int, value: int) -> None:
        await self._async_set_adjust(zone, "preamp", "PREAMPSET", value, PREAMP_MAX)

    async def async_set_volume_limit(self, zone: int, value: int) -> None:
        await self._async_set_adjust(
            zone, "volume_limit", "VOLLIMIT", value, MAX_VOLUME, minimum=VOLUME_LIMIT_MIN
        )

    # ------------------------------------------------------------------
    # whole-unit commands (every zone of this amplifier at once)
    # ------------------------------------------------------------------

    async def _async_all_zones(self, command: str, attr: str, value: bool) -> None:
        def _apply() -> None:
            for zone_state in self.zones.values():
                setattr(zone_state, attr, value)

        await self._run_command(_apply, command, all_zones=True)

    async def async_mute_all(self) -> None:
        await self._async_all_zones("MUTEALL", "mute", True)

    async def async_unmute_all(self) -> None:
        await self._async_all_zones("UNMUTEALL", "mute", False)

    async def async_standby_all(self) -> None:
        await self._async_all_zones("ZSTDBYALL", "standby", True)

    async def async_turn_on_all(self) -> None:
        await self._async_all_zones("ZTONALL", "standby", False)

    async def async_set_all_volume(self, volume: int) -> None:
        """Same volume on every zone - one VOLSET per zone, so each zone's
        own reply (and any VOLLIMIT clamp) is reflected individually."""
        for zone in sorted(self.zones):
            await self.async_set_volume(zone, volume)

    async def async_select_input_all(self, input_num: int) -> None:
        for zone in sorted(self.zones):
            await self.async_select_input(zone, input_num)

    async def async_set_output_mode(self, zone: int, mode: str) -> None:
        """SWSTEREO / SWMONO / SWBRIDGE. Write-only on the device: there is
        no command to read the mode back, so nothing local to update."""
        await self._run_command(lambda: None, OUTPUT_MODES[mode], zone, zone=zone)

    async def async_reset(self) -> None:
        """Reboot the multiroom over the network (RESET, manual 1.17).

        The connection dropping is the expected outcome here, not an error:
        the usual reconnect loop picks the unit back up once it's booted."""
        try:
            await self._run_command(lambda: None, "RESET")
        except HomeAssistantError as err:
            if err.translation_key != "connection_error":
                raise

    async def async_master_power(self, on: bool) -> None:
        def _apply() -> None:
            self.power = on

        await self._run_command(_apply, "PWRON" if on else "PWROFF")
