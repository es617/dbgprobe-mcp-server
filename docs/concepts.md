# Concepts

How the Debug Probe MCP server works, and how the pieces fit together.

---

## Architecture

The server gives an AI agent (like Claude) a set of debug probe tools over the MCP protocol. The agent uses these tools to interact with embedded targets — listing probes, connecting, flashing firmware, reading/writing memory, setting breakpoints, and controlling execution.

Everything is **stateful**: sessions persist across tool calls. The agent doesn't have to reconnect between each operation.

```
┌─────────────┐       stdio/MCP        ┌─────────────────────┐  GDB RSP (TCP)  ┌────────────────┐   USB    ┌──────────┐
│  AI Agent   │ ◄────────────────────► │  Debug Probe MCP    │ ◄──────────────► │ JLinkGDBServer │ ◄──────► │          │  SWD/JTAG  ┌────────┐
│ (Claude etc)│   structured JSON      │  Server             │   persistent     │ (persistent)   │          │  J-Link  │ ◄────────► │ Target │
└─────────────┘                        │                     │                  └────────────────┘          │  Probe   │            │  MCU   │
                                       │                     │  subprocess       ┌────────────────┐          │          │            └────────┘
                                       │                     │ ◄──────────────► │ JLinkExe       │ ◄──────► │          │
                                       └─────────────────────┘  one-shot        │ (flash/erase)  │   USB    └──────────┘
                                                                                └────────────────┘
```

### Hybrid approach: GDBServer + JLinkExe

The J-Link backend uses two SEGGER tools concurrently:

- **JLinkGDBServer** — persistent process, speaks GDB Remote Serial Protocol over TCP. Handles all session operations: halt, go, step, memory read/write, breakpoints, reset. Gives low-latency access through an open TCP socket.

- **JLinkExe** (Commander) — one-shot subprocess for flash and erase. Supports all firmware formats (.hex, .elf, .bin) natively. Also used for `list_probes` and session-less erase/flash.

For session-based flash, the server tears down the GDB connection, flashes via JLinkExe, and restarts GDB automatically. Session-based erase uses `monitor flash erase` through GDB (no USB contention). Session-less operations use JLinkExe directly.

### Backend abstraction

The server is backend-agnostic. A `Backend` abstract class defines the interface that all probe backends implement:

```
Backend (ABC)
  ├── JLinkBackend    ← implemented (hybrid GDBServer + JLinkExe)
  ├── OpenOCDBackend  ← planned
  └── PyOCDBackend    ← planned
```

Tool names (`dbgprobe.*`) stay the same regardless of backend. The backend is selected via `DBGPROBE_BACKEND` env var or the `backend` argument to `dbgprobe.connect`.

### Sessions

A session represents a connection to a specific probe + target combination. Created by `dbgprobe.connect`, it stores:

- The backend instance (e.g. JLinkBackend with live GDBServer + GDB client)
- Resolved configuration (device, interface, speed, probe serial)
- Active breakpoints (tracked in session state)
- Optional attached ELF (symbol table for address↔name resolution)
- Timestamps

Multiple sessions can be open simultaneously (up to 10 by default). Each session gets its own GDBServer process and TCP port.

---

## The agent's workflow

After connecting, the agent typically:

```
Connect to probe
       │
       ├──► dbgprobe.elf.attach (if ELF path known — enables symbol breakpoints,
       │                          address→symbol enrichment in status/step/halt)
       │
       ▼
  Use debug probe tools:
  halt, go, step, status,
  mem.read, mem.write,
  breakpoint.set, flash, etc.
```

The tool descriptions guide it through each step. When flashing an `.elf` file with a session, the ELF is auto-attached — no explicit `elf.attach` needed.

---

## J-Link executable auto-detection

The server searches for J-Link executables in this order:

1. **Environment variable** — `DBGPROBE_JLINK_PATH` (or `_GDBSERVER_PATH`, `_RTTCLIENT_PATH`)
2. **PATH** — `shutil.which()` search
3. **Common install directories** — platform-specific locations

| Platform | Search directories |
|---|---|
| macOS | `/Applications/SEGGER/JLink/`, `~/Applications/SEGGER/JLink/` |
| Linux | `/opt/SEGGER/JLink/`, `/usr/bin/`, `/usr/local/bin/` |
| Windows | `C:\Program Files\SEGGER\JLink\`, `C:\Program Files (x86)\SEGGER\JLink\` |

The resolved paths are included in the `dbgprobe.connect` response so the agent (and user) can verify which executables are being used.
