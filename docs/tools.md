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

Establish a debug probe session. Returns a `session_id` and the resolved configuration (backend, executable paths, defaults applied).

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
