"""Tests for the switch entities - the ones behind the "painted icon when
selected" feature, which had no dedicated coverage before (only
device.py's underlying state logic did)."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.aat_multiroom.device import ZoneState
from custom_components.aat_multiroom.switch import (
    AatInputSwitch,
    AatMasterPowerSwitch,
    AatZonePowerSwitch,
)


class FakeDevice:
    model = "PMR7"
    connected = True
    signal = "aat_multiroom_test_update"

    def __init__(self, zones: dict[int, ZoneState] | None = None, power: bool = True) -> None:
        self.zones = zones if zones is not None else {}
        self.power = power
        self.master_power_calls: list[bool] = []
        self.zone_power_calls: list[tuple[int, bool]] = []
        self.selected_input_calls: list[tuple[int, int]] = []

    def zone_signal(self, zone_num: int) -> str:
        return f"aat_multiroom_test_zone_{zone_num}_update"

    async def async_master_power(self, on: bool) -> None:
        self.master_power_calls.append(on)

    async def async_zone_power(self, zone_num: int, on: bool) -> None:
        self.zone_power_calls.append((zone_num, on))

    async def async_select_input(self, zone_num: int, input_num: int) -> None:
        self.selected_input_calls.append((zone_num, input_num))


def make_entry():
    return SimpleNamespace(
        entry_id="entry1",
        title="AAT Multiroom PMR7",
        options={
            "zone_names": {"1": "Sala"},
            "input_names": {"1": "TV", "2": "Rádio", "3": "Spotify"},
        },
    )


# ---------------------------------------------------------------------
# AatMasterPowerSwitch
# ---------------------------------------------------------------------


def test_master_power_switch_reflects_device_power() -> None:
    switch = AatMasterPowerSwitch(FakeDevice(power=True), make_entry())
    assert switch.is_on is True
    assert switch.icon == "mdi:power"

    switch_off = AatMasterPowerSwitch(FakeDevice(power=False), make_entry())
    assert switch_off.is_on is False
    assert switch_off.icon == "mdi:power-off"


def test_master_power_switch_uses_device_wide_signal() -> None:
    device = FakeDevice()
    switch = AatMasterPowerSwitch(device, make_entry())
    assert switch._signal == device.signal


async def test_master_power_switch_turn_on_off_calls_device() -> None:
    device = FakeDevice()
    switch = AatMasterPowerSwitch(device, make_entry())

    await switch.async_turn_on()
    await switch.async_turn_off()

    assert device.master_power_calls == [True, False]


# ---------------------------------------------------------------------
# AatZonePowerSwitch
# ---------------------------------------------------------------------


def test_zone_power_switch_reflects_zone_standby() -> None:
    device = FakeDevice({1: ZoneState(standby=False)})
    switch = AatZonePowerSwitch(device, make_entry(), 1)
    assert switch.is_on is True
    assert switch.icon == "mdi:speaker"

    device.zones[1].standby = True
    assert switch.is_on is False
    assert switch.icon == "mdi:speaker-off"


def test_zone_power_switch_is_off_when_zone_unknown() -> None:
    switch = AatZonePowerSwitch(FakeDevice({}), make_entry(), 1)
    assert switch.is_on is False


def test_zone_power_switch_uses_its_own_zone_signal_not_device_signal() -> None:
    device = FakeDevice({1: ZoneState()})
    switch = AatZonePowerSwitch(device, make_entry(), 1)
    assert switch._signal == device.zone_signal(1)
    assert switch._signal != device.signal


async def test_zone_power_switch_turn_on_off_calls_device() -> None:
    device = FakeDevice({1: ZoneState()})
    switch = AatZonePowerSwitch(device, make_entry(), 1)

    await switch.async_turn_on()
    await switch.async_turn_off()

    assert device.zone_power_calls == [(1, True), (1, False)]


# ---------------------------------------------------------------------
# AatInputSwitch
# ---------------------------------------------------------------------


def test_input_switch_is_on_only_for_the_active_input() -> None:
    device = FakeDevice({1: ZoneState(input=2)})
    active = AatInputSwitch(device, make_entry(), 1, 2)
    inactive = AatInputSwitch(device, make_entry(), 1, 3)

    assert active.is_on is True
    assert active.icon == "mdi:radiobox-marked"
    assert inactive.is_on is False
    assert inactive.icon == "mdi:radiobox-blank"


def test_input_switch_is_off_when_zone_unknown() -> None:
    switch = AatInputSwitch(FakeDevice({}), make_entry(), 1, 1)
    assert switch.is_on is False


def test_input_switch_uses_its_zone_signal() -> None:
    device = FakeDevice({1: ZoneState()})
    switch = AatInputSwitch(device, make_entry(), 1, 1)
    assert switch._signal == device.zone_signal(1)


async def test_input_switch_turn_on_selects_that_input() -> None:
    device = FakeDevice({1: ZoneState(input=1)})
    switch = AatInputSwitch(device, make_entry(), 1, 3)

    await switch.async_turn_on()

    assert device.selected_input_calls == [(1, 3)]


async def test_input_switch_turn_off_is_a_no_op() -> None:
    """The hardware has no "deselect" state - turning off the already
    active input switch must not call any device command."""
    device = FakeDevice({1: ZoneState(input=2)})
    switch = AatInputSwitch(device, make_entry(), 1, 2)

    await switch.async_turn_off()

    assert device.selected_input_calls == []
    assert device.zone_power_calls == []
    assert device.master_power_calls == []
