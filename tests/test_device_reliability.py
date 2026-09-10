"""Tests for the reliability/stability fixes on top of AatMultiroomDevice:

* per-zone dispatcher signals (a change in one zone must not notify every
  other zone's entities too)
* the periodic-refresh and reconnect loops must survive an unexpected
  exception instead of dying silently forever
* async_close() must actually await its cancelled background tasks
* concurrent async_refresh_full_state() calls must coalesce into a single
  GETALL/ZSTDBYGET pass instead of hammering the device in parallel
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest_asyncio

from custom_components.aat_multiroom.device import AatMultiroomDevice, ZoneState

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
        options={},
    )


def make_device() -> AatMultiroomDevice:
    return AatMultiroomDevice(make_hass(), make_entry())


class _SignalRecorder:
    """Subscribes to a dispatcher signal and records every fire."""

    def __init__(self, device: AatMultiroomDevice, signal: str) -> None:
        from homeassistant.helpers.dispatcher import async_dispatcher_connect
        from homeassistant.core import callback

        self.fired = 0

        @callback
        def _on_fire(*args: object) -> None:
            self.fired += 1

        async_dispatcher_connect(device.hass, signal, _on_fire)


# ---------------------------------------------------------------------
# Per-zone vs device-wide signal targeting
# ---------------------------------------------------------------------


def test_zone_scoped_change_only_notifies_its_own_zone_signal() -> None:
    device = make_device()
    zone1 = _SignalRecorder(device, device.zone_signal(1))
    zone2 = _SignalRecorder(device, device.zone_signal(2))
    device_wide = _SignalRecorder(device, device.signal)

    device._on_message("MUTEON", ["1"])

    assert zone1.fired == 1
    assert zone2.fired == 0
    assert device_wide.fired == 0


def test_device_wide_change_does_not_notify_any_zone_signal() -> None:
    device = make_device()
    zone1 = _SignalRecorder(device, device.zone_signal(1))
    device_wide = _SignalRecorder(device, device.signal)

    device._on_message("PWRON", [])

    assert zone1.fired == 0
    assert device_wide.fired == 1


def test_notify_all_fires_device_signal_and_every_known_zone() -> None:
    device = make_device()
    device.zones[1] = ZoneState()
    device.zones[2] = ZoneState()
    zone1 = _SignalRecorder(device, device.zone_signal(1))
    zone2 = _SignalRecorder(device, device.zone_signal(2))
    device_wide = _SignalRecorder(device, device.signal)

    device._notify_all()

    assert zone1.fired == 1
    assert zone2.fired == 1
    assert device_wide.fired == 1


async def test_connectivity_change_notifies_every_zone_and_the_device_signal() -> None:
    """A reconnect/disconnect affects every entity's `available`, so it
    must use the broad notify, not a single zone's."""
    device = make_device()
    device.zones[1] = ZoneState()
    device.zones[2] = ZoneState()
    zone1 = _SignalRecorder(device, device.zone_signal(1))
    zone2 = _SignalRecorder(device, device.zone_signal(2))
    device_wide = _SignalRecorder(device, device.signal)

    device._schedule_reconnect()

    assert zone1.fired == 1
    assert zone2.fired == 1
    assert device_wide.fired == 1

    # Clean up the reconnect task _schedule_reconnect just started, so it
    # doesn't outlive the test.
    device._closing = True
    if device._reconnect_task is not None:
        device._reconnect_task.cancel()
        await asyncio.gather(device._reconnect_task, return_exceptions=True)


# ---------------------------------------------------------------------
# Background loops must survive unexpected exceptions
# ---------------------------------------------------------------------


async def test_periodic_refresh_survives_unexpected_exception(monkeypatch) -> None:
    device = make_device()
    monkeypatch.setattr("custom_components.aat_multiroom.device.REFRESH_INTERVAL", 0.01)
    device.client._connected = True  # so the loop actually calls refresh

    calls = 0

    async def fake_refresh() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected bug, not an AatConnectionError")

    monkeypatch.setattr(device, "async_refresh_full_state", fake_refresh)

    task = asyncio.create_task(device._periodic_refresh())
    try:
        for _ in range(50):
            if calls >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert calls >= 2, "the loop died after the first unexpected exception"


async def test_reconnect_loop_survives_unexpected_exception_after_connect(monkeypatch) -> None:
    device = make_device()
    # The real loop sleeps _RECONNECT_MIN_DELAY (3s) before each attempt;
    # that's too slow for a test.
    monkeypatch.setattr("custom_components.aat_multiroom.device._RECONNECT_MIN_DELAY", 0.01)

    async def fake_connect() -> None:
        device.client._connected = True

    async def fake_refresh() -> None:
        raise RuntimeError("unexpected bug during post-reconnect refresh")

    monkeypatch.setattr(device.client, "async_connect", fake_connect)
    monkeypatch.setattr(device, "async_refresh_full_state", fake_refresh)
    device.zones[1] = ZoneState()
    zone1 = _SignalRecorder(device, device.zone_signal(1))
    device_wide = _SignalRecorder(device, device.signal)

    # client.connected starts False, so the while condition holds for one
    # pass; fake_connect() flips it True, so the real _reconnect_loop()
    # attempts exactly once, hits the RuntimeError from fake_refresh, and
    # must still return cleanly instead of propagating it.
    await asyncio.wait_for(device._reconnect_loop(), timeout=2)

    assert device.client.connected is True
    assert zone1.fired >= 1
    assert device_wide.fired >= 1


# ---------------------------------------------------------------------
# async_close() must await its cancelled tasks, not just fire-and-forget
# ---------------------------------------------------------------------


async def test_async_close_awaits_cancelled_background_tasks() -> None:
    device = make_device()

    async def _never_ending() -> None:
        await asyncio.sleep(3600)

    device._refresh_task = asyncio.ensure_future(_never_ending())
    device._reconnect_task = asyncio.ensure_future(_never_ending())

    async def fake_disconnect() -> None:
        return None

    device.client.async_disconnect = fake_disconnect  # avoid touching a real socket

    await device.async_close()

    assert device._refresh_task.done()
    assert device._reconnect_task.done()


# ---------------------------------------------------------------------
# Concurrent refresh calls coalesce into a single GETALL/ZSTDBYGET pass
# ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def server():
    srv = FakeAatServer()
    srv.script["GETALL"] = "GETALL PMR4 V1.13 ON 5000 0 1 30 OFF 14 14 20 7"
    srv.script["ZSTDBYGET"] = "ZSTDBYGET 1 ON"
    await srv.start()
    yield srv
    await srv.stop()


@pytest_asyncio.fixture
async def device_on_server(server: FakeAatServer):
    entry = make_entry()
    entry.data["host"] = server.host
    entry.data["port"] = server.port
    dev = AatMultiroomDevice(make_hass(), entry)
    await dev.client.async_connect()
    yield dev
    await dev.client.async_disconnect()


async def test_concurrent_refresh_calls_coalesce_into_one_getall(
    server: FakeAatServer, device_on_server: AatMultiroomDevice
) -> None:
    await asyncio.gather(
        device_on_server.async_refresh_full_state(),
        device_on_server.async_refresh_full_state(),
        device_on_server.async_refresh_full_state(),
    )

    getall_count = sum(1 for msg in server.received if "GETALL" in msg)
    assert getall_count == 1
