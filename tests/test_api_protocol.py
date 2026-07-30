"""Protocol-level tests for AatMultiroomClient.

Every scripted reply below is copied verbatim from the examples in "AAT
Digital Matrix Amplifiers - API (TCP/SERIAL/IR) Rev.12", so these tests
double as a check that the client parses the manual's own examples
correctly - without needing real hardware.

One exception: the manual never shows the exact wire bytes of an error
reply (only the meaning of each error code, e.g. "17 - zona invalida").
`test_error_code_reply` documents and tests our assumption that an error
reply looks like ``[r001 17]`` (a bare numeric code, no command echoed).
That assumption should be double-checked against a real device.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from custom_components.aat_multiroom.api import (
    AatCommandError,
    AatConnectionError,
    AatMultiroomClient,
    async_probe,
)

from .fake_aat_server import FakeAatServer


@pytest_asyncio.fixture
async def server():
    srv = FakeAatServer()
    await srv.start()
    yield srv
    await srv.stop()


@pytest_asyncio.fixture
async def client(server: FakeAatServer):
    c = AatMultiroomClient(server.host, server.port)
    await c.async_connect()
    yield c
    await c.async_disconnect()


# ---------------------------------------------------------------------
# Basic commands (manual section 1.4 - Power)
# ---------------------------------------------------------------------


async def test_pwron_pwroff_roundtrip(server: FakeAatServer, client: AatMultiroomClient) -> None:
    server.script["PWRON"] = "PWRON"
    server.script["PWROFF"] = "PWROFF"

    assert await client.async_send_command("PWRON") == []
    assert await client.async_send_command("PWROFF") == []


async def test_pwrtog_toggle_sequence(server: FakeAatServer, client: AatMultiroomClient) -> None:
    # Manual 1.4.4: [t001 PWRTOG] -> [r001 PWRTOG ON], [t002 PWRTOG] -> [r002 PWRTOG OFF]
    server.script["PWRTOG"] = ["PWRTOG ON", "PWRTOG OFF"]

    assert await client.async_send_command("PWRTOG") == ["ON"]
    assert await client.async_send_command("PWRTOG") == ["OFF"]


# ---------------------------------------------------------------------
# Volume (manual section 1.5)
# ---------------------------------------------------------------------


async def test_volset(server: FakeAatServer, client: AatMultiroomClient) -> None:
    # Manual 1.5.5: [t001 VOLSET 1 15] -> [r001 VOLSET 1 15]
    server.script["VOLSET"] = "VOLSET 1 15"
    assert await client.async_send_command("VOLSET", 1, 15) == ["1", "15"]


async def test_volget(server: FakeAatServer, client: AatMultiroomClient) -> None:
    # Manual 1.5.4: [t001 VOLGET 1] -> [r001 VOLGET 1 25]
    server.script["VOLGET"] = "VOLGET 1 25"
    assert await client.async_send_command("VOLGET", 1) == ["1", "25"]


# ---------------------------------------------------------------------
# Mute / stand-by toggles (manual sections 1.6, 1.7)
# ---------------------------------------------------------------------


async def test_muteon(server: FakeAatServer, client: AatMultiroomClient) -> None:
    server.script["MUTEON"] = "MUTEON 1"
    assert await client.async_send_command("MUTEON", 1) == ["1"]


async def test_zstdbytog_toggle_sequence(
    server: FakeAatServer, client: AatMultiroomClient
) -> None:
    # Manual 1.7.4: zone 4 toggled ON then OFF
    server.script["ZSTDBYTOG"] = ["ZSTDBYTOG 4 ON", "ZSTDBYTOG 4 OFF"]
    assert await client.async_send_command("ZSTDBYTOG", 4) == ["4", "ON"]
    assert await client.async_send_command("ZSTDBYTOG", 4) == ["4", "OFF"]


# ---------------------------------------------------------------------
# GETALL (manual section 1.13) - the main bulk-state command
# ---------------------------------------------------------------------


async def test_getall_pmr7_six_zones(server: FakeAatServer, client: AatMultiroomClient) -> None:
    # Verbatim example from manual page 36 for a PMR-7 (6 zones).
    server.script["GETALL"] = (
        "GETALL PMR7 V1.13 OFF 12345 60 "
        "6 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7"
    )
    args = await client.async_send_command("GETALL")
    assert args[0] == "PMR7"
    assert args[1] == "V1.13"
    assert args[2] == "OFF"
    assert args[3] == "12345"
    assert args[4] == "60"
    zone_data = args[5:]
    assert len(zone_data) == 6 * 7
    zone_count = len(zone_data) // 7
    assert zone_count == 6
    # Zone 1: input=6 volume=30 mute=OFF bass=14 treble=14 balance=20 preamp=7
    assert zone_data[0:7] == ["6", "30", "OFF", "14", "14", "20", "7"]


async def test_getall_pmr6_four_zones(server: FakeAatServer, client: AatMultiroomClient) -> None:
    # Verbatim example from manual page 37 for a PMR-6 (4 zones).
    server.script["GETALL"] = (
        "GETALL PMR6 V1.13 ON 12345 60 "
        "6 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7 "
        "5 30 OFF 14 14 20 7"
    )
    args = await client.async_send_command("GETALL")
    assert args[2] == "ON"
    zone_data = args[5:]
    assert len(zone_data) // 7 == 4


async def test_async_probe_returns_model_and_zone_count(server: FakeAatServer) -> None:
    server.script["MODEL"] = "MODEL PMR4"
    server.script["GETALL"] = (
        "GETALL PMR4 V1.13 ON 12345 60 "
        "1 30 OFF 14 14 20 7 "
        "1 30 OFF 14 14 20 7 "
        "1 30 OFF 14 14 20 7 "
        "1 30 OFF 14 14 20 7"
    )
    model, zone_count = await async_probe(server.host, server.port)
    assert model == "PMR4"
    assert zone_count == 4


# ---------------------------------------------------------------------
# Error codes (manual section 1.3.8) - our assumed wire format
# ---------------------------------------------------------------------


async def test_error_code_reply_raises_command_error(
    server: FakeAatServer, client: AatMultiroomClient
) -> None:
    """Assumed format: [r001 17] - a bare error code, command not echoed.

    The manual only lists what each code means (17 = invalid zone number),
    not the exact bytes of an error reply. This should be verified against
    real hardware.
    """
    server.script["VOLGET"] = "17"
    with pytest.raises(AatCommandError) as excinfo:
        await client.async_send_command("VOLGET", 99)
    assert excinfo.value.code == "17"


# ---------------------------------------------------------------------
# Framing robustness
# ---------------------------------------------------------------------


async def test_unsolicited_message_reaches_listener(
    server: FakeAatServer, client: AatMultiroomClient
) -> None:
    received: list[tuple[str, list[str]]] = []
    client.add_listener(lambda cmd, args: received.append((cmd, args)))

    # Manual 1.3.4: unsolicited (type "n") messages are not replies to
    # anything we sent, they just show up on their own.
    await server.send_raw("[n001 ZSTDBYON 2]")
    await asyncio.sleep(0.1)

    assert received == [("ZSTDBYON", ["2"])]


async def test_message_split_across_tcp_reads(
    server: FakeAatServer, client: AatMultiroomClient
) -> None:
    """A reply arriving in two separate TCP writes must still be parsed
    once fully received, not dropped or mis-parsed. No script entry for
    PWRGET, so the fake server's auto-reply stays out of the way and we
    dribble the reply ourselves."""

    async def _send_split_reply() -> None:
        while not server.received:
            await asyncio.sleep(0.01)
        seq = server.received[-1].split()[0][1:]
        await server.send_raw(f"[r{seq} PWR")
        await asyncio.sleep(0.02)
        await server.send_raw("GET ON]")

    task = asyncio.create_task(_send_split_reply())
    args = await client.async_send_command("PWRGET")
    await task

    assert args == ["ON"]


async def test_command_times_out_if_no_reply(client: AatMultiroomClient) -> None:
    with pytest.raises(AatConnectionError):
        await client.async_send_command("VOLGET", 1, timeout=0.2)


async def test_disconnect_fails_pending_commands(
    server: FakeAatServer, client: AatMultiroomClient
) -> None:
    # No script entry for VOLGET -> the fake server never answers, so the
    # pending future is still open when we forcibly close the connection.
    pending = asyncio.ensure_future(client.async_send_command("VOLGET", 1, timeout=5))
    await asyncio.sleep(0.05)
    await client.async_disconnect()
    with pytest.raises(AatConnectionError):
        await pending
