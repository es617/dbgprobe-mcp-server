# Debug Probe MCP Server

<!-- mcp-name: io.github.es617/dbgprobe-mcp-server -->

![MCP](https://img.shields.io/badge/MCP-compatible-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Debug Probe](https://img.shields.io/badge/Debug_Probe-J--Link-green)
<!-- TODO: add badges when backends are implemented -->
<!-- ![Debug Probe](https://img.shields.io/badge/Debug_Probe-OpenOCD-green) -->
<!-- ![Debug Probe](https://img.shields.io/badge/Debug_Probe-pyOCD-green) -->

A stateful debug probe Model Context Protocol (MCP) server for developer tooling and AI agents.
Works out of the box with Claude Code and any MCP-compatible runtime. Communicates over **stdio** and drives on-chip debug probes (J-Link first, OpenOCD and pyOCD planned) to flash, debug, and inspect embedded targets.

> **Example:** Let Claude Code list attached J-Link probes, connect to your nRF52840, flash a new firmware, read memory, and reset the target — all conversationally.

---

## Why this exists

If you've ever typed J-Link Commander commands by hand, copy-pasted memory addresses between a datasheet and a terminal, re-flashed the same firmware 20 times during a debug session, and juggled multiple tool windows — this is for you.

You have a microcontroller on a debug probe. You want an AI agent to interact with it — connect, flash firmware, read/write memory, reset, halt, resume. This server makes that possible.

It gives any MCP-compatible agent a full set of debug probe tools. The agent calls these tools, gets structured JSON back, and reasons about what to do next — without you manually driving JLinkExe for every operation.

**What agents can do with it:**

- **Flash and iterate** — build firmware, flash it, reset, check behavior — all in one conversation
- **Inspect memory** — read peripheral registers, check RAM contents, verify flash writes
- **Debug interactively** — halt, step, set breakpoints, inspect state, resume
- **Automate test flows** — flash → reset → read output → validate
- **Multi-probe setups** — connect to multiple probes simultaneously, each with its own session

---

## Who is this for?

- **Embedded engineers** — faster iteration: flash, debug, inspect memory conversationally
- **Hobbyists and makers** — interact with microcontrollers without learning JLinkExe command syntax
- **QA and test engineers** — automated flash-and-test sequences across multiple boards
- **Researchers** — systematic exploration of embedded systems, register inspection

---

## Quickstart (Claude Code)

```bash
pip install dbgprobe-mcp-server

# Register the MCP server with Claude Code
claude mcp add dbgprobe -- dbgprobe_mcp

# Or with explicit J-Link path
claude mcp add dbgprobe \
  -e DBGPROBE_JLINK_PATH=/Applications/SEGGER/JLink/JLinkExe \
  -- dbgprobe_mcp
```

Then in Claude Code, try:

> "List attached debug probes, connect to the J-Link, and read 16 bytes from address 0x20000000."

---

## Supported backends

| Backend | Status | Probe hardware |
|---|---|---|
| **J-Link** | Working (v0) | SEGGER J-Link (EDU, EDU Mini, PLUS, PRO, etc.) |
| **OpenOCD** | Planned | ST-Link, CMSIS-DAP, and many others |
| **pyOCD** | Planned | CMSIS-DAP, ST-Link, J-Link (via pyOCD) |

The server is backend-agnostic — tool names (`dbgprobe.*`) stay the same regardless of which probe you use.

### J-Link requirements

Install the [SEGGER J-Link Software Pack](https://www.segger.com/downloads/jlink/). The server auto-detects `JLinkExe` on PATH or in common install locations:

- **macOS:** `/Applications/SEGGER/JLink/`
- **Linux:** `/opt/SEGGER/JLink/`, `/usr/bin/`
- **Windows:** `C:\Program Files\SEGGER\JLink\`

Or set `DBGPROBE_JLINK_PATH` to point to the executable directly.

---

## Tools

| Category | Tools |
|---|---|
| **Probe** | `dbgprobe.list_probes`, `dbgprobe.connect`, `dbgprobe.erase`, `dbgprobe.disconnect`, `dbgprobe.reset`, `dbgprobe.halt`, `dbgprobe.go`, `dbgprobe.step`, `dbgprobe.status`, `dbgprobe.flash`, `dbgprobe.mem.read`, `dbgprobe.mem.write`, `dbgprobe.breakpoint.set`, `dbgprobe.breakpoint.clear`, `dbgprobe.breakpoint.list` |
| **Introspection** | `dbgprobe.connections.list` |
| **Protocol Specs** | `dbgprobe.spec.template`, `dbgprobe.spec.register`, `dbgprobe.spec.list`, `dbgprobe.spec.attach`, `dbgprobe.spec.get`, `dbgprobe.spec.read`, `dbgprobe.spec.search` |
| **Tracing** | `dbgprobe.trace.status`, `dbgprobe.trace.tail` |
| **Plugins** | `dbgprobe.plugin.template`, `dbgprobe.plugin.list`, `dbgprobe.plugin.reload`, `dbgprobe.plugin.load` |

See [docs/tools.md](docs/tools.md) for full schemas and examples.

---

## Install (development)

```bash
# Editable install from repo root
pip install -e ".[test]"

# Or with uv
uv pip install -e ".[test]"
```

## Add to Claude Code

```bash
# Standard setup
claude mcp add dbgprobe -- dbgprobe_mcp

# With default target device
claude mcp add dbgprobe \
  -e DBGPROBE_JLINK_DEVICE=nRF52840_xxAA \
  -- dbgprobe_mcp

# Enable plugins
claude mcp add dbgprobe -e DBGPROBE_MCP_PLUGINS=all -- dbgprobe_mcp

# Debug logging
claude mcp add dbgprobe -e DBGPROBE_LOG_LEVEL=DEBUG -- dbgprobe_mcp
```

> MCP is a protocol. Claude Code is one MCP client; other agent runtimes can also connect to this server.

## Environment variables

### Server

| Variable | Default | Description |
|---|---|---|
| `DBGPROBE_BACKEND` | `jlink` | Debug probe backend. Future: `openocd`, `pyocd`. |
| `DBGPROBE_LOG_LEVEL` | `WARNING` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Logs go to stderr. |
| `DBGPROBE_MCP_PLUGINS` | disabled | Plugin policy: `all` to allow all, or `name1,name2` to allow specific plugins. |
| `DBGPROBE_MCP_TRACE` | enabled | JSONL tracing of every tool call. Set to `0`, `false`, or `no` to disable. |
| `DBGPROBE_MCP_TRACE_PAYLOADS` | disabled | Include memory data payloads in traced args (stripped by default). |
| `DBGPROBE_MCP_TRACE_MAX_BYTES` | `16384` | Max payload chars before truncation (only when `TRACE_PAYLOADS` is on). |

### J-Link backend

| Variable | Default | Description |
|---|---|---|
| `DBGPROBE_JLINK_PATH` | auto-detect | Explicit path to `JLinkExe` (or `JLink.exe` on Windows). |
| `DBGPROBE_JLINK_GDBSERVER_PATH` | auto-detect | Explicit path to `JLinkGDBServerCLExe`. |
| `DBGPROBE_JLINK_RTTCLIENT_PATH` | auto-detect | Explicit path to `JLinkRTTClient`. |
| `DBGPROBE_JLINK_DEVICE` | *(none)* | Default target device string (e.g. `nRF52840_xxAA`). Can be overridden per-session. |
| `DBGPROBE_INTERFACE` | `SWD` | Debug interface: `SWD` or `JTAG`. |
| `DBGPROBE_SPEED_KHZ` | `4000` | Interface clock speed in kHz. |

---

## Protocol Specs

Specs are markdown files that describe a target device's debug protocol — register maps, memory layout, boot sequences, and multi-step flows. They live in `.dbgprobe_mcp/specs/` and teach the agent what the hardware can do.

Without a spec, the agent can still connect and interact with the probe. With a spec, it knows what memory regions matter, what register values mean, and how to perform device-specific operations.

---

## Plugins

Plugins add device-specific shortcut tools to the server. Instead of the agent composing raw mem.read/mem.write sequences, a plugin provides high-level operations like `nrf52.read_ficr` or `stm32.unlock_flash`.

To enable plugins:

```bash
claude mcp add dbgprobe -e DBGPROBE_MCP_PLUGINS=all -- dbgprobe_mcp
```

---

## Tracing

Every tool call is traced to `.dbgprobe_mcp/traces/trace.jsonl` and an in-memory ring buffer (last 2000 events). Tracing is **on by default** — set `DBGPROBE_MCP_TRACE=0` to disable.

Use `dbgprobe.trace.status` and `dbgprobe.trace.tail` to inspect the trace without reading the file directly.

---

## Try without an agent

You can test the server interactively using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector python -m dbgprobe_mcp_server
```

---

## Roadmap / TODO

- [ ] **OpenOCD backend** — support ST-Link, CMSIS-DAP, and other probes via OpenOCD subprocess
- [ ] **pyOCD backend** — native Python probe access via pyOCD library
- [ ] **RTT support** — Real-Time Transfer (read target output via persistent debug connection)
- [ ] **Register-level tools** — named peripheral register read/write using SVD files
- [x] **Breakpoint support** — hardware and software breakpoints via GDB RSP
- [x] **GDB integration** — persistent JLinkGDBServer connection with GDB Remote Serial Protocol
- [ ] **Multi-core support** — target specific cores on multi-core SoCs
- [ ] **Cortex-A/R support** — ARM-mode breakpoints (`kind=4`); currently Thumb-only (Cortex-M)
---

## Known limitations

- **Single-client only.** The server handles one MCP session at a time (stdio transport).
- **No RTT yet.** RTT (Real-Time Transfer) is planned for a future version.
- **Flash clears breakpoints.** Flashing new firmware invalidates breakpoints (the code at those addresses may have changed). The session stays alive but breakpoints are cleared.
- **Cortex-M only.** Breakpoints use Thumb-mode (`kind=2`). Cortex-A/R targets (ARM-mode, `kind=4`) are not yet supported.

---

## Safety

This server connects an AI agent to real debug hardware. That's the point — and it means the stakes are higher than pure-software tools.

**Plugins execute arbitrary code.** When plugins are enabled, the agent can create and run Python code on your machine with full server privileges. Review agent-generated plugins before loading them.

**Writes affect real hardware.** A bad memory write or flash operation can brick a device, wipe calibration data, or trigger unintended behavior. Consider what the agent can reach.

**Use tool approval deliberately.** When your MCP client prompts you to approve a tool call, consider whether you want to allow it once or always.

This software is provided as-is under the MIT License. You are responsible for what the agent does with your hardware.

---

## License

This project is licensed under the MIT License — see [LICENSE](https://github.com/es617/dbgprobe-mcp-server/blob/main/LICENSE) for details.
