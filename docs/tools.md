# Tools Reference

All tools return structured JSON:
`{ "ok": true, ... }` on success,
`{ "ok": false, "error": { "code": "...", "message": "..." } }` on failure.

---

## Probe

### dbgprobe.list_probes

List attached debug probes. Returns vendor/backend-specific info (serial number, description).

```json
{ "backend": "jlink" }
```

`backend` is optional (defaults to `DBGPROBE_BACKEND` env var, typically `jlink`).

Returns:

```json
{
  "ok": true,
  "probes": [{
    "serial": "683456789",
    "description": "J-Link EDU Mini",
    "backend": "jlink"
  }],
  "count": 1,
  "backend": "jlink"
}
```

### dbgprobe.connect

Establish a debug probe session. Returns a `session_id` and the resolved configuration (backend, executable paths, defaults applied). The target is halted after connecting — this is inherent to the debug probe connection. Use `dbgprobe.go` to resume execution if needed.

```json
{
  "backend": "jlink",
  "probe_id": "683456789",
  "device": "nRF52840_xxAA",
  "interface": "swd",
  "speed_khz": 4000
}
```

All parameters are optional. Defaults come from environment variables.

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "config": {
    "backend": "jlink",
    "device": "nRF52840_xxAA",
    "interface": "SWD",
    "speed_khz": 4000,
    "probe_serial": null
  },
  "resolved_paths": {
    "jlink_exe": "/Applications/SEGGER/JLink/JLinkExe",
    "jlink_gdbserver": "/Applications/SEGGER/JLink/JLinkGDBServerCLExe",
    "jlink_rttclient": "/Applications/SEGGER/JLink/JLinkRTTClient"
  }
}
```

### dbgprobe.erase

Erase target flash. With no address params: full chip erase (unlocks secured/read-protected devices like Nordic APPROTECT). With `start_addr` and `end_addr`: erase only that range. Does not require a session.

Full chip erase:

```json
{
  "device": "nRF52840_xxAA"
}
```

Range erase:

```json
{
  "device": "nRF52840_xxAA",
  "start_addr": 262144,
  "end_addr": 524288
}
```

All parameters are optional. Defaults come from environment variables. `start_addr` and `end_addr` must both be provided for a range erase.

Returns (full erase):

```json
{
  "ok": true,
  "erased": true,
  "config": {
    "backend": "jlink",
    "device": "nRF52840_xxAA",
    "interface": "SWD",
    "speed_khz": 4000,
    "probe_serial": null
  }
}
```

Returns (range erase):

```json
{
  "ok": true,
  "erased": true,
  "start_addr": 262144,
  "end_addr": 524288,
  "config": { "..." : "..." }
}
```

### dbgprobe.disconnect

Close a debug probe session and release resources.

```json
{ "session_id": "p1a2b3c4" }
```

### dbgprobe.reset

Reset the target.

```json
{ "session_id": "p1a2b3c4", "mode": "soft" }
```

| Mode | Behavior |
|---|---|
| `soft` | Software reset and resume (default) |
| `hard` | Hardware reset |
| `halt` | Reset and halt at first instruction |

### dbgprobe.halt

Halt the target CPU.

```json
{ "session_id": "p1a2b3c4" }
```

### dbgprobe.go

Resume target execution.

```json
{ "session_id": "p1a2b3c4" }
```

### dbgprobe.flash

Program a firmware image to the target.

```json
{
  "session_id": "p1a2b3c4",
  "path": "/path/to/firmware.hex",
  "addr": null,
  "verify": true,
  "reset_after": true
}
```

- `.hex` and `.elf` files: address is auto-detected from the file
- `.bin` files: `addr` is **required** (e.g. `0x08000000`)
- `verify`: verify flash contents after programming (default: true)
- `reset_after`: reset and run after programming (default: true)

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "file": "/path/to/firmware.hex",
  "verified": true,
  "reset": true
}
```

### dbgprobe.mem.read

Read memory from the target.

```json
{
  "session_id": "p1a2b3c4",
  "address": 536870912,
  "length": 16,
  "format": "hex"
}
```

`format` options:
- `hex` (default) — hex string, e.g. `"deadbeef01020304"`
- `base64` — base64-encoded bytes
- `u32` — array of 32-bit unsigned integers (little-endian)

Returns (hex format):

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "address": 536870912,
  "length": 16,
  "format": "hex",
  "data": "deadbeef0102030405060708090a0b0c"
}
```

Returns (u32 format):

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "address": 536870912,
  "length": 16,
  "format": "u32",
  "data": [3735928559, 67305985, 134678021, 201950253]
}
```

### dbgprobe.mem.write

Write data to target memory.

```json
{
  "session_id": "p1a2b3c4",
  "address": 536870912,
  "data": "deadbeef",
  "format": "hex"
}
```

`format` options:
- `hex` (default) — provide `data` as hex string
- `base64` — provide `data` as base64 string
- `u32` — provide `data_u32` as array of 32-bit unsigned integers

Example with u32:

```json
{
  "session_id": "p1a2b3c4",
  "address": 536870912,
  "data_u32": [3735928559, 3405691582],
  "format": "u32"
}
```

### dbgprobe.step

Single-step one instruction. Target must be halted first.

```json
{ "session_id": "p1a2b3c4" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "pc": 134218000,
  "reason": "breakpoint",
  "signal": 5
}
```

### dbgprobe.status

Query target state — running or halted.

```json
{ "session_id": "p1a2b3c4" }
```

Returns (halted):

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "state": "halted",
  "pc": 134218000,
  "reason": "breakpoint",
  "signal": 5
}
```

Returns (running):

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "state": "running"
}
```

### dbgprobe.breakpoint.set

Set a breakpoint at a target address.

```json
{
  "session_id": "p1a2b3c4",
  "address": 134218000,
  "bp_type": "sw"
}
```

`bp_type` is optional (default: `sw`). Options:
- `sw` — software breakpoint (default). Handled by the debug probe, works on both flash and RAM.
- `hw` — hardware breakpoint. Uses the CPU's FPB registers (limited to 4-6 slots on Cortex-M).

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "address": 134218000,
  "bp_type": "sw"
}
```

### dbgprobe.breakpoint.clear

Clear a breakpoint at a target address.

```json
{
  "session_id": "p1a2b3c4",
  "address": 134218000
}
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "address": 134218000
}
```

### dbgprobe.breakpoint.list

List all active breakpoints for a session.

```json
{ "session_id": "p1a2b3c4" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "breakpoints": [
    { "address": 134218000, "bp_type": "hw" },
    { "address": 536870912, "bp_type": "sw" }
  ],
  "count": 2
}
```

---

## Introspection

### dbgprobe.connections.list

List all open probe sessions with their status, backend, device, and config. Useful for recovering session IDs after context loss.

```json
{}
```

Returns:

```json
{
  "ok": true,
  "message": "1 session(s).",
  "sessions": [{
    "session_id": "p1a2b3c4",
    "backend": "jlink",
    "created_at": 1700000000.0,
    "device": "nRF52840_xxAA",
    "interface": "SWD",
    "speed_khz": 4000,
    "probe_serial": null
  }],
  "count": 1
}
```

When a spec is attached, the session includes `"spec": { "spec_id": "...", "name": "..." }`.

---

## Protocol Specs

Tools for managing target device protocol specs. Specs are markdown files with YAML front-matter stored in `.dbgprobe_mcp/specs/`.

### dbgprobe.spec.template

Return a markdown template for a new debug probe protocol spec.

```json
{ "device_name": "nRF52840" }
```

Returns `{ "ok": true, "template": "---\nkind: dbgprobe-protocol\n...", "suggested_path": ".dbgprobe_mcp/specs/nrf52840.md" }`.

### dbgprobe.spec.register

Register a spec file in the index. Validates YAML front-matter (requires `kind: dbgprobe-protocol` and `name`). The path must be inside the project directory.

```json
{ "path": ".dbgprobe_mcp/specs/nrf52840.md" }
```

### dbgprobe.spec.list

List all registered specs with their metadata.

```json
{}
```

### dbgprobe.spec.attach

Attach a registered spec to a session (in-memory only). The spec will be available via `dbgprobe.spec.get` for the duration of the session.

```json
{ "connection_id": "p1a2b3c4", "spec_id": "a1b2c3d4e5f67890" }
```

### dbgprobe.spec.get

Get the attached spec for a session (returns `null` if none attached).

```json
{ "connection_id": "p1a2b3c4" }
```

### dbgprobe.spec.read

Read full spec content, file path, and metadata by spec_id.

```json
{ "spec_id": "a1b2c3d4e5f67890" }
```

### dbgprobe.spec.search

Full-text search over a spec's content. Returns matching snippets with line numbers and context.

```json
{ "spec_id": "a1b2c3d4e5f67890", "query": "reset vector", "k": 10 }
```

---

## ELF

Tools for attaching ELF files to sessions, enabling symbol-based breakpoints and address-to-symbol resolution.

### dbgprobe.elf.attach

Parse an ELF file and attach it to a session. Enables symbol-based breakpoints (by function name), address-to-symbol resolution in status/step/halt responses, and symbol search. Re-attaching replaces the previous ELF.

```json
{ "session_id": "p1a2b3c4", "path": "/path/to/build/zephyr/zephyr.elf" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "path": "/path/to/build/zephyr/zephyr.elf",
  "entry_point": 134217728,
  "symbol_count": 1234,
  "function_count": 567,
  "sections": [
    { "name": ".text", "address": 134217984, "size": 65536, "type": "SHT_PROGBITS" }
  ]
}
```

### dbgprobe.elf.info

Get ELF metadata for a session. Returns `null` if no ELF is attached.

```json
{ "session_id": "p1a2b3c4" }
```

Returns (attached):

```json
{
  "ok": true,
  "elf": {
    "path": "/path/to/zephyr.elf",
    "entry_point": 134217728,
    "symbol_count": 1234,
    "function_count": 567,
    "sections": []
  }
}
```

Returns (not attached):

```json
{ "ok": true, "elf": null }
```

### dbgprobe.elf.lookup

Bidirectional symbol lookup. Provide `symbol` for name-to-address, or `address` for address-to-name+offset. Exactly one is required.

Name to address:

```json
{ "session_id": "p1a2b3c4", "symbol": "main" }
```

Returns:

```json
{
  "ok": true,
  "symbol": "main",
  "address": 134218000,
  "size": 42,
  "type": "FUNC"
}
```

Address to name:

```json
{ "session_id": "p1a2b3c4", "address": 134218006 }
```

Returns:

```json
{
  "ok": true,
  "address": 134218006,
  "symbol": "main",
  "symbol_offset": 6
}
```

### dbgprobe.elf.symbols

Search or list ELF symbols. Optional substring filter, optional type filter, default limit 50.

```json
{ "session_id": "p1a2b3c4", "filter": "main", "type": "FUNC", "limit": 10 }
```

Returns:

```json
{
  "ok": true,
  "symbols": [
    { "name": "main", "address": 134218000, "size": 42, "type": "FUNC" }
  ],
  "count": 1
}
```

### Response enrichment

When an ELF is attached, `dbgprobe.status`, `dbgprobe.step`, and `dbgprobe.halt` automatically include symbol context:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "state": "halted",
  "pc": 134218006,
  "symbol": "main",
  "symbol_offset": 6
}
```

### Breakpoint by symbol

`dbgprobe.breakpoint.set` accepts an optional `symbol` parameter. When provided (and an ELF is attached), the symbol is resolved to an address:

```json
{ "session_id": "p1a2b3c4", "symbol": "main" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "address": 134218000,
  "bp_type": "sw",
  "symbol": "main"
}
```

### Flash ELF handling

After `dbgprobe.flash`, the response may include:

- `"elf_reloaded": true` — attached ELF was re-parsed (symbols updated)
- `"elf_detached": true` — ELF file was missing, attachment removed
- `"elf_hint": "/path/to/zephyr.elf"` — sibling `.elf` found near the flashed file

---

## Tracing

Tools for inspecting the JSONL trace log. Tracing is enabled by default and records every tool call.

### dbgprobe.trace.status

Return tracing config and event count.

```json
{}
```

Returns `{ "ok": true, "enabled": true, "event_count": 42, "file_path": ".dbgprobe_mcp/traces/trace.jsonl", "payloads_logged": false, "max_payload_bytes": 16384 }`.

### dbgprobe.trace.tail

Return last N trace events (default 50).

```json
{ "n": 20 }
```

---

## Plugins

Tools for managing user plugins. Plugins live in `.dbgprobe_mcp/plugins/` and add device-specific tools without modifying the core server. Requires `DBGPROBE_MCP_PLUGINS` env var to be set.

### dbgprobe.plugin.template

Return a Python plugin template. Optionally pre-fill with a device name.

```json
{ "device_name": "nRF52840" }
```

### dbgprobe.plugin.list

List loaded plugins with their tool names and metadata.

```json
{}
```

### dbgprobe.plugin.reload

Hot-reload a plugin by name. Re-imports the module and refreshes tools.

```json
{ "name": "nrf52" }
```

### dbgprobe.plugin.load

Load a new plugin from a file or directory path. The path must be inside `.dbgprobe_mcp/plugins/`.

```json
{ "path": ".dbgprobe_mcp/plugins/nrf52.py" }
```
