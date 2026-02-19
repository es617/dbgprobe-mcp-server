# Changelog

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
