"""Group and party-mode commands (manual 1.19 / 1.20.2, firmware >= 3.08).

These are different in kind from everything else this integration sends,
and worth being explicit about:

* They go out as a **UDP broadcast** to port 5000, not over the per-unit
  TCP connection.
* They are **never answered** - no reply, no error, no way to confirm they
  arrived or what state resulted. The caller should re-read state
  afterwards (see `async_send_group_command` below, which does).
* They reach **every** AAT multiroom on the network that belongs to the
  addressed group, not just the unit of the config entry used to send
  them. That is the whole point of a group - but it also means they cut
  across the otherwise strict "each multiroom is independent" isolation of
  this integration, which is why the entities built on them are disabled
  by default.
* Group 0 is "party mode": any group command with group 0 is accepted by
  every unit that hasn't opted out of party mode.

Message framing is the same bracketed ASCII as the TCP protocol.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN, GROUP_MAX, GROUP_UDP_PORT, MAX_VOLUME

_LOGGER = logging.getLogger(__name__)

LIMITED_BROADCAST = "255.255.255.255"

# action -> (wire command, takes a trailing value)
GROUP_ACTIONS: dict[str, tuple[str, bool]] = {
    "power_on": ("G_PWRON", False),
    "power_off": ("G_PWROFF", False),
    "mute_on": ("G_MUTEON", False),
    "mute_off": ("G_MUTEOFF", False),
    "volume": ("G_VOLSET", True),
    "input": ("G_INPSET", True),
}


def build_group_message(action: str, group: int, value: int | None = None) -> str:
    """Wire message for a group action, e.g. ``[t001 G_VOLSET 0 15]``.

    Raises ValueError for an unknown action, a group outside 0..32, a
    missing value where one is required, or a value out of range - nothing
    here should ever put a malformed command on a shared broadcast
    network."""
    if action not in GROUP_ACTIONS:
        raise ValueError(f"unknown group action {action!r}")
    if not 0 <= group <= GROUP_MAX:
        raise ValueError(f"group must be 0..{GROUP_MAX}, got {group}")
    command, takes_value = GROUP_ACTIONS[action]
    if not takes_value:
        return f"[t001 {command} {group}]"
    if value is None:
        raise ValueError(f"action {action!r} needs a value")
    if action == "volume" and not 0 <= value <= MAX_VOLUME:
        raise ValueError(f"volume must be 0..{MAX_VOLUME}, got {value}")
    if action == "input" and not 1 <= value <= 8:
        raise ValueError(f"input must be 1..8, got {value}")
    return f"[t001 {command} {group} {value}]"


def build_party_message(on: bool) -> str:
    """G_PARTY: only synchronizes the "party active" indicator between the
    AAT apps - the firmware stores it but doesn't act on it (manual
    1.20.2.1). The actual party behavior is the group-0 commands above."""
    return f"[t001 G_PARTY {1 if on else 0}]"


def broadcast_address_for(host: str, adapters: Iterable[dict[str, Any]]) -> str:
    """Directed broadcast address of the local network that contains
    `host`, from Home Assistant's own network-adapter list.

    Falls back to the limited broadcast (255.255.255.255) when `host`
    isn't on any known adapter's subnet - which works on a plain single-NIC
    setup, but can leave through the wrong interface on a multi-homed one,
    hence trying the directed address first."""
    try:
        target = ipaddress.ip_address(host)
    except ValueError:
        return LIMITED_BROADCAST
    for adapter in adapters:
        for entry in adapter.get("ipv4", []) or []:
            try:
                network = ipaddress.ip_network(
                    f"{entry['address']}/{entry['network_prefix']}", strict=False
                )
            except (KeyError, ValueError):
                continue
            if target in network and network.prefixlen < 32:
                return str(network.broadcast_address)
    return LIMITED_BROADCAST


async def async_send_udp(message: str, address: str, port: int = GROUP_UDP_PORT) -> None:
    """Fire one UDP datagram (with SO_BROADCAST enabled) and close."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        family=socket.AF_INET,
        allow_broadcast=True,
    )
    try:
        transport.sendto(message.encode("ascii"), (address, port))
    finally:
        transport.close()


PARTY_ACTIONS = ("party_on", "party_off")
ALL_ACTIONS: tuple[str, ...] = (*GROUP_ACTIONS, *PARTY_ACTIONS)

# How long to wait before re-reading state after a group command: the
# units need a moment to apply it, and there's no reply to wait for.
RESYNC_DELAY = 1.0


def build_message(action: str, group: int = 0, value: int | None = None) -> str:
    """Wire message for any action in ALL_ACTIONS (party actions ignore
    `group` - there's only one party)."""
    if action in PARTY_ACTIONS:
        return build_party_message(action == "party_on")
    return build_group_message(action, group, value)


async def async_send_group_command(
    hass: HomeAssistant, host: str, action: str, group: int = 0, value: int | None = None
) -> None:
    """Validate, broadcast, then (in the background) re-read every loaded
    unit's state - group commands are unanswered, so reading back is the
    only way the entities find out what changed."""
    message = build_message(action, group, value)
    await async_send_group_message(hass, host, message)
    hass.async_create_task(_async_resync_all(hass))


async def _async_resync_all(hass: HomeAssistant) -> None:
    await asyncio.sleep(RESYNC_DELAY)
    devices = list(hass.data.get(DOMAIN, {}).values())
    results = await asyncio.gather(
        *(device.async_refresh_full_state() for device in devices), return_exceptions=True
    )
    for result in results:
        if isinstance(result, Exception):
            _LOGGER.debug("Resync after group command failed: %s", result)


async def async_send_group_message(hass: HomeAssistant, host: str, message: str) -> None:
    """Broadcast `message` on the network `host` lives on."""
    # Imported here: the network component is only needed for this path.
    from homeassistant.components import network

    try:
        adapters = await network.async_get_adapters(hass)
    except Exception:  # noqa: BLE001 - never let adapter discovery block the send
        _LOGGER.debug("Could not list network adapters; using limited broadcast", exc_info=True)
        adapters = []
    address = broadcast_address_for(host, adapters)
    _LOGGER.debug("Group broadcast %s -> %s:%s", message, address, GROUP_UDP_PORT)
    await async_send_udp(message, address)
