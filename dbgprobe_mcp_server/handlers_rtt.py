"""RTT (Real-Time Transfer) tool definitions and handlers."""

from __future__ import annotations

from typing import Any

from mcp.types import Tool

from dbgprobe_mcp_server.helpers import _coerce_bool, _ok, _parse_addr
from dbgprobe_mcp_server.state import ProbeState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="dbgprobe.rtt.start",
        description=(
            "Start RTT (Real-Time Transfer) on a connected session. "
            "Connects to the JLinkGDBServer RTT telnet port and begins "
            "buffering target output (channel 0). Optional address hint "
            "for the RTT control block in target RAM."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID from dbgprobe.connect.",
                },
                "address": {
                    "type": ["integer", "string"],
                    "description": (
                        "Optional RTT control block address in target RAM. "
                        "Accepts integer or hex string (e.g. '0x20000000'). "
                        "If omitted, JLinkGDBServer auto-searches for 'SEGGER RTT'."
                    ),
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.rtt.stop",
        description="Stop RTT and disconnect from the telnet port.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID from dbgprobe.connect.",
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.rtt.read",
        description=(
            "Read buffered RTT data from the target. Returns data as text "
            "(UTF-8 by default) or hex. Non-blocking — returns whatever is "
            "buffered, waiting up to timeout seconds for initial data."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID from dbgprobe.connect.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Max seconds to wait for data if buffer is empty (default 0.1).",
                    "default": 0.1,
                },
                "encoding": {
                    "type": "string",
                    "enum": ["utf-8", "hex"],
                    "description": "Output encoding: 'utf-8' (default) or 'hex' for binary data.",
                    "default": "utf-8",
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.rtt.write",
        description=(
            "Write data to the target via RTT channel 0. Input is text "
            "(UTF-8 by default) or hex-encoded bytes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID from dbgprobe.connect.",
                },
                "data": {
                    "type": "string",
                    "description": "Data to write. Text (UTF-8) or hex string depending on encoding.",
                },
                "encoding": {
                    "type": "string",
                    "enum": ["utf-8", "hex"],
                    "description": "Input encoding: 'utf-8' (default) or 'hex'.",
                    "default": "utf-8",
                },
                "newline": {
                    "type": ["boolean", "string"],
                    "description": "Append '\\n' to the data (default false). Convenience for terminal input.",
                    "default": False,
                },
            },
            "required": ["session_id", "data"],
        },
    ),
    Tool(
        name="dbgprobe.rtt.status",
        description="Return RTT status: active, bytes buffered, total read/written.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID from dbgprobe.connect.",
                },
            },
            "required": ["session_id"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_rtt_start(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session_id = args["session_id"]
    session = state.get_session(session_id)
    if session.backend is None:
        raise ConnectionError("No backend attached to this session.")
    address = _parse_addr(args.get("address"))
    result = await session.backend.rtt_start(address=address)
    return _ok(session_id=session_id, **result)


async def handle_rtt_stop(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session_id = args["session_id"]
    session = state.get_session(session_id)
    if session.backend is None:
        raise ConnectionError("No backend attached to this session.")
    await session.backend.rtt_stop()
    return _ok(session_id=session_id, message="RTT stopped.")


async def handle_rtt_read(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session_id = args["session_id"]
    session = state.get_session(session_id)
    if session.backend is None:
        raise ConnectionError("No backend attached to this session.")
    timeout = float(args.get("timeout", 0.1))
    encoding = args.get("encoding", "utf-8")
    raw = await session.backend.rtt_read(timeout=timeout)
    data_str = raw.hex() if encoding == "hex" else raw.decode("utf-8", errors="replace")
    return _ok(session_id=session_id, data=data_str, bytes_read=len(raw), encoding=encoding)


async def handle_rtt_write(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session_id = args["session_id"]
    session = state.get_session(session_id)
    if session.backend is None:
        raise ConnectionError("No backend attached to this session.")
    data_str = args["data"]
    encoding = args.get("encoding", "utf-8")
    append_newline = _coerce_bool(args.get("newline", False))
    if encoding == "hex":
        try:
            data = bytes.fromhex(data_str)
        except ValueError as exc:
            raise ValueError(f"Invalid hex string: {exc}") from exc
    else:
        data = data_str.encode("utf-8")
    if append_newline:
        data += b"\n"
    written = await session.backend.rtt_write(data)
    return _ok(session_id=session_id, bytes_written=written)


async def handle_rtt_status(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session_id = args["session_id"]
    session = state.get_session(session_id)
    if session.backend is None:
        raise ConnectionError("No backend attached to this session.")
    result = session.backend.rtt_status()
    result["session_id"] = session_id
    return _ok(**result)


HANDLERS: dict[str, Any] = {
    "dbgprobe.rtt.start": handle_rtt_start,
    "dbgprobe.rtt.stop": handle_rtt_stop,
    "dbgprobe.rtt.read": handle_rtt_read,
    "dbgprobe.rtt.write": handle_rtt_write,
    "dbgprobe.rtt.status": handle_rtt_status,
}
