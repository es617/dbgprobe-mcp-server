"""Tests for dbgprobe_mcp_server.handlers_rtt — no hardware required."""

from __future__ import annotations

import pytest

from dbgprobe_mcp_server.backend import Backend, ConnectConfig, ProbeInfo
from dbgprobe_mcp_server.handlers_rtt import (
    handle_rtt_read,
    handle_rtt_start,
    handle_rtt_status,
    handle_rtt_stop,
    handle_rtt_write,
)
from dbgprobe_mcp_server.state import DbgProbeSession, ProbeState

# ---------------------------------------------------------------------------
# Mock backend with RTT support
# ---------------------------------------------------------------------------


class MockRttBackend(Backend):
    name = "mock"

    def __init__(self) -> None:
        self._config: ConnectConfig | None = None
        self._rtt_running = False
        self._rtt_buf = bytearray()
        self._rtt_total_read = 0
        self._rtt_total_written = 0
        self._rtt_data_to_return = b""

    # Required abstract methods
    async def list_probes(self) -> list[ProbeInfo]:
        return []

    async def connect(self, config: ConnectConfig) -> dict:
        self._config = config
        return {}

    async def disconnect(self) -> None:
        pass

    async def reset(self, mode: str) -> dict:
        return {}

    async def halt(self) -> dict:
        return {}

    async def go(self) -> dict:
        return {}

    async def flash(self, path, addr=None, verify=True, reset_after=True, config=None) -> dict:
        return {}

    async def mem_read(self, address: int, length: int) -> bytes:
        return b"\x00" * length

    async def mem_write(self, address: int, data: bytes) -> dict:
        return {}

    async def erase(self, config, start_addr=None, end_addr=None) -> dict:
        return {}

    # RTT methods
    @property
    def rtt_active(self) -> bool:
        return self._rtt_running

    async def rtt_start(self, address: int | None = None) -> dict:
        self._rtt_running = True
        return {"rtt_port": 19021}

    async def rtt_stop(self) -> None:
        self._rtt_running = False
        self._rtt_buf.clear()

    async def rtt_read(self, timeout: float = 0.1) -> bytes:
        data = self._rtt_data_to_return
        self._rtt_data_to_return = b""
        self._rtt_total_read += len(data)
        return data

    async def rtt_write(self, data: bytes) -> int:
        self._rtt_total_written += len(data)
        return len(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(state: ProbeState) -> tuple[str, DbgProbeSession, MockRttBackend]:
    sid = state.generate_id()
    backend = MockRttBackend()
    backend._config = ConnectConfig(
        backend="mock",
        device="nRF52840_xxAA",
        interface="SWD",
        speed_khz=4000,
        probe_serial=None,
    )
    session = DbgProbeSession(
        connection_id=sid,
        backend=backend,
        config=backend._config,
    )
    state.sessions[sid] = session
    return sid, session, backend


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRttStart:
    async def test_start(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        result = await handle_rtt_start(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["session_id"] == sid
        assert result["rtt_port"] == 19021
        assert backend.rtt_active is True

    async def test_start_with_address(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        result = await handle_rtt_start(state, {"session_id": sid, "address": "0x20000000"})
        assert result["ok"] is True

    async def test_start_bad_session(self):
        state = ProbeState()
        with pytest.raises(KeyError, match="Unknown session_id"):
            await handle_rtt_start(state, {"session_id": "nonexistent"})


class TestRttStop:
    async def test_stop(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        await handle_rtt_start(state, {"session_id": sid})
        assert backend.rtt_active is True
        result = await handle_rtt_stop(state, {"session_id": sid})
        assert result["ok"] is True
        assert backend.rtt_active is False

    async def test_stop_bad_session(self):
        state = ProbeState()
        with pytest.raises(KeyError):
            await handle_rtt_stop(state, {"session_id": "nonexistent"})


class TestRttRead:
    async def test_read_utf8(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        backend._rtt_data_to_return = b"Hello RTT\n"
        result = await handle_rtt_read(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["data"] == "Hello RTT\n"
        assert result["bytes_read"] == 10
        assert result["encoding"] == "utf-8"

    async def test_read_hex(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        backend._rtt_data_to_return = b"\xde\xad\xbe\xef"
        result = await handle_rtt_read(state, {"session_id": sid, "encoding": "hex"})
        assert result["ok"] is True
        assert result["data"] == "deadbeef"
        assert result["bytes_read"] == 4

    async def test_read_empty(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        backend._rtt_data_to_return = b""
        result = await handle_rtt_read(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["data"] == ""
        assert result["bytes_read"] == 0

    async def test_read_custom_timeout(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        backend._rtt_data_to_return = b"data"
        result = await handle_rtt_read(state, {"session_id": sid, "timeout": 1.0})
        assert result["ok"] is True
        assert result["bytes_read"] == 4


class TestRttWrite:
    async def test_write_utf8(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        result = await handle_rtt_write(state, {"session_id": sid, "data": "hello"})
        assert result["ok"] is True
        assert result["bytes_written"] == 5

    async def test_write_hex(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        result = await handle_rtt_write(state, {"session_id": sid, "data": "deadbeef", "encoding": "hex"})
        assert result["ok"] is True
        assert result["bytes_written"] == 4

    async def test_write_with_newline(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        result = await handle_rtt_write(state, {"session_id": sid, "data": "hello", "newline": True})
        assert result["ok"] is True
        assert result["bytes_written"] == 6  # "hello" + "\n"

    async def test_write_invalid_hex(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        with pytest.raises(ValueError, match="Invalid hex"):
            await handle_rtt_write(state, {"session_id": sid, "data": "not-hex", "encoding": "hex"})


class TestRttStatus:
    async def test_status_inactive(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        result = await handle_rtt_status(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["active"] is False

    async def test_status_active(self):
        state = ProbeState()
        sid, _, backend = _make_session(state)
        backend._rtt_running = True
        backend._rtt_total_read = 100
        backend._rtt_total_written = 50
        result = await handle_rtt_status(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["active"] is True
        assert result["total_read"] == 100
        assert result["total_written"] == 50
