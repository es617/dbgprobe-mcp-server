"""Unit tests for dbgprobe_mcp_server.state."""

from __future__ import annotations

import pytest

from dbgprobe_mcp_server.state import Breakpoint, DbgProbeSession, ProbeState


class TestProbeState:
    def test_generate_id(self):
        state = ProbeState()
        cid = state.generate_id()
        assert cid.startswith("p")
        assert len(cid) == 9  # "p" + 8 hex chars

    def test_generate_id_unique(self):
        state = ProbeState()
        ids = {state.generate_id() for _ in range(100)}
        assert len(ids) == 100

    async def test_shutdown_clears_sessions(self):
        state = ProbeState()
        state.sessions["p1"] = DbgProbeSession(connection_id="p1")
        await state.shutdown()
        assert len(state.sessions) == 0

    def test_get_session(self):
        state = ProbeState()
        session = DbgProbeSession(connection_id="p1")
        state.sessions["p1"] = session
        assert state.get_session("p1") is session

    def test_get_session_missing(self):
        state = ProbeState()
        with pytest.raises(KeyError, match="Unknown session_id"):
            state.get_session("nope")


class TestDbgProbeSession:
    def test_backend_name_default(self):
        session = DbgProbeSession(connection_id="p1")
        assert session.backend_name == "unknown"

    def test_spec_default_none(self):
        session = DbgProbeSession(connection_id="p1")
        assert session.spec is None

    def test_created_at(self):
        session = DbgProbeSession(connection_id="p1")
        assert session.created_at > 0

    def test_breakpoints_default_empty(self):
        session = DbgProbeSession(connection_id="p1")
        assert session.breakpoints == {}

    def test_breakpoints_add_and_remove(self):
        session = DbgProbeSession(connection_id="p1")
        bp = Breakpoint(address=0x0800_0100, bp_type="hw")
        session.breakpoints[bp.address] = bp
        assert 0x0800_0100 in session.breakpoints
        assert session.breakpoints[0x0800_0100].bp_type == "hw"
        del session.breakpoints[0x0800_0100]
        assert session.breakpoints == {}


class TestBreakpoint:
    def test_fields(self):
        bp = Breakpoint(address=0x0800_0000, bp_type="sw")
        assert bp.address == 0x0800_0000
        assert bp.bp_type == "sw"
