# Changelog

## 0.1.3

### Fixed
- Fix GDB protocol desynchronization caused by unsolicited T/S stop replies being routed to the wrong event during memory read/write exchanges. This caused connection drops after ~65 sustained `mem_read`/`mem_write` cycles. Added `expect_stop` flag to `send_packet()` so T/S replies are only treated as command responses when explicitly expected (`?`, `s`, `vCont`), and are otherwise routed to `_stop_event`. Also fixed `halt()` losing already-arrived stop replies.

## 0.1.2

### Fixed
- Raise minimum `mcp` SDK dependency to >=1.23.0 to exclude versions with known CVEs (CVE-2025-53366, CVE-2025-53365, CVE-2025-66416). These affect HTTP/SSE transport only — stdio servers were never vulnerable — but the wider range allowed scanners to flag the package.

## 0.1.1

### Added
- VS Code / Copilot setup instructions in README (`.vscode/mcp.json`)
- Cursor setup instructions in README (`.cursor/mcp.json`)
- `DBGPROBE_MCP_TOOL_SEPARATOR` env var — configurable separator for tool names (default `.`). Set to `_` for MCP clients that reject dots in tool names (e.g. Cursor).

## 0.1.0

Initial release.

- Backend-agnostic architecture with J-Link backend (OpenOCD and pyOCD planned)
- 15 MCP tools: probe enumeration, connect/disconnect, flash, erase, memory read/write, halt/go/step/reset, breakpoints, status
- Persistent JLinkGDBServer connection via GDB Remote Serial Protocol
- ELF support: symbol lookup, breakpoints by name, auto-enriched responses
- SVD support: named register reads/writes, field-level access, batch updates
- RTT (Real-Time Transfer): start/stop/read/write on channel 0
- Protocol spec system (markdown + YAML front-matter)
- Plugin system with hot-reload and policy enforcement
- JSONL tracing with in-memory ring buffer and file sink
- stdio MCP transport
