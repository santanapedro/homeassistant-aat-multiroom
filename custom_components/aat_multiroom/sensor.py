"""One sensor per zone, showing the name of its currently selected audio
input as plain text - a reliable, always-visible way to see it, rather
than relying on the media_player's "source" attribute being surfaced by
whatever dashboard card the user happens to have."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_INPUT_NAMES, CONF_ZONE_NAMES, DOMAIN
from .device import AatMultiroomDevice


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: AatMultiroomDevice = hass.data[DOMAIN][entry.entry_id]
    entities = [
        AatZoneInputSensor(device, entry, zone_num) for zone_num in sorted(device.zones)
    ]
    async_add_entities(entities)


class AatZoneInputSensor(SensorEntity):
    """Current audio input of a zone, as a plain-text sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:import"

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry, zone_num: int) -> None:
        self._device = device
        self._entry = entry
        self._zone_num = zone_num
        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        self._attr_name = "Entrada atual"
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_current_input"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone_num}")},
            name=zone_name,
            manufacturer="AAT - Advanced Audio Technologies",
            model=device.model,
            via_device=(DOMAIN, entry.entry_id),
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
    def native_value(self) -> str | None:
        zone = self._device.zones.get(self._zone_num)
        if zone is None:
            return None
        input_names = self._entry.options.get(CONF_INPUT_NAMES, {})
        return input_names.get(str(zone.input), f"Entrada {zone.input}")
