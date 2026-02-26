"""Tests for dbgprobe_mcp_server.handlers_svd — no hardware required."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from dbgprobe_mcp_server.backend import Backend, ConnectConfig, ProbeInfo
from dbgprobe_mcp_server.handlers_svd import (
    handle_svd_attach,
    handle_svd_describe,
    handle_svd_info,
    handle_svd_list_fields,
    handle_svd_list_peripherals,
    handle_svd_list_registers,
    handle_svd_read,
    handle_svd_set_field,
    handle_svd_update_fields,
    handle_svd_write,
)
from dbgprobe_mcp_server.state import DbgProbeSession, ProbeState
from dbgprobe_mcp_server.svd import parse_svd

FIXTURES = Path(__file__).parent / "fixtures"
MINIMAL_SVD = str(FIXTURES / "minimal.svd")


# ---------------------------------------------------------------------------
# Mock backend with controllable memory
# ---------------------------------------------------------------------------


class MockBackend(Backend):
    """Mock backend that stores memory as a dict of address → bytes."""

    name = "mock"

    def __init__(self):
        self._config = None
        self._memory: dict[int, int] = {}  # address → byte value

    def set_register(self, address: int, value: int, size_bytes: int = 4):
        """Pre-load a register value into mock memory."""
        if size_bytes == 4:
            data = struct.pack("<I", value)
        elif size_bytes == 2:
            data = struct.pack("<H", value)
        else:
            data = bytes([value & 0xFF])
        for i, b in enumerate(data):
            self._memory[address + i] = b

    def get_register(self, address: int, size_bytes: int = 4) -> int:
        """Read a register value from mock memory."""
        data = bytes(self._memory.get(address + i, 0) for i in range(size_bytes))
        if size_bytes == 4:
            return struct.unpack("<I", data)[0]
        elif size_bytes == 2:
            return struct.unpack("<H", data)[0]
        return data[0]

    async def list_probes(self):
        return [ProbeInfo(serial="MOCK001", description="Mock Probe", backend="mock")]

    async def connect(self, config):
        self._config = config
        return {}

    async def disconnect(self):
        pass

    async def reset(self, mode):
        return {}

    async def halt(self):
        return {}

    async def go(self):
        return {}

    async def flash(self, path, addr=None, verify=True, reset_after=True, config=None):
        return {}

    async def mem_read(self, address, length):
        return bytes(self._memory.get(address + i, 0) for i in range(length))

    async def mem_write(self, address, data):
        for i, b in enumerate(data):
            self._memory[address + i] = b
        return {"address": address, "length": len(data)}

    async def erase(self, config, start_addr=None, end_addr=None):
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(state: ProbeState) -> tuple[str, DbgProbeSession, MockBackend]:
    sid = state.generate_id()
    backend = MockBackend()
    backend._config = ConnectConfig(
        backend="mock", device=None, interface="SWD", speed_khz=4000, probe_serial=None
    )
    session = DbgProbeSession(connection_id=sid, backend=backend, config=backend._config)
    state.sessions[sid] = session
    return sid, session, backend


def _attach_svd(session: DbgProbeSession):
    """Attach the minimal SVD to a session."""
    session.svd = parse_svd(MINIMAL_SVD)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSvdAttach:
    async def test_success(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        result = await handle_svd_attach(state, {"session_id": sid, "path": MINIMAL_SVD})
        assert result["ok"] is True
        assert result["device_name"] == "TestDevice"
        assert result["peripheral_count"] == 2
        assert result["register_count"] > 0
        assert session.svd is not None

    async def test_file_not_found(self):
        state = ProbeState()
        sid, _, _ = _make_session(state)
        result = await handle_svd_attach(state, {"session_id": sid, "path": "/nonexistent.svd"})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"

    async def test_invalid_svd(self, tmp_path):
        state = ProbeState()
        sid, _, _ = _make_session(state)
        bad = tmp_path / "bad.svd"
        bad.write_text("not xml")
        result = await handle_svd_attach(state, {"session_id": sid, "path": str(bad)})
        assert result["ok"] is False
        assert result["error"]["code"] == "parse_error"

    async def test_bad_extension(self, tmp_path):
        state = ProbeState()
        sid, _, _ = _make_session(state)
        bad = tmp_path / "data.txt"
        bad.write_text("not an svd")
        result = await handle_svd_attach(state, {"session_id": sid, "path": str(bad)})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_path"

    async def test_replace_existing(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_attach(state, {"session_id": sid, "path": MINIMAL_SVD})
        assert result["ok"] is True

    async def test_unknown_session(self):
        state = ProbeState()
        with pytest.raises(KeyError, match="Unknown session_id"):
            await handle_svd_attach(state, {"session_id": "nope", "path": MINIMAL_SVD})


class TestSvdInfo:
    async def test_no_svd(self):
        state = ProbeState()
        sid, _, _ = _make_session(state)
        result = await handle_svd_info(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["svd"] is None

    async def test_with_svd(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_info(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["svd"]["device_name"] == "TestDevice"
        assert result["svd"]["peripheral_count"] == 2


class TestSvdRead:
    async def test_register_read(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        # Set GPIO.OUT at 0x50000504 to 0x03 (PIN0=High, PIN1=High)
        backend.set_register(0x5000_0504, 0x03)
        result = await handle_svd_read(state, {"session_id": sid, "target": "GPIO.OUT"})
        assert result["ok"] is True
        assert result["raw"] == 0x03
        assert result["fields"]["PIN0"]["value"] == 1
        assert result["fields"]["PIN0"]["enum"] == "High"
        assert result["fields"]["PIN1"]["value"] == 1
        assert result["fields"]["PIN1"]["enum"] == "High"

    async def test_field_read(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        # PIN_CNF[3] at 0x5000070C: PULL=3 (PullUp) => bits [2:3]=11 => 0x0C
        backend.set_register(0x5000_070C, 0x0C)
        result = await handle_svd_read(state, {"session_id": sid, "target": "GPIO.PIN_CNF[3].PULL"})
        assert result["ok"] is True
        assert result["value"] == 3
        assert result["enum"] == "PullUp"
        assert result["raw_register"] == 0x0C

    async def test_write_only_warning(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        backend.set_register(0x5000_0508, 0x00)
        result = await handle_svd_read(state, {"session_id": sid, "target": "GPIO.OUTSET"})
        assert result["ok"] is True
        assert "warning" in result

    async def test_no_svd(self):
        state = ProbeState()
        sid, _, _ = _make_session(state)
        result = await handle_svd_read(state, {"session_id": sid, "target": "GPIO.OUT"})
        assert result["ok"] is False

    async def test_unknown_field(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        backend.set_register(0x5000_0504, 0x00)
        result = await handle_svd_read(state, {"session_id": sid, "target": "GPIO.OUT.NOPE"})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"


class TestSvdWrite:
    async def test_success(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_write(state, {"session_id": sid, "register": "GPIO.OUT", "value": 0x01})
        assert result["ok"] is True
        assert result["value"] == 0x01
        assert backend.get_register(0x5000_0504) == 0x01

    async def test_hex_string_value(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_write(state, {"session_id": sid, "register": "GPIO.OUT", "value": "0xFF"})
        assert result["ok"] is True
        assert result["value"] == 0xFF

    async def test_read_only_register(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_write(state, {"session_id": sid, "register": "GPIO.IN", "value": 0x01})
        assert result["ok"] is False
        assert result["error"]["code"] == "read_only"

    async def test_field_target_rejected(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_write(state, {"session_id": sid, "register": "GPIO.OUT.PIN0", "value": 1})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"


class TestSvdSetField:
    async def test_success_with_enum(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        # Start with PULL=Disabled (0)
        backend.set_register(0x5000_0700, 0x00)
        result = await handle_svd_set_field(
            state, {"session_id": sid, "field": "GPIO.PIN_CNF[0].PULL", "value": "PullUp"}
        )
        assert result["ok"] is True
        assert result["old_value"] == 0
        assert result["new_value"] == 3
        assert result["old_enum"] == "Disabled"
        assert result["new_enum"] == "PullUp"
        # Verify memory: 3 << 2 = 0x0C
        assert backend.get_register(0x5000_0700) == 0x0C

    async def test_success_with_int(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        backend.set_register(0x5000_0700, 0x00)
        result = await handle_svd_set_field(
            state, {"session_id": sid, "field": "GPIO.PIN_CNF[0].PULL", "value": 1}
        )
        assert result["ok"] is True
        assert result["new_value"] == 1
        assert result["new_enum"] == "PullDown"

    async def test_preserves_other_fields(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        # DIR=1 (bit 0 set)
        backend.set_register(0x5000_0700, 0x01)
        result = await handle_svd_set_field(
            state, {"session_id": sid, "field": "GPIO.PIN_CNF[0].PULL", "value": "PullUp"}
        )
        assert result["ok"] is True
        # DIR should still be 1, PULL=3: 0x01 | 0x0C = 0x0D
        assert backend.get_register(0x5000_0700) == 0x0D

    async def test_register_target_rejected(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_set_field(state, {"session_id": sid, "field": "GPIO.OUT", "value": 1})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_read_only_register(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_set_field(state, {"session_id": sid, "field": "GPIO.IN.PIN0", "value": 1})
        assert result["ok"] is False
        assert result["error"]["code"] == "read_only"


class TestSvdUpdateFields:
    async def test_batch_update(self):
        state = ProbeState()
        sid, session, backend = _make_session(state)
        _attach_svd(session)
        backend.set_register(0x5000_0700, 0x00)
        result = await handle_svd_update_fields(
            state,
            {
                "session_id": sid,
                "register": "GPIO.PIN_CNF[0]",
                "fields": {"DIR": "Output", "PULL": "PullUp"},
            },
        )
        assert result["ok"] is True
        assert result["changes"]["DIR"]["new_value"] == 1
        assert result["changes"]["DIR"]["new_enum"] == "Output"
        assert result["changes"]["PULL"]["new_value"] == 3
        assert result["changes"]["PULL"]["new_enum"] == "PullUp"
        # DIR=1 (bit 0), PULL=3 (bits 2:3) => 0x0D
        assert backend.get_register(0x5000_0700) == 0x0D

    async def test_unknown_field(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_update_fields(
            state,
            {
                "session_id": sid,
                "register": "GPIO.PIN_CNF[0]",
                "fields": {"NOPE": 1},
            },
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"

    async def test_empty_fields(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_update_fields(
            state,
            {"session_id": sid, "register": "GPIO.PIN_CNF[0]", "fields": {}},
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_read_only_register(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_update_fields(
            state,
            {"session_id": sid, "register": "GPIO.IN", "fields": {"PIN0": 1}},
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "read_only"


class TestSvdListPeripherals:
    async def test_success(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_list_peripherals(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["count"] == 2
        names = {p["name"] for p in result["peripherals"]}
        assert "GPIO" in names
        assert "TIMER0" in names

    async def test_no_svd(self):
        state = ProbeState()
        sid, _, _ = _make_session(state)
        result = await handle_svd_list_peripherals(state, {"session_id": sid})
        assert result["ok"] is False


class TestSvdListRegisters:
    async def test_success(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_list_registers(state, {"session_id": sid, "peripheral": "GPIO"})
        assert result["ok"] is True
        assert result["count"] >= 7  # OUT, IN, DIR, OUTSET, PIN_CNF[0..3]
        reg_names = {r["name"] for r in result["registers"]}
        assert "OUT" in reg_names
        assert "PIN_CNF[3]" in reg_names

    async def test_unknown_peripheral(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_list_registers(state, {"session_id": sid, "peripheral": "NOPE"})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"


class TestSvdListFields:
    async def test_success(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_list_fields(
            state, {"session_id": sid, "peripheral": "GPIO", "register": "PIN_CNF[0]"}
        )
        assert result["ok"] is True
        assert result["count"] == 5
        field_names = {f["name"] for f in result["fields"]}
        assert "DIR" in field_names
        assert "PULL" in field_names

    async def test_includes_enums(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_list_fields(
            state, {"session_id": sid, "peripheral": "GPIO", "register": "PIN_CNF[0]"}
        )
        pull_field = next(f for f in result["fields"] if f["name"] == "PULL")
        assert pull_field["enum_values"]["PullUp"] == 3


class TestSvdDescribe:
    async def test_peripheral(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_describe(state, {"session_id": sid, "target": "GPIO"})
        assert result["ok"] is True
        assert result["type"] == "peripheral"
        assert result["name"] == "GPIO"
        assert result["base_address"] == 0x5000_0000

    async def test_register(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_describe(state, {"session_id": sid, "target": "GPIO.OUT"})
        assert result["ok"] is True
        assert result["type"] == "register"
        assert result["address"] == 0x5000_0504
        assert result["access"] == "read-write"

    async def test_field(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_describe(state, {"session_id": sid, "target": "GPIO.PIN_CNF[0].PULL"})
        assert result["ok"] is True
        assert result["type"] == "field"
        assert result["bit_width"] == 2
        assert "PullUp" in result["enum_values"]

    async def test_unknown_peripheral(self):
        state = ProbeState()
        sid, session, _ = _make_session(state)
        _attach_svd(session)
        result = await handle_svd_describe(state, {"session_id": sid, "target": "NOPE"})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"
