# Changelog

## 0.3.0

- **Persistent JLinkGDBServer connection** — `dbgprobe.connect` now starts a persistent JLinkGDBServer subprocess and communicates via GDB Remote Serial Protocol over TCP, replacing one-shot JLinkExe calls for session operations
- **New tool: `dbgprobe.step`** — single-step one instruction, returns PC and stop reason
- **New tool: `dbgprobe.status`** — query target state (running/halted, PC, stop reason)
- **New tool: `dbgprobe.breakpoint.set`** — set hardware or software breakpoints
- **New tool: `dbgprobe.breakpoint.clear`** — clear breakpoints by address
- **New tool: `dbgprobe.breakpoint.list`** — list active breakpoints for a session
- **GDB RSP client** — new async `GdbClient` class for GDB Remote Serial Protocol over TCP (zero dependencies beyond asyncio)
- **Flash teardown/reconnect** — `dbgprobe.flash` tears down GDB, flashes via JLinkExe (all file formats supported), and reconnects GDBServer automatically
- **Breakpoint tracking** — breakpoints are tracked per session and cleared on flash
- 15 probe tools total (up from 10)

## 0.2.0

- **New tool: `dbgprobe.erase`** — mass-erase the target chip, unlocking secured/read-protected devices (e.g. Nordic APPROTECT)
- **Secured device detection** — `dbgprobe.connect` now returns `device_secured` error code (instead of generic `connect_failed`) when the target is secured
- **`DeviceSecuredError` exception** — distinguishes secured devices from other connection errors
- **Suppress J-Link GUI dialogs** — added `-NoGui 1` to prevent JLinkExe from showing blocking popups on secured devices

## 0.1.0

Initial release.

- Backend-agnostic architecture with `Backend` ABC and `BackendRegistry`
- **J-Link backend** — drives targets via `JLinkExe` subprocess calls
  - Auto-detection of J-Link executables on macOS, Linux, and Windows
  - Explicit path configuration via `DBGPROBE_JLINK_PATH` env var
  - Probe enumeration via `ShowEmuList`
  - Connect with config resolution (device, interface, speed, probe serial)
- **10 MCP tools:**
  - `dbgprobe.list_probes` — enumerate attached probes
  - `dbgprobe.connect` / `dbgprobe.disconnect` — session management
  - `dbgprobe.reset` — soft, hard, and halt reset modes
  - `dbgprobe.halt` / `dbgprobe.go` — CPU execution control
  - `dbgprobe.flash` — program .hex, .elf, and .bin firmware files
  - `dbgprobe.mem.read` / `dbgprobe.mem.write` — memory access (hex, base64, u32 formats)
- Session-based state management with timestamps and backend instances
- Protocol spec system (markdown + YAML front-matter)
- Plugin system with hot-reload and policy enforcement
- JSONL tracing with in-memory ring buffer and file sink
- Environment variable configuration for backend, device, interface, speed
- Structured JSON responses (`ok`/`error` pattern)
- stdio MCP transport
