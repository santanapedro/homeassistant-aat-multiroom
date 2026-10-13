"""Whole-multiroom action buttons.

Everything here acts on every zone of one amplifier at once, over that
unit's own TCP connection (so it never touches any other multiroom):

* mute all / unmute all            (MUTEALL / UNMUTEALL)
* every zone to standby / turn on  (ZSTDBYALL / ZTONALL)
* "all zones -> <input>"           one button per configured input
* restart the multiroom            (RESET) - disabled by default, since an
  accidental press drops the audio of every zone while it reboots

Buttons have no on/off state in Home Assistant, which is the right shape
for one-shot actions like these (the per-zone *state* lives in the zone
switches and media players, which update right after a press).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_INPUT_NAMES, DOMAIN
from .device import AatMultiroomDevice
from .entity import hub_device_info


@dataclass(frozen=True)
class HubAction:
    key: str  # unique_id suffix
    name: str
    icon: str
    method: str  # AatMultiroomDevice coroutine method to call


HUB_ACTIONS: tuple[HubAction, ...] = (
    HubAction("mute_all", "Mutar todas as zonas", "mdi:volume-off", "async_mute_all"),
    HubAction("unmute_all", "Desmutar todas as zonas", "mdi:volume-high", "async_unmute_all"),
    HubAction("standby_all", "Todas as zonas em standby", "mdi:speaker-off", "async_standby_all"),
    HubAction("turn_on_all", "Ligar todas as zonas", "mdi:speaker", "async_turn_on_all"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: AatMultiroomDevice = hass.data[DOMAIN][entry.entry_id]
    input_names: dict[str, str] = entry.options.get(CONF_INPUT_NAMES, {})

    entities: list[ButtonEntity] = [AatHubActionButton(device, entry, a) for a in HUB_ACTIONS]
    entities.extend(
        AatAllZonesInputButton(device, entry, input_num, input_names[str(input_num)])
        for input_num in sorted(int(key) for key in input_names)
    )
    entities.append(AatRestartButton(device, entry))
    async_add_entities(entities)


class _AatHubButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_device_info = hub_device_info(device, entry)

    @property
    def available(self) -> bool:
        return self._device.connected


class AatHubActionButton(_AatHubButton):
    """Mute all / unmute all / standby all / turn on all."""

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry, action: HubAction) -> None:
        super().__init__(device, entry)
        self._action = action
        self._attr_name = action.name
        self._attr_icon = action.icon
        self._attr_unique_id = f"{entry.entry_id}_{action.key}"

    async def async_press(self) -> None:
        await getattr(self._device, self._action.method)()


class AatAllZonesInputButton(_AatHubButton):
    """Puts every zone of this multiroom on one input."""

    _attr_icon = "mdi:import"

    def __init__(
        self, device: AatMultiroomDevice, entry: ConfigEntry, input_num: int, input_name: str
    ) -> None:
        super().__init__(device, entry)
        self._input_num = input_num
        self._attr_name = f"Todas as zonas → {input_name}"
        self._attr_unique_id = f"{entry.entry_id}_all_input_{input_num}"

    async def async_press(self) -> None:
        await self._device.async_select_input_all(self._input_num)


class AatRestartButton(_AatHubButton):
    """Reboots the multiroom over the network (RESET)."""

    _attr_name = "Reiniciar multiroom"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    # An accidental press cuts the audio of every zone while it reboots, so
    # it has to be enabled on purpose.
    _attr_entity_registry_enabled_default = False

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry) -> None:
        super().__init__(device, entry)
        self._attr_unique_id = f"{entry.entry_id}_reset"

    async def async_press(self) -> None:
        await self._device.async_reset()
