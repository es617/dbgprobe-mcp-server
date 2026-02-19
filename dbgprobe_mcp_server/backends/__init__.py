"""Debug probe backends."""

from __future__ import annotations

from dbgprobe_mcp_server.backend import registry
from dbgprobe_mcp_server.backends.jlink import JLinkBackend

registry.register("jlink", JLinkBackend)

__all__ = ["JLinkBackend"]
