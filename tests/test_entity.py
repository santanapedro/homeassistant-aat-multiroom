"""Tests for zone_device_info() - the shared DeviceInfo builder.

Regression coverage for two bugs caught live, in sequence:

1. Home Assistant deprecated DeviceInfo's `via_device` in favor of
   `via_device_id` (removal planned for 2027.8.0).
2. Sending BOTH at once (an attempted fix for #1) turned out to raise
   `HomeAssistantError: Passing both via_device and via_device_id is not
   allowed` on newer Home Assistant (confirmed live on 2026.9.1) - which
   aborted setup for every zone entity. So exactly one of the two must be
   sent, never both: `via_device_id` when the hub's registry id is known,
   `via_device` otherwise.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.aat_multiroom.entity import zone_device_info


def make_entry():
    return SimpleNamespace(entry_id="entry1")


def test_uses_via_device_id_only_when_hub_device_id_is_known() -> None:
    device = SimpleNamespace(model="PMR7", hub_device_id="hub-123")

    info = zone_device_info(device, make_entry(), 1, "Sala")

    assert info["via_device_id"] == "hub-123"
    assert "via_device" not in info
    assert info["identifiers"] == {("aat_multiroom", "entry1_zone_1")}
    assert info["name"] == "Sala"
    assert info["model"] == "PMR7"


def test_falls_back_to_via_device_when_hub_device_id_is_unknown() -> None:
    """Must not send via_device_id=None - that's not the same as omitting
    the key, and could be read by Home Assistant as "explicitly no via
    device" instead of "use via_device instead"."""
    device = SimpleNamespace(model="PMR7", hub_device_id=None)

    info = zone_device_info(device, make_entry(), 1, "Sala")

    assert "via_device_id" not in info
    assert info["via_device"] == ("aat_multiroom", "entry1")
