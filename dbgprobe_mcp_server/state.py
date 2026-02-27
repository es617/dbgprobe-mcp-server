"""In-memory state for probe sessions."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dbgprobe_mcp_server.backend import Backend, ConnectConfig

if TYPE_CHECKING:
    from dbgprobe_mcp_server.elf import ElfData
    from dbgprobe_mcp_server.svd import SvdData

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
    elf: ElfData | None = None
    svd: SvdData | None = None
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


class ProbeState:
    """Central mutable state shared by all tool handlers.

    All mutations happen on the asyncio event loop (single-threaded), so no
    lock is needed.
    """

    def __init__(self, max_sessions: int = 10) -> None:
        self.sessions: dict[str, DbgProbeSession] = {}
        self.max_sessions = max_sessions

    def generate_id(self) -> str:
        return f"p{uuid.uuid4().hex[:8]}"

    def get_session(self, session_id: str) -> DbgProbeSession:
        if session_id not in self.sessions:
            raise KeyError(
                f"Unknown session_id: {session_id}. Call dbgprobe.connections.list to see active sessions."
            )
        return self.sessions[session_id]

    async def shutdown(self) -> None:
        """Disconnect all backends and clear sessions."""
        for session in list(self.sessions.values()):
            if session.backend is not None:
                try:
                    await session.backend.disconnect()
                except Exception:
                    logger.debug("Error disconnecting session %s", session.connection_id, exc_info=True)
        self.sessions.clear()
