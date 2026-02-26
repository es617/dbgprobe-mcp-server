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

Erase target flash. With no address params: full chip erase (unlocks secured/read-protected devices like Nordic APPROTECT). With `start_addr` and `end_addr`: erase only that range.

Two modes:

- **Session-based** (preferred) — provide `session_id`. Erases through the active GDB session via `monitor flash erase`. No USB contention.
- **Session-less** — omit `session_id`. Uses JLinkExe directly. For unlocking secured devices before connect, etc.

Session-based full erase:

```json
{
  "session_id": "p1a2b3c4"
}
```

Session-less full erase:

```json
{
  "device": "nRF52840_xxAA"
}
```

Range erase (addresses accept integers or hex strings):

```json
{
  "session_id": "p1a2b3c4",
  "start_addr": "0x40000",
  "end_addr": "0x80000"
}
```

`start_addr` and `end_addr` must both be provided for a range erase.

Returns (session-based):

```json
{
  "ok": true,
  "erased": true,
  "session_id": "p1a2b3c4"
}
```

Returns (session-less):

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

Two modes:

- **Session-based** (preferred) — provide `session_id`. Tears down GDB, flashes via JLinkExe, and restarts GDB automatically.
- **Session-less** — omit `session_id`. Uses JLinkExe directly. Requires `device` (and optionally `probe_id`).

Session-based:

```json
{
  "session_id": "p1a2b3c4",
  "path": "/path/to/firmware.hex",
  "verify": true,
  "reset_after": true
}
```

Session-less:

```json
{
  "path": "/path/to/firmware.hex",
  "device": "nRF52840_xxAA"
}
```

- `.hex` and `.elf` files: address is auto-detected from the file
- `.bin` files: `addr` is **required** (e.g. `"0x08000000"` or `134217728`)
- `verify`: verify flash contents after programming (default: true)
- `reset_after`: reset and run after programming (default: true)

**ELF auto-attach:** When flashing an `.elf` file with a session, the ELF is automatically parsed and attached (or updated if one was already attached). No manual `elf.attach` needed.

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "file": "/path/to/firmware.hex",
  "verified": true,
  "reset": true,
  "elf_hint": "/path/to/zephyr.elf"
}
```

Additional response fields (when applicable):

- `"elf_attached": true` — ELF auto-attached from flashed `.elf` file (no prior ELF)
- `"elf_reloaded": true` — existing ELF re-parsed (symbols updated)
- `"elf_detached": true` — ELF file missing, attachment removed
- `"elf_hint": "/path/to.elf"` — sibling `.elf` found near the flashed file

### dbgprobe.mem.read

Read memory from the target.

```json
{
  "session_id": "p1a2b3c4",
  "address": "0x20000000",
  "length": 16,
  "format": "hex"
}
```

Addresses accept integers or hex strings (e.g. `536870912` or `"0x20000000"`).

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
  "address": "0x20000000",
  "data": "deadbeef",
  "format": "hex"
}
```

Addresses accept integers or hex strings.

`format` options:
- `hex` (default) — provide `data` as hex string
- `base64` — provide `data` as base64 string
- `u32` — provide `data_u32` as array of 32-bit unsigned integers

Example with u32:

```json
{
  "session_id": "p1a2b3c4",
  "address": "0x20000000",
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

Set a breakpoint at a target address or symbol name.

By address (accepts integers or hex strings):

```json
{
  "session_id": "p1a2b3c4",
  "address": "0x08000100",
  "bp_type": "sw"
}
```

By symbol (requires ELF attached):

```json
{
  "session_id": "p1a2b3c4",
  "symbol": "main"
}
```

Provide `address` or `symbol`, not both. `bp_type` is optional (default: `sw`). Options:
- `sw` — software breakpoint (default). Handled by the debug probe, works on both flash and RAM.
- `hw` — hardware breakpoint. Uses the CPU's FPB registers (limited to 4-6 slots on Cortex-M).

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

`symbol` is included in the response only when set by symbol name.

### dbgprobe.breakpoint.clear

Clear a breakpoint at a target address (accepts integers or hex strings).

```json
{
  "session_id": "p1a2b3c4",
  "address": "0x08000100"
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

After `dbgprobe.flash` (session-based), the response may include:

- `"elf_attached": true` — flashed `.elf` file was auto-attached (no prior ELF)
- `"elf_reloaded": true` — existing ELF re-parsed with updated symbols (either from flashed `.elf` or re-parsed from previously attached path)
- `"elf_detached": true` — ELF file was missing, attachment removed
- `"elf_hint": "/path/to/zephyr.elf"` — sibling `.elf` found near the flashed file

---

## SVD

Tools for register-level peripheral access using SVD (System View Description) files. Attach an SVD file to enable named register reads/writes, field-level access with enum names, and automatic decode on `mem.read`.

### dbgprobe.svd.attach

Parse an SVD file and attach it to a session. Enables named register reads/writes, field-level access, and auto-decode on `mem.read`. Re-attaching replaces the previous SVD.

```json
{ "session_id": "p1a2b3c4", "path": "/path/to/nrf52840.svd" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "path": "/path/to/nrf52840.svd",
  "device_name": "nRF52840",
  "peripheral_count": 52,
  "register_count": 1234
}
```

### dbgprobe.svd.info

Get SVD metadata for a session. Returns `null` if no SVD is attached.

```json
{ "session_id": "p1a2b3c4" }
```

Returns (attached):

```json
{
  "ok": true,
  "svd": {
    "path": "/path/to/nrf52840.svd",
    "device_name": "nRF52840",
    "peripheral_count": 52,
    "register_count": 1234
  }
}
```

Returns (not attached):

```json
{ "ok": true, "svd": null }
```

### dbgprobe.svd.read

Read a register or field by name. For registers, returns the raw value and all decoded fields. For fields, returns just the field value and enum name.

Register read:

```json
{ "session_id": "p1a2b3c4", "target": "GPIO.OUT" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "target": "GPIO.OUT",
  "address": 1342179588,
  "raw": 3,
  "fields": {
    "PIN0": { "value": 1, "bit_range": "[0:0]", "enum": "High" },
    "PIN1": { "value": 1, "bit_range": "[1:1]", "enum": "High" }
  }
}
```

Field read:

```json
{ "session_id": "p1a2b3c4", "target": "GPIO.PIN_CNF[3].PULL" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "target": "GPIO.PIN_CNF[3].PULL",
  "register": "GPIO.PIN_CNF[3]",
  "field": "PULL",
  "address": 1342179084,
  "raw_register": 12,
  "value": 3,
  "enum": "PullUp"
}
```

### dbgprobe.svd.write

Write a raw value to a full register (no read-modify-write). For field-level writes, use `svd.set_field`.

```json
{ "session_id": "p1a2b3c4", "register": "GPIO.OUT", "value": "0x01" }
```

Values accept integers or hex strings. Returns error on read-only registers.

### dbgprobe.svd.set_field

Read-modify-write a single register field. Reads the current register value, modifies the specified field, writes back. Accepts enum names or integer values.

```json
{ "session_id": "p1a2b3c4", "field": "GPIO.PIN_CNF[3].PULL", "value": "PullUp" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "field": "GPIO.PIN_CNF[3].PULL",
  "address": 1342179084,
  "old_value": 0,
  "new_value": 3,
  "old_enum": "Disabled",
  "new_enum": "PullUp",
  "old_raw": 0,
  "new_raw": 12
}
```

### dbgprobe.svd.update_fields

Batch read-modify-write: update multiple fields in one register with a single read and write.

```json
{
  "session_id": "p1a2b3c4",
  "register": "GPIO.PIN_CNF[3]",
  "fields": {
    "DIR": "Output",
    "PULL": "PullUp"
  }
}
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "register": "GPIO.PIN_CNF[3]",
  "address": 1342179084,
  "old_raw": 0,
  "new_raw": 13,
  "changes": {
    "DIR": { "old_value": 0, "new_value": 1, "old_enum": "Input", "new_enum": "Output" },
    "PULL": { "old_value": 0, "new_value": 3, "old_enum": "Disabled", "new_enum": "PullUp" }
  }
}
```

### dbgprobe.svd.list_peripherals

List all peripherals defined in the attached SVD.

```json
{ "session_id": "p1a2b3c4" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "peripherals": [
    { "name": "GPIO", "base_address": 1342177280, "description": "General purpose input/output", "register_count": 42 }
  ],
  "count": 52
}
```

### dbgprobe.svd.list_registers

List all registers for a peripheral.

```json
{ "session_id": "p1a2b3c4", "peripheral": "GPIO" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "peripheral": "GPIO",
  "registers": [
    { "name": "OUT", "address": 1342179588, "size": 32, "access": "read-write", "field_count": 32 }
  ],
  "count": 42
}
```

### dbgprobe.svd.list_fields

List all fields for a register.

```json
{ "session_id": "p1a2b3c4", "peripheral": "GPIO", "register": "PIN_CNF[3]" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "peripheral": "GPIO",
  "register": "PIN_CNF[3]",
  "fields": [
    { "name": "DIR", "bit_offset": 0, "bit_width": 1, "access": null, "enum_values": { "Input": 0, "Output": 1 } },
    { "name": "PULL", "bit_offset": 2, "bit_width": 2, "access": null, "enum_values": { "Disabled": 0, "PullDown": 1, "PullUp": 3 } }
  ],
  "count": 5
}
```

### dbgprobe.svd.describe

Detailed description of a peripheral, register, or field. Accepts any level: `"GPIO"`, `"GPIO.OUT"`, or `"GPIO.PIN_CNF[3].PULL"`.

```json
{ "session_id": "p1a2b3c4", "target": "GPIO.PIN_CNF[3].PULL" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "type": "field",
  "name": "PULL",
  "register": "GPIO.PIN_CNF[3]",
  "bit_offset": 2,
  "bit_width": 2,
  "bit_range": "[2:3]",
  "access": "read-write",
  "description": "Pull configuration",
  "enum_values": { "Disabled": 0, "PullDown": 1, "PullUp": 3 }
}
```

### mem.read SVD enrichment

When an SVD is attached, `dbgprobe.mem.read` automatically includes decoded register fields if the address and length exactly match a known register:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "address": 1342179588,
  "length": 4,
  "format": "hex",
  "data": "03000000",
  "svd": {
    "peripheral": "GPIO",
    "register": "OUT",
    "raw": 3,
    "fields": {
      "PIN0": { "value": 1, "bit_range": "[0:0]", "enum": "High" },
      "PIN1": { "value": 1, "bit_range": "[1:1]", "enum": "High" }
    }
  }
}
```

---

## RTT (Real-Time Transfer)

Tools for streaming data between host and target over RTT channel 0. RTT uses a control block in target RAM to exchange data without halting the CPU. JLinkGDBServer exposes channel 0 via a telnet port — these tools connect to it.

### dbgprobe.rtt.start

Start RTT on a connected session. Connects to JLinkGDBServer's RTT telnet port and begins buffering target output.

```json
{ "session_id": "p1a2b3c4" }
```

Optional: provide `address` to specify the RTT control block location in target RAM (accepts integer or hex string). If omitted, JLinkGDBServer auto-searches for the "SEGGER RTT" string.

```json
{ "session_id": "p1a2b3c4", "address": "0x20000000" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "rtt_port": 19021
}
```

### dbgprobe.rtt.stop

Stop RTT and disconnect from the telnet port.

```json
{ "session_id": "p1a2b3c4" }
```

### dbgprobe.rtt.read

Read buffered RTT data from the target. Non-blocking — returns whatever is buffered, waiting up to `timeout` seconds for initial data.

```json
{ "session_id": "p1a2b3c4", "timeout": 0.5, "encoding": "utf-8" }
```

- `timeout`: max seconds to wait for data if buffer is empty (default 0.1)
- `encoding`: `utf-8` (default) or `hex` for binary data

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "data": "Hello from RTT\n",
  "bytes_read": 15,
  "encoding": "utf-8"
}
```

### dbgprobe.rtt.write

Write data to the target via RTT channel 0.

```json
{ "session_id": "p1a2b3c4", "data": "hello", "newline": true }
```

- `data`: text (UTF-8) or hex string depending on `encoding`
- `encoding`: `utf-8` (default) or `hex`
- `newline`: append `\n` to the data (default false)

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "bytes_written": 6
}
```

### dbgprobe.rtt.status

Return RTT status for a session.

```json
{ "session_id": "p1a2b3c4" }
```

Returns:

```json
{
  "ok": true,
  "session_id": "p1a2b3c4",
  "active": true,
  "bytes_buffered": 42,
  "total_read": 1024,
  "total_written": 128
}
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

