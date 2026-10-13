"""Tests for the unit-wide commands and the firmware >= 3.08 / diagnostic
additions on AatMultiroomDevice:

* MUTEALL / UNMUTEALL / ZSTDBYALL / ZTONALL, "all zones -> input X",
  "all zones volume", stereo/mono/bridge, RESET
* VOLLIMIT (read in the refresh, with graceful handling of old firmware)
* firmware / TCP port / TCP timeout from GETALL, and the refresh interval
  that adapts to a configured TCP timeout

Wire-level behavior runs against the FakeAatServer; pure logic is checked
directly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from homeassistant.exceptions import HomeAssistantError

from custom_components.aat_multiroom import device as device_module
from custom_components.aat_multiroom.device import AatMultiroomDevice, ZoneState

from .fake_aat_server import FakeAatServer


def make_hass():
    hass = SimpleNamespace(data={}, verify_event_loop_thread=lambda *a, **k: None)
    hass.async_create_task = lambda coro, name=None: asyncio.ensure_future(coro)
    hass.async_create_background_task = lambda coro, name=None, **k: asyncio.ensure_future(coro)
    return hass


def make_entry():
    return SimpleNamespace(
        data={"host": "127.0.0.1", "port": 5000, "model": ""},
        entry_id="test_entry",
        options={},
    )


GETALL_TWO_ZONES = "GETALL PMR4 V1.13 ON 12345 60 6 30 OFF 14 14 20 7 5 31 ON 0 1 2 3"


@pytest_asyncio.fixture
async def server():
    srv = FakeAatServer()
    srv.script["GETALL"] = GETALL_TWO_ZONES
    srv.script["ZSTDBYGET"] = ["ZSTDBYGET 1 OFF", "ZSTDBYGET 2 OFF"]
    srv.script["VOLLIMIT"] = ["VOLLIMIT 1 87", "VOLLIMIT 2 70"]
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
    await dev.async_refresh_full_state()
    server.received.clear()
    yield dev
    await dev.client.async_disconnect()


def sent(server: FakeAatServer) -> list[str]:
    """Commands the server received, without the "t001 " sequence prefix."""
    return [msg.split(" ", 1)[1] for msg in server.received]


# ---------------------------------------------------------------------
# GETALL extras: firmware, second TCP port, timeout
# ---------------------------------------------------------------------


async def test_refresh_reads_firmware_port_and_timeout(device: AatMultiroomDevice) -> None:
    assert device.firmware == "V1.13"
    assert device.tcp_port2 == 12345
    assert device.tcp_timeout == 60


def test_refresh_interval_is_the_default_without_a_timeout() -> None:
    dev = AatMultiroomDevice(make_hass(), make_entry())
    assert dev._refresh_interval() == device_module.REFRESH_INTERVAL
    dev.tcp_timeout = 0  # 0 = timeout disabled
    assert dev._refresh_interval() == device_module.REFRESH_INTERVAL
    dev.tcp_timeout = 300
    assert dev._refresh_interval() == device_module.REFRESH_INTERVAL


@pytest.mark.parametrize(("timeout", "expected"), [(10, 5.0), (20, 10.0), (59, 29.5)])
def test_refresh_interval_stays_inside_a_short_tcp_timeout(timeout, expected) -> None:
    dev = AatMultiroomDevice(make_hass(), make_entry())
    dev.tcp_timeout = timeout
    assert dev._refresh_interval() == expected


# ---------------------------------------------------------------------
# VOLLIMIT
# ---------------------------------------------------------------------


async def test_refresh_reads_the_volume_limit_of_every_zone(device: AatMultiroomDevice) -> None:
    assert device.vollimit_supported is True
    assert device.zones[1].volume_limit == 87
    assert device.zones[2].volume_limit == 70


async def test_old_firmware_without_vollimit_is_remembered_and_not_asked_again(
    server: FakeAatServer,
) -> None:
    server.script["VOLLIMIT"] = "7"  # error 7 = unknown command
    server.script["ZSTDBYGET"] = "ZSTDBYGET 1 OFF"
    entry = make_entry()
    entry.data["host"], entry.data["port"] = server.host, server.port
    dev = AatMultiroomDevice(make_hass(), entry)
    await dev.client.async_connect()
    try:
        await dev.async_refresh_full_state()
        assert dev.vollimit_supported is False
        first_round = [m for m in sent(server) if m.startswith("VOLLIMIT")]
        assert len(first_round) == 1  # gave up after the first rejection

        await dev.async_refresh_full_state()
        assert [m for m in sent(server) if m.startswith("VOLLIMIT")] == first_round
    finally:
        await dev.client.async_disconnect()


async def test_set_volume_limit_clamps_to_the_documented_range(
    server: FakeAatServer, device: AatMultiroomDevice
) -> None:
    server.script["VOLLIMIT"] = ["VOLLIMIT 1 44", "VOLLIMIT 1 87"]

    await device.async_set_volume_limit(1, 10)  # below the 44 minimum
    await device.async_set_volume_limit(1, 999)  # above the 87 maximum

    assert [m for m in sent(server) if m.startswith("VOLLIMIT")] == [
        "VOLLIMIT 1 44",
        "VOLLIMIT 1 87",
    ]


# ---------------------------------------------------------------------
# whole-unit commands
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "command", "attr", "expected"),
    [
        ("async_mute_all", "MUTEALL", "mute", True),
        ("async_unmute_all", "UNMUTEALL", "mute", False),
        ("async_standby_all", "ZSTDBYALL", "standby", True),
        ("async_turn_on_all", "ZTONALL", "standby", False),
    ],
)
async def test_all_zone_commands_update_every_zone_and_notify_all(
    server: FakeAatServer, device: AatMultiroomDevice, monkeypatch, method, command, attr, expected
) -> None:
    server.script[command] = command
    notified: list[str] = []
    monkeypatch.setattr(device, "_notify_all", lambda: notified.append("all"))

    await getattr(device, method)()

    assert sent(server) == [command]
    assert all(getattr(zone, attr) is expected for zone in device.zones.values())
    # Zone entities listen on their own signals, so the device-wide notify
    # alone would leave them stale.
    assert notified == ["all"]


async def test_unmute_all_rejected_by_hardware_mute_is_a_clean_error(
    server: FakeAatServer, device: AatMultiroomDevice
) -> None:
    """Manual 1.6.7: error 17 also means "hardware mute active"."""
    server.script["UNMUTEALL"] = "17"

    with pytest.raises(HomeAssistantError) as excinfo:
        await device.async_unmute_all()

    assert excinfo.value.translation_key == "invalid_zone_or_value"


async def test_set_all_volume_sends_one_volset_per_zone(
    server: FakeAatServer, device: AatMultiroomDevice
) -> None:
    server.script["VOLSET"] = ["VOLSET 1 25", "VOLSET 2 25"]

    await device.async_set_all_volume(25)

    assert [m for m in sent(server) if m.startswith("VOLSET")] == ["VOLSET 1 25", "VOLSET 2 25"]
    assert {z.volume for z in device.zones.values()} == {25}


async def test_select_input_all_sends_one_inpset_per_zone(
    server: FakeAatServer, device: AatMultiroomDevice
) -> None:
    server.script["INPSET"] = ["INPSET 1 3", "INPSET 2 3"]

    await device.async_select_input_all(3)

    assert [m for m in sent(server) if m.startswith("INPSET")] == ["INPSET 1 3", "INPSET 2 3"]
    assert {z.input for z in device.zones.values()} == {3}


@pytest.mark.parametrize(
    ("mode", "command"),
    [("stereo", "SWSTEREO"), ("mono", "SWMONO"), ("bridge", "SWBRIDGE")],
)
async def test_output_mode_sends_the_matching_command(
    server: FakeAatServer, device: AatMultiroomDevice, mode, command
) -> None:
    server.script[command] = f"{command} 2"

    await device.async_set_output_mode(2, mode)

    assert sent(server) == [f"{command} 2"]


async def test_reset_sends_reset(server: FakeAatServer, device: AatMultiroomDevice) -> None:
    server.script["RESET"] = "RESET"

    await device.async_reset()

    assert sent(server) == ["RESET"]


async def test_reset_treats_the_connection_dropping_as_success(monkeypatch) -> None:
    """The unit reboots, so the socket going away is the expected outcome."""
    dev = AatMultiroomDevice(make_hass(), make_entry())

    async def _drops(*args, **kwargs):
        raise HomeAssistantError(translation_domain="aat_multiroom", translation_key="connection_error")

    monkeypatch.setattr(dev, "_run_command", _drops)
    await dev.async_reset()  # must not raise


async def test_reset_still_raises_for_a_real_rejection(monkeypatch) -> None:
    dev = AatMultiroomDevice(make_hass(), make_entry())

    async def _rejected(*args, **kwargs):
        raise HomeAssistantError(translation_domain="aat_multiroom", translation_key="device_off")

    monkeypatch.setattr(dev, "_run_command", _rejected)
    with pytest.raises(HomeAssistantError):
        await dev.async_reset()


def test_zone_state_has_no_volume_limit_by_default() -> None:
    assert ZoneState().volume_limit == 87
