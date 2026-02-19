"""Shared fixtures for Debug Probe MCP handler tests."""

from __future__ import annotations

import pytest

from dbgprobe_mcp_server.state import ProbeState


@pytest.fixture()
def probe_state():
    """Fresh ProbeState instance."""
    return ProbeState()
