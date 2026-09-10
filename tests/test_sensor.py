"""Tests for AatZoneInputSensor - the "current input, as plain text"
sensor, which had no dedicated coverage before."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.aat_multiroom.device import ZoneState
from custom_components.aat_multiroom.sensor import AatZoneInputSensor


class FakeDevice:
    model = "PMR7"
    connected = True
    signal = "aat_multiroom_test_update"
    hub_device_id = None

    def __init__(self, zones: dict[int, ZoneState] | None = None) -> None:
        self.zones = zones if zones is not None else {}

    def zone_signal(self, zone_num: int) -> str:
        return f"aat_multiroom_test_zone_{zone_num}_update"


def make_entry():
    return SimpleNamespace(
        entry_id="entry1",
        options={
            "zone_names": {"1": "Sala"},
            "input_names": {"1": "TV", "2": "Rádio", "3": "Spotify"},
        },
    )


def test_native_value_reflects_current_input_name() -> None:
    device = FakeDevice({1: ZoneState(input=3)})
    sensor = AatZoneInputSensor(device, make_entry(), 1)

    assert sensor.native_value == "Spotify"


def test_native_value_falls_back_to_generic_name_for_unnamed_input() -> None:
    device = FakeDevice({1: ZoneState(input=7)})
    sensor = AatZoneInputSensor(device, make_entry(), 1)

    assert sensor.native_value == "Entrada 7"


def test_native_value_is_none_when_zone_unknown() -> None:
    sensor = AatZoneInputSensor(FakeDevice({}), make_entry(), 1)

    assert sensor.native_value is None


def test_sensor_uses_its_own_zone_signal_not_device_signal() -> None:
    device = FakeDevice({1: ZoneState()})
    sensor = AatZoneInputSensor(device, make_entry(), 1)

    assert sensor._device.zone_signal(1) != device.signal
