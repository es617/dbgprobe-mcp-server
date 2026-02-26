"""Introspection tool definitions and handlers — list sessions."""

from __future__ import annotations

from typing import Any

from mcp.types import Tool

from dbgprobe_mcp_server.helpers import _ok
from dbgprobe_mcp_server.state import ProbeState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="dbgprobe.connections.list",
        description=(
            "List all open probe sessions with their status, backend, device, "
            "and config. Useful for recovering session IDs after context loss."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_connections_list(state: ProbeState, _args: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for session in state.sessions.values():
        entry: dict[str, Any] = {
            "session_id": session.connection_id,
            "backend": session.backend_name,
            "created_at": session.created_at,
        }
        if session.config is not None:
            entry["device"] = session.config.device
            entry["interface"] = session.config.interface
            entry["speed_khz"] = session.config.speed_khz
            entry["probe_serial"] = session.config.probe_serial
        if session.spec is not None:
            entry["spec"] = {
                "spec_id": session.spec.get("spec_id"),
                "name": session.spec.get("meta", {}).get("name"),
            }
        if session.elf is not None:
            entry["elf"] = {
                "path": session.elf.path,
                "symbol_count": sum(len(v) for v in session.elf.symbols.values()),
                "function_count": len(session.elf._sorted_functions),
            }
        if session.svd is not None:
            entry["svd"] = {
                "path": session.svd.path,
                "device_name": session.svd.device_name,
                "peripheral_count": len(session.svd.peripherals),
            }
        if session.backend is not None and session.backend.rtt_active:
            entry["rtt"] = {"active": True}
        items.append(entry)
    return _ok(
        message=f"{len(items)} session(s).",
        sessions=items,
        count=len(items),
    )


HANDLERS: dict[str, Any] = {
    "dbgprobe.connections.list": handle_connections_list,
}
