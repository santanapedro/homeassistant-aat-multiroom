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

    Sets both `via_device` (an identifiers tuple) and, when available,
    `via_device_id` (the hub's actual device registry id): newer Home
    Assistant versions want the latter (via_device is deprecated, removal
    planned for 2027.8.0 per a warning caught against a live instance),
    while older ones only understand the former. Passing both keeps this
    working across the version range without betting on just one.
    """
    info: dict = {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_zone_{zone_num}")},
        "name": zone_name,
        "manufacturer": "AAT - Advanced Audio Technologies",
        "model": device.model,
        "via_device": (DOMAIN, entry.entry_id),
    }
    if device.hub_device_id:
        info["via_device_id"] = device.hub_device_id
    return DeviceInfo(**info)
