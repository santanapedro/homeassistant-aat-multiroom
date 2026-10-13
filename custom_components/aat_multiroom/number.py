"""Per-zone audio adjustments as `number` sliders: bass, treble, balance
and pre-amp gain (manual sections 1.8, 1.9 and 1.11).

The device speaks small raw integers (bass/treble 0..14, balance 0..20,
pre-amp 0..7); these entities show the values in dB instead, which is what
actually means something to whoever is adjusting them:

    bass / treble   -14 .. +14 dB, 2 dB steps   (raw 0..14, 7 = flat)
    balance         -10 .. +10 dB, 1 dB steps   (raw 0..20, 10 = centered;
                                                 negative = left, positive = right)
    pre-amp gain      0 .. +14 dB, 2 dB steps   (raw 0..7)

They're marked as configuration entities: these are set-and-forget
adjustments, so they shouldn't crowd the default entity lists - the
automatic dashboard puts them in a collapsible "Ajustes de áudio" section
per zone instead (see dashboard.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BALANCE_CENTER,
    BALANCE_MAX,
    CONF_ZONE_NAMES,
    DOMAIN,
    MAX_VOLUME,
    PREAMP_MAX,
    TONE_FLAT,
    TONE_MAX,
    VOLUME_LIMIT_MIN,
)
from .device import AatMultiroomDevice
from .entity import hub_device_info, zone_device_info


@dataclass(frozen=True)
class AudioControl:
    """How one raw device value maps to the dB value shown to the user:
    `display = (raw - offset) * step`."""

    key: str  # unique_id suffix
    name: str
    icon: str
    attr: str  # ZoneState attribute holding the raw value
    setter: str  # AatMultiroomDevice method that sends it
    raw_max: int
    offset: int  # raw value that corresponds to display 0 (the "flat" point)
    step: int  # dB per raw step

    @property
    def min_value(self) -> float:
        return float((0 - self.offset) * self.step)

    @property
    def max_value(self) -> float:
        return float((self.raw_max - self.offset) * self.step)

    def to_display(self, raw: int) -> float:
        return float((raw - self.offset) * self.step)

    def to_raw(self, display: float) -> int:
        # Half-up (not Python's round(), which rounds ties to even): a value
        # exactly between two steps should always go the same direction.
        raw = math.floor(display / self.step + 0.5) + self.offset
        return max(0, min(self.raw_max, raw))


AUDIO_CONTROLS: tuple[AudioControl, ...] = (
    AudioControl("bass", "Graves", "mdi:music-clef-bass", "bass", "async_set_bass",
                 TONE_MAX, TONE_FLAT, 2),
    AudioControl("treble", "Agudos", "mdi:music-clef-treble", "treble", "async_set_treble",
                 TONE_MAX, TONE_FLAT, 2),
    AudioControl("balance", "Balanço", "mdi:scale-balance", "balance", "async_set_balance",
                 BALANCE_MAX, BALANCE_CENTER, 1),
    AudioControl("preamp", "Ganho pré-amp", "mdi:amplifier", "preamp", "async_set_preamp",
                 PREAMP_MAX, 0, 2),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: AatMultiroomDevice = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [AatAllZonesVolumeNumber(device, entry)]
    for zone_num in sorted(device.zones):
        entities.extend(
            AatAudioNumber(device, entry, zone_num, control) for control in AUDIO_CONTROLS
        )
        # VOLLIMIT needs firmware >= 3.08; units that answered "unknown
        # command" to it simply don't get the slider.
        if device.vollimit_supported is not False:
            entities.append(AatVolumeLimitNumber(device, entry, zone_num))
    async_add_entities(entities)


class AatAudioNumber(NumberEntity):
    """One audio adjustment (bass, treble, balance or pre-amp) of one zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = "dB"

    def __init__(
        self,
        device: AatMultiroomDevice,
        entry: ConfigEntry,
        zone_num: int,
        control: AudioControl,
    ) -> None:
        self._device = device
        self._zone_num = zone_num
        self._control = control
        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        self._attr_name = control.name
        self._attr_icon = control.icon
        self._attr_native_min_value = control.min_value
        self._attr_native_max_value = control.max_value
        self._attr_native_step = float(control.step)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_{control.key}"
        self._attr_device_info = zone_device_info(device, entry, zone_num, zone_name)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._device.zone_signal(self._zone_num), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._device.connected

    @property
    def native_value(self) -> float | None:
        zone = self._device.zones.get(self._zone_num)
        if zone is None:
            return None
        return self._control.to_display(getattr(zone, self._control.attr))

    async def async_set_native_value(self, value: float) -> None:
        setter = getattr(self._device, self._control.setter)
        await setter(self._zone_num, self._control.to_raw(value))


class AatVolumeLimitNumber(NumberEntity):
    """Per-zone volume ceiling (VOLLIMIT, firmware >= 3.08).

    44..87, where 87 means "no limit". Protects speakers from a runaway
    volume setting - the device enforces it, including against changes
    made from the AAT app."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:volume-lock"
    _attr_name = "Limite de volume"
    _attr_native_min_value = float(VOLUME_LIMIT_MIN)
    _attr_native_max_value = float(MAX_VOLUME)
    _attr_native_step = 1.0

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry, zone_num: int) -> None:
        self._device = device
        self._zone_num = zone_num
        zone_name = entry.options.get(CONF_ZONE_NAMES, {}).get(str(zone_num), f"Zona {zone_num}")
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone_num}_volume_limit"
        self._attr_device_info = zone_device_info(device, entry, zone_num, zone_name)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._device.zone_signal(self._zone_num), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._device.connected

    @property
    def native_value(self) -> float | None:
        zone = self._device.zones.get(self._zone_num)
        return None if zone is None else float(zone.volume_limit)

    async def async_set_native_value(self, value: float) -> None:
        await self._device.async_set_volume_limit(self._zone_num, int(round(value)))


class AatAllZonesVolumeNumber(NumberEntity):
    """One slider for the volume of every zone of this multiroom at once.

    Shows the average of the zones' current volumes; setting it applies the
    same volume to all of them (one VOLSET per zone, so a zone's own
    volume limit still applies to it)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-high"
    _attr_name = "Volume geral"
    _attr_native_min_value = 0.0
    _attr_native_max_value = float(MAX_VOLUME)
    _attr_native_step = 1.0

    def __init__(self, device: AatMultiroomDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_unique_id = f"{entry.entry_id}_all_volume"
        self._attr_device_info = hub_device_info(device, entry)

    async def async_added_to_hass(self) -> None:
        # The average moves whenever any zone's volume does.
        signals = [self._device.signal] + [
            self._device.zone_signal(zone) for zone in sorted(self._device.zones)
        ]
        for signal in signals:
            self.async_on_remove(
                async_dispatcher_connect(self.hass, signal, self._handle_update)
            )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._device.connected

    @property
    def native_value(self) -> float | None:
        volumes = [zone.volume for zone in self._device.zones.values()]
        if not volumes:
            return None
        return float(round(sum(volumes) / len(volumes)))

    async def async_set_native_value(self, value: float) -> None:
        await self._device.async_set_all_volume(int(round(value)))
