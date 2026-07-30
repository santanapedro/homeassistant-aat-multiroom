"""Tests for the friendly-error translation added on top of the raw
protocol/connection errors.

These drive a real AatMultiroomDevice against the same FakeAatServer used
in test_api_protocol.py, so the full path is exercised: our command is
sent, the device replies with an error/never replies, and _run_command
must turn that into a HomeAssistantError carrying the right
translation_key (not just re-raise the low-level AatCommandError /
AatConnectionError).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from homeassistant.exceptions import HomeAssistantError

from custom_components.aat_multiroom.device import AatMultiroomDevice

from .fake_aat_server import FakeAatServer


def make_hass():
    """Minimal stand-in for HomeAssistant, good enough for
    async_dispatcher_send and async_create_task (used to schedule the
    background resync after a failed command)."""
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
        options={},
    )


@pytest_asyncio.fixture
async def server():
    srv = FakeAatServer()
    # So the background resync triggered on failure has something sane to
    # parse instead of erroring a second time.
    srv.script["GETALL"] = "GETALL PMR4 V1.13 ON 5000 0 1 30 OFF 14 14 20 7"
    # Deliberately different from what the optimistic update below would
    # guess, so the resync test can tell "resync happened" apart from
    # "the optimistic guess just happened to match".
    srv.script["ZSTDBYGET"] = "ZSTDBYGET 1 ON"
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
    ("code", "expected_key"),
    [
        ("7", "unknown_command"),
        ("8", "device_off"),
        ("17", "invalid_zone_or_value"),
        ("18", "value_out_of_range"),
        ("99", "command_failed"),  # unknown code -> generic fallback
    ],
)
async def test_command_error_translated(
    server: FakeAatServer, device: AatMultiroomDevice, code: str, expected_key: str
) -> None:
    server.script["ZSTDBYOFF"] = code

    with pytest.raises(HomeAssistantError) as excinfo:
        await device.async_zone_power(1, True)

    err = excinfo.value
    assert err.translation_domain == "aat_multiroom"
    assert err.translation_key == expected_key
    assert err.translation_placeholders["command"] == "ZSTDBYOFF"


async def test_command_error_still_resyncs_state(
    server: FakeAatServer, device: AatMultiroomDevice
) -> None:
    """Even though the command failed, a resync should still have been
    scheduled so the optimistic (wrong) local state gets corrected."""
    server.script["ZSTDBYOFF"] = "17"

    with pytest.raises(HomeAssistantError):
        await device.async_zone_power(1, True)

    # Let the fire-and-forget resync task run.
    await asyncio.sleep(0.1)

    # The optimistic guess (from async_zone_power(1, True)) would have set
    # standby=False; the scripted ZSTDBYGET says "ON" (standby=True), so
    # this only passes if the resync actually overwrote the guess.
    assert device.zones[1].standby is True


async def test_connection_error_translated(device: AatMultiroomDevice) -> None:
    # No script entry for ZSTDBYON, and we cut the connection before the
    # device ever gets to reply, so async_send_command must time out /
    # fail with a connection error.
    await device.client.async_disconnect()

    with pytest.raises(HomeAssistantError) as excinfo:
        await device.async_zone_power(1, False)

    err = excinfo.value
    assert err.translation_domain == "aat_multiroom"
    assert err.translation_key == "connection_error"
    assert err.translation_placeholders["command"] == "ZSTDBYON"
