"""Tests for the per-zone audio adjustments: bass, treble, balance and
pre-amp gain.

Three layers, each against the thing it actually is:

* device setters - against the FakeAatServer, so the real wire format
  (BASSSET/TREBLESET/BALSET/PREAMPSET) and the optimistic update are
  exercised end to end;
* the dB <-> raw mapping and the `number` entities built on it - against a
  small FakeDevice, since that's pure logic;
* the "Ajustes de áudio" expand/collapse switch.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from custom_components.aat_multiroom.device import AatMultiroomDevice, ZoneState
from custom_components.aat_multiroom.number import AUDIO_CONTROLS, AatAudioNumber
from custom_components.aat_multiroom.switch import AatAudioSectionSwitch

from .fake_aat_server import FakeAatServer


def make_hass():
    hass = SimpleNamespace(
        data={},
        verify_event_loop_thread=lambda *args, **kwargs: None,
    )
    hass.async_create_task = lambda coro, name=None: asyncio.ensure_future(coro)
    return hass


def make_entry(entry_id: str = "test_entry"):
    return SimpleNamespace(
        data={"host": "127.0.0.1", "port": 5000, "model": ""},
        entry_id=entry_id,
        options={"zone_names": {"1": "Sala"}},
    )


# ---------------------------------------------------------------------
# Device setters, over the real client/wire path
# ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def server():
    srv = FakeAatServer()
    await srv.start()
    yield srv
    await srv.stop()


@pytest_asyncio.fixture
async def device(server: FakeAatServer):
    entry = make_entry()
    entry.data["host"] = server.host
    entry.data["port"] = server.port
    dev = AatMultiroomDevice(make_hass(), entry)
    await dev.client.async_connect()
    yield dev
    await dev.client.async_disconnect()


@pytest.mark.parametrize(
    ("method", "command", "attr", "sent", "expected_state"),
    [
        ("async_set_bass", "BASSSET", "bass", 3, 3),
        ("async_set_treble", "TREBLESET", "treble", 11, 11),
        ("async_set_balance", "BALSET", "balance", 4, 4),
        ("async_set_preamp", "PREAMPSET", "preamp", 5, 5),
    ],
)
async def test_setters_send_the_right_command_and_update_state(
    server: FakeAatServer, device: AatMultiroomDevice, method, command, attr, sent, expected_state
) -> None:
    server.script[command] = f"{command} 1 {sent}"

    await getattr(device, method)(1, sent)

    assert f"{command} 1 {sent}" in [msg.split(" ", 1)[1] for msg in server.received]
    assert getattr(device.zones[1], attr) == expected_state


@pytest.mark.parametrize(
    ("method", "command", "high", "low"),
    [
        ("async_set_bass", "BASSSET", 14, 0),
        ("async_set_treble", "TREBLESET", 14, 0),
        ("async_set_balance", "BALSET", 20, 0),
        ("async_set_preamp", "PREAMPSET", 7, 0),
    ],
)
async def test_setters_clamp_out_of_range_values(
    server: FakeAatServer, device: AatMultiroomDevice, method, command, high, low
) -> None:
    """The device would answer error 17/18 to an out-of-range value; clamp
    instead so a slider overshoot never reaches the wire."""
    server.script[command] = [f"{command} 1 {high}", f"{command} 1 {low}"]

    await getattr(device, method)(1, 999)
    await getattr(device, method)(1, -5)

    sent = [msg.split(" ", 1)[1] for msg in server.received if command in msg]
    assert sent == [f"{command} 1 {high}", f"{command} 1 {low}"]


async def test_unsolicited_adjustment_message_updates_state(
    server: FakeAatServer, device: AatMultiroomDevice
) -> None:
    """A change made from the AAT app / front panel arrives as an "n"
    message - same handler table as our own replies."""
    await server.send_raw("[n001 BASS+ 2 9]")
    await asyncio.sleep(0.05)

    assert device.zones[2].bass == 9


# ---------------------------------------------------------------------
# dB <-> raw mapping
# ---------------------------------------------------------------------

CONTROLS = {c.key: c for c in AUDIO_CONTROLS}


@pytest.mark.parametrize(
    ("key", "raw", "display"),
    [
        ("bass", 0, -14.0),
        ("bass", 7, 0.0),
        ("bass", 14, 14.0),
        ("treble", 0, -14.0),
        ("treble", 7, 0.0),
        ("treble", 14, 14.0),
        ("balance", 0, -10.0),
        ("balance", 10, 0.0),
        ("balance", 20, 10.0),
        ("preamp", 0, 0.0),
        ("preamp", 7, 14.0),
    ],
)
def test_raw_and_display_values_round_trip(key, raw, display) -> None:
    control = CONTROLS[key]
    assert control.to_display(raw) == display
    assert control.to_raw(display) == raw


@pytest.mark.parametrize(
    ("key", "low", "high"),
    [("bass", -14.0, 14.0), ("treble", -14.0, 14.0), ("balance", -10.0, 10.0), ("preamp", 0.0, 14.0)],
)
def test_display_range_matches_what_the_manual_documents(key, low, high) -> None:
    control = CONTROLS[key]
    assert (control.min_value, control.max_value) == (low, high)


def test_to_raw_snaps_to_the_nearest_step_and_clamps() -> None:
    bass = CONTROLS["bass"]
    assert bass.to_raw(4.9) == 9  # nearer 4 dB (raw 9) than 6 dB (raw 10)
    assert bass.to_raw(5.1) == 10
    # Exact ties always go up, in both directions (not round-half-to-even).
    assert bass.to_raw(5) == 10
    assert bass.to_raw(-5) == 5
    assert bass.to_raw(100) == 14
    assert bass.to_raw(-100) == 0


# ---------------------------------------------------------------------
# number entities
# ---------------------------------------------------------------------


class FakeDevice:
    model = "PMR7"
    connected = True
    signal = "aat_multiroom_test_update"
    hub_device_id = None

    def __init__(self, zones: dict[int, ZoneState] | None = None) -> None:
        self.zones = zones if zones is not None else {}
        self.calls: list[tuple[str, int, int]] = []

    def zone_signal(self, zone_num: int) -> str:
        return f"aat_multiroom_test_zone_{zone_num}_update"

    async def async_set_bass(self, zone: int, value: int) -> None:
        self.calls.append(("bass", zone, value))

    async def async_set_treble(self, zone: int, value: int) -> None:
        self.calls.append(("treble", zone, value))

    async def async_set_balance(self, zone: int, value: int) -> None:
        self.calls.append(("balance", zone, value))

    async def async_set_preamp(self, zone: int, value: int) -> None:
        self.calls.append(("preamp", zone, value))


def test_number_entity_exposes_db_values_and_metadata() -> None:
    device = FakeDevice({1: ZoneState(bass=14, treble=0, balance=5, preamp=7)})
    entry = make_entry()

    bass = AatAudioNumber(device, entry, 1, CONTROLS["bass"])
    treble = AatAudioNumber(device, entry, 1, CONTROLS["treble"])
    balance = AatAudioNumber(device, entry, 1, CONTROLS["balance"])
    preamp = AatAudioNumber(device, entry, 1, CONTROLS["preamp"])

    assert bass.native_value == 14.0
    assert treble.native_value == -14.0
    assert balance.native_value == -5.0
    assert preamp.native_value == 14.0

    assert bass.name == "Graves"
    assert bass.native_unit_of_measurement == "dB"
    assert bass.native_min_value == -14.0
    assert bass.native_max_value == 14.0
    assert bass.native_step == 2.0
    assert bass.unique_id == "test_entry_zone_1_bass"
    assert bass.available is True


def test_number_entity_value_is_none_for_unknown_zone() -> None:
    entity = AatAudioNumber(FakeDevice({}), make_entry(), 1, CONTROLS["bass"])
    assert entity.native_value is None


def test_number_entities_have_distinct_unique_ids_per_zone_and_control() -> None:
    device = FakeDevice({1: ZoneState(), 2: ZoneState()})
    entry = make_entry()
    ids = {
        AatAudioNumber(device, entry, zone, control).unique_id
        for zone in (1, 2)
        for control in AUDIO_CONTROLS
    }
    assert len(ids) == 8


@pytest.mark.parametrize(
    ("key", "display", "expected_call"),
    [
        ("bass", -6.0, ("bass", 1, 4)),
        ("treble", 10.0, ("treble", 1, 12)),
        ("balance", 3.0, ("balance", 1, 13)),
        ("preamp", 8.0, ("preamp", 1, 4)),
    ],
)
async def test_setting_a_number_sends_the_raw_value_to_the_device(
    key, display, expected_call
) -> None:
    device = FakeDevice({1: ZoneState()})
    entity = AatAudioNumber(device, make_entry(), 1, CONTROLS[key])

    await entity.async_set_native_value(display)

    assert device.calls == [expected_call]


# ---------------------------------------------------------------------
# "Ajustes de áudio" expand/collapse switch
# ---------------------------------------------------------------------


def make_section_switch() -> AatAudioSectionSwitch:
    switch = AatAudioSectionSwitch(FakeDevice({1: ZoneState()}), make_entry(), 1)
    # No hass in these tests; the state write itself isn't what's under test.
    switch.async_write_ha_state = lambda: None
    return switch


def test_section_switch_starts_collapsed_and_has_a_stable_unique_id() -> None:
    switch = make_section_switch()
    assert switch.is_on is False
    assert switch.unique_id == "test_entry_zone_1_audio_section"


async def test_section_switch_toggles_without_touching_the_device() -> None:
    switch = make_section_switch()

    await switch.async_turn_on()
    assert switch.is_on is True

    await switch.async_turn_off()
    assert switch.is_on is False
