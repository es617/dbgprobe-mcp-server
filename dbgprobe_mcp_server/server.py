"""Debug Probe MCP server – stdio transport, stateful debug probe tools."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any

import anyio
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from dbgprobe_mcp_server import (
    backends as _backends,  # noqa: F401 — triggers backend registration
)
from dbgprobe_mcp_server import (
    handlers_elf,
    handlers_introspection,
    handlers_plugin,
    handlers_probe,
    handlers_rtt,
    handlers_svd,
    handlers_trace,
)
from dbgprobe_mcp_server.backend import DeviceSecuredError
from dbgprobe_mcp_server.helpers import (
    _err,
    _result_text,
)
from dbgprobe_mcp_server.plugins import PluginManager, parse_plugin_policy
from dbgprobe_mcp_server.specs import resolve_spec_root
from dbgprobe_mcp_server.state import ProbeState
from dbgprobe_mcp_server.trace import get_trace_buffer, init_trace, sanitize_args

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Tool-name separator (default ".").  Set DBGPROBE_MCP_TOOL_SEPARATOR=_ for
# MCP clients that reject dots in tool names (e.g. Cursor).
_TOOL_SEP = os.environ.get("DBGPROBE_MCP_TOOL_SEPARATOR", ".")

_LOG_LEVEL = os.environ.get("DBGPROBE_MCP_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("dbgprobe_mcp_server")

# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def _apply_tool_separator(tools: list[Tool], handlers: dict[str, Any], sep: str) -> None:
    """Replace '.' with *sep* in every tool name and handler key."""
    if sep == ".":
        return
    for t in tools:
        t.name = t.name.replace(".", sep)
    for old_key in list(handlers):
        new_key = old_key.replace(".", sep)
        if new_key != old_key:
            handlers[new_key] = handlers.pop(old_key)


def build_server() -> tuple[Server, ProbeState]:
    state = ProbeState()
    server = Server("dbgprobe-mcp-server")

    tools: list[Tool] = (
        handlers_probe.TOOLS
        + handlers_introspection.TOOLS
        + handlers_elf.TOOLS
        + handlers_svd.TOOLS
        + handlers_rtt.TOOLS
        + handlers_trace.TOOLS
        + handlers_plugin.TOOLS
    )
    handlers: dict[str, Any] = {
        **handlers_probe.HANDLERS,
        **handlers_introspection.HANDLERS,
        **handlers_elf.HANDLERS,
        **handlers_svd.HANDLERS,
        **handlers_rtt.HANDLERS,
        **handlers_trace.HANDLERS,
    }

    # --- Plugin system ---
    plugins_dir = resolve_spec_root() / "plugins"
    plugins_enabled, plugins_allowlist = parse_plugin_policy()
    manager = PluginManager(
        plugins_dir,
        tools,
        handlers,
        enabled=plugins_enabled,
        allowlist=plugins_allowlist,
        tool_separator=_TOOL_SEP,
    )
    manager.load_all()
    handlers.update(handlers_plugin.make_handlers(manager, server))

    # Rename tool names / handler keys for clients that reject dots.
    _apply_tool_separator(tools, handlers, _TOOL_SEP)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        arguments = arguments or {}

        buf = get_trace_buffer()
        if buf:
            cid = arguments.get("connection_id")
            safe_args = sanitize_args(arguments)
            buf.emit({"event": "tool_call_start", "tool": name, "args": safe_args, "connection_id": cid})
            t0 = time.monotonic()

        handler = handlers.get(name)
        if handler is None:
            return _result_text(_err("unknown_tool", f"No tool named {name}"))

        try:
            result = await handler(state, arguments)
        except KeyError as exc:
            result = _err("not_found", str(exc))
        except (ValueError, TypeError) as exc:
            result = _err("invalid_params", str(exc))
        except RuntimeError as exc:
            result = _err("limit_reached", str(exc))
        except DeviceSecuredError as exc:
            result = _err("device_secured", str(exc))
        except ConnectionError as exc:
            result = _err("disconnected", str(exc))
        except TimeoutError:
            result = _err("timeout", "Probe operation timed out.")
        except Exception as exc:
            logger.error("Unhandled error in %s: %s", name, exc, exc_info=True)
            result = _err("internal", f"Internal error in {name}. Check server logs for details.")

        if buf:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            buf.emit(
                {
                    "event": "tool_call_end",
                    "tool": name,
                    "ok": result.get("ok"),
                    "error_code": result.get("error", {}).get("code")
                    if isinstance(result.get("error"), dict)
                    else None,
                    "duration_ms": duration_ms,
                    "connection_id": cid,
                }
            )

        return _result_text(result)

    init_trace()
    return server, state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run() -> None:
    server, state = build_server()

    logger.info("Starting Debug Probe MCP server")

    _BENIGN_ASYNC = (EOFError, BrokenPipeError, anyio.ClosedResourceError, anyio.BrokenResourceError)

    try:
        async with stdio_server() as (read_stream, write_stream):
            init_options = server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True),
            )
            await server.run(read_stream, write_stream, init_options)
    except _BENIGN_ASYNC:
        # Normal termination — client closed stdin / streams broke.
        pass
    except BaseExceptionGroup as eg:
        # anyio wraps stream-closure errors in ExceptionGroup on Python 3.11+.
        if not all(isinstance(e, _BENIGN_ASYNC) for e in eg.exceptions):
            raise
    finally:
        try:
            await asyncio.wait_for(asyncio.shield(state.shutdown()), timeout=0.25)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass
        buf = get_trace_buffer()
        if buf:
            try:
                buf.close()
            except Exception:
                pass


_BENIGN_SYNC = (
    KeyboardInterrupt,
    BrokenPipeError,
    EOFError,
    ConnectionError,
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
)


def main() -> None:
    try:
        asyncio.run(_run())
    except _BENIGN_SYNC:
        pass
    except BaseExceptionGroup as eg:
        if not all(isinstance(e, _BENIGN_SYNC) for e in eg.exceptions):
            raise


if __name__ == "__main__":
    main()
