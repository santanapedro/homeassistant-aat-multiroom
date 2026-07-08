"""One button per zone per audio input, instead of a source dropdown.

Pressing a button sends INPSET for that zone/input straight away. The
currently active input for a zone is still shown as text on the zone's
media_player entity (its "source" attribute); buttons in Home Assistant are
stateless/momentary, so they cannot be highlighted as "selected" - this is a
deliberate trade-off in exchange for a simple, dashboard-friendly grid of
input buttons instead of a dropdown.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_INPUT_NAMES, CONF_ZONE_NAMES, DOMAIN
from .device import AatMultiroomDevice


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: AatMultiroomDevice = hass.data[DOMAIN][entry.entry_id]
    input_names: dict[str, str] = entry.options.get(CONF_INPUT_NAMES, {})
    input_numbers = sorted(int(key) for key in input_names)

    entities = [
        AatInputButton(device, entry, zone_num, input_num)
        for zone_num in sorted(device.zones)
        for input_num in input_numbers
    ]
    async_add_entities(entities)


class AatInputButton(ButtonEntity):
    """A single "select this input for this zone" button."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:import"

    def __init__(
        self,
        device: AatMultiroomDevice,
        entry: ConfigEntry,
        zone_num: int,
        input_num: int,
    ) -> None:
        self._device = device
        self._zone_num = zone_num
        self._input_num = input_num

        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        input_name = entry.options.get(CONF_INPUT_NAMES, {}).get(
            str(input_num), f"Entrada {input_num}"
        )

        self._attr_name = input_name
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_input_{input_num}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone_num}")},
            name=zone_name,
            manufacturer="AAT - Advanced Audio Technologies",
            model=device.model,
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def available(self) -> bool:
        return self._device.connected

    async def async_press(self) -> None:
        await self._device.async_select_input(self._zone_num, self._input_num)
