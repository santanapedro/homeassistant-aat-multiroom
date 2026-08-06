"""The AAT Multiroom integration.

Each config entry represents one physical AAT multiroom amplifier, with its
own TCP connection and its own cached state. Entries are fully independent
of one another, so multiple amplifiers can be added and none of them
affects the others.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .api import AatConnectionError
from .const import DOMAIN
from .device import AatMultiroomDevice

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    device = AatMultiroomDevice(hass, entry)
    try:
        await device.async_setup()
    except AatConnectionError as err:
        _LOGGER.warning(
            "Could not connect to AAT multiroom %s at %s:%s: %s",
            entry.title,
            device.host,
            device.port,
            err,
        )
        raise ConfigEntryNotReady(
            f"Não foi possível conectar ao Multiroom AAT em {device.host}:{device.port}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = device

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="AAT - Advanced Audio Technologies",
        model=device.model,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options (zone/input names) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        device: AatMultiroomDevice = hass.data[DOMAIN].pop(entry.entry_id)
        await device.async_close()
    return unload_ok
