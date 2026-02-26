"""In-memory state for probe sessions."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from dbgprobe_mcp_server.backend import Backend, ConnectConfig

logger = logging.getLogger("dbgprobe_mcp_server")


@dataclass
class Breakpoint:
    """A breakpoint set on the target."""

    address: int
    bp_type: str  # "hw" or "sw"


@dataclass
class DbgProbeSession:
    """A managed debug probe session."""

    connection_id: str
    backend: Backend | None = None
    config: ConnectConfig | None = None
    spec: dict[str, Any] | None = None
    elf: Any | None = None  # ElfData when attached, avoids import dependency
    svd: Any | None = None  # SvdData when attached, avoids import dependency
    created_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)
    breakpoints: dict[int, Breakpoint] = field(default_factory=dict)  # addr -> Breakpoint

    @property
    def backend_name(self) -> str:
        if self.backend is not None:
            return self.backend.name
        if self.config is not None:
            return self.config.backend
        return "unknown"


# Backward-compatible alias used by handlers_spec / handlers_plugin / etc.
ProbeConnection = DbgProbeSession


class ProbeState:
    """Central mutable state shared by all tool handlers.

    All mutations happen on the asyncio event loop (single-threaded), so no
    lock is needed.

    ``connections`` and ``sessions`` point to the same dict for backward
    compatibility — spec/plugin/trace handlers use ``state.connections`` and
    ``state.get_connection()``.
    """

    def __init__(self, max_sessions: int = 10) -> None:
        self.sessions: dict[str, DbgProbeSession] = {}
        self.connections = self.sessions  # alias for backward compat
        self.max_sessions = max_sessions

    def generate_id(self) -> str:
        return f"p{uuid.uuid4().hex[:8]}"

    def get_session(self, session_id: str) -> DbgProbeSession:
        if session_id not in self.sessions:
            raise KeyError(
                f"Unknown connection_id: {session_id}. Call dbgprobe.connections.list to see active sessions."
            )
        return self.sessions[session_id]

    # Backward compat alias — handlers_spec.py calls state.get_connection(cid)
    def get_connection(self, connection_id: str) -> DbgProbeSession:
        return self.get_session(connection_id)

    async def shutdown(self) -> None:
        """Disconnect all backends and clear sessions."""
        for session in list(self.sessions.values()):
            if session.backend is not None:
                try:
                    await session.backend.disconnect()
                except Exception:
                    pass
        self.sessions.clear()
