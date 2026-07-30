"""High level state holder for one AAT multiroom amplifier.

Wraps AatMultiroomClient with:
  * a cache of per-zone state (input/volume/mute/standby)
  * optimistic local updates so the UI reacts instantly to a tap, before the
    device even answers
  * a dispatcher signal fired on every state change (from our own commands,
    from unsolicited device messages, or from periodic re-sync)
  * automatic reconnection with backoff
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import AatConnectionError, AatMultiroomClient
from .const import CONF_MODEL, DEFAULT_PORT, DOMAIN, MAX_VOLUME, REFRESH_INTERVAL
from homeassistant.const import CONF_HOST, CONF_PORT

_LOGGER = logging.getLogger(__name__)

_RECONNECT_MIN_DELAY = 3
_RECONNECT_MAX_DELAY = 60


@dataclass
class ZoneState:
    """Cached state of a single zone."""

    input: int = 1
    volume: int = 0
    mute: bool = False
    standby: bool = True


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

        self.client = AatMultiroomClient(self.host, self.port)
        self.client.add_listener(self._on_message)
        self.client.on_disconnected = self._schedule_reconnect

        self._refresh_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._closing = False

    @property
    def signal(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_update"

    @property
    def connected(self) -> bool:
        return self.client.connected

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

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
        self._refresh_task = self.hass.async_create_task(self._periodic_refresh())

    async def async_close(self) -> None:
        self._closing = True
        for task in (self._refresh_task, self._reconnect_task):
            if task is not None:
                task.cancel()
        await self.client.async_disconnect()

    async def _periodic_refresh(self) -> None:
        while not self._closing:
            await asyncio.sleep(REFRESH_INTERVAL)
            if not self.client.connected:
                continue
            try:
                await self.async_refresh_full_state()
            except AatConnectionError:
                _LOGGER.debug("Periodic refresh failed for %s", self.host)

    def _schedule_reconnect(self) -> None:
        self._notify()
        if self._closing:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.hass.async_create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        delay = _RECONNECT_MIN_DELAY
        while not self._closing and not self.client.connected:
            await asyncio.sleep(delay)
            if self._closing:
                return
            try:
                await self.client.async_connect()
                await self.async_refresh_full_state()
            except AatConnectionError:
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)
            else:
                _LOGGER.info("Reconnected to AAT multiroom %s:%s", self.host, self.port)
                self._notify()
                return

    # ------------------------------------------------------------------
    # state sync
    # ------------------------------------------------------------------

    async def async_refresh_full_state(self) -> None:
        args = await self.client.async_send_command("GETALL")
        self._parse_getall(args)
        for zone_num in list(self.zones):
            try:
                zargs = await self.client.async_send_command("ZSTDBYGET", zone_num)
                if len(zargs) >= 2:
                    self.zones[zone_num].standby = zargs[1].upper() == "ON"
            except AatConnectionError:
                break
        self._notify()

    def _parse_getall(self, args: list[str]) -> None:
        if len(args) < 5:
            return
        self.model = args[0]
        self.power = args[2].upper() == "ON"
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
        self.zone_count = zone_count

    # ------------------------------------------------------------------
    # push updates (our own replies AND unsolicited device messages)
    # ------------------------------------------------------------------

    def _on_message(self, cmd: str, args: list[str]) -> None:
        handler = self._HANDLERS.get(cmd)
        if handler is None:
            return
        try:
            handler(self, args)
        except (ValueError, IndexError):
            _LOGGER.debug("Could not parse %s %s", cmd, args)
            return
        self._notify()

    def _h_zstdbyon(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).standby = True

    def _h_zstdbyoff(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).standby = False

    def _h_zstdbytog(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).standby = args[1].upper() == "ON"

    def _h_zstdbyget(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).standby = args[1].upper() == "ON"

    def _h_muteon(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).mute = True

    def _h_muteoff(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).mute = False

    def _h_mutetog(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).mute = args[1].upper() == "ON"

    def _h_muteget(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).mute = args[1].upper() == "ON"

    def _h_volchange(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).volume = int(args[1])

    def _h_inpchange(self, args: list[str]) -> None:
        self._get_zone(int(args[0])).input = int(args[1])

    def _h_pwron(self, args: list[str]) -> None:
        self.power = True

    def _h_pwroff(self, args: list[str]) -> None:
        self.power = False

    def _h_pwrtog(self, args: list[str]) -> None:
        self.power = args[0].upper() == "ON"

    def _h_pwrget(self, args: list[str]) -> None:
        self.power = args[0].upper() == "ON"

    _HANDLERS: dict[str, Callable[["AatMultiroomDevice", list[str]], None]] = {
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
        "PWRON": _h_pwron,
        "PWROFF": _h_pwroff,
        "PWRTOG": _h_pwrtog,
        "PWRGET": _h_pwrget,
    }

    # ------------------------------------------------------------------
    # commands (optimistic: update cache immediately, then talk to device)
    # ------------------------------------------------------------------

    async def _run_command(
        self, apply_optimistic: Callable[[], None], cmd: str, *args: object
    ) -> None:
        apply_optimistic()
        self._notify()
        try:
            await self.client.async_send_command(cmd, *args)
        except Exception:
            # Local state may now be wrong; resync as soon as possible.
            self.hass.async_create_task(self.async_refresh_full_state())
            raise

    async def async_zone_power(self, zone: int, on: bool) -> None:
        def _apply() -> None:
            self._get_zone(zone).standby = not on

        await self._run_command(_apply, "ZSTDBYOFF" if on else "ZSTDBYON", zone)

    async def async_set_volume(self, zone: int, volume: int) -> None:
        volume = max(0, min(MAX_VOLUME, volume))

        def _apply() -> None:
            self._get_zone(zone).volume = volume

        await self._run_command(_apply, "VOLSET", zone, volume)

    async def async_volume_step(self, zone: int, up: bool) -> None:
        def _apply() -> None:
            zone_state = self._get_zone(zone)
            if up:
                zone_state.volume = min(MAX_VOLUME, zone_state.volume + 1)
            else:
                zone_state.volume = max(0, zone_state.volume - 1)

        await self._run_command(_apply, "VOL+" if up else "VOL-", zone)

    async def async_set_mute(self, zone: int, mute: bool) -> None:
        def _apply() -> None:
            self._get_zone(zone).mute = mute

        await self._run_command(_apply, "MUTEON" if mute else "MUTEOFF", zone)

    async def async_select_input(self, zone: int, input_num: int) -> None:
        def _apply() -> None:
            self._get_zone(zone).input = input_num

        await self._run_command(_apply, "INPSET", zone, input_num)

    async def async_master_power(self, on: bool) -> None:
        def _apply() -> None:
            self.power = on

        await self._run_command(_apply, "PWRON" if on else "PWROFF")
