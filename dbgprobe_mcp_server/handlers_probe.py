"""Debug probe tool definitions and handlers."""

from __future__ import annotations

import base64
import struct
from typing import Any

from mcp.types import Tool

from dbgprobe_mcp_server.backend import ConnectConfig, DeviceSecuredError, registry
from dbgprobe_mcp_server.helpers import (
    DBGPROBE_BACKEND,
    DBGPROBE_INTERFACE,
    DBGPROBE_JLINK_DEVICE,
    DBGPROBE_SPEED_KHZ,
    _err,
    _ok,
)
from dbgprobe_mcp_server.state import DbgProbeSession, ProbeState

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
            "resolved configuration (backend, executable paths, defaults applied)."
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
                    "type": "integer",
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
            "and end_addr: erase only that range. Does not require a session. "
            "Use when dbgprobe.connect returns device_secured."
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
                    "description": "Serial number of the probe to use.",
                },
                "device": {
                    "type": "string",
                    "description": "Target device string (e.g. nRF52840_xxAA).",
                },
                "interface": {
                    "type": "string",
                    "enum": ["swd", "jtag"],
                    "description": "Debug interface (default from DBGPROBE_INTERFACE).",
                },
                "speed_khz": {
                    "type": "integer",
                    "description": "Interface speed in kHz (default from DBGPROBE_SPEED_KHZ).",
                },
                "start_addr": {
                    "type": "integer",
                    "description": "Start address for range erase (e.g. 0x00040000). Omit for full chip erase.",
                },
                "end_addr": {
                    "type": "integer",
                    "description": "End address for range erase (e.g. 0x00080000). Required if start_addr is set.",
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
            "and raw .bin (requires explicit addr). Optionally verify and reset after flashing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Path to firmware file (.hex, .elf, .bin).",
                },
                "addr": {
                    "type": "integer",
                    "description": "Base address for .bin files (e.g. 0x08000000). Not needed for .hex/.elf.",
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
            "required": ["session_id", "path"],
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
                    "type": "integer",
                    "description": "Start address (e.g. 0x20000000).",
                },
                "length": {
                    "type": "integer",
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
                    "type": "integer",
                    "description": "Start address.",
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
]

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
        speed_khz=args.get("speed_khz") or DBGPROBE_SPEED_KHZ,
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
    backend_name = args.get("backend", DBGPROBE_BACKEND)
    try:
        backend = registry.create(backend_name)
    except ValueError as exc:
        return _err("invalid_backend", str(exc))

    config = ConnectConfig(
        backend=backend_name,
        device=args.get("device") or DBGPROBE_JLINK_DEVICE,
        interface=(args.get("interface") or DBGPROBE_INTERFACE).upper(),
        speed_khz=args.get("speed_khz") or DBGPROBE_SPEED_KHZ,
        probe_serial=args.get("probe_id"),
    )

    start_addr = args.get("start_addr")
    end_addr = args.get("end_addr")

    if (start_addr is None) != (end_addr is None):
        return _err("invalid_params", "Both start_addr and end_addr are required for range erase.")
    if start_addr is not None and end_addr is not None and start_addr >= end_addr:
        return _err("invalid_params", "start_addr must be less than end_addr.")

    try:
        erase_info = await backend.erase(config, start_addr=start_addr, end_addr=end_addr)
    except FileNotFoundError as exc:
        return _err("exe_not_found", str(exc))
    except ConnectionError as exc:
        return _err("erase_failed", str(exc))

    result_kwargs: dict[str, Any] = {"erased": True, "config": config.to_dict()}
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
            pass

    del state.sessions[session_id]
    return _ok(session_id=session_id)


async def handle_reset(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    mode = args.get("mode", "soft")
    if mode not in ("soft", "hard", "halt"):
        return _err("invalid_params", f"Invalid reset mode: {mode!r}. Use soft, hard, or halt.")

    result = await session.backend.reset(mode)
    return _ok(session_id=args["session_id"], **result)


async def handle_halt(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    result = await session.backend.halt()
    return _ok(session_id=args["session_id"], **result)


async def handle_go(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    result = await session.backend.go()
    return _ok(session_id=args["session_id"], **result)


async def handle_flash(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    path = args["path"]
    addr = args.get("addr")
    verify = args.get("verify", True)
    reset_after = args.get("reset_after", True)

    result = await session.backend.flash(path, addr=addr, verify=verify, reset_after=reset_after)
    return _ok(session_id=args["session_id"], **result)


async def handle_mem_read(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    address = args["address"]
    length = args["length"]
    fmt = args.get("format", "hex")

    data = await session.backend.mem_read(address, length)

    if fmt == "base64":
        return _ok(
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
        return _ok(
            session_id=args["session_id"],
            address=address,
            length=len(data),
            format="u32",
            data=words,
        )
    else:
        return _ok(
            session_id=args["session_id"],
            address=address,
            length=len(data),
            format="hex",
            data=data.hex(),
        )


async def handle_mem_write(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    address = args["address"]
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
}
