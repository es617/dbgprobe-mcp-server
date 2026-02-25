"""Async GDB Remote Serial Protocol client over TCP.

Zero dependencies beyond asyncio.  Talks to JLinkGDBServer (or any
GDB-RSP compliant stub) on a local TCP port.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass, field

logger = logging.getLogger("dbgprobe_mcp_server")

_GDB_TRACE = os.environ.get("DBGPROBE_GDB_TRACE", "").lower() in ("1", "true", "yes")
_GDB_TRACE_FILE = os.environ.get(
    "DBGPROBE_GDB_TRACE_FILE", os.path.join(tempfile.gettempdir(), "gdb_trace.log")
)


def _trace(msg: str) -> None:
    if not _GDB_TRACE:
        return
    import time

    with open(_GDB_TRACE_FILE, "a") as f:
        f.write(f"{time.time():.3f} {msg}\n")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GdbProtocolError(ConnectionError):
    """The stub sent an unexpected or malformed response."""


class GdbTimeoutError(TimeoutError):
    """A GDB operation timed out."""


class GdbConnectionError(ConnectionError):
    """TCP connection to GDB stub lost or could not be established."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

_SIGNAL_REASONS = {
    2: "interrupt",
    5: "halted",  # SIGTRAP — generic debug halt (bp, step, or query)
}


@dataclass
class StopReply:
    """Parsed GDB stop reply (T packet)."""

    signal: int  # 5 = SIGTRAP (debug event), 2 = SIGINT (interrupt)
    reason: str  # "halted", "breakpoint", "interrupt", "unknown"
    registers: dict[int, int] = field(default_factory=dict)  # reg_num -> value


def _parse_stop_reply(payload: str) -> StopReply:
    """Parse a T or S stop reply packet payload."""
    if not payload:
        raise GdbProtocolError("Empty stop reply")

    kind = payload[0]
    if kind == "S":
        sig = int(payload[1:3], 16)
        return StopReply(signal=sig, reason=_SIGNAL_REASONS.get(sig, "unknown"))

    if kind != "T":
        raise GdbProtocolError(f"Unexpected stop reply: {payload!r}")

    sig = int(payload[1:3], 16)
    regs: dict[int, int] = {}
    reason = _SIGNAL_REASONS.get(sig, "unknown")
    rest = payload[3:]
    if rest.startswith(";"):
        rest = rest[1:]
    for pair in rest.rstrip(";").split(";"):
        if not pair:
            continue
        if ":" not in pair:
            continue
        key, val = pair.split(":", 1)
        if key in ("swbreak", "hwbreak"):
            reason = "breakpoint"
            continue
        try:
            reg_num = int(key, 16)
            reg_val = int(val, 16)
            regs[reg_num] = reg_val
        except ValueError:
            pass  # named keys like "thread" — skip

    return StopReply(
        signal=sig,
        reason=reason,
        registers=regs,
    )


# ---------------------------------------------------------------------------
# Checksum helpers
# ---------------------------------------------------------------------------


def _checksum(data: str) -> str:
    s = sum(ord(c) for c in data) & 0xFF
    return f"{s:02x}"


def _make_packet(payload: str) -> bytes:
    return f"${payload}#{_checksum(payload)}".encode("ascii")


# ---------------------------------------------------------------------------
# GdbClient
# ---------------------------------------------------------------------------

# Chunk limits — keep individual GDB packets reasonably sized.
_MEM_READ_CHUNK = 1024  # bytes per 'm' request
_MEM_WRITE_CHUNK = 512  # bytes per 'M' request


class GdbClient:
    """Async GDB Remote Serial Protocol client.

    Usage::

        client = GdbClient("127.0.0.1", 2331)
        await client.connect()
        sr = await client.halt()
        data = await client.read_memory(0x2000_0000, 256)
        await client.close()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 2331) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None

        # Command response channel — set when a packet arrives.
        self._response_event = asyncio.Event()
        self._response_data: str = ""

        # Async stop reply channel (from 'c' or interrupt).
        self._stop_event = asyncio.Event()
        self._stop_data: str = ""

        self._closed = False
        self._ack_mode = True  # start with ack enabled

    # -- properties ----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._closed

    # -- connection ----------------------------------------------------------

    async def connect(self, timeout: float = 5.0) -> None:
        """Open TCP connection and perform GDB handshake."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=timeout,
            )
        except (OSError, TimeoutError) as exc:
            raise GdbConnectionError(
                f"Cannot connect to GDB stub at {self._host}:{self._port}: {exc}"
            ) from exc

        self._closed = False
        self._reader_task = asyncio.create_task(self._read_loop())

        # Handshake — query supported features.
        await self.send_packet("qSupported")
        # Query initial status.
        await self.send_packet("?")

    async def close(self) -> None:
        """Close TCP connection and cancel reader task."""
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    # -- low-level -----------------------------------------------------------

    async def send_packet(self, payload: str, timeout: float = 10.0) -> str:
        """Send a GDB packet and wait for the response.

        Returns the response payload (without $ and checksum).
        """
        if not self.connected:
            raise GdbConnectionError("Not connected to GDB stub")

        self._response_event.clear()
        pkt = _make_packet(payload)
        logger.debug("GDB TX: %s", pkt)
        self._writer.write(pkt)  # type: ignore[union-attr]
        await self._writer.drain()  # type: ignore[union-attr]

        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
        except TimeoutError:
            raise GdbTimeoutError(f"Timeout waiting for response to {payload!r}") from None

        return self._response_data

    async def send_interrupt(self) -> None:
        """Send Ctrl-C (\\x03) to halt the target."""
        if not self.connected:
            raise GdbConnectionError("Not connected to GDB stub")
        self._writer.write(b"\x03")  # type: ignore[union-attr]
        await self._writer.drain()  # type: ignore[union-attr]

    # -- reader task ---------------------------------------------------------

    async def _read_loop(self) -> None:
        """Background task: read GDB packets from TCP stream."""
        if self._reader is None:
            return
        try:
            while not self._closed:
                byte = await self._reader.read(1)
                if not byte:
                    break  # EOF

                ch = byte[0:1]
                if ch == b"+":
                    continue  # ack — ignore
                if ch == b"-":
                    logger.warning("GDB stub sent NACK")
                    continue

                if ch == b"$":
                    raw = await self._read_until_hash()
                    payload = raw.decode("ascii", errors="replace")
                    logger.debug("GDB RX: $%s", payload)

                    # Send ack
                    if self._ack_mode and self._writer is not None:
                        self._writer.write(b"+")
                        await self._writer.drain()

                    # Strip checksum (last 2 chars after #)
                    body = payload[:-3] if len(payload) >= 3 and "#" in payload else payload

                    # Dispatch
                    self._dispatch_packet(body)

        except asyncio.CancelledError:
            return
        except (ConnectionError, OSError) as exc:
            if not self._closed:
                logger.error("GDB connection lost: %s", exc)
                self._closed = True
                # Wake any waiters so they get an error.
                self._response_data = ""
                self._response_event.set()
                self._stop_data = ""
                self._stop_event.set()

    async def _read_until_hash(self) -> bytes:
        """Read until we see '#XX' (hash + 2 hex checksum chars)."""
        if self._reader is None:
            raise GdbConnectionError("Not connected")
        buf = bytearray()
        while True:
            b = await self._reader.read(1)
            if not b:
                raise GdbConnectionError("Connection closed while reading packet")
            buf.extend(b)
            # Check if we have '#XX' at the end
            if len(buf) >= 3 and buf[-3:][0:1] == b"#":
                break
        return bytes(buf)

    def _dispatch_packet(self, body: str) -> None:
        """Route a received packet body to the right channel."""
        _trace(
            f"dispatch: body={body[:40]} response_event={self._response_event.is_set()} stop_event={self._stop_event.is_set()}"
        )
        if body.startswith("O") and body != "OK" and len(body) > 1:
            # Console output (O<hex-encoded-text>) — log and discard.
            # "OK" is a normal response, not console output.
            hex_part = body[1:]
            if all(c in "0123456789abcdefABCDEF" for c in hex_part) and len(hex_part) % 2 == 0:
                try:
                    text = bytes.fromhex(hex_part).decode("utf-8", errors="replace")
                    logger.debug("GDB console: %s", text.rstrip())
                except ValueError:
                    pass
                return

        if body and body[0] in ("T", "S"):
            # Stop reply — could be from 'c', 's', or interrupt.
            # If someone is waiting for a command response, this IS the response
            # (e.g. for '?' or 's').  Otherwise it's an async stop.
            if not self._response_event.is_set():
                self._response_data = body
                self._response_event.set()
            else:
                self._stop_data = body
                self._stop_event.set()
            return

        # Normal command response.
        self._response_data = body
        self._response_event.set()

    # -- high-level ----------------------------------------------------------

    async def read_memory(self, addr: int, length: int) -> bytes:
        """Read *length* bytes from target memory."""
        result = bytearray()
        remaining = length
        offset = 0
        while remaining > 0:
            chunk = min(remaining, _MEM_READ_CHUNK)
            pkt = f"m{addr + offset:x},{chunk:x}"
            resp = await self.send_packet(pkt)
            if resp.startswith("E"):
                raise GdbProtocolError(f"Memory read error at 0x{addr + offset:08x}: {resp}")
            result.extend(bytes.fromhex(resp))
            offset += chunk
            remaining -= chunk
        return bytes(result)

    async def write_memory(self, addr: int, data: bytes) -> None:
        """Write *data* to target memory."""
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + _MEM_WRITE_CHUNK]
            hex_data = chunk.hex()
            resp = await self.send_packet(f"M{addr + offset:x},{len(chunk):x}:{hex_data}")
            if resp != "OK":
                raise GdbProtocolError(f"Memory write error at 0x{addr + offset:08x}: {resp}")
            offset += len(chunk)

    async def continue_execution(self) -> None:
        """Send 'c' (continue).  Returns immediately — the stop reply
        will arrive asynchronously via :meth:`wait_stop`.
        """
        if not self.connected:
            raise GdbConnectionError("Not connected to GDB stub")
        self._stop_event.clear()
        pkt = _make_packet("c")
        _trace(
            f"continue: stop_event={self._stop_event.is_set()} response_event={self._response_event.is_set()}, sending $c"
        )
        self._writer.write(pkt)  # type: ignore[union-attr]
        await self._writer.drain()  # type: ignore[union-attr]
        _trace(f"continue: sent, stop_event={self._stop_event.is_set()}")

    async def step(self, timeout: float = 10.0) -> StopReply:
        """Single-step one instruction.  Returns the stop reply."""
        resp = await self.send_packet("s", timeout=timeout)
        return _parse_stop_reply(resp)

    async def halt(self, timeout: float = 5.0) -> StopReply:
        """Send interrupt (\\x03) and wait for the stop reply."""
        _trace("halt: clearing stop_event, sending \\x03")
        self._stop_event.clear()
        await self.send_interrupt()
        # The stop reply may come as an async stop or as a direct response.
        # Try waiting on both channels.
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            return _parse_stop_reply(self._stop_data)
        except TimeoutError:
            # Maybe it arrived on the response channel
            if self._response_event.is_set() and self._response_data and self._response_data[0] in ("T", "S"):
                return _parse_stop_reply(self._response_data)
            raise GdbTimeoutError("Timeout waiting for halt stop reply") from None

    async def query_status(self) -> StopReply:
        """Query current target status via '?'."""
        _trace("query_status: sending ?")
        resp = await self.send_packet("?")
        _trace(f"query_status: resp={resp[:40]}")
        return _parse_stop_reply(resp)

    async def set_breakpoint(self, bp_type: int, addr: int, kind: int = 2) -> None:
        """Set a breakpoint.  *bp_type*: 0=software, 1=hardware.  *kind*: 2=Thumb, 4=ARM."""
        resp = await self.send_packet(f"Z{bp_type},{addr:x},{kind}")
        if resp != "OK":
            raise GdbProtocolError(f"Failed to set breakpoint at 0x{addr:08x}: {resp}")

    async def clear_breakpoint(self, bp_type: int, addr: int, kind: int = 2) -> None:
        """Clear a breakpoint.  *bp_type*: 0=software, 1=hardware.  *kind*: 2=Thumb, 4=ARM."""
        resp = await self.send_packet(f"z{bp_type},{addr:x},{kind}")
        if resp != "OK":
            raise GdbProtocolError(f"Failed to clear breakpoint at 0x{addr:08x}: {resp}")

    async def monitor_command(self, cmd: str) -> str:
        """Send a monitor command (qRcmd).  Returns decoded output."""
        hex_cmd = cmd.encode("ascii").hex()
        resp = await self.send_packet(f"qRcmd,{hex_cmd}")
        if resp.startswith("E"):
            raise GdbProtocolError(f"Monitor command failed: {resp}")
        # Response may be 'OK' directly, or we already consumed 'O' packets
        # in the reader loop.
        return resp

    async def read_register(self, reg_num: int) -> int:
        """Read a single register by GDB register number (p packet)."""
        resp = await self.send_packet(f"p{reg_num:x}")
        if resp.startswith("E"):
            raise GdbProtocolError(f"Register read error: {resp}")
        raw = bytes.fromhex(resp)
        # Little-endian 32-bit for ARM
        return int.from_bytes(raw[:4], "little")

    async def read_registers(self) -> bytes:
        """Read all registers (g packet)."""
        resp = await self.send_packet("g")
        if resp.startswith("E"):
            raise GdbProtocolError(f"Register read error: {resp}")
        return bytes.fromhex(resp)

    async def wait_stop(self, timeout: float = 30.0) -> StopReply:
        """Wait for an asynchronous stop reply (e.g. after continue)."""
        _trace(
            f"wait_stop: stop_event={self._stop_event.is_set()} stop_data={self._stop_data[:40] if self._stop_data else '(empty)'} timeout={timeout}"
        )
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except TimeoutError:
            _trace("wait_stop: TIMEOUT")
            raise GdbTimeoutError("Timeout waiting for target to stop") from None
        _trace(f"wait_stop: got stop_data={self._stop_data[:40]}")
        return _parse_stop_reply(self._stop_data)
