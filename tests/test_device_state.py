"""Unit tests for AatMultiroomDevice's state parsing and push-update
handlers - the device-level layer that sits on top of what
test_api_protocol.py already validates at the wire/client level.

These use plain SimpleNamespace stand-ins for hass/ConfigEntry instead of
the full pytest-homeassistant-custom-component harness, since the code
under test here never touches anything beyond entry.data/entry.entry_id
and hass.data.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.aat_multiroom.device import AatMultiroomDevice, ZoneState


def make_entry(model: str = "", entry_id: str = "test_entry"):
    return SimpleNamespace(
        data={"host": "127.0.0.1", "port": 5000, "model": model},
        entry_id=entry_id,
        options={},
    )


def make_hass():
    # Minimal stand-in for HomeAssistant: just enough for
    # async_dispatcher_send (used by AatMultiroomDevice._notify) to work
    # without raising outside of a real running Home Assistant instance.
    return SimpleNamespace(data={}, verify_event_loop_thread=lambda *args, **kwargs: None)


def make_device(model: str = "") -> AatMultiroomDevice:
    return AatMultiroomDevice(make_hass(), make_entry(model=model))


def test_zone_state_defaults() -> None:
    zone = ZoneState()
    assert zone.input == 1
    assert zone.volume == 0
    assert zone.mute is False
    assert zone.standby is True


# ---------------------------------------------------------------------
# GETALL parsing (manual section 1.13), same examples as
# test_api_protocol.py but checked at the ZoneState level.
# ---------------------------------------------------------------------


def test_parse_getall_pmr7_six_zones() -> None:
    device = make_device()
    args = (
        "PMR7 V1.13 OFF 12345 60 "
        "6 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7"
    ).split()

    device._parse_getall(args)

    assert device.model == "PMR7"
    assert device.power is False  # "OFF" in the manual's example
    assert device.zone_count == 6
    assert set(device.zones) == {1, 2, 3, 4, 5, 6}
    zone1 = device.zones[1]
    assert (zone1.input, zone1.volume, zone1.mute) == (6, 30, False)
    assert device.zones[2].input == 5


def test_parse_getall_pmr6_four_zones_power_on() -> None:
    device = make_device()
    args = (
        "PMR6 V1.13 ON 12345 60 "
        "6 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7"
    ).split()

    device._parse_getall(args)

    assert device.power is True
    assert device.zone_count == 4
    assert set(device.zones) == {1, 2, 3, 4}


def test_parse_getall_ignores_short_response() -> None:
    device = make_device()
    device._parse_getall(["only", "four", "tokens"])
    assert device.zone_count == 0
    assert device.zones == {}


# ---------------------------------------------------------------------
# Push-update handlers - covers our own command replies AND unsolicited
# ("n") messages, since both flow through the same _HANDLERS table.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cmd", "args", "check"),
    [
        ("ZSTDBYON", ["2"], lambda d: d.zones[2].standby is True),
        ("ZSTDBYOFF", ["2"], lambda d: d.zones[2].standby is False),
        ("ZSTDBYTOG", ["2", "ON"], lambda d: d.zones[2].standby is True),
        ("ZSTDBYGET", ["2", "OFF"], lambda d: d.zones[2].standby is False),
        ("MUTEON", ["3"], lambda d: d.zones[3].mute is True),
        ("MUTEOFF", ["3"], lambda d: d.zones[3].mute is False),
        ("MUTETOG", ["3", "ON"], lambda d: d.zones[3].mute is True),
        ("MUTEGET", ["3", "OFF"], lambda d: d.zones[3].mute is False),
        ("VOL+", ["1", "10"], lambda d: d.zones[1].volume == 10),
        ("VOL-", ["1", "5"], lambda d: d.zones[1].volume == 5),
        ("VOLGET", ["1", "25"], lambda d: d.zones[1].volume == 25),
        ("VOLSET", ["1", "15"], lambda d: d.zones[1].volume == 15),
        ("INPSET", ["1", "4"], lambda d: d.zones[1].input == 4),
        ("INPGET", ["1", "2"], lambda d: d.zones[1].input == 2),
        ("PWRON", [], lambda d: d.power is True),
        ("PWROFF", [], lambda d: d.power is False),
        ("PWRTOG", ["ON"], lambda d: d.power is True),
        ("PWRGET", ["OFF"], lambda d: d.power is False),
    ],
)
def test_push_handlers_update_state(cmd, args, check) -> None:
    device = make_device()
    handler = device._HANDLERS[cmd]
    handler(device, args)
    assert check(device)


def test_on_message_ignores_unknown_command() -> None:
    device = make_device()
    # MODEL isn't a state-changing command we track; must be a silent no-op.
    device._on_message("MODEL", ["PMR7"])


def test_on_message_full_dispatch_path_does_not_raise() -> None:
    """Exercises the real _on_message -> _notify -> async_dispatcher_send
    path (not just the handler function in isolation), so we know the
    dispatcher signal used to update entities actually fires cleanly."""
    device = make_device()
    device._on_message("ZSTDBYON", ["1"])
    assert device.zones[1].standby is True
