"""Per-zone output mode: stereo / mono / bridge (SWSTEREO / SWMONO /
SWBRIDGE, manual 1.10.4 - 1.10.6).

Handled with more caution than the other entities, because of what the
commands are:

* They are **write-only** on the device: there is no command to read the
  mode back and it isn't part of GETALL. The select therefore can't show
  what the amplifier is actually set to - it is an *assumed* state, the
  last option chosen through Home Assistant (restored across restarts),
  and a change made elsewhere (installer menu, AAT app) isn't reflected.
* **Bridge** changes how the amplifier drives the speaker terminals
  (doubling the power of a channel). Choosing it for a zone whose speakers
  are wired for stereo is a hardware-level mistake, not a cosmetic one.
* The PMRH 2/4/6 and the non-amplified PMA 1/2 have no such hardware: the
  commands are accepted and do nothing (manual 1.10.1.1), so no select is
  created for those models at all.

For those reasons these entities are disabled by default and have to be
enabled on purpose.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_ZONE_NAMES, DOMAIN, MODELS_WITHOUT_OUTPUT_MODE_PREFIXES
from .device import AatMultiroomDevice
from .entity import zone_device_info

# Option label -> OUTPUT_MODES key.
OPTIONS: dict[str, str] = {"Estéreo": "stereo", "Mono": "mono", "Bridge": "bridge"}


def model_supports_output_mode(model: str) -> bool:
    return not model.upper().startswith(MODELS_WITHOUT_OUTPUT_MODE_PREFIXES)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: AatMultiroomDevice = hass.data[DOMAIN][entry.entry_id]
    if not model_supports_output_mode(device.model):
        return
    async_add_entities(
        AatOutputModeSelect(device, entry, zone_num) for zone_num in sorted(device.zones)
    )


class AatOutputModeSelect(SelectEntity, RestoreEntity):
    """Assumed-state stereo / mono / bridge selector for one zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = True
    _attr_name = "Modo de saída"
    _attr_icon = "mdi:speaker-multiple"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_options = list(OPTIONS)

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry, zone_num: int) -> None:
        self._device = device
        self._zone_num = zone_num
        self._attr_current_option: str | None = None
        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_output_mode"
        self._attr_device_info = zone_device_info(device, entry, zone_num, zone_name)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in OPTIONS:
            self._attr_current_option = last_state.state

    @property
    def available(self) -> bool:
        return self._device.connected

    async def async_select_option(self, option: str) -> None:
        await self._device.async_set_output_mode(self._zone_num, OPTIONS[option])
        self._attr_current_option = option
        self.async_write_ha_state()
