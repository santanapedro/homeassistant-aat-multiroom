"""Low level async TCP client for the AAT Multiroom protocol.

Protocol reference: "AAT Digital Matrix Amplifiers - API (TCP/SERIAL/IR) Rev.12".

Every message is ASCII, wrapped in ``[`` / ``]`` and looks like::

    [t001 VOLSET 1 15]      -> sent to the device (t = "to device")
    [r001 VOLSET 1 15]      -> reply from the device (same sequence number)
    [n007 ZSTDBYON 2]       -> unsolicited notification (e.g. front panel/IR)

The sequence number lets us match a reply to the request that caused it,
while unsolicited messages let us reflect state changes made outside of
Home Assistant (front panel, remote control, another app) almost instantly.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

_HEADER_RE = re.compile(r"^\s*([trn])\s*0*?(\d{1,3})\s+(.*)$", re.IGNORECASE)

MessageListener = Callable[[str, list[str]], None]


class AatConnectionError(Exception):
    """Raised when the TCP connection to the multiroom fails, drops or times out."""


class AatCommandError(Exception):
    """Raised when the device rejects a command with one of its error codes."""

    def __init__(self, code: str) -> None:
        super().__init__(f"AAT multiroom returned error code {code}")
        self.code = code


class AatMultiroomClient:
    """Maintains a persistent TCP connection to one AAT multiroom amplifier."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: list[MessageListener] = []
        self._buffer = ""
        self._connected = False

        # Called (no args) whenever the connection is lost unexpectedly.
        self.on_disconnected: Callable[[], None] | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    def add_listener(self, callback: MessageListener) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: MessageListener) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    async def async_connect(self) -> None:
        """Open the TCP connection and start the background reader."""
        try:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        except OSError as err:
            raise AatConnectionError(str(err)) from err
        self._buffer = ""
        self._connected = True
        self._read_task = asyncio.create_task(self._reader_loop())

    async def async_disconnect(self) -> None:
        """Close the connection. Does not trigger on_disconnected."""
        self.on_disconnected = None
        self._connected = False
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
        self._fail_pending(AatConnectionError("connection closed"))

    async def async_send_command(self, command: str, *args: object, timeout: float = 3.0) -> list[str]:
        """Send a command, wait for its matching reply and return the reply args."""
        if not self._connected or self._writer is None:
            raise AatConnectionError("not connected")

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        async with self._write_lock:
            self._seq = (self._seq % 999) + 1
            seq = self._seq
            self._pending[seq] = fut
            payload = "[t" + f"{seq:03d}" + " " + command
            for arg in args:
                payload += f" {arg}"
            payload += "]"
            try:
                self._writer.write(payload.encode("ascii"))
                await self._writer.drain()
            except OSError as err:
                self._pending.pop(seq, None)
                raise AatConnectionError(str(err)) from err

        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as err:
            self._pending.pop(seq, None)
            raise AatConnectionError(f"timeout waiting for reply to {command}") from err

    async def _reader_loop(self) -> None:
        try:
            while True:
                assert self._reader is not None
                chunk = await self._reader.read(1024)
                if not chunk:
                    break
                self._buffer += chunk.decode("ascii", errors="ignore")
                self._process_buffer()
        except asyncio.CancelledError:
            raise
        except OSError:
            _LOGGER.debug("Connection to %s:%s lost", self._host, self._port, exc_info=True)
        finally:
            was_connected = self._connected
            self._connected = False
            self._fail_pending(AatConnectionError("connection lost"))
            if was_connected and self.on_disconnected is not None:
                self.on_disconnected()

    def _process_buffer(self) -> None:
        while True:
            start = self._buffer.find("[")
            if start == -1:
                self._buffer = ""
                return
            end = self._buffer.find("]", start)
            if end == -1:
                self._buffer = self._buffer[start:]
                return
            message = self._buffer[start + 1 : end]
            self._buffer = self._buffer[end + 1 :]
            self._handle_message(message)

    def _handle_message(self, message: str) -> None:
        match = _HEADER_RE.match(message)
        if not match:
            _LOGGER.debug("Ignoring malformed message: %r", message)
            return

        msg_type = match.group(1).lower()
        seq_str = match.group(2)
        body = match.group(3).split()
        if not body:
            return

        token = body[0]
        cmd: str | None
        args: list[str]
        error_code: str | None = None
        if token.isdigit():
            cmd = None
            error_code = token
            args = []
        else:
            cmd = token.upper()
            args = body[1:]

        if msg_type == "r":
            try:
                seq = int(seq_str)
            except ValueError:
                seq = None
            fut = self._pending.pop(seq, None) if seq is not None else None
            if fut is not None and not fut.done():
                if cmd is None:
                    fut.set_exception(AatCommandError(error_code or "?"))
                else:
                    fut.set_result(args)

        if cmd is not None:
            for listener in list(self._listeners):
                try:
                    listener(cmd, args)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Error in AAT multiroom message listener")

    def _fail_pending(self, err: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()


async def async_probe(host: str, port: int) -> tuple[str, int]:
    """Connect briefly, ask MODEL + GETALL and return (model, zone_count)."""
    client = AatMultiroomClient(host, port)
    await client.async_connect()
    try:
        model_args = await client.async_send_command("MODEL")
        model = model_args[0] if model_args else "PMR"
        getall_args = await client.async_send_command("GETALL")
        zone_data = getall_args[5:]
        zone_count = max(1, len(zone_data) // 7)
    finally:
        await client.async_disconnect()
    return model, zone_count
