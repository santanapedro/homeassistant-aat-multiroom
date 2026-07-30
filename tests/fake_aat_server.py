"""A tiny fake AAT multiroom TCP "device" used to exercise the real
AatMultiroomClient connect/read/write/framing code path in tests, instead
of poking at its private methods directly.

Usage::

    server = FakeAatServer()
    server.script["PWRON"] = "PWRON"          # single canned reply
    server.script["PWRTOG"] = ["PWRTOG ON", "PWRTOG OFF"]  # consumed in order
    await server.start()
    ...
    await server.stop()

The reply body is whatever comes after the sequence number, e.g. "PWRON" or
"VOLSET 1 15" or "17" (a bare error code) - the server wraps it with
"[r<seq> ...]" itself.
"""

from __future__ import annotations

import asyncio


class FakeAatServer:
    def __init__(self) -> None:
        self.script: dict[str, str | list[str]] = {}
        self.received: list[str] = []
        self.host = "127.0.0.1"
        self.port = 0
        self._server: asyncio.base_events.Server | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def send_raw(self, message: str) -> None:
        """Send an already-formed ``[...]`` message straight to the client."""
        assert self._writer is not None
        self._writer.write(message.encode("ascii"))
        await self._writer.drain()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        buffer = ""
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                buffer += chunk.decode("ascii")
                while True:
                    start = buffer.find("[")
                    end = buffer.find("]", start)
                    if start == -1 or end == -1:
                        break
                    message = buffer[start + 1 : end]
                    buffer = buffer[end + 1 :]
                    self.received.append(message)
                    await self._auto_reply(message)
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass

    async def _auto_reply(self, message: str) -> None:
        header, _, rest = message.partition(" ")
        seq = header[1:]
        parts = rest.split()
        if not parts:
            return
        cmd = parts[0].upper()
        scripted = self.script.get(cmd)
        if scripted is None:
            return
        if isinstance(scripted, list):
            body = scripted.pop(0) if scripted else cmd
        else:
            body = scripted
        await self.send_raw(f"[r{seq} {body}]")
