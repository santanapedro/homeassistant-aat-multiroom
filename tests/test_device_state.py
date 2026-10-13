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
    # Audio adjustments default to "flat/centered" until the device says
    # otherwise: bass/treble 7 (0 dB), balance 10 (center), pre-amp 0.
    assert zone.bass == 7
    assert zone.treble == 7
    assert zone.balance == 10
    assert zone.preamp == 0


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


def test_parse_getall_reads_audio_adjustments_per_zone() -> None:
    """Chunk order per zone (manual 1.13.1): input, volume, mute, bass,
    treble, balance, pre-amp - deliberately different values per zone so a
    wrong offset would show up."""
    device = make_device()
    args = ("PMR4 V1.13 ON 12345 60 6 30 OFF 14 13 20 7 5 31 ON 0 1 2 3").split()

    device._parse_getall(args)

    zone1, zone2 = device.zones[1], device.zones[2]
    assert (zone1.bass, zone1.treble, zone1.balance, zone1.preamp) == (14, 13, 20, 7)
    assert (zone2.bass, zone2.treble, zone2.balance, zone2.preamp) == (0, 1, 2, 3)
    # ...without disturbing what was already parsed from the same chunk.
    assert (zone2.input, zone2.volume, zone2.mute) == (5, 31, True)


def test_parse_getall_keeps_other_fields_when_one_audio_value_is_garbled() -> None:
    device = make_device()
    args = "PMR4 V1.13 ON 12345 60 6 30 OFF 14 xx 20 7".split()

    device._parse_getall(args)

    zone = device.zones[1]
    assert (zone.bass, zone.balance, zone.preamp) == (14, 20, 7)
    assert zone.treble == 7  # untouched default, not a crash
    assert zone.volume == 30


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
        ("BASS+", ["1", "8"], lambda d: d.zones[1].bass == 8),
        ("BASS-", ["1", "12"], lambda d: d.zones[1].bass == 12),
        ("BASSGET", ["1", "0"], lambda d: d.zones[1].bass == 0),
        ("BASSSET", ["1", "14"], lambda d: d.zones[1].bass == 14),
        ("TREBLE+", ["1", "1"], lambda d: d.zones[1].treble == 1),
        ("TREBLE-", ["1", "12"], lambda d: d.zones[1].treble == 12),
        ("TREBLEGET", ["1", "0"], lambda d: d.zones[1].treble == 0),
        ("TREBLESET", ["1", "14"], lambda d: d.zones[1].treble == 14),
        ("LEFT+", ["1", "9"], lambda d: d.zones[1].balance == 9),
        ("RIGHT+", ["1", "15"], lambda d: d.zones[1].balance == 15),
        ("BALGET", ["1", "10"], lambda d: d.zones[1].balance == 10),
        ("BALSET", ["1", "20"], lambda d: d.zones[1].balance == 20),
        ("PREAMP+", ["1", "1"], lambda d: d.zones[1].preamp == 1),
        ("PREAMP-", ["1", "6"], lambda d: d.zones[1].preamp == 6),
        ("PREAMPGET", ["1", "0"], lambda d: d.zones[1].preamp == 0),
        ("PREAMPSET", ["1", "7"], lambda d: d.zones[1].preamp == 7),
        ("VOLLIMIT", ["1", "60"], lambda d: d.zones[1].volume_limit == 60),
        ("VOLX+", ["1", "75", "1"], lambda d: d.zones[1].volume == 75),
        ("VOLX-", ["1", "74", "0"], lambda d: d.zones[1].volume == 74),
        ("VOLGETX", ["1", "25", "0"], lambda d: d.zones[1].volume == 25),
        ("VOLSETX", ["1", "15", "0"], lambda d: d.zones[1].volume == 15),
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
