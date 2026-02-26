"""Tests for dbgprobe_mcp_server.handlers_elf — no hardware required."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbgprobe_mcp_server.elf import ElfData, SymbolInfo
from dbgprobe_mcp_server.handlers_elf import (
    handle_elf_attach,
    handle_elf_info,
    handle_elf_lookup,
    handle_elf_symbols,
)
from dbgprobe_mcp_server.state import DbgProbeSession, ProbeState

FIXTURES = Path(__file__).parent / "fixtures"
MINIMAL_ELF = str(FIXTURES / "minimal.elf")


def _make_session(state: ProbeState) -> tuple[str, DbgProbeSession]:
    sid = state.generate_id()
    session = DbgProbeSession(connection_id=sid)
    state.sessions[sid] = session
    return sid, session


def _mock_elf() -> ElfData:
    """Build a mock ElfData without parsing a file."""
    main = SymbolInfo(name="main", address=0x0800_00ED, size=42, sym_type="FUNC")
    loop = SymbolInfo(name="loop", address=0x0800_0117, size=16, sym_type="FUNC")
    some_var = SymbolInfo(name="some_var", address=0x2000_0000, size=4, sym_type="OBJECT")
    funcs = sorted([main, loop], key=lambda s: s.address)
    return ElfData(
        path="/mock/firmware.elf",
        entry_point=0x0800_0000,
        symbols={
            "main": [main],
            "loop": [loop],
            "some_var": [some_var],
        },
        _sorted_functions=funcs,
        _func_addrs=[s.address for s in funcs],
        sections=[{"name": ".text", "address": 0x080000ED, "size": 64, "type": "SHT_PROGBITS"}],
    )


class TestElfAttach:
    async def test_success(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_elf_attach(state, {"session_id": sid, "path": MINIMAL_ELF})
        assert result["ok"] is True
        assert result["symbol_count"] == 4
        assert result["function_count"] == 3
        assert result["entry_point"] == 0x0800_0000
        assert state.sessions[sid].elf is not None

    async def test_file_not_found(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_elf_attach(state, {"session_id": sid, "path": "/nonexistent.elf"})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"

    async def test_invalid_elf(self, tmp_path):
        state = ProbeState()
        sid, _ = _make_session(state)
        bad = tmp_path / "bad.elf"
        bad.write_text("not an elf")
        result = await handle_elf_attach(state, {"session_id": sid, "path": str(bad)})
        assert result["ok"] is False
        assert result["error"]["code"] == "parse_error"

    async def test_bad_extension(self, tmp_path):
        state = ProbeState()
        sid, _ = _make_session(state)
        bad = tmp_path / "data.txt"
        bad.write_text("not an elf")
        result = await handle_elf_attach(state, {"session_id": sid, "path": str(bad)})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_path"

    async def test_replace_existing(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_attach(state, {"session_id": sid, "path": MINIMAL_ELF})
        assert result["ok"] is True
        # ELF should be replaced
        assert session.elf.path == str(Path(MINIMAL_ELF).resolve())

    async def test_unknown_session(self):
        state = ProbeState()
        with pytest.raises(KeyError, match="Unknown session_id"):
            await handle_elf_attach(state, {"session_id": "nope", "path": MINIMAL_ELF})


class TestElfInfo:
    async def test_no_elf(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_elf_info(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["elf"] is None

    async def test_with_elf(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_info(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["elf"]["path"] == "/mock/firmware.elf"
        assert result["elf"]["symbol_count"] == 3
        assert result["elf"]["function_count"] == 2


class TestElfLookup:
    async def test_symbol_to_address(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_lookup(state, {"session_id": sid, "symbol": "main"})
        assert result["ok"] is True
        assert result["address"] == 0x0800_00ED
        assert result["symbol"] == "main"

    async def test_address_to_symbol(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_lookup(state, {"session_id": sid, "address": 0x0800_00ED + 6})
        assert result["ok"] is True
        assert result["symbol"] == "main"
        assert result["symbol_offset"] == 6

    async def test_symbol_not_found(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_lookup(state, {"session_id": sid, "symbol": "nonexistent"})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"

    async def test_address_not_found(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_lookup(state, {"session_id": sid, "address": 0xFFFF_FFFF})
        assert result["ok"] is False
        assert result["error"]["code"] == "not_found"

    async def test_both_params_error(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_lookup(state, {"session_id": sid, "symbol": "main", "address": 0x0800_00ED})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_neither_param_error(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_lookup(state, {"session_id": sid})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_params"

    async def test_no_elf_attached(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_elf_lookup(state, {"session_id": sid, "symbol": "main"})
        assert result["ok"] is False
        assert result["error"]["code"] == "no_elf"


class TestElfSymbols:
    async def test_list_all(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_symbols(state, {"session_id": sid})
        assert result["ok"] is True
        assert result["count"] == 3

    async def test_filter(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_symbols(state, {"session_id": sid, "filter": "main"})
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["symbols"][0]["name"] == "main"

    async def test_type_filter(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_symbols(state, {"session_id": sid, "type": "OBJECT"})
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["symbols"][0]["name"] == "some_var"

    async def test_limit(self):
        state = ProbeState()
        sid, session = _make_session(state)
        session.elf = _mock_elf()
        result = await handle_elf_symbols(state, {"session_id": sid, "limit": 1})
        assert result["ok"] is True
        assert result["count"] == 1

    async def test_no_elf(self):
        state = ProbeState()
        sid, _ = _make_session(state)
        result = await handle_elf_symbols(state, {"session_id": sid})
        assert result["ok"] is False
        assert result["error"]["code"] == "no_elf"
