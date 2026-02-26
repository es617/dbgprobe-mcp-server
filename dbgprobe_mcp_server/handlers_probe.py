"""Debug probe tool definitions and handlers."""

from __future__ import annotations

import base64
import logging
import struct
from typing import Any

from mcp.types import Tool

from dbgprobe_mcp_server.backend import ConnectConfig, DeviceSecuredError, registry
from dbgprobe_mcp_server.elf import find_sibling_elf, parse_elf, resolve_address, resolve_symbol
from dbgprobe_mcp_server.helpers import (
    DBGPROBE_BACKEND,
    DBGPROBE_INTERFACE,
    DBGPROBE_JLINK_DEVICE,
    DBGPROBE_SPEED_KHZ,
    _err,
    _ok,
    _parse_addr,
)
from dbgprobe_mcp_server.state import Breakpoint, DbgProbeSession, ProbeState

logger = logging.getLogger("dbgprobe_mcp_server")

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="dbgprobe.list_probes",
        description=(
            "List attached debug probes. Returns vendor/backend-specific info "
            "(serial number, description). For J-Link, enumerates via JLinkExe."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "backend": {
                    "type": "string",
                    "description": "Backend to query (default from DBGPROBE_BACKEND env var).",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="dbgprobe.connect",
        description=(
            "Establish a debug probe session. Returns a session_id and the "
            "resolved configuration (backend, executable paths, defaults applied). "
            "The target is halted after connecting — this is inherent to the debug "
            "probe connection. Use dbgprobe.go to resume execution if needed. "
            "For symbol-aware debugging, attach an ELF file with dbgprobe.elf.attach "
            "after connecting — this enables breakpoints by function name and "
            "address-to-symbol resolution in status/step/halt responses. "
            "For register-level peripheral access, attach an SVD file with "
            "dbgprobe.svd.attach."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "backend": {
                    "type": "string",
                    "description": "Backend to use (default from DBGPROBE_BACKEND env var).",
                },
                "probe_id": {
                    "type": "string",
                    "description": "Serial number of the probe to connect to.",
                },
                "device": {
                    "type": "string",
                    "description": (
                        "Target device string (e.g. nRF52840_xxAA). This is the TARGET chip "
                        "on the board, not the debug probe MCU. The probe name (e.g. "
                        "OB-nRF5340) refers to the debugger, not the target. "
                        "Overrides DBGPROBE_JLINK_DEVICE."
                    ),
                },
                "interface": {
                    "type": "string",
                    "enum": ["swd", "jtag"],
                    "description": "Debug interface (default from DBGPROBE_INTERFACE, typically SWD).",
                },
                "speed_khz": {
                    "type": ["integer", "string"],
                    "description": "Interface speed in kHz (default from DBGPROBE_SPEED_KHZ, typically 4000).",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="dbgprobe.erase",
        description=(
            "Erase target flash. With no address params: full chip erase (unlocks "
            "secured/read-protected devices like Nordic APPROTECT). With start_addr "
            "and end_addr: erase only that range. If session_id is provided, erases "
            "through the active GDB session (preferred — no USB contention). "
            "Without session_id, uses JLinkExe directly (for session-less erase, "
            "e.g. unlocking a secured device before connect)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID for session-based erase (preferred). Omit for session-less erase.",
                },
                "backend": {
                    "type": "string",
                    "description": "Backend to use (default from DBGPROBE_BACKEND env var). Only for session-less erase.",
                },
                "probe_id": {
                    "type": "string",
                    "description": "Serial number of the probe. Only for session-less erase.",
                },
                "device": {
                    "type": "string",
                    "description": "Target device string (e.g. nRF52840_xxAA). Only for session-less erase.",
                },
                "interface": {
                    "type": "string",
                    "enum": ["swd", "jtag"],
                    "description": "Debug interface (default from DBGPROBE_INTERFACE). Only for session-less erase.",
                },
                "speed_khz": {
                    "type": ["integer", "string"],
                    "description": "Interface speed in kHz (default from DBGPROBE_SPEED_KHZ). Only for session-less erase.",
                },
                "start_addr": {
                    "type": ["integer", "string"],
                    "description": 'Start address for range erase (e.g. 0x00040000 or "0x40000"). Omit for full chip erase.',
                },
                "end_addr": {
                    "type": ["integer", "string"],
                    "description": 'End address for range erase (e.g. 0x00080000 or "0x80000"). Required if start_addr is set.',
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="dbgprobe.disconnect",
        description="Close a debug probe session and release resources.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.reset",
        description=(
            "Reset the target. Modes: 'soft' (default) — software reset and resume, "
            "'hard' — hardware reset, 'halt' — reset and halt at first instruction."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["soft", "hard", "halt"],
                    "description": "Reset mode (default: soft).",
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.halt",
        description="Halt the target CPU.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.go",
        description="Resume target execution (run/go).",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.flash",
        description=(
            "Program a firmware image to the target. Supports .hex, .elf (address auto-detected) "
            "and raw .bin (requires explicit addr). Optionally verify and reset after flashing. "
            "If session_id is provided, tears down GDB, flashes, and restarts (preferred). "
            "Without session_id, uses JLinkExe directly (session-less, no debug session needed)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID for session-based flash (preferred). Omit for session-less flash.",
                },
                "path": {
                    "type": "string",
                    "description": "Path to firmware file (.hex, .elf, .bin).",
                },
                "backend": {
                    "type": "string",
                    "description": "Backend to use (default from DBGPROBE_BACKEND env var). Only for session-less flash.",
                },
                "probe_id": {
                    "type": "string",
                    "description": "Serial number of the probe. Only for session-less flash.",
                },
                "device": {
                    "type": "string",
                    "description": "Target device string (e.g. nRF52840_xxAA). Only for session-less flash.",
                },
                "interface": {
                    "type": "string",
                    "enum": ["swd", "jtag"],
                    "description": "Debug interface (default from DBGPROBE_INTERFACE). Only for session-less flash.",
                },
                "speed_khz": {
                    "type": ["integer", "string"],
                    "description": "Interface speed in kHz (default from DBGPROBE_SPEED_KHZ). Only for session-less flash.",
                },
                "addr": {
                    "type": ["integer", "string"],
                    "description": 'Base address for .bin files (e.g. 0x08000000 or "0x8000000"). Not needed for .hex/.elf.',
                },
                "verify": {
                    "type": "boolean",
                    "description": "Verify after programming (default: true).",
                },
                "reset_after": {
                    "type": "boolean",
                    "description": "Reset and run after programming (default: true).",
                },
            },
            "required": ["path"],
        },
    ),
    Tool(
        name="dbgprobe.mem.read",
        description=(
            "Read memory from the target. Returns data in the requested format: "
            "'hex' (hex string), 'base64', or 'u32' (array of 32-bit words, little-endian)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "address": {
                    "type": ["integer", "string"],
                    "description": 'Start address (e.g. 0x20000000 or "0x20000000").',
                },
                "length": {
                    "type": ["integer", "string"],
                    "description": "Number of bytes to read.",
                },
                "format": {
                    "type": "string",
                    "enum": ["hex", "base64", "u32"],
                    "description": "Output format (default: hex).",
                },
            },
            "required": ["session_id", "address", "length"],
        },
    ),
    Tool(
        name="dbgprobe.mem.write",
        description=(
            "Write data to target memory. Provide data in one of: "
            "'hex' (hex string), 'base64', or 'u32' (array of 32-bit words, little-endian)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "address": {
                    "type": ["integer", "string"],
                    "description": 'Start address (e.g. 0x20000000 or "0x20000000").',
                },
                "data": {
                    "type": "string",
                    "description": "Data as hex string or base64 string.",
                },
                "data_u32": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Data as array of 32-bit unsigned integers.",
                },
                "format": {
                    "type": "string",
                    "enum": ["hex", "base64", "u32"],
                    "description": "Input format (default: hex).",
                },
            },
            "required": ["session_id", "address"],
        },
    ),
    Tool(
        name="dbgprobe.step",
        description=(
            "Single-step one instruction. Target must be halted first. Returns the new PC and stop reason."
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
        name="dbgprobe.status",
        description=(
            "Query target state. Returns whether the target is running or halted, "
            "and if halted, the current PC and stop reason (e.g. breakpoint hit)."
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
        name="dbgprobe.breakpoint.set",
        description=(
            "Set a breakpoint at a target address or symbol name. Software breakpoints (default) "
            "are handled by the debug probe and work on both flash and RAM. "
            "Hardware breakpoints use the CPU's FPB and are limited in number (typically 4-6). "
            "If 'symbol' is provided and an ELF is attached, resolves the symbol to an address."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "address": {
                    "type": ["integer", "string"],
                    "description": 'Address to set the breakpoint at (e.g. 0x08000100 or "0x8000100").',
                },
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to resolve to an address (requires ELF attached).",
                },
                "bp_type": {
                    "type": "string",
                    "enum": ["hw", "sw"],
                    "description": "Breakpoint type: 'sw' (software, default) or 'hw' (hardware).",
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.breakpoint.clear",
        description="Clear a breakpoint at a target address.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "address": {
                    "type": ["integer", "string"],
                    "description": 'Address of the breakpoint to clear (e.g. 0x08000100 or "0x8000100").',
                },
            },
            "required": ["session_id", "address"],
        },
    ),
    Tool(
        name="dbgprobe.breakpoint.list",
        description="List all active breakpoints for a session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enrich_mem_read_svd(
    result: dict[str, Any],
    session: DbgProbeSession,
    address: int,
    length: int,
    data: bytes,
) -> None:
    """If SVD is attached and address matches a register, add decoded fields."""
    if session.svd is None:
        return
    from dbgprobe_mcp_server.svd import decode_register, register_at_address

    match = register_at_address(session.svd, address)
    if match is None:
        return
    periph, reg = match
    # Only decode if the read length matches the register size
    reg_bytes = reg.size // 8
    if length != reg_bytes:
        return
    # Unpack the raw value
    if reg_bytes == 1:
        raw = data[0]
    elif reg_bytes == 2:
        raw = struct.unpack("<H", data[:2])[0]
    else:
        raw = struct.unpack("<I", data[:4])[0]
    decoded = decode_register(reg, raw)
    result["svd"] = {
        "peripheral": periph.name,
        "register": reg.name,
        "raw": raw,
        "fields": decoded,
    }


def _enrich_pc(result: dict[str, Any], session: DbgProbeSession) -> None:
    """If ELF is attached and result has a 'pc' key, add symbol + offset."""
    if session.elf is None:
        return
    pc = result.get("pc")
    if pc is None:
        return
    resolved = resolve_address(session.elf, pc)
    if resolved is not None:
        result["symbol"] = resolved[0]
        result["symbol_offset"] = resolved[1]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_list_probes(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    backend_name = args.get("backend", DBGPROBE_BACKEND)
    try:
        backend = registry.create(backend_name)
    except ValueError as exc:
        return _err("invalid_backend", str(exc))

    try:
        probes = await backend.list_probes()
    except FileNotFoundError as exc:
        return _err("exe_not_found", str(exc))

    return _ok(
        probes=[{"serial": p.serial, "description": p.description, "backend": p.backend} for p in probes],
        count=len(probes),
        backend=backend_name,
    )


async def handle_connect(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    if len(state.sessions) >= state.max_sessions:
        raise RuntimeError(
            f"Maximum sessions ({state.max_sessions}) reached. Disconnect an existing session first."
        )

    backend_name = args.get("backend", DBGPROBE_BACKEND)
    try:
        backend = registry.create(backend_name)
    except ValueError as exc:
        return _err("invalid_backend", str(exc))

    config = ConnectConfig(
        backend=backend_name,
        device=args.get("device") or DBGPROBE_JLINK_DEVICE,
        interface=(args.get("interface") or DBGPROBE_INTERFACE).upper(),
        speed_khz=int(args.get("speed_khz") or DBGPROBE_SPEED_KHZ),
        probe_serial=args.get("probe_id"),
    )

    try:
        connect_info = await backend.connect(config)
    except FileNotFoundError as exc:
        return _err("exe_not_found", str(exc))
    except DeviceSecuredError as exc:
        return _err("device_secured", str(exc))
    except ConnectionError as exc:
        return _err("connect_failed", str(exc))

    session_id = state.generate_id()
    session = DbgProbeSession(
        connection_id=session_id,
        backend=backend,
        config=config,
    )
    state.sessions[session_id] = session

    return _ok(
        session_id=session_id,
        config=config.to_dict(),
        **connect_info,
    )


async def handle_erase(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    start_addr = _parse_addr(args.get("start_addr"))
    end_addr = _parse_addr(args.get("end_addr"))

    if (start_addr is None) != (end_addr is None):
        return _err("invalid_params", "Both start_addr and end_addr are required for range erase.")
    if start_addr is not None and end_addr is not None and start_addr >= end_addr:
        return _err("invalid_params", "start_addr must be less than end_addr.")

    session_id = args.get("session_id")

    # Session-based erase: route through GDB (no USB contention)
    if session_id is not None:
        session = state.get_session(session_id)
        try:
            erase_info = await session.backend.erase_via_gdb(start_addr=start_addr, end_addr=end_addr)
        except NotImplementedError:
            return _err("not_supported", "This backend does not support session-based erase.")
        except ConnectionError as exc:
            return _err("erase_failed", str(exc))

        result_kwargs: dict[str, Any] = {"erased": True, "session_id": session_id}
        if start_addr is not None:
            result_kwargs["start_addr"] = start_addr
            result_kwargs["end_addr"] = end_addr
        return _ok(**result_kwargs, **erase_info)

    # Session-less erase: JLinkExe direct (for unlocking secured devices, etc.)
    backend_name = args.get("backend", DBGPROBE_BACKEND)
    try:
        backend = registry.create(backend_name)
    except ValueError as exc:
        return _err("invalid_backend", str(exc))

    config = ConnectConfig(
        backend=backend_name,
        device=args.get("device") or DBGPROBE_JLINK_DEVICE,
        interface=(args.get("interface") or DBGPROBE_INTERFACE).upper(),
        speed_khz=int(args.get("speed_khz") or DBGPROBE_SPEED_KHZ),
        probe_serial=args.get("probe_id"),
    )

    try:
        erase_info = await backend.erase(config, start_addr=start_addr, end_addr=end_addr)
    except FileNotFoundError as exc:
        return _err("exe_not_found", str(exc))
    except ConnectionError as exc:
        return _err("erase_failed", str(exc))

    result_kwargs = {"erased": True, "config": config.to_dict()}
    if start_addr is not None:
        result_kwargs["start_addr"] = start_addr
        result_kwargs["end_addr"] = end_addr
    return _ok(**result_kwargs, **erase_info)


async def handle_disconnect(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session_id = args["session_id"]
    session = state.get_session(session_id)

    if session.backend is not None:
        try:
            await session.backend.disconnect()
        except Exception:
            logger.debug("Error during disconnect for session %s", session_id, exc_info=True)

    del state.sessions[session_id]
    return _ok(session_id=session_id)


async def handle_reset(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    mode = args.get("mode", "soft")
    if mode not in ("soft", "hard", "halt"):
        return _err("invalid_params", f"Invalid reset mode: {mode!r}. Use soft, hard, or halt.")

    result = await session.backend.reset(mode)

    # Re-set breakpoints — reset may clear CPU debug registers (FPB).
    # Clear all first to avoid duplicates (soft reset may NOT clear FPB).
    if session.breakpoints:
        try:
            await session.backend.clear_all_breakpoints()
        except (NotImplementedError, ConnectionError):
            pass
        restored = 0
        for bp in session.breakpoints.values():
            try:
                await session.backend.set_breakpoint(bp.address, bp_type=bp.bp_type)
                restored += 1
            except (NotImplementedError, ConnectionError):
                pass
        if restored:
            result["breakpoints_restored"] = restored

    return _ok(session_id=args["session_id"], **result)


async def handle_halt(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    result = await session.backend.halt()
    _enrich_pc(result, session)
    return _ok(session_id=args["session_id"], **result)


async def handle_go(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])

    # Remove breakpoint at current PC before continuing, re-insert after.
    # This is standard GDB behavior — without it, hardware breakpoints
    # (FPB) re-trigger immediately, and software breakpoints may too.
    bp_to_restore = None
    if session.breakpoints:
        try:
            status = await session.backend.status()
            pc = status.get("pc")
            if pc is not None and pc in session.breakpoints:
                bp_to_restore = session.breakpoints[pc]
                await session.backend.clear_breakpoint(pc)
        except (NotImplementedError, ConnectionError):
            pass

    result = await session.backend.go()

    if bp_to_restore is not None:
        try:
            await session.backend.set_breakpoint(bp_to_restore.address, bp_type=bp_to_restore.bp_type)
        except (NotImplementedError, ConnectionError):
            pass

    return _ok(session_id=args["session_id"], **result)


async def handle_flash(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    path = args["path"]
    addr = _parse_addr(args.get("addr"))
    verify = args.get("verify", True)
    reset_after = args.get("reset_after", True)
    session_id = args.get("session_id")

    # Session-based flash: teardown GDB → JLinkExe → restart GDB
    if session_id is not None:
        session = state.get_session(session_id)
        try:
            result = await session.backend.flash(path, addr=addr, verify=verify, reset_after=reset_after)
        except FileNotFoundError as exc:
            return _err("file_not_found", str(exc))
        except (ValueError, ConnectionError) as exc:
            return _err("flash_failed", str(exc))

        # Flashing new firmware invalidates all breakpoints.
        if session.breakpoints:
            session.breakpoints.clear()

        # ELF handling after flash:
        # - If flashing an .elf, use it as the ELF source (auto-attach or update).
        # - Otherwise, re-parse the previously attached ELF from the same path.
        flashed_is_elf = path.lower().endswith(".elf")
        had_elf = session.elf is not None

        if flashed_is_elf:
            try:
                session.elf = parse_elf(path)
                result["elf_reloaded" if had_elf else "elf_attached"] = True
                result["elf_path"] = path
            except Exception:
                logger.debug("ELF auto-attach failed for %s", path)
                if session.elf is not None:
                    session.elf = None
                    result["elf_detached"] = True
        elif session.elf is not None:
            elf_path = session.elf.path
            try:
                session.elf = parse_elf(elf_path)
                result["elf_reloaded"] = True
                result["elf_path"] = elf_path
            except Exception:
                logger.debug("ELF auto-reload failed for %s, detaching", elf_path)
                session.elf = None
                result["elf_detached"] = True

        # Look for sibling .elf near the flashed file.
        hint = find_sibling_elf(path)
        if hint is not None:
            result["elf_hint"] = hint

        return _ok(session_id=session_id, **result)

    # Session-less flash: JLinkExe direct (no GDB involved)
    backend_name = args.get("backend", DBGPROBE_BACKEND)
    try:
        backend = registry.create(backend_name)
    except ValueError as exc:
        return _err("invalid_backend", str(exc))

    config = ConnectConfig(
        backend=backend_name,
        device=args.get("device") or DBGPROBE_JLINK_DEVICE,
        interface=(args.get("interface") or DBGPROBE_INTERFACE).upper(),
        speed_khz=int(args.get("speed_khz") or DBGPROBE_SPEED_KHZ),
        probe_serial=args.get("probe_id"),
    )
    try:
        result = await backend.flash(path, addr=addr, verify=verify, reset_after=reset_after, config=config)
    except FileNotFoundError as exc:
        return _err("file_not_found", str(exc))
    except TimeoutError:
        return _err(
            "timeout",
            "JLinkExe timed out. For session-less flash, ensure device and probe_id are provided.",
        )
    except (ValueError, ConnectionError) as exc:
        return _err("flash_failed", str(exc))

    result["config"] = config.to_dict()

    # Look for sibling .elf near the flashed file.
    hint = find_sibling_elf(path)
    if hint is not None:
        result["elf_hint"] = hint

    return _ok(**result)


async def handle_mem_read(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    address = _parse_addr(args["address"])
    length = int(args["length"])
    fmt = args.get("format", "hex")

    data = await session.backend.mem_read(address, length)

    if fmt == "base64":
        result = _ok(
            session_id=args["session_id"],
            address=address,
            length=len(data),
            format="base64",
            data=base64.b64encode(data).decode("ascii"),
        )
    elif fmt == "u32":
        # Pad to multiple of 4 bytes
        padded = data + b"\x00" * ((-len(data)) % 4)
        words = list(struct.unpack(f"<{len(padded) // 4}I", padded))
        result = _ok(
            session_id=args["session_id"],
            address=address,
            length=len(data),
            format="u32",
            data=words,
        )
    else:
        result = _ok(
            session_id=args["session_id"],
            address=address,
            length=len(data),
            format="hex",
            data=data.hex(),
        )

    _enrich_mem_read_svd(result, session, address, length, data)
    return result


async def handle_mem_write(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    address = _parse_addr(args["address"])
    fmt = args.get("format", "hex")

    if fmt == "u32":
        data_u32 = args.get("data_u32")
        if not data_u32 or not isinstance(data_u32, list):
            return _err("invalid_params", "u32 format requires data_u32 array.")
        data = struct.pack(f"<{len(data_u32)}I", *data_u32)
    elif fmt == "base64":
        raw = args.get("data", "")
        try:
            data = base64.b64decode(raw)
        except Exception:
            return _err("invalid_params", "Invalid base64 data.")
    else:
        raw = args.get("data", "")
        try:
            data = bytes.fromhex(raw)
        except ValueError:
            return _err("invalid_params", "Invalid hex data.")

    result = await session.backend.mem_write(address, data)
    return _ok(session_id=args["session_id"], **result)


async def handle_step(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    try:
        result = await session.backend.step()
    except NotImplementedError:
        return _err("not_supported", "step() is not supported by this backend.")
    _enrich_pc(result, session)
    return _ok(session_id=args["session_id"], **result)


async def handle_status(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    try:
        result = await session.backend.status()
    except NotImplementedError:
        return _err("not_supported", "status() is not supported by this backend.")
    _enrich_pc(result, session)
    return _ok(session_id=args["session_id"], **result)


async def handle_breakpoint_set(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    bp_type = args.get("bp_type", "sw")
    symbol_name = args.get("symbol")
    address = _parse_addr(args.get("address"))

    if bp_type not in ("hw", "sw"):
        return _err("invalid_params", f"Invalid breakpoint type: {bp_type!r}. Use 'hw' or 'sw'.")

    # Resolve symbol to address if provided
    if symbol_name is not None:
        if address is not None:
            return _err("invalid_params", "Provide 'address' or 'symbol', not both.")
        if session.elf is None:
            return _err("no_elf", "No ELF attached. Use dbgprobe.elf.attach to resolve symbols.")
        sym = resolve_symbol(session.elf, symbol_name)
        if sym is None:
            return _err("not_found", f"Symbol {symbol_name!r} not found in ELF.")
        address = sym.address
    elif address is None:
        return _err("invalid_params", "Provide 'address' or 'symbol'.")

    try:
        result = await session.backend.set_breakpoint(address, bp_type=bp_type)
    except NotImplementedError:
        return _err("not_supported", "Breakpoints are not supported by this backend.")

    session.breakpoints[address] = Breakpoint(address=address, bp_type=bp_type)
    if symbol_name is not None:
        result["symbol"] = symbol_name
    return _ok(session_id=args["session_id"], **result)


async def handle_breakpoint_clear(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    address = _parse_addr(args["address"])

    if address not in session.breakpoints:
        return _err("not_found", f"No breakpoint at 0x{address:08x}.")

    try:
        result = await session.backend.clear_breakpoint(address)
    except NotImplementedError:
        return _err("not_supported", "Breakpoints are not supported by this backend.")

    del session.breakpoints[address]
    return _ok(session_id=args["session_id"], **result)


async def handle_breakpoint_list(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    bps = [{"address": bp.address, "bp_type": bp.bp_type} for bp in session.breakpoints.values()]
    return _ok(session_id=args["session_id"], breakpoints=bps, count=len(bps))


HANDLERS: dict[str, Any] = {
    "dbgprobe.list_probes": handle_list_probes,
    "dbgprobe.connect": handle_connect,
    "dbgprobe.erase": handle_erase,
    "dbgprobe.disconnect": handle_disconnect,
    "dbgprobe.reset": handle_reset,
    "dbgprobe.halt": handle_halt,
    "dbgprobe.go": handle_go,
    "dbgprobe.flash": handle_flash,
    "dbgprobe.mem.read": handle_mem_read,
    "dbgprobe.mem.write": handle_mem_write,
    "dbgprobe.step": handle_step,
    "dbgprobe.status": handle_status,
    "dbgprobe.breakpoint.set": handle_breakpoint_set,
    "dbgprobe.breakpoint.clear": handle_breakpoint_clear,
    "dbgprobe.breakpoint.list": handle_breakpoint_list,
}
