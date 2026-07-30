"""Switch entities for AAT Multiroom.

Two kinds, both in addition to (not replacing) the power icon already built
into each zone's media_player entity:

* AatMasterPowerSwitch - one per multiroom unit, turns the whole amplifier
  on/off (PWRON/PWROFF). Attached to the multiroom "hub" device.
* AatZonePowerSwitch - one per zone, an explicit, clearly visible on/off
  control (ZSTDBYON/OFF, the same command the zone's media_player already
  uses) whose icon changes between "on" and "off" states.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ZONE_NAMES, DOMAIN
from .device import AatMultiroomDevice


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: AatMultiroomDevice = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [AatMasterPowerSwitch(device, entry)]
    entities.extend(
        AatZonePowerSwitch(device, entry, zone_num) for zone_num in sorted(device.zones)
    )
    async_add_entities(entities)


class _AatSwitchBase(SwitchEntity):
    """Shared push-update wiring for both switch types."""

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
