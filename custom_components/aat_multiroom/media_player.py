"""Media player entity: one per zone of an AAT multiroom amplifier.

State changes arrive through a dispatcher signal fired by the device object
(on every command reply and every unsolicited device message), so entities
never poll and reflect changes made anywhere (Home Assistant, the front
panel, the IR remote) almost instantly. Commands issued from Home Assistant
also update the local cache optimistically before the device confirms them,
so the UI reacts the moment you tap something.

The current input is exposed two ways here: as `media_title` (the field
most dashboard cards render as secondary/subtitle text under the entity
name - there's no real "now playing" track since this is an amplifier
zone, not a media source, so we repurpose it for the input name, which is
standard practice for AVR-style integrations), and as a selectable
`source` (SELECT_SOURCE), so the input can be changed from the
media_player card itself. This is in addition to, not a replacement for,
the per-input switch.* entities in switch.py, which stay as the
always-visible, "painted when active" way to do the same thing.
"""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_INPUT_NAMES, CONF_ZONE_NAMES, DOMAIN, MAX_VOLUME
from .device import AatMultiroomDevice


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: AatMultiroomDevice = hass.data[DOMAIN][entry.entry_id]
    entities = [
        AatZoneMediaPlayer(device, entry, zone_num) for zone_num in sorted(device.zones)
    ]
    async_add_entities(entities)


class AatZoneMediaPlayer(MediaPlayerEntity):
    """Represents a single output zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry, zone_num: int) -> None:
        self._device = device
        self._entry = entry
        self._zone_num = zone_num

        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_media_player"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone_num}")},
            name=zone_name,
            manufacturer="AAT - Advanced Audio Technologies",
            model=device.model,
            via_device=(DOMAIN, entry.entry_id),
        )
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._device.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._device.connected

    @property
    def state(self) -> MediaPlayerState:
        zone = self._device.zones.get(self._zone_num)
        if zone is None or zone.standby:
            return MediaPlayerState.OFF
        return MediaPlayerState.ON

    @property
    def volume_level(self) -> float | None:
        zone = self._device.zones.get(self._zone_num)
        if zone is None:
            return None
        return zone.volume / MAX_VOLUME

    @property
    def is_volume_muted(self) -> bool | None:
        zone = self._device.zones.get(self._zone_num)
        return None if zone is None else zone.mute

    @property
    def source(self) -> str | None:
        zone = self._device.zones.get(self._zone_num)
        if zone is None:
            return None
        input_names = self._entry.options.get(CONF_INPUT_NAMES, {})
        return input_names.get(str(zone.input), f"Entrada {zone.input}")

    @property
    def media_title(self) -> str | None:
        # Shown as secondary/subtitle text by most cards; there's no real
        # "now playing" track on an amplifier zone, so this doubles up as
        # the current-input display.
        return self.source

    @property
    def source_list(self) -> list[str] | None:
        input_names = self._entry.options.get(CONF_INPUT_NAMES, {})
        return [input_names[key] for key in sorted(input_names, key=int)]

    async def async_select_source(self, source: str) -> None:
        input_names = self._entry.options.get(CONF_INPUT_NAMES, {})
        for input_num, name in input_names.items():
            if name == source:
                await self._device.async_select_input(self._zone_num, int(input_num))
                return

    async def async_turn_on(self) -> None:
        await self._device.async_zone_power(self._zone_num, True)

    async def async_turn_off(self) -> None:
        await self._device.async_zone_power(self._zone_num, False)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._device.async_set_volume(self._zone_num, round(volume * MAX_VOLUME))

    async def async_volume_up(self) -> None:
        await self._device.async_volume_step(self._zone_num, True)

    async def async_volume_down(self) -> None:
        await self._device.async_volume_step(self._zone_num, False)

    async def async_mute_volume(self, mute: bool) -> None:
        await self._device.async_set_mute(self._zone_num, mute)
