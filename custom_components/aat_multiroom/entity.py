"""Small shared helper so every zone entity builds its DeviceInfo the
same way, instead of repeating this in media_player.py/switch.py/sensor.py."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .device import AatMultiroomDevice


def zone_device_info(
    device: AatMultiroomDevice, entry: ConfigEntry, zone_num: int, zone_name: str
) -> DeviceInfo:
    """DeviceInfo for a zone's own device, linked to the multiroom hub.

    Prefers `via_device_id` (the hub's actual device registry id) when we
    have it, falling back to the older `via_device` identifiers-tuple form
    otherwise. `via_device` is deprecated (removal planned for 2027.8.0,
    per a warning caught against a live instance) in favor of
    `via_device_id` - but passing BOTH at once is not a safe transitional
    move: newer Home Assistant (confirmed live on 2026.9.1) raises
    `HomeAssistantError: Passing both via_device and via_device_id is not
    allowed`, which aborted every zone entity's setup outright. So this
    picks exactly one, never both.
    """
    info: dict = {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_zone_{zone_num}")},
        "name": zone_name,
        "manufacturer": "AAT - Advanced Audio Technologies",
        "model": device.model,
    }
    if device.hub_device_id:
        info["via_device_id"] = device.hub_device_id
    else:
        info["via_device"] = (DOMAIN, entry.entry_id)
    return DeviceInfo(**info)
