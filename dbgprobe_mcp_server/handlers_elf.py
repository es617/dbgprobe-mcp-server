"""ELF tool definitions and handlers — attach, info, lookup, symbols."""

from __future__ import annotations

from typing import Any

from mcp.types import Tool

from dbgprobe_mcp_server.elf import ElfData, parse_elf, resolve_address, resolve_symbol, search_symbols
from dbgprobe_mcp_server.helpers import _err, _ok, _parse_addr, _validate_file_path
from dbgprobe_mcp_server.state import ProbeState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="dbgprobe.elf.attach",
        description=(
            "Parse an ELF file and attach it to a session. Enables symbol-based "
            "breakpoints (by function name), address-to-symbol resolution in "
            "status/step/halt responses, and symbol search. Re-attaching replaces "
            "the previous ELF."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Path to the ELF file.",
                },
            },
            "required": ["session_id", "path"],
        },
    ),
    Tool(
        name="dbgprobe.elf.info",
        description=(
            "Get ELF metadata for a session: file path, symbol count, entry point, "
            "sections. Returns null if no ELF is attached."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.elf.lookup",
        description=(
            "Bidirectional symbol lookup. Provide 'symbol' for name-to-address, "
            "or 'address' for address-to-name+offset. Exactly one is required."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to look up (returns address).",
                },
                "address": {
                    "type": ["integer", "string"],
                    "description": 'Address to resolve (e.g. 0x08000100 or "0x8000100").',
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.elf.symbols",
        description=(
            "Search or list ELF symbols. Optional substring filter, optional type "
            "filter (FUNC, OBJECT, NOTYPE, etc.), default limit 50."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "filter": {
                    "type": "string",
                    "description": "Case-insensitive substring filter on symbol name.",
                },
                "type": {
                    "type": "string",
                    "description": "Symbol type filter (e.g. FUNC, OBJECT).",
                },
                "limit": {
                    "type": ["integer", "string"],
                    "description": "Max results (default 50).",
                },
            },
            "required": ["session_id"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _elf_summary(elf: ElfData) -> dict[str, Any]:
    """Build a summary dict for an attached ELF."""
    total_symbols = sum(len(v) for v in elf.symbols.values())
    func_count = len(elf._sorted_functions)
    return {
        "path": elf.path,
        "entry_point": elf.entry_point,
        "symbol_count": total_symbols,
        "function_count": func_count,
        "sections": elf.sections,
    }


async def handle_elf_attach(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    try:
        path = _validate_file_path(args["path"], {".elf", ".axf", ".out"})
    except FileNotFoundError as exc:
        return _err("not_found", str(exc))
    except ValueError as exc:
        return _err("invalid_path", str(exc))

    try:
        elf = parse_elf(str(path))
    except FileNotFoundError as exc:
        return _err("not_found", str(exc))
    except Exception as exc:
        return _err("parse_error", f"Failed to parse ELF: {exc}")

    session.elf = elf
    return _ok(session_id=args["session_id"], **_elf_summary(elf))


async def handle_elf_info(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    if session.elf is None:
        return _ok(elf=None)
    return _ok(elf=_elf_summary(session.elf))


async def handle_elf_lookup(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    if session.elf is None:
        return _err("no_elf", "No ELF attached. Use dbgprobe.elf.attach first.")

    has_symbol = "symbol" in args
    has_address = "address" in args

    if has_symbol == has_address:
        return _err("invalid_params", "Provide exactly one of 'symbol' or 'address'.")

    elf: ElfData = session.elf

    if has_symbol:
        sym = resolve_symbol(elf, args["symbol"])
        if sym is None:
            return _err("not_found", f"Symbol {args['symbol']!r} not found.")
        return _ok(
            symbol=sym.name,
            address=sym.address,
            size=sym.size,
            type=sym.sym_type,
        )
    else:
        addr = _parse_addr(args["address"])
        result = resolve_address(elf, addr)
        if result is None:
            return _err("not_found", f"No symbol found for address 0x{addr:08x}.")
        name, offset = result
        return _ok(
            address=addr,
            symbol=name,
            symbol_offset=offset,
        )


async def handle_elf_symbols(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    if session.elf is None:
        return _err("no_elf", "No ELF attached. Use dbgprobe.elf.attach first.")

    elf: ElfData = session.elf
    query = args.get("filter", "")
    sym_type = args.get("type")
    limit = int(args.get("limit", 50))

    if query:
        results = search_symbols(elf, query, sym_type=sym_type, limit=limit)
    else:
        # No filter: list all (respecting type filter and limit)
        results = []
        for entries in elf.symbols.values():
            for entry in entries:
                if sym_type is not None and entry.sym_type != sym_type:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

    symbols_out = [
        {"name": s.name, "address": s.address, "size": s.size, "type": s.sym_type} for s in results
    ]
    return _ok(symbols=symbols_out, count=len(symbols_out))


HANDLERS: dict[str, Any] = {
    "dbgprobe.elf.attach": handle_elf_attach,
    "dbgprobe.elf.info": handle_elf_info,
    "dbgprobe.elf.lookup": handle_elf_lookup,
    "dbgprobe.elf.symbols": handle_elf_symbols,
}
