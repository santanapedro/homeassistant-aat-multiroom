"""Tests for the group / party-mode commands.

They are the one place this integration puts something on the shared
network that isn't scoped to a single unit's TCP connection, so the
message builder is tested strictly (nothing malformed should ever be
broadcast) and the UDP send is exercised against a real local socket."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.aat_multiroom import group
from custom_components.aat_multiroom.group import (
    ALL_ACTIONS,
    broadcast_address_for,
    build_group_message,
    build_message,
    build_party_message,
)


# ---------------------------------------------------------------------
# message building
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "group_num", "value", "expected"),
    [
        ("power_on", 0, None, "[t001 G_PWRON 0]"),
        ("power_off", 4, None, "[t001 G_PWROFF 4]"),
        ("mute_on", 0, None, "[t001 G_MUTEON 0]"),
        ("mute_off", 7, None, "[t001 G_MUTEOFF 7]"),
        ("volume", 0, 15, "[t001 G_VOLSET 0 15]"),
        ("volume", 32, 60, "[t001 G_VOLSET 32 60]"),
        ("input", 0, 1, "[t001 G_INPSET 0 1]"),
        ("input", 22, 4, "[t001 G_INPSET 22 4]"),
    ],
)
def test_group_messages_match_the_manuals_examples(action, group_num, value, expected) -> None:
    assert build_group_message(action, group_num, value) == expected


@pytest.mark.parametrize(("on", "expected"), [(True, "[t001 G_PARTY 1]"), (False, "[t001 G_PARTY 0]")])
def test_party_messages(on, expected) -> None:
    assert build_party_message(on) == expected


def test_build_message_routes_party_actions_and_ignores_their_group() -> None:
    assert build_message("party_on", group=17) == "[t001 G_PARTY 1]"
    assert build_message("party_off") == "[t001 G_PARTY 0]"
    assert build_message("volume", 3, 40) == "[t001 G_VOLSET 3 40]"


def test_every_documented_action_is_exposed() -> None:
    assert set(ALL_ACTIONS) == {
        "power_on",
        "power_off",
        "mute_on",
        "mute_off",
        "volume",
        "input",
        "party_on",
        "party_off",
    }


@pytest.mark.parametrize(
    ("action", "group_num", "value"),
    [
        ("explode", 0, None),  # unknown action
        ("power_on", -1, None),  # group below range
        ("power_on", 33, None),  # group above range
        ("volume", 0, None),  # value required
        ("volume", 0, 88),  # volume above range
        ("volume", 0, -1),
        ("input", 0, 0),  # inputs are 1..8
        ("input", 0, 9),
    ],
)
def test_invalid_group_commands_are_rejected_before_anything_is_sent(
    action, group_num, value
) -> None:
    with pytest.raises(ValueError):
        build_group_message(action, group_num, value)


# ---------------------------------------------------------------------
# broadcast address selection
# ---------------------------------------------------------------------

ADAPTERS = [
    {"name": "lo", "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}]},
    {"name": "eth0", "ipv4": [{"address": "192.168.80.2", "network_prefix": 24}]},
    {"name": "wlan0", "ipv4": [{"address": "10.0.4.9", "network_prefix": 16}]},
]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.168.80.34", "192.168.80.255"),
        ("10.0.200.1", "10.0.255.255"),
        ("172.16.0.5", "255.255.255.255"),  # on no known subnet -> limited broadcast
        ("not-an-ip", "255.255.255.255"),
    ],
)
def test_broadcast_address_is_the_directed_one_for_the_hosts_subnet(host, expected) -> None:
    assert broadcast_address_for(host, ADAPTERS) == expected


def test_broadcast_address_tolerates_odd_adapter_data() -> None:
    adapters = [{"ipv4": None}, {"ipv4": [{"address": "x"}]}, {}]
    assert broadcast_address_for("192.168.1.5", adapters) == "255.255.255.255"


# ---------------------------------------------------------------------
# the UDP send itself, against a real local datagram socket
# ---------------------------------------------------------------------


class _Receiver(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.received: asyncio.Queue[tuple[bytes, tuple]] = asyncio.Queue()

    def datagram_received(self, data, addr) -> None:
        self.received.put_nowait((data, addr))


async def test_async_send_udp_delivers_the_datagram() -> None:
    loop = asyncio.get_running_loop()
    receiver = _Receiver()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: receiver, local_addr=("127.0.0.1", 0)
    )
    port = transport.get_extra_info("sockname")[1]
    try:
        await group.async_send_udp("[t001 G_PARTY 1]", "127.0.0.1", port)
        data, _addr = await asyncio.wait_for(receiver.received.get(), timeout=2)
    finally:
        transport.close()

    assert data == b"[t001 G_PARTY 1]"


async def test_send_group_command_validates_before_touching_the_network(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise AssertionError("must not send anything for an invalid command")

    monkeypatch.setattr(group, "async_send_group_message", _boom)
    hass = SimpleNamespace(data={}, async_create_task=lambda coro: coro.close())

    with pytest.raises(ValueError):
        await group.async_send_group_command(hass, "192.168.80.34", "volume", 0, 999)


async def test_send_group_command_broadcasts_then_schedules_a_resync(monkeypatch) -> None:
    broadcasts: list[tuple[str, str]] = []

    async def _fake_send(hass, host, message):
        broadcasts.append((host, message))

    scheduled: list[object] = []
    monkeypatch.setattr(group, "async_send_group_message", _fake_send)
    hass = SimpleNamespace(
        data={}, async_create_task=lambda coro: (scheduled.append(coro), coro.close())
    )

    await group.async_send_group_command(hass, "192.168.80.34", "volume", 0, 15)

    assert broadcasts == [("192.168.80.34", "[t001 G_VOLSET 0 15]")]
    assert len(scheduled) == 1  # the follow-up state re-read


async def test_resync_after_group_command_refreshes_every_loaded_unit(monkeypatch) -> None:
    monkeypatch.setattr(group, "RESYNC_DELAY", 0)
    calls: list[str] = []

    class FakeDevice:
        def __init__(self, name: str, fail: bool = False) -> None:
            self.name, self.fail = name, fail

        async def async_refresh_full_state(self) -> None:
            calls.append(self.name)
            if self.fail:
                raise RuntimeError("unit unreachable")

    hass = SimpleNamespace(
        data={"aat_multiroom": {"a": FakeDevice("a"), "b": FakeDevice("b", fail=True)}}
    )

    await group._async_resync_all(hass)  # one unit failing must not abort the rest

    assert sorted(calls) == ["a", "b"]
