"""The AAT Multiroom integration.

Each config entry represents one physical AAT multiroom amplifier, with its
own TCP connection and its own cached state. Entries are fully independent
of one another, so multiple amplifiers can be added and none of them
affects the others.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .api import AatConnectionError
from .const import DOMAIN, GROUP_MAX, MAX_VOLUME
from .dashboard import async_ensure_dashboard
from .device import AatMultiroomDevice
from .group import ALL_ACTIONS, async_send_group_command

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_GROUP_COMMAND = "group_command"
GROUP_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("action"): vol.In(ALL_ACTIONS),
        vol.Optional("group", default=0): vol.All(vol.Coerce(int), vol.Range(0, GROUP_MAX)),
        vol.Optional("value"): vol.All(vol.Coerce(int), vol.Range(0, MAX_VOLUME)),
        vol.Optional("config_entry_id"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide services (once, not per config entry)."""

    async def _handle_group_command(call: ServiceCall) -> None:
        devices: dict[str, AatMultiroomDevice] = hass.data.get(DOMAIN, {})
        entry_id = call.data.get("config_entry_id")
        if entry_id is not None:
            device = devices.get(entry_id)
            if device is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="unknown_config_entry"
                )
        else:
            device = next(iter(devices.values()), None)
            if device is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="no_multiroom_loaded"
                )
        try:
            await async_send_group_command(
                hass,
                device.host,
                call.data["action"],
                call.data["group"],
                call.data.get("value"),
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_group_command",
                translation_placeholders={"error": str(err)},
            ) from err

    hass.services.async_register(
        DOMAIN, SERVICE_GROUP_COMMAND, _handle_group_command, schema=GROUP_COMMAND_SCHEMA
    )
    return True


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
    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="AAT - Advanced Audio Technologies",
        model=device.model,
    )
    # Used by every zone entity's DeviceInfo.via_device_id (in addition to
    # the older via_device identifiers tuple, for backward compatibility).
    device.hub_device_id = hub_device.id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Best-effort convenience dashboard; never blocks or fails setup - see
    # dashboard.py's module docstring for why this is scheduled instead of
    # awaited inline, and why it's wrapped so defensively.
    hass.async_create_task(async_ensure_dashboard(hass, entry, device))

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
