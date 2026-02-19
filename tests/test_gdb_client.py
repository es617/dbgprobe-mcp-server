"""Tests for dbgprobe_mcp_server.gdb_client — mock TCP server, no hardware."""

from __future__ import annotations

import asyncio

import pytest

from dbgprobe_mcp_server.gdb_client import (
    GdbClient,
    GdbConnectionError,
    GdbProtocolError,
    StopReply,
    _checksum,
    _make_packet,
    _parse_stop_reply,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _packet(payload: str) -> bytes:
    """Build a well-formed GDB packet."""
    return _make_packet(payload)


class MockGdbServer:
    """Minimal mock GDB-RSP server for testing."""

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None
        self.host = "127.0.0.1"
        self.port = 0  # assigned on start
        self._responses: list[str] = []
        self._writer: asyncio.StreamWriter | None = None

    def enqueue(self, *payloads: str) -> None:
        """Queue response payloads to send back."""
        self._responses.extend(payloads)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                # For each received packet, send the next queued response.
                decoded = data.decode("ascii", errors="replace")
                # Count how many packets we received (each starts with $)
                # Also handle bare \x03 (interrupt)
                for ch in decoded:
                    if ch == "$":
                        # Read until we process this packet, send ack + response
                        writer.write(b"+")
                        if self._responses:
                            resp = self._responses.pop(0)
                            writer.write(_packet(resp))
                        await writer.drain()
                    elif ch == "\x03":
                        # Interrupt — send stop reply
                        if self._responses:
                            resp = self._responses.pop(0)
                            writer.write(_packet(resp))
                            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()


@pytest.fixture()
async def mock_server():
    server = MockGdbServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture()
async def gdb_client(mock_server: MockGdbServer):
    # Queue handshake responses: qSupported response + ? status response
    mock_server.enqueue("qSupported:PacketSize=4096", "T05")
    client = GdbClient(mock_server.host, mock_server.port)
    await client.connect(timeout=5.0)
    yield client
    await client.close()


# ---------------------------------------------------------------------------
# Unit tests — packet building / parsing
# ---------------------------------------------------------------------------


class TestChecksum:
    def test_simple(self):
        assert _checksum("g") == "67"

    def test_empty(self):
        assert _checksum("") == "00"


class TestMakePacket:
    def test_simple(self):
        pkt = _make_packet("g")
        assert pkt == b"$g#67"

    def test_question(self):
        pkt = _make_packet("?")
        assert pkt == b"$?#3f"


class TestParseStopReply:
    def test_t_packet(self):
        sr = _parse_stop_reply("T05")
        assert sr.signal == 5
        assert sr.reason == "halted"
        assert sr.registers == {}

    def test_t_packet_with_registers(self):
        sr = _parse_stop_reply("T05f:12345678;")
        assert sr.signal == 5
        assert sr.registers == {0xF: 0x12345678}

    def test_s_packet(self):
        sr = _parse_stop_reply("S02")
        assert sr.signal == 2
        assert sr.reason == "interrupt"

    def test_hwbreak(self):
        sr = _parse_stop_reply("T05hwbreak:;")
        assert sr.signal == 5
        assert sr.reason == "breakpoint"

    def test_swbreak(self):
        sr = _parse_stop_reply("T05swbreak:;")
        assert sr.signal == 5
        assert sr.reason == "breakpoint"

    def test_unknown_signal(self):
        sr = _parse_stop_reply("T09")
        assert sr.signal == 9
        assert sr.reason == "unknown"

    def test_empty_raises(self):
        with pytest.raises(GdbProtocolError, match="Empty"):
            _parse_stop_reply("")

    def test_invalid_kind_raises(self):
        with pytest.raises(GdbProtocolError, match="Unexpected"):
            _parse_stop_reply("X05")


# ---------------------------------------------------------------------------
# Integration tests with mock TCP server
# ---------------------------------------------------------------------------


class TestGdbClientConnect:
    async def test_connect_and_close(self, mock_server: MockGdbServer):
        mock_server.enqueue("qSupported:PacketSize=4096", "T05")
        client = GdbClient(mock_server.host, mock_server.port)
        await client.connect()
        assert client.connected is True
        await client.close()
        assert client.connected is False

    async def test_connect_failure(self):
        client = GdbClient("127.0.0.1", 1)  # unlikely to be open
        with pytest.raises(GdbConnectionError, match="Cannot connect"):
            await client.connect(timeout=0.5)

    async def test_not_connected_raises(self):
        client = GdbClient("127.0.0.1", 1)
        with pytest.raises(GdbConnectionError, match="Not connected"):
            await client.send_packet("?")


class TestGdbClientMemory:
    async def test_read_memory(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("deadbeef")
        data = await gdb_client.read_memory(0x2000_0000, 4)
        assert data == b"\xde\xad\xbe\xef"

    async def test_read_memory_error(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("E01")
        with pytest.raises(GdbProtocolError, match="Memory read error"):
            await gdb_client.read_memory(0x2000_0000, 4)

    async def test_write_memory(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("OK")
        await gdb_client.write_memory(0x2000_0000, b"\x01\x02\x03\x04")

    async def test_write_memory_error(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("E01")
        with pytest.raises(GdbProtocolError, match="Memory write error"):
            await gdb_client.write_memory(0x2000_0000, b"\x01\x02")


class TestGdbClientExecution:
    async def test_step(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("T05")
        sr = await gdb_client.step()
        assert isinstance(sr, StopReply)
        assert sr.signal == 5

    async def test_halt(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("T02")
        sr = await gdb_client.halt()
        assert sr.signal == 2
        assert sr.reason == "interrupt"

    async def test_query_status(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("T05")
        sr = await gdb_client.query_status()
        assert sr.signal == 5


class TestGdbClientBreakpoints:
    async def test_set_breakpoint(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("OK")
        await gdb_client.set_breakpoint(1, 0x0800_0100)

    async def test_set_breakpoint_error(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("E01")
        with pytest.raises(GdbProtocolError, match="Failed to set"):
            await gdb_client.set_breakpoint(1, 0x0800_0100)

    async def test_clear_breakpoint(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("OK")
        await gdb_client.clear_breakpoint(1, 0x0800_0100)

    async def test_clear_breakpoint_error(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("E01")
        with pytest.raises(GdbProtocolError, match="Failed to clear"):
            await gdb_client.clear_breakpoint(1, 0x0800_0100)


class TestGdbClientMonitor:
    async def test_monitor_command(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("OK")
        resp = await gdb_client.monitor_command("reset")
        assert resp == "OK"

    async def test_monitor_command_error(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("E01")
        with pytest.raises(GdbProtocolError, match="Monitor command failed"):
            await gdb_client.monitor_command("bad")


class TestGdbClientRegisters:
    async def test_read_registers(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("0000000011111111")
        data = await gdb_client.read_registers()
        assert data == bytes.fromhex("0000000011111111")

    async def test_read_registers_error(self, mock_server: MockGdbServer, gdb_client: GdbClient):
        mock_server.enqueue("E01")
        with pytest.raises(GdbProtocolError, match="Register read error"):
            await gdb_client.read_registers()


class TestStopReplyDataclass:
    def test_defaults(self):
        sr = StopReply(signal=5, reason="halted")
        assert sr.registers == {}

    def test_with_registers(self):
        sr = StopReply(signal=5, reason="halted", registers={15: 0x0800_0100})
        assert sr.registers[15] == 0x0800_0100
