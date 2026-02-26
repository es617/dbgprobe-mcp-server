"""Tests for dbgprobe_mcp_server.handlers_probe — no hardware required."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dbgprobe_mcp_server.backend import Backend, ConnectConfig, DeviceSecuredError, ProbeInfo
from dbgprobe_mcp_server.handlers_probe import (
    handle_breakpoint_clear,
    handle_breakpoint_list,
    handle_breakpoint_set,
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
    handle_status,
    handle_step,
)
from dbgprobe_mcp_server.state import Breakpoint, DbgProbeSession, ProbeState

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
        return {"mode": mode, "state": "halted"}

    async def halt(self):
        return {}

    async def go(self):
        return {}

    async def flash(self, path, addr=None, verify=True, reset_after=True):
        return {"file": path, "verified": verify, "reset": reset_after, "breakpoints_cleared": True}

    async def mem_read(self, address, length):
        return bytes(range(length % 256))

    async def mem_write(self, address, data):
        return {"address": address, "length": len(data)}

    async def erase(self, config, start_addr=None, end_addr=None):
        return {"resolved_paths": {"mock_exe": "/mock/path"}}

    async def erase_via_gdb(self, start_addr=None, end_addr=None):
        return {"monitor_output": "Erase done"}

    async def step(self):
        return {"pc": 0x0800_0100, "reason": "step", "signal": 5}

    async def status(self):
        return {"state": "halted", "pc": 0x0800_0200, "reason": "halted", "signal": 5}

    async def set_breakpoint(self, address, bp_type="sw"):
        return {"address": address, "bp_type": bp_type}

    async def clear_breakpoint(self, address):
        return {"address": address}

    async def clear_all_breakpoints(self):
        pass

    async def list_breakpoints(self):
        return []


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
    # -- Session-less erase (JLinkExe) --

    async def test_sessionless_success(self):
        state = ProbeState()
        with _patch_registry(), _patch_defaults():
            result = await handle_erase(state, {"backend": "mock"})
        assert result["ok"] is True
        assert result["erased"] is True
        assert result["config"]["backend"] == "mock"
        assert "start_addr" not in result

    async def test_sessionless_range_erase(self):
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

    # -- Session-based erase (GDB monitor) --

    async def test_session_erase_success(self):
        state = ProbeState()
        sid, _session = _make_session(state)
        result = await handle_erase(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["erased"] is True
        assert result["session_id"] == sid
        assert "config" not in result  # session-based doesn't include config

    async def test_session_erase_range(self):
        state = ProbeState()
        sid, _session = _make_session(state)
        result = await handle_erase(state, {"session_id": sid, "start_addr": 0x40000, "end_addr": 0x80000})
        assert result["ok"] is True
        assert result["erased"] is True
        assert result["start_addr"] == 0x40000
        assert result["end_addr"] == 0x80000

    async def test_session_erase_unknown_session(self):
        state = ProbeState()
        with pytest.raises(KeyError, match="Unknown connection_id"):
            await handle_erase(state, {"session_id": "nope"})


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

    async def test_restores_breakpoints(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.breakpoints[0x0800_0100] = Breakpoint(address=0x0800_0100, bp_type="hw")
        session.breakpoints[0x0800_0200] = Breakpoint(address=0x0800_0200, bp_type="sw")
        result = await handle_reset(state, {"session_id": sid, "mode": "hard"})
        assert result["ok"] is True
        assert result["breakpoints_restored"] == 2


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

    async def test_removes_breakpoint_at_pc_before_continue(self):
        """Go removes breakpoint at current PC, continues, re-inserts."""
        state = ProbeState()
        sid, session = _make_session(state)
        # Set up: breakpoint at the address status() returns as PC (0x0800_0200)
        session.breakpoints[0x0800_0200] = Breakpoint(address=0x0800_0200, bp_type="sw")
        calls = []
        original_clear = session.backend.clear_breakpoint
        original_set = session.backend.set_breakpoint
        original_go = session.backend.go

        async def track_clear(address):
            calls.append(("clear", address))
            return await original_clear(address)

        async def track_set(address, bp_type="sw"):
            calls.append(("set", address, bp_type))
            return await original_set(address, bp_type)

        async def track_go():
            calls.append(("go",))
            return await original_go()

        session.backend.clear_breakpoint = track_clear
        session.backend.set_breakpoint = track_set
        session.backend.go = track_go

        result = await handle_go(state, {"session_id": sid})
        assert result["ok"] is True
        assert calls == [
            ("clear", 0x0800_0200),
            ("go",),
            ("set", 0x0800_0200, "sw"),
        ]

    async def test_no_dance_when_pc_not_at_breakpoint(self):
        """Go sends plain continue when PC is not at a breakpoint."""
        state = ProbeState()
        sid, session = _make_session(state)
        # Breakpoint at different address than PC (0x0800_0200)
        session.breakpoints[0x0800_0100] = Breakpoint(address=0x0800_0100, bp_type="sw")
        calls = []
        original_go = session.backend.go

        async def track_go():
            calls.append("go")
            return await original_go()

        session.backend.go = track_go
        result = await handle_go(state, {"session_id": sid})
        assert result["ok"] is True
        assert calls == ["go"]


class TestFlash:
    async def test_success(self, tmp_path):
        state = ProbeState()
        sid, _ = _make_session(state)
        fw = tmp_path / "test.hex"
        fw.write_text(":00000001FF\n")

        result = await handle_flash(state, {"session_id": sid, "path": str(fw)})
        assert result["ok"] is True
        assert result["verified"] is True
        assert result["session_id"] == sid

    async def test_sessionless_success(self, tmp_path):
        state = ProbeState()
        fw = tmp_path / "test.hex"
        fw.write_text(":00000001FF\n")

        with _patch_registry(), _patch_defaults():
            result = await handle_flash(state, {"backend": "mock", "path": str(fw)})
        assert result["ok"] is True
        assert result["verified"] is True
        assert result["config"]["backend"] == "mock"
        assert "session_id" not in result

    async def test_sessionless_unknown_backend(self, tmp_path):
        state = ProbeState()
        fw = tmp_path / "test.hex"
        fw.write_text(":00000001FF\n")

        with _patch_registry():
            result = await handle_flash(state, {"backend": "nope", "path": str(fw)})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_backend"


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


class TestStep:
    async def test_success(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_step(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["pc"] == 0x0800_0100
        assert result["reason"] == "step"

    async def test_not_supported(self):
        """Backend without step() returns not_supported error."""
        state = ProbeState()
        sid, session = _make_session(state)

        # Replace with a backend that doesn't override step()
        class NoStepBackend(MockBackend):
            async def step(self):
                raise NotImplementedError("step() not supported by this backend")

        session.backend = NoStepBackend()
        result = await handle_step(state, {"session_id": sid})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_supported"


class TestStatus:
    async def test_success(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_status(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["state"] == "halted"

    async def test_not_supported(self):
        state = ProbeState()
        sid, session = _make_session(state)

        class NoStatusBackend(MockBackend):
            async def status(self):
                raise NotImplementedError("status() not supported by this backend")

        session.backend = NoStatusBackend()
        result = await handle_status(state, {"session_id": sid})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_supported"


class TestBreakpointSet:
    async def test_success(self):
        state = ProbeState()
        sid, session = _make_session(state)
        result = await handle_breakpoint_set(state, {"session_id": sid, "address": 0x0800_0100})
        assert result["ok"] is True
        assert result["address"] == 0x0800_0100
        assert result["bp_type"] == "sw"
        assert 0x0800_0100 in session.breakpoints
        assert session.breakpoints[0x0800_0100].bp_type == "sw"

    async def test_software_breakpoint(self):
        state = ProbeState()
        sid, session = _make_session(state)
        result = await handle_breakpoint_set(
            state, {"session_id": sid, "address": 0x2000_0000, "bp_type": "sw"}
        )
        assert result["ok"] is True
        assert result["bp_type"] == "sw"
        assert session.breakpoints[0x2000_0000].bp_type == "sw"

    async def test_invalid_type(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_breakpoint_set(
            state, {"session_id": sid, "address": 0x0800_0100, "bp_type": "bad"}
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"


class TestBreakpointClear:
    async def test_success(self):
        state = ProbeState()
        sid, session = _make_session(state)
        # First set a breakpoint
        session.breakpoints[0x0800_0100] = Breakpoint(address=0x0800_0100, bp_type="hw")
        result = await handle_breakpoint_clear(state, {"session_id": sid, "address": 0x0800_0100})
        assert result["ok"] is True
        assert 0x0800_0100 not in session.breakpoints

    async def test_not_found(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_breakpoint_clear(state, {"session_id": sid, "address": 0xDEAD_BEEF})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"


class TestBreakpointList:
    async def test_empty(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_breakpoint_list(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["breakpoints"] == []
        assert result["count"] == 0

    async def test_with_breakpoints(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.breakpoints[0x0800_0100] = Breakpoint(address=0x0800_0100, bp_type="hw")
        session.breakpoints[0x0800_0200] = Breakpoint(address=0x0800_0200, bp_type="sw")
        result = await handle_breakpoint_list(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["count"] == 2
        addrs = {bp["address"] for bp in result["breakpoints"]}
        assert addrs == {0x0800_0100, 0x0800_0200}


class TestFlashClearsBreakpoints:
    async def test_flash_clears_breakpoints(self, tmp_path):
        state = ProbeState()
        sid, session = _make_session(state)
        fw = tmp_path / "test.hex"
        fw.write_text(":00000001FF\n")
        # Set a breakpoint
        session.breakpoints[0x0800_0100] = Breakpoint(address=0x0800_0100, bp_type="hw")
        assert len(session.breakpoints) == 1

        result = await handle_flash(state, {"session_id": sid, "path": str(fw)})
        assert result["ok"] is True
        assert session.breakpoints == {}


class TestPcEnrichment:
    """Test that halt/step/status add symbol info when ELF is attached."""

    def _attach_mock_elf(self, session):
        from dbgprobe_mcp_server.elf import ElfData, SymbolInfo

        main = SymbolInfo(name="main", address=0x0800_0200, size=64, sym_type="FUNC")
        step_func = SymbolInfo(name="step_target", address=0x0800_0100, size=32, sym_type="FUNC")
        funcs = sorted([main, step_func], key=lambda s: s.address)
        session.elf = ElfData(
            path="/mock/fw.elf",
            entry_point=0x0800_0000,
            symbols={"main": [main], "step_target": [step_func]},
            _sorted_functions=funcs,
            _func_addrs=[s.address for s in funcs],
        )

    async def test_status_enriched(self):
        state = ProbeState()
        sid, session = _make_session(state)
        self._attach_mock_elf(session)
        # MockBackend.status() returns pc=0x0800_0200 which is main+0
        result = await handle_status(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["symbol"] == "main"
        assert result["symbol_offset"] == 0

    async def test_step_enriched(self):
        state = ProbeState()
        sid, session = _make_session(state)
        self._attach_mock_elf(session)
        # MockBackend.step() returns pc=0x0800_0100 which is step_target+0
        result = await handle_step(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["symbol"] == "step_target"
        assert result["symbol_offset"] == 0

    async def test_halt_enriched(self):
        state = ProbeState()
        sid, session = _make_session(state)
        self._attach_mock_elf(session)
        # MockBackend.halt() returns {} — no pc, so no enrichment
        result = await handle_halt(state, {"session_id": sid})
        assert result["ok"] is True
        assert "symbol" not in result

    async def test_status_no_elf(self):
        """Without ELF, no symbol fields appear."""
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_status(state, {"session_id": sid})
        assert result["ok"] is True
        assert "symbol" not in result


class TestBreakpointSetSymbol:
    """Test breakpoint.set with symbol parameter."""

    def _attach_mock_elf(self, session):
        from dbgprobe_mcp_server.elf import ElfData, SymbolInfo

        main = SymbolInfo(name="main", address=0x0800_0200, size=64, sym_type="FUNC")
        session.elf = ElfData(
            path="/mock/fw.elf",
            entry_point=0x0800_0000,
            symbols={"main": [main]},
            _sorted_functions=[main],
            _func_addrs=[main.address],
        )

    async def test_symbol_resolved(self):
        state = ProbeState()
        sid, session = _make_session(state)
        self._attach_mock_elf(session)
        result = await handle_breakpoint_set(state, {"session_id": sid, "symbol": "main"})
        assert result["ok"] is True
        assert result["address"] == 0x0800_0200
        assert result["symbol"] == "main"
        assert 0x0800_0200 in session.breakpoints

    async def test_symbol_not_found(self):
        state = ProbeState()
        sid, session = _make_session(state)
        self._attach_mock_elf(session)
        result = await handle_breakpoint_set(state, {"session_id": sid, "symbol": "nonexistent"})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"

    async def test_symbol_no_elf(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_breakpoint_set(state, {"session_id": sid, "symbol": "main"})
        assert result["ok"] is False
        assert result["error"]["code"] == "no_elf"

    async def test_both_symbol_and_address_error(self):
        state = ProbeState()
        sid, session = _make_session(state)
        self._attach_mock_elf(session)
        result = await handle_breakpoint_set(
            state, {"session_id": sid, "symbol": "main", "address": 0x0800_0200}
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_neither_symbol_nor_address_error(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_breakpoint_set(state, {"session_id": sid})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"


class TestFlashElfHandling:
    """Test flash ELF auto-reload and sibling hint."""

    async def test_flash_elf_hint(self, tmp_path):
        """Flash finds sibling .elf file and includes hint."""
        state = ProbeState()
        sid, _ = _make_session(state)
        fw = tmp_path / "firmware.hex"
        fw.write_text(":00000001FF\n")
        elf = tmp_path / "firmware.elf"
        elf.write_bytes(b"\x7fELF")  # just needs to exist for find_sibling_elf

        result = await handle_flash(state, {"session_id": sid, "path": str(fw)})
        assert result["ok"] is True
        assert "elf_hint" in result
        assert result["elf_hint"].endswith(".elf")

    async def test_flash_no_elf_hint_when_no_sibling(self, tmp_path):
        state = ProbeState()
        sid, _ = _make_session(state)
        fw = tmp_path / "firmware.hex"
        fw.write_text(":00000001FF\n")

        result = await handle_flash(state, {"session_id": sid, "path": str(fw)})
        assert result["ok"] is True
        assert "elf_hint" not in result

    async def test_flash_elf_auto_reload(self, tmp_path):
        """If ELF is attached, flash re-parses from same path."""
        from pathlib import Path as P

        from dbgprobe_mcp_server.elf import parse_elf

        state = ProbeState()
        sid, session = _make_session(state)

        # Use the real minimal.elf fixture
        elf_path = str(P(__file__).parent / "fixtures" / "minimal.elf")
        session.elf = parse_elf(elf_path)

        fw = tmp_path / "firmware.hex"
        fw.write_text(":00000001FF\n")

        result = await handle_flash(state, {"session_id": sid, "path": str(fw)})
        assert result["ok"] is True
        assert result["elf_reloaded"] is True
        assert result["elf_path"] == elf_path
        # ELF should still be attached
        assert session.elf is not None

    async def test_flash_elf_detach_on_missing(self, tmp_path):
        """If ELF file is gone after flash, detach it."""
        from dbgprobe_mcp_server.elf import ElfData, SymbolInfo

        state = ProbeState()
        sid, session = _make_session(state)

        # Attach an ELF pointing to a path that doesn't exist
        main = SymbolInfo(name="main", address=0x0800_0000, size=10, sym_type="FUNC")
        session.elf = ElfData(
            path="/nonexistent/deleted.elf",
            entry_point=0x0800_0000,
            symbols={"main": [main]},
            _sorted_functions=[main],
            _func_addrs=[main.address],
        )

        fw = tmp_path / "firmware.hex"
        fw.write_text(":00000001FF\n")

        result = await handle_flash(state, {"session_id": sid, "path": str(fw)})
        assert result["ok"] is True
        assert result["elf_detached"] is True
        assert session.elf is None


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
        assert "elf" not in result["sessions"][0]

    async def test_list_sessions_with_elf(self):
        from dbgprobe_mcp_server.elf import ElfData, SymbolInfo
        from dbgprobe_mcp_server.handlers_introspection import handle_connections_list

        state = ProbeState()
        sid, session = _make_session(state)
        main = SymbolInfo(name="main", address=0x0800_0000, size=10, sym_type="FUNC")
        session.elf = ElfData(
            path="/mock/fw.elf",
            entry_point=0x0800_0000,
            symbols={"main": [main]},
            _sorted_functions=[main],
            _func_addrs=[main.address],
        )
        result = await handle_connections_list(state, {})
        assert result["ok"] is True
        elf_info = result["sessions"][0]["elf"]
        assert elf_info["path"] == "/mock/fw.elf"
        assert elf_info["symbol_count"] == 1
        assert elf_info["function_count"] == 1


class TestMemReadSvdEnrichment:
    """Test that mem.read adds SVD decode when address matches a register."""

    async def test_enriched_when_svd_attached(self):
        from pathlib import Path as P

        from dbgprobe_mcp_server.svd import parse_svd

        state = ProbeState()
        sid, session = _make_session(state)
        svd_path = str(P(__file__).parent / "fixtures" / "minimal.svd")
        session.svd = parse_svd(svd_path)

        # GPIO.OUT is at 0x50000504, 4 bytes
        # MockBackend.mem_read returns bytes(range(length % 256)) => [0,1,2,3]
        # which unpacks to 0x03020100
        result = await handle_mem_read(
            state,
            {"session_id": sid, "address": 0x5000_0504, "length": 4},
        )
        assert result["ok"] is True
        assert "svd" in result
        assert result["svd"]["peripheral"] == "GPIO"
        assert result["svd"]["register"] == "OUT"
        assert "fields" in result["svd"]
        assert "PIN0" in result["svd"]["fields"]

    async def test_no_enrichment_without_svd(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_mem_read(
            state,
            {"session_id": sid, "address": 0x5000_0504, "length": 4},
        )
        assert result["ok"] is True
        assert "svd" not in result

    async def test_no_enrichment_wrong_size(self):
        from pathlib import Path as P

        from dbgprobe_mcp_server.svd import parse_svd

        state = ProbeState()
        sid, session = _make_session(state)
        svd_path = str(P(__file__).parent / "fixtures" / "minimal.svd")
        session.svd = parse_svd(svd_path)

        # Read 8 bytes at a 4-byte register address — no enrichment
        result = await handle_mem_read(
            state,
            {"session_id": sid, "address": 0x5000_0504, "length": 8},
        )
        assert result["ok"] is True
        assert "svd" not in result

    async def test_no_enrichment_unknown_address(self):
        from pathlib import Path as P

        from dbgprobe_mcp_server.svd import parse_svd

        state = ProbeState()
        sid, session = _make_session(state)
        svd_path = str(P(__file__).parent / "fixtures" / "minimal.svd")
        session.svd = parse_svd(svd_path)

        # Read at an address that doesn't match any register
        result = await handle_mem_read(
            state,
            {"session_id": sid, "address": 0x2000_0000, "length": 4},
        )
        assert result["ok"] is True
        assert "svd" not in result
