"""Shared helpers, configuration, and response builders for handler modules."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from mcp.types import TextContent

logger = logging.getLogger("dbgprobe_mcp_server")

# ---------------------------------------------------------------------------
# Environment variable configuration
# ---------------------------------------------------------------------------

DBGPROBE_BACKEND = os.environ.get("DBGPROBE_BACKEND", "jlink").strip().lower()
DBGPROBE_JLINK_DEVICE = os.environ.get("DBGPROBE_JLINK_DEVICE", "").strip() or None
DBGPROBE_INTERFACE = os.environ.get("DBGPROBE_INTERFACE", "swd").strip().upper()
DBGPROBE_SPEED_KHZ = int(os.environ.get("DBGPROBE_SPEED_KHZ", "4000"))

# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _result_text(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, default=str))]
