# Changelog

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
