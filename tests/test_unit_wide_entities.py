"""Tests for the entities and the service built on the unit-wide commands:
buttons, the all-zones volume slider, per-zone volume limit, stereo/mono/
bridge select, the party switch, the diagnostic sensors and the
`group_command` service."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.aat_multiroom import (
    GROUP_COMMAND_SCHEMA,
    async_setup,
)
from custom_components.aat_multiroom.button import (
    HUB_ACTIONS,
    AatAllZonesInputButton,
    AatHubActionButton,
    AatRestartButton,
)
from custom_components.aat_multiroom.device import ZoneState
from custom_components.aat_multiroom.number import (
    AatAllZonesVolumeNumber,
    AatVolumeLimitNumber,
)
from custom_components.aat_multiroom.select import (
    OPTIONS,
    AatOutputModeSelect,
    model_supports_output_mode,
)
from custom_components.aat_multiroom.sensor import DIAGNOSTIC_FIELDS, AatDiagnosticSensor
from custom_components.aat_multiroom.switch import AatPartySwitch


class FakeDevice:
    model = "PMR7"
    host = "192.168.80.34"
    connected = True
    signal = "aat_multiroom_test_update"
    hub_device_id = None
    firmware = "V3.08"
    tcp_port2 = 1024
    tcp_timeout = 60

    def __init__(self, zones: dict[int, ZoneState] | None = None) -> None:
        self.zones = zones if zones is not None else {}
        self.calls: list[tuple] = []

    def zone_signal(self, zone_num: int) -> str:
        return f"aat_multiroom_test_zone_{zone_num}_update"

    def __getattr__(self, name: str):
        # Record any async_* command the entities call.
        if name.startswith("async_"):

            async def _record(*args):
                self.calls.append((name, *args))

            return _record
        raise AttributeError(name)


def make_entry():
    return SimpleNamespace(
        entry_id="entry1",
        title="AAT Multiroom PMR7",
        options={
            "zone_names": {"1": "Sala", "2": "Quarto"},
            "input_names": {"1": "TV", "2": "Spotify"},
        },
    )


# ---------------------------------------------------------------------
# buttons
# ---------------------------------------------------------------------


@pytest.mark.parametrize("action", HUB_ACTIONS, ids=lambda a: a.key)
async def test_hub_action_buttons_call_their_device_method(action) -> None:
    device = FakeDevice()
    button = AatHubActionButton(device, make_entry(), action)

    await button.async_press()

    assert device.calls == [(action.method,)]
    assert button.unique_id == f"entry1_{action.key}"
    assert button.name == action.name


async def test_all_zones_input_button_selects_that_input_everywhere() -> None:
    device = FakeDevice()
    button = AatAllZonesInputButton(device, make_entry(), 2, "Spotify")

    await button.async_press()

    assert device.calls == [("async_select_input_all", 2)]
    assert button.name == "Todas as zonas → Spotify"
    assert button.unique_id == "entry1_all_input_2"


async def test_restart_button_is_disabled_by_default_and_resets() -> None:
    device = FakeDevice()
    button = AatRestartButton(device, make_entry())

    assert button.entity_registry_enabled_default is False
    await button.async_press()
    assert device.calls == [("async_reset",)]


def test_hub_buttons_are_unavailable_while_disconnected() -> None:
    device = FakeDevice()
    device.connected = False
    assert AatHubActionButton(device, make_entry(), HUB_ACTIONS[0]).available is False


# ---------------------------------------------------------------------
# volume sliders
# ---------------------------------------------------------------------


def test_volume_limit_number_metadata_and_value() -> None:
    device = FakeDevice({1: ZoneState(volume_limit=70)})
    entity = AatVolumeLimitNumber(device, make_entry(), 1)

    assert entity.native_value == 70.0
    assert (entity.native_min_value, entity.native_max_value) == (44.0, 87.0)
    assert entity.unique_id == "entry1_zone_1_volume_limit"
    assert entity.entity_category.value == "config"


async def test_setting_the_volume_limit_calls_the_device() -> None:
    device = FakeDevice({1: ZoneState()})
    entity = AatVolumeLimitNumber(device, make_entry(), 1)

    await entity.async_set_native_value(60.0)

    assert device.calls == [("async_set_volume_limit", 1, 60)]


def test_all_zones_volume_shows_the_average_volume() -> None:
    device = FakeDevice({1: ZoneState(volume=10), 2: ZoneState(volume=21)})
    entity = AatAllZonesVolumeNumber(device, make_entry())
    assert entity.native_value == 16.0  # round(15.5) -> 16 (banker's: 16)


def test_all_zones_volume_is_none_without_zones() -> None:
    assert AatAllZonesVolumeNumber(FakeDevice({}), make_entry()).native_value is None


async def test_setting_the_all_zones_volume_applies_it_to_every_zone() -> None:
    device = FakeDevice({1: ZoneState(), 2: ZoneState()})
    entity = AatAllZonesVolumeNumber(device, make_entry())

    await entity.async_set_native_value(33.0)

    assert device.calls == [("async_set_all_volume", 33)]


# ---------------------------------------------------------------------
# stereo / mono / bridge
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "supported"),
    [
        ("PMR7", True),
        ("PMR4", True),
        ("PMR12", True),
        ("pmr7", True),
        ("PMRH6", False),  # high-power: no such hardware
        ("PMRH2", False),
        ("PMA1", False),  # non-amplified
        ("PMA2", False),
    ],
)
def test_output_mode_is_only_offered_for_models_that_have_the_hardware(model, supported) -> None:
    assert model_supports_output_mode(model) is supported


def test_output_mode_select_is_disabled_by_default_and_assumed_state() -> None:
    select = AatOutputModeSelect(FakeDevice({1: ZoneState()}), make_entry(), 1)

    assert select.entity_registry_enabled_default is False
    assert select.assumed_state is True
    assert select.current_option is None  # unknown until chosen: write-only on the device
    assert list(select.options) == ["Estéreo", "Mono", "Bridge"]


@pytest.mark.parametrize(("option", "mode"), list(OPTIONS.items()))
async def test_output_mode_select_sends_the_command_and_remembers_the_choice(option, mode) -> None:
    device = FakeDevice({1: ZoneState()})
    select = AatOutputModeSelect(device, make_entry(), 1)
    select.async_write_ha_state = lambda: None

    await select.async_select_option(option)

    assert device.calls == [("async_set_output_mode", 1, mode)]
    assert select.current_option == option


# ---------------------------------------------------------------------
# party switch
# ---------------------------------------------------------------------


async def test_party_switch_broadcasts_and_tracks_assumed_state(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    async def _fake_send(hass, host, action, *args, **kwargs):
        sent.append((host, action))

    monkeypatch.setattr("custom_components.aat_multiroom.switch.async_send_group_command", _fake_send)
    switch = AatPartySwitch(FakeDevice(), make_entry())
    switch.hass = SimpleNamespace()
    switch.async_write_ha_state = lambda: None

    assert switch.entity_registry_enabled_default is False
    assert switch.assumed_state is True
    assert switch.is_on is False

    await switch.async_turn_on()
    await switch.async_turn_off()

    assert sent == [("192.168.80.34", "party_on"), ("192.168.80.34", "party_off")]
    assert switch.is_on is False


# ---------------------------------------------------------------------
# diagnostic sensors
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected", "unit"),
    [("firmware", "V3.08", None), ("tcp_port2", 1024, None), ("tcp_timeout", 60, "s")],
)
def test_diagnostic_sensors_report_values_from_the_device(key, expected, unit) -> None:
    field = next(f for f in DIAGNOSTIC_FIELDS if f.key == key)
    sensor = AatDiagnosticSensor(FakeDevice(), make_entry(), field)

    assert sensor.native_value == expected
    assert sensor.native_unit_of_measurement == unit
    assert sensor.entity_category.value == "diagnostic"
    assert sensor.unique_id == f"entry1_{key}"


# ---------------------------------------------------------------------
# group_command service
# ---------------------------------------------------------------------


async def _registered_handler():
    registered = {}
    hass = SimpleNamespace(
        data={},
        services=SimpleNamespace(
            async_register=lambda domain, name, handler, schema=None: registered.update(
                {(domain, name): handler}
            )
        ),
    )
    assert await async_setup(hass, {}) is True
    return hass, registered[("aat_multiroom", "group_command")]


def test_service_schema_defaults_the_group_to_party_mode() -> None:
    assert GROUP_COMMAND_SCHEMA({"action": "mute_on"})["group"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "nope"},
        {"action": "volume", "group": 33},
        {"action": "volume", "group": -1},
        {"action": "volume", "value": 99},
    ],
)
def test_service_schema_rejects_bad_input(payload) -> None:
    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        GROUP_COMMAND_SCHEMA(payload)


async def test_service_fails_clearly_when_no_multiroom_is_loaded() -> None:
    _hass, handler = await _registered_handler()

    with pytest.raises(ServiceValidationError) as excinfo:
        await handler(SimpleNamespace(data={"action": "power_on", "group": 0}))

    assert excinfo.value.translation_key == "no_multiroom_loaded"


async def test_service_fails_clearly_for_an_unknown_config_entry() -> None:
    hass, handler = await _registered_handler()
    hass.data["aat_multiroom"] = {"real": FakeDevice()}

    with pytest.raises(ServiceValidationError) as excinfo:
        await handler(
            SimpleNamespace(data={"action": "power_on", "group": 0, "config_entry_id": "ghost"})
        )

    assert excinfo.value.translation_key == "unknown_config_entry"


async def test_service_broadcasts_through_the_chosen_multiroom(monkeypatch) -> None:
    calls: list[tuple] = []

    async def _fake_send(hass, host, action, group, value):
        calls.append((host, action, group, value))

    monkeypatch.setattr("custom_components.aat_multiroom.async_send_group_command", _fake_send)
    hass, handler = await _registered_handler()
    first, second = FakeDevice(), FakeDevice()
    second.host = "192.168.90.10"
    hass.data["aat_multiroom"] = {"a": first, "b": second}

    await handler(SimpleNamespace(data={"action": "volume", "group": 3, "value": 40}))
    await handler(
        SimpleNamespace(data={"action": "mute_on", "group": 0, "config_entry_id": "b"})
    )

    assert calls == [
        ("192.168.80.34", "volume", 3, 40),
        ("192.168.90.10", "mute_on", 0, None),
    ]


async def test_service_turns_a_rejected_command_into_a_validation_error(monkeypatch) -> None:
    async def _rejects(*args, **kwargs):
        raise ValueError("volume must be 0..87, got 99")

    monkeypatch.setattr("custom_components.aat_multiroom.async_send_group_command", _rejects)
    hass, handler = await _registered_handler()
    hass.data["aat_multiroom"] = {"a": FakeDevice()}

    with pytest.raises(ServiceValidationError) as excinfo:
        await handler(SimpleNamespace(data={"action": "volume", "group": 0, "value": 99}))

    assert excinfo.value.translation_key == "invalid_group_command"
    assert "99" in excinfo.value.translation_placeholders["error"]
