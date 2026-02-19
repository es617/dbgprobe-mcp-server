"""Tests for dbgprobe_mcp_server.handlers_probe — no hardware required."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dbgprobe_mcp_server.backend import Backend, ConnectConfig, DeviceSecuredError, ProbeInfo
from dbgprobe_mcp_server.handlers_probe import (
    handle_connect,
    handle_disconnect,
    handle_erase,
    handle_flash,
    handle_go,
    handle_halt,
    handle_list_probes,
    handle_mem_read,
    handle_mem_write,
    handle_reset,
)
from dbgprobe_mcp_server.state import DbgProbeSession, ProbeState

# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockBackend(Backend):
    name = "mock"

    def __init__(self):
        self._config = None
        self.disconnected = False

    async def list_probes(self):
        return [ProbeInfo(serial="MOCK001", description="Mock Probe", backend="mock")]

    async def connect(self, config):
        self._config = config
        return {"resolved_paths": {"mock_exe": "/mock/path"}}

    async def disconnect(self):
        self.disconnected = True

    async def reset(self, mode):
        return {"mode": mode}

    async def halt(self):
        return {}

    async def go(self):
        return {}

    async def flash(self, path, addr=None, verify=True, reset_after=True):
        return {"file": path, "verified": verify, "reset": reset_after}

    async def mem_read(self, address, length):
        return bytes(range(length % 256))

    async def mem_write(self, address, data):
        return {"address": address, "length": len(data)}

    async def erase(self, config, start_addr=None, end_addr=None):
        return {"resolved_paths": {"mock_exe": "/mock/path"}}


class SecuredMockBackend(MockBackend):
    """Mock backend that raises DeviceSecuredError on connect."""

    async def connect(self, config):
        raise DeviceSecuredError("Target device is secured. Use dbgprobe.erase to mass-erase and unlock.")


def _mock_registry_create(name):
    if name == "mock":
        return MockBackend()
    raise ValueError(f"Unknown backend {name!r}")


def _patch_registry():
    return patch("dbgprobe_mcp_server.handlers_probe.registry.create", side_effect=_mock_registry_create)


def _patch_defaults():
    """Patch env var defaults used by handlers."""
    return patch.multiple(
        "dbgprobe_mcp_server.handlers_probe",
        DBGPROBE_BACKEND="mock",
        DBGPROBE_JLINK_DEVICE=None,
        DBGPROBE_INTERFACE="SWD",
        DBGPROBE_SPEED_KHZ=4000,
    )


def _make_session(state: ProbeState) -> tuple[str, DbgProbeSession]:
    sid = state.generate_id()
    backend = MockBackend()
    backend._config = ConnectConfig(
        backend="mock",
        device=None,
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
    return sid, session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListProbes:
    async def test_success(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_list_probes(state, {})
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["probes"][0]["serial"] == "MOCK001"

    async def test_explicit_backend(self):
        state = ProbeState()
        with _patch_registry():
            result = await handle_list_probes(state, {"backend": "mock"})
        assert result["ok"] is True

    async def test_unknown_backend(self):
        state = ProbeState()
        with _patch_registry():
            result = await handle_list_probes(state, {"backend": "nope"})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_backend"


class TestConnect:
    async def test_success(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_connect(state, {"backend": "mock"})
        assert result["ok"] is True
        assert "session_id" in result
        assert result["config"]["backend"] == "mock"
        assert len(state.sessions) == 1

    async def test_with_device(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_connect(state, {"backend": "mock", "device": "STM32F4"})
        assert result["ok"] is True
        assert result["config"]["device"] == "STM32F4"

    async def test_max_sessions(self):
        state = ProbeState(max_sessions=1)
        with _patch_registry(), _patch_defaults():
            await handle_connect(state, {"backend": "mock"})
            with pytest.raises(RuntimeError, match="Maximum sessions"):
                await handle_connect(state, {"backend": "mock"})

    async def test_unknown_backend(self):
        state = ProbeState()
        with _patch_registry():
            result = await handle_connect(state, {"backend": "nope"})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_backend"

    async def test_device_secured(self):
        state = ProbeState()

        def _create_secured(name):
            if name == "mock":
                return SecuredMockBackend()
            raise ValueError(f"Unknown backend {name!r}")

        with (
            patch("dbgprobe_mcp_server.handlers_probe.registry.create", side_effect=_create_secured),
            _patch_defaults(),
        ):
            result = await handle_connect(state, {"backend": "mock"})
        assert result["ok"] is False
        assert result["error"]["code"] == "device_secured"
        assert len(state.sessions) == 0


class TestErase:
    async def test_success(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_erase(state, {"backend": "mock"})
        assert result["ok"] is True
        assert result["erased"] is True
        assert result["config"]["backend"] == "mock"
        assert "start_addr" not in result

    async def test_range_erase(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_erase(
                state, {"backend": "mock", "start_addr": 0x40000, "end_addr": 0x80000}
            )
        assert result["ok"] is True
        assert result["erased"] is True
        assert result["start_addr"] == 0x40000
        assert result["end_addr"] == 0x80000

    async def test_start_addr_without_end_addr(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_erase(state, {"backend": "mock", "start_addr": 0x40000})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_end_addr_without_start_addr(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_erase(state, {"backend": "mock", "end_addr": 0x80000})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_start_addr_not_less_than_end_addr(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_erase(
                state, {"backend": "mock", "start_addr": 0x80000, "end_addr": 0x40000}
            )
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_unknown_backend(self):
        state = ProbeState()
        with _patch_registry():
            result = await handle_erase(state, {"backend": "nope"})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_backend"


class TestDisconnect:
    async def test_success(self):
        state = ProbeState()
        sid, session = _make_session(state)
        result = await handle_disconnect(state, {"session_id": sid})
        assert result["ok"] is True
        assert sid not in state.sessions
        assert session.backend.disconnected

    async def test_unknown_session(self):
        state = ProbeState()
        with pytest.raises(KeyError, match="Unknown connection_id"):
            await handle_disconnect(state, {"session_id": "nope"})


class TestReset:
    async def test_soft(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_reset(state, {"session_id": sid, "mode": "soft"})
        assert result["ok"] is True
        assert result["mode"] == "soft"

    async def test_halt(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_reset(state, {"session_id": sid, "mode": "halt"})
        assert result["ok"] is True

    async def test_default_mode(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_reset(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["mode"] == "soft"

    async def test_invalid_mode(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_reset(state, {"session_id": sid, "mode": "bogus"})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"


class TestHalt:
    async def test_success(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_halt(state, {"session_id": sid})
        assert result["ok"] is True


class TestGo:
    async def test_success(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_go(state, {"session_id": sid})
        assert result["ok"] is True


class TestFlash:
    async def test_success(self, tmp_path):
        state = ProbeState()
        sid, _ = _make_session(state)
        fw = tmp_path / "test.hex"
        fw.write_text(":00000001FF\n")

        result = await handle_flash(state, {"session_id": sid, "path": str(fw)})
        assert result["ok"] is True
        assert result["verified"] is True


class TestMemRead:
    async def test_hex_format(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_read(
            state,
            {
                "session_id": sid,
                "address": 0x2000_0000,
                "length": 4,
            },
        )
        assert result["ok"] is True
        assert result["format"] == "hex"
        assert isinstance(result["data"], str)
        assert result["length"] == 4

    async def test_base64_format(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_read(
            state,
            {
                "session_id": sid,
                "address": 0x2000_0000,
                "length": 4,
                "format": "base64",
            },
        )
        assert result["ok"] is True
        assert result["format"] == "base64"

    async def test_u32_format(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_read(
            state,
            {
                "session_id": sid,
                "address": 0x2000_0000,
                "length": 8,
                "format": "u32",
            },
        )
        assert result["ok"] is True
        assert result["format"] == "u32"
        assert isinstance(result["data"], list)


class TestMemWrite:
    async def test_hex_format(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_write(
            state,
            {
                "session_id": sid,
                "address": 0x2000_0000,
                "data": "deadbeef",
            },
        )
        assert result["ok"] is True
        assert result["length"] == 4

    async def test_base64_format(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_write(
            state,
            {
                "session_id": sid,
                "address": 0x2000_0000,
                "data": "3q2+7w==",
                "format": "base64",
            },
        )
        assert result["ok"] is True

    async def test_u32_format(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_write(
            state,
            {
                "session_id": sid,
                "address": 0x2000_0000,
                "data_u32": [0xDEADBEEF, 0xCAFEBABE],
                "format": "u32",
            },
        )
        assert result["ok"] is True
        assert result["length"] == 8

    async def test_invalid_hex(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_write(
            state,
            {
                "session_id": sid,
                "address": 0x2000_0000,
                "data": "not-hex",
            },
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"


class TestHandlersIntrospection:
    async def test_list_sessions(self):
        from dbgprobe_mcp_server.handlers_introspection import handle_connections_list

        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_connections_list(state, {})
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["sessions"][0]["session_id"] == sid
        assert result["sessions"][0]["backend"] == "mock"
