"""Shared helpers, configuration, and response builders for handler modules."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
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


def _validate_file_path(path: str, allowed_extensions: set[str]) -> Path:
    """Resolve *path* and validate it for file-attach operations.

    Checks:
    - Path resolves to an existing regular file (not a directory, device, etc.)
    - File extension is in *allowed_extensions* (lowercase, with leading dot)

    Returns the resolved ``Path``.
    Raises ``FileNotFoundError`` or ``ValueError`` on failure.
    """
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    if resolved.suffix.lower() not in allowed_extensions:
        exts = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"Unsupported file type {resolved.suffix!r} — expected: {exts}")
    return resolved


def _parse_addr(value: int | str | None) -> int | None:
    """Parse an address that may be an integer or a hex string like '0x10001208'."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)  # auto-detects 0x prefix, decimal, etc.
        except ValueError:
            raise ValueError(f"Invalid address: {value!r}") from None
    raise ValueError(f"Invalid address type: {type(value).__name__}")
