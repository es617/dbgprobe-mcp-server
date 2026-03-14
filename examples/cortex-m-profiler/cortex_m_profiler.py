# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Enrico Santagati

"""Cortex-M cycle profiler — non-invasive latency measurement via DWT_CYCCNT.

Uses the Data Watchpoint and Trace (DWT) hardware cycle counter plus
software breakpoints to measure execution time between two code addresses.
No firmware instrumentation required — works with any Cortex-M3/M4/M33 target.

Registers used:
  CoreDebug_DEMCR  0xE000EDFC  — bit 24 (TRCENA) enables DWT/ITM
  DWT_CTRL         0xE0001000  — bit 0 (CYCCNTENA) enables cycle counter
  DWT_CYCCNT       0xE0001004  — 32-bit free-running cycle count
"""

import asyncio
import struct

from mcp.types import Tool

from dbgprobe_mcp_server.elf import resolve_symbol
from dbgprobe_mcp_server.helpers import _err, _ok
from dbgprobe_mcp_server.state import ProbeState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEMCR = 0xE000EDFC
DWT_CTRL = 0xE0001000
DWT_CYCCNT = 0xE0001004

TRCENA_BIT = 1 << 24
CYCCNTENA = 1 << 0

DEFAULT_CPU_FREQ_MHZ = 64  # nRF52840, STM32F4, etc.

# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------

META = {
    "description": "Cortex-M cycle profiler — measure latency between breakpoints via DWT_CYCCNT",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lookup(session, name):
    if session.elf is None:
        return None
    sym = resolve_symbol(session.elf, name)
    if sym is None:
        return None
    return sym.address, sym.size


async def _read_u32(backend, addr):
    data = await backend.mem_read(addr, 4)
    return struct.unpack("<I", data)[0]


async def _write_u32(backend, addr, val):
    await backend.mem_write(addr, struct.pack("<I", val))


def _resolve(session, target):
    """Resolve a symbol name or '0x...' address string to an integer address."""
    if isinstance(target, int):
        return target
    t = target.strip()
    if t.startswith("0x") or t.startswith("0X"):
        return int(t, 16)
    sym = _lookup(session, t)
    if sym is None:
        raise ValueError(f"Symbol '{t}' not found in ELF")
    return sym[0]


async def _dwt_enable(backend):
    """Enable DWT cycle counter (idempotent)."""
    demcr = await _read_u32(backend, DEMCR)
    if not (demcr & TRCENA_BIT):
        await _write_u32(backend, DEMCR, demcr | TRCENA_BIT)
    ctrl = await _read_u32(backend, DWT_CTRL)
    if not (ctrl & CYCCNTENA):
        await _write_u32(backend, DWT_CTRL, ctrl | CYCCNTENA)


async def _wait_halt(backend, timeout_s, poll_s=0.05):
    """Poll backend.status() until halted or timeout. Returns status dict or None."""
    iterations = int(timeout_s / poll_s)
    for _ in range(iterations):
        await asyncio.sleep(poll_s)
        st = await backend.status()
        if st.get("state") == "halted":
            return st
    return None


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="cortex_m_profiler.dwt_setup",
        description=(
            "Enable the DWT cycle counter (TRCENA + CYCCNTENA). "
            "Idempotent — safe to call multiple times. Returns current CYCCNT value."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Debug probe session ID."},
                "cpu_freq_mhz": {
                    "type": "number",
                    "description": "CPU frequency in MHz for time conversion (default 64).",
                    "default": 64,
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="cortex_m_profiler.dwt_read",
        description=(
            "Read the current DWT_CYCCNT value and convert to microseconds. "
            "Optionally reset the counter to zero."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "reset": {
                    "type": "boolean",
                    "description": "Zero CYCCNT after reading (default false).",
                    "default": False,
                },
                "cpu_freq_mhz": {
                    "type": "number",
                    "description": "CPU frequency in MHz for time conversion (default 64).",
                    "default": 64,
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="cortex_m_profiler.measure",
        description=(
            "Measure CPU cycles between two breakpoint addresses. "
            "Sets a breakpoint at 'start', resumes the target, waits for the hit, "
            "zeros CYCCNT, sets a breakpoint at 'end', resumes, waits, reads CYCCNT. "
            "Accepts ELF symbol names or hex addresses (e.g. '0x00012345'). "
            "Target is left running when done."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "start": {
                    "type": "string",
                    "description": "Start symbol name or hex address.",
                },
                "end": {
                    "type": "string",
                    "description": "End symbol name or hex address.",
                },
                "trigger_symbol": {
                    "type": "string",
                    "description": (
                        "Optional: write 1 to this int32 symbol before resuming "
                        "(e.g. 'tflm_run_preprocess' to trigger a processing cycle)."
                    ),
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Max seconds to wait for each breakpoint (default 5).",
                    "default": 5,
                },
                "cpu_freq_mhz": {
                    "type": "number",
                    "description": "CPU frequency in MHz for time conversion (default 64).",
                    "default": 64,
                },
            },
            "required": ["session_id", "start", "end"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_dwt_setup(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    await _dwt_enable(backend)
    cyccnt = await _read_u32(backend, DWT_CYCCNT)
    freq = float(args.get("cpu_freq_mhz", DEFAULT_CPU_FREQ_MHZ))

    return _ok(
        enabled=True,
        cyccnt=cyccnt,
        time_us=round(cyccnt / freq, 1),
        freq_mhz=freq,
    )


async def handle_dwt_read(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend

    cyccnt = await _read_u32(backend, DWT_CYCCNT)
    freq = float(args.get("cpu_freq_mhz", DEFAULT_CPU_FREQ_MHZ))

    if args.get("reset", False):
        await _write_u32(backend, DWT_CYCCNT, 0)

    return _ok(
        cyccnt=cyccnt,
        time_us=round(cyccnt / freq, 1),
        time_ms=round(cyccnt / freq / 1000, 2),
        freq_mhz=freq,
    )


async def handle_measure(state: ProbeState, args: dict) -> dict:
    session = state.get_session(args["session_id"])
    backend = session.backend
    timeout = float(args.get("timeout_s", 5))

    # Resolve addresses
    try:
        start_addr = _resolve(session, args["start"])
        end_addr = _resolve(session, args["end"])
    except ValueError as e:
        return _err("resolve", str(e))

    # Resolve optional trigger symbol
    trigger_addr = None
    trigger_name = args.get("trigger_symbol")
    if trigger_name:
        try:
            trigger_addr = _resolve(session, trigger_name)
        except ValueError as e:
            return _err("resolve", str(e))

    # Enable DWT
    await _dwt_enable(backend)

    # Halt to set up cleanly
    await backend.halt()

    # Set breakpoint at start
    await backend.set_breakpoint(start_addr, bp_type="hw")

    # Write trigger flag if specified
    if trigger_addr is not None:
        await _write_u32(backend, trigger_addr, 1)

    # Resume and wait for start breakpoint
    await backend.go()

    st = await _wait_halt(backend, timeout)
    if st is None:
        await backend.clear_breakpoint(start_addr)
        await backend.halt()
        return _err("timeout", f"Timed out waiting for start breakpoint at 0x{start_addr:08X}")

    # Zero cycle counter
    await _write_u32(backend, DWT_CYCCNT, 0)

    # Swap breakpoints: clear start, set end
    await backend.clear_breakpoint(start_addr)
    await backend.set_breakpoint(end_addr, bp_type="hw")

    # Resume and wait for end breakpoint
    await backend.go()

    st = await _wait_halt(backend, timeout)
    if st is None:
        await backend.clear_breakpoint(end_addr)
        await backend.halt()
        return _err("timeout", f"Timed out waiting for end breakpoint at 0x{end_addr:08X}")

    # Read cycle count
    cycles = await _read_u32(backend, DWT_CYCCNT)

    # Clean up and resume target so it's ready for the next measurement
    await backend.clear_breakpoint(end_addr)
    await backend.go()

    # If a trigger was used, wait for the firmware to clear the flag before
    # returning.  Otherwise the next measure() could write the trigger while
    # the firmware is about to overwrite it with 0.
    if trigger_addr is not None:
        for _ in range(int(timeout / 0.05)):
            await asyncio.sleep(0.05)
            val = await _read_u32(backend, trigger_addr)
            if val == 0:
                break

    freq = float(args.get("cpu_freq_mhz", DEFAULT_CPU_FREQ_MHZ))
    time_us = cycles / freq

    # Resolve symbol names for display
    start_name = args["start"]
    end_name = args["end"]

    return _ok(
        start=start_name,
        start_addr=f"0x{start_addr:08X}",
        end=end_name,
        end_addr=f"0x{end_addr:08X}",
        cycles=cycles,
        time_us=round(time_us, 1),
        time_ms=round(time_us / 1000, 2),
        freq_mhz=freq,
    )


HANDLERS = {
    "cortex_m_profiler.dwt_setup": handle_dwt_setup,
    "cortex_m_profiler.dwt_read": handle_dwt_read,
    "cortex_m_profiler.measure": handle_measure,
}
