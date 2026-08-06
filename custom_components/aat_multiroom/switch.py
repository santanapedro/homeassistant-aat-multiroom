"""Switch entities for AAT Multiroom.

Three kinds:

* AatMasterPowerSwitch - one per multiroom unit, turns the whole amplifier
  on/off (PWRON/PWROFF). Attached to the multiroom "hub" device.
* AatZonePowerSwitch - one per zone, an explicit, clearly visible on/off
  control (ZSTDBYON/OFF, the same command the zone's media_player already
  uses) whose icon changes between "on" and "off" states.
* AatInputSwitch - one per zone per audio input. Turning one on selects
  that input (INPSET); it lights up (icon + accent color) whenever it's
  the zone's currently active input, and all its sibling inputs
  automatically show as off. This replaced the old ButtonEntity-based
  input selector: buttons have no on/off state in Home Assistant, so
  their icon could never be highlighted when selected - switches can.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    input_names: dict[str, str] = entry.options.get(CONF_INPUT_NAMES, {})
    input_numbers = sorted(int(key) for key in input_names)

    entities: list[SwitchEntity] = [AatMasterPowerSwitch(device, entry)]
    for zone_num in sorted(device.zones):
        entities.append(AatZonePowerSwitch(device, entry, zone_num))
        entities.extend(
            AatInputSwitch(device, entry, zone_num, input_num) for input_num in input_numbers
        )
    async_add_entities(entities)


class _AatSwitchBase(SwitchEntity):
    """Shared push-update wiring for every switch type."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: AatMultiroomDevice) -> None:
        self._device = device

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


class AatMasterPowerSwitch(_AatSwitchBase):
    """Turns the whole amplifier on/off (PWRON/PWROFF), all zones at once."""

    _attr_name = "Power"

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{entry.entry_id}_master_power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="AAT - Advanced Audio Technologies",
            model=device.model,
        )

    @property
    def is_on(self) -> bool:
        return self._device.power

    @property
    def icon(self) -> str:
        return "mdi:power" if self.is_on else "mdi:power-off"

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.async_master_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.async_master_power(False)


class AatZonePowerSwitch(_AatSwitchBase):
    """Explicit, clearly-visible on/off switch for a single zone."""

    _attr_name = "Power"

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry, zone_num: int) -> None:
        super().__init__(device)
        self._zone_num = zone_num
        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_power_switch"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone_num}")},
            name=zone_name,
            manufacturer="AAT - Advanced Audio Technologies",
            model=device.model,
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def is_on(self) -> bool:
        zone = self._device.zones.get(self._zone_num)
        return False if zone is None else not zone.standby

    @property
    def icon(self) -> str:
        return "mdi:speaker" if self.is_on else "mdi:speaker-off"

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.async_zone_power(self._zone_num, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._device.async_zone_power(self._zone_num, False)


class AatInputSwitch(_AatSwitchBase):
    """One per zone per audio input; "on" means it's the zone's currently
    selected input (mutually exclusive with its siblings)."""

    def __init__(
        self, device: AatMultiroomDevice, entry: ConfigEntry, zone_num: int, input_num: int
    ) -> None:
        super().__init__(device)
        self._zone_num = zone_num
        self._input_num = input_num
        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        input_name = entry.options.get(CONF_INPUT_NAMES, {}).get(
            str(input_num), f"Entrada {input_num}"
        )
        self._attr_name = input_name
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_input_{input_num}_switch"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone_num}")},
            name=zone_name,
            manufacturer="AAT - Advanced Audio Technologies",
            model=device.model,
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def is_on(self) -> bool:
        zone = self._device.zones.get(self._zone_num)
        return zone is not None and zone.input == self._input_num

    @property
    def icon(self) -> str:
        return "mdi:radiobox-marked" if self.is_on else "mdi:radiobox-blank"

    async def async_turn_on(self, **kwargs) -> None:
        await self._device.async_select_input(self._zone_num, self._input_num)

    async def async_turn_off(self, **kwargs) -> None:
        # The hardware has no "deselect" / "no input" state - you can only
        # switch to a different input (by turning that sibling switch on
        # instead). Turning the active one off is a no-op; it will simply
        # stay "on" since the zone's real input never changed.
        return
