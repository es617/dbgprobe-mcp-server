# Manual Test Plan — nRF52840-DK

Hardware: nRF52840-DK with onboard J-Link (OB-nRF5340)
Target device string: `nRF52840_xxAA`

## Prerequisites

- [ ] nRF52840-DK connected via USB
- [ ] J-Link Software installed
- [ ] MCP server installed and registered with correct `DBGPROBE_JLINK_DEVICE=nRF52840_xxAA`
- [ ] MCP server restarted after latest code changes
- [ ] A `.hex` firmware file available (e.g. a blinky example)
- [ ] A `.elf` firmware file available (e.g. `build/zephyr/zephyr.elf`)

---

## 1. Probe discovery

| # | Test | Expected |
|---|------|----------|
| 1.1 | `dbgprobe.list_probes` | Returns probe with serial and description containing "nRF5340" (the debugger MCU). `ok: true`, `count: 1`. |
| 1.2 | `dbgprobe.list_probes` with no board plugged in | Returns `ok: true`, `count: 0`, empty `probes` array. |

---

## 2. Connect (happy path)

| # | Test | Expected |
|---|------|----------|
| 2.1 | `dbgprobe.connect` with `device: "nRF52840_xxAA"` | Returns `ok: true`, `session_id`, `config` with correct device/interface/speed, `resolved_paths` with JLinkExe path. |
| 2.2 | `dbgprobe.connect` using env default (no device param) | Same as 2.1 — picks up `DBGPROBE_JLINK_DEVICE` from env. |
| 2.3 | `dbgprobe.connections.list` after connect | Shows the session with correct device and backend. |

---

## 3. Connect (error cases)

| # | Test | Expected |
|---|------|----------|
| 3.1 | `dbgprobe.connect` with wrong device (e.g. `"nRF5340_xxAA_APP"`) | Returns `ok: false`, error code `connect_failed`, message mentions InitTarget() failure and probe-vs-target hint. |
| 3.2 | `dbgprobe.connect` with bogus device (e.g. `"DOESNOTEXIST"`) | Returns `ok: false`, error code `connect_failed`. |
| 3.3 | `dbgprobe.connect` with no board plugged in | Returns `ok: false`, error code `connect_failed`, message about cannot connect. |

---

## 4. Memory read/write

Connect first, then:

| # | Test | Expected |
|---|------|----------|
| 4.1 | `dbgprobe.mem.read` — FICR DEVICEID at `"0x10000060"`, 8 bytes, format `hex` | Returns `ok: true`, non-zero hex string (unique device ID). |
| 4.2 | `dbgprobe.mem.read` — same address, format `u32` | Returns array of 2 u32 values matching the hex from 4.1. |
| 4.3 | `dbgprobe.mem.read` — same address, format `base64` | Returns base64 string that decodes to same bytes. |
| 4.4 | `dbgprobe.mem.read` — FICR DEVICEADDR at `"0x100000A0"`, 8 bytes | Returns device BLE address bytes. |
| 4.5 | `dbgprobe.mem.write` — write `"deadbeef"` to RAM at `"0x20000000"`, then read it back | Read returns `"deadbeef"` at those bytes. |
| 4.6 | `dbgprobe.mem.write` — u32 format: `data_u32: [0x12345678], format: "u32"` to `"0x20000000"`, read back as hex | Returns `"78563412"` (little-endian). Note: `format: "u32"` is required — without it, `data_u32` is ignored and 0 bytes are written. |
| 4.7 | `dbgprobe.mem.read` with integer address `536870912` (= 0x20000000) | Same result as hex string — both formats accepted. |

---

## 5. Halt / Go / Reset

| # | Test | Expected |
|---|------|----------|
| 5.1 | `dbgprobe.halt` | Returns `ok: true`. |
| 5.2 | `dbgprobe.mem.read` while halted (RAM at `"0x20000000"`, 4 bytes) | Returns data successfully. |
| 5.3 | `dbgprobe.go` | Returns `ok: true`, target resumes. |
| 5.4 | `dbgprobe.reset` with mode `soft` | Returns `ok: true`. |
| 5.5 | `dbgprobe.reset` with mode `hard` | Returns `ok: true`. |
| 5.6 | `dbgprobe.reset` with mode `halt` | Returns `ok: true`, target is halted after reset. Verify with a mem.read. |

---

## 6. Flash (session-based)

| # | Test | Expected |
|---|------|----------|
| 6.1 | `dbgprobe.flash` with session_id and a `.hex` file, defaults | Returns `ok: true`. Board runs the new firmware. GDB session survives (verify with `status`). |
| 6.2 | `dbgprobe.flash` with session_id, `verify: true, reset_after: false` | Returns `ok: true`. Board does NOT restart. |
| 6.3 | `dbgprobe.flash` with session_id and a `.elf` file (no prior ELF attached) | Returns `ok: true`, `elf_attached: true`, `elf_path` in response. `elf.info` confirms ELF is attached. |
| 6.4 | `dbgprobe.flash` with session_id and a `.elf` file (ELF already attached) | Returns `ok: true`, `elf_reloaded: true`. |
| 6.5 | `dbgprobe.flash` with session_id and a `.hex` file (ELF attached) | Returns `ok: true`, `elf_reloaded: true` (re-parsed from original ELF path). |
| 6.6 | `dbgprobe.flash` with `elf_hint` — flash a `.hex`, check response | Response includes `elf_hint` pointing to a sibling `.elf` file (if one exists nearby). |
| 6.7 | `dbgprobe.flash` with nonexistent path | Returns error. |

---

## 7. Flash (session-less)

| # | Test | Expected |
|---|------|----------|
| 7.1 | `dbgprobe.flash` without session_id, with `device: "nRF52840_xxAA"` and a `.hex` file | Returns `ok: true`. No GDB server left running (`ps aux | grep JLinkGDBServer` shows none). |
| 7.2 | After session-less flash, `dbgprobe.connect` | Connects successfully — probe is not stuck. |
| 7.3 | `dbgprobe.flash` session-less without `device` param | Returns error or times out with helpful message about providing device/probe_id. |

---

## 8. Disconnect

| # | Test | Expected |
|---|------|----------|
| 8.1 | `dbgprobe.disconnect` with valid session_id | Returns `ok: true`. |
| 8.2 | `dbgprobe.connections.list` after disconnect | Session no longer listed. |
| 8.3 | `dbgprobe.mem.read` with disconnected session_id | Returns error (session not found). |

---

## 9. Erase (session-based)

| # | Test | Expected |
|---|------|----------|
| 9.1 | Flash firmware, then `dbgprobe.erase` with `session_id` (no address params) | Returns `ok: true`, `erased: true`, `session_id` in response. GDB session survives. |
| 9.2 | Read flash at `"0x00000000"` (4 bytes) after session-based erase | All `0xFF` (erased flash). |
| 9.3 | `dbgprobe.erase` with `session_id`, `start_addr: "0x00000000"`, `end_addr: "0x1000"` | Returns `ok: true`, range erase through GDB. |

---

## 10. Erase (session-less)

| # | Test | Expected |
|---|------|----------|
| 10.1 | `dbgprobe.erase` with `device: "nRF52840_xxAA"` (no session_id, no address params) | Returns `ok: true`, `erased: true`, `config` in response. |
| 10.2 | Connect after session-less erase, read flash at `"0x00000000"` (4 bytes) | All `0xFF` (erased flash). |
| 10.3 | `dbgprobe.erase` with `start_addr: "0x00000000"`, `end_addr: "0x1000"` (session-less range) | Returns `ok: true`. |
| 10.4 | Read outside erased range (higher address in flash) | Returns non-FF data (firmware still there). |
| 10.5 | `dbgprobe.erase` with only `start_addr` (no `end_addr`) | Returns `ok: false`, error `invalid_params`. |
| 10.6 | `dbgprobe.erase` with `start_addr > end_addr` | Returns `ok: false`, error `invalid_params`. |

---

## 11. ELF support

Connect first, then:

| # | Test | Expected |
|---|------|----------|
| 11.1 | `dbgprobe.elf.attach` with path to `.elf` file | Returns `ok: true`, `symbol_count`, `function_count`, `entry_point`, `sections`. |
| 11.2 | `dbgprobe.elf.info` | Returns ELF metadata matching 11.1. |
| 11.3 | `dbgprobe.elf.lookup` with `symbol: "main"` | Returns address, size, type `"FUNC"`. |
| 11.4 | `dbgprobe.elf.lookup` with `address` (use address from 11.3) | Returns `symbol: "main"`, `symbol_offset: 0`. |
| 11.5 | `dbgprobe.elf.lookup` with hex string address (e.g. `"0x112AC"`) | Same result as integer — hex strings accepted. |
| 11.6 | `dbgprobe.elf.symbols` with `filter: "main"` | Returns matching symbols. |
| 11.7 | `dbgprobe.elf.symbols` with `type: "FUNC"`, `limit: 5` | Returns up to 5 function symbols. |
| 11.8 | `dbgprobe.status` with ELF attached | Response includes `symbol` and `symbol_offset` for the current PC. |
| 11.9 | `dbgprobe.halt` with ELF attached | Response includes `symbol` and `symbol_offset`. |
| 11.10 | `dbgprobe.step` with ELF attached (halt first) | Response includes `symbol` and `symbol_offset`. |
| 11.11 | `dbgprobe.elf.info` after disconnect + reconnect | Returns `elf: null` — ELF is not persisted across sessions. |

---

## 12. Breakpoints

Connect and attach ELF, then:

| # | Test | Expected |
|---|------|----------|
| 12.1 | `dbgprobe.breakpoint.set` with `address: "0x08000100"` (hex string) | Returns `ok: true`, `address`, `bp_type: "sw"`. |
| 12.2 | `dbgprobe.breakpoint.set` with `symbol: "main"` | Returns `ok: true`, `address` (resolved), `symbol: "main"`. |
| 12.3 | `dbgprobe.breakpoint.list` | Shows breakpoints from 12.1 and 12.2. |
| 12.4 | `dbgprobe.breakpoint.clear` with `address: "0x08000100"` | Returns `ok: true`. |
| 12.5 | `dbgprobe.breakpoint.set` with `symbol` but no ELF attached | Returns `ok: false`, error `no_elf`. |
| 12.6 | `dbgprobe.breakpoint.set` with both `address` and `symbol` | Returns `ok: false`, error `invalid_params`. |
| 12.7 | Set breakpoint on `main`, `go`, wait for halt, `status` | Status shows PC matching breakpoint address, `symbol: "main"`, `symbol_offset: 0`. Note: reason may show `"halted"` instead of `"breakpoint"` — J-Link's GDB server doesn't include `swbreak` in stop replies. Verify by checking PC matches the breakpoint address. |
| 12.8 | Flash new firmware — `breakpoint.list` after flash | Empty — breakpoints cleared on flash. |

---

## 13. SVD support

Prerequisites: connected session, nRF52840 SVD file available (download from Nordic or CMSIS-Pack).

| # | Test | Expected |
|---|------|----------|
| 13.1 | `dbgprobe.svd.attach` with path to nRF52840 SVD file | Returns `ok: true`, `device_name`, `peripheral_count`, `register_count`. |
| 13.2 | `dbgprobe.svd.info` | Returns SVD metadata matching 13.1. |
| 13.3 | `dbgprobe.svd.list_peripherals` | Returns list of peripherals (GPIO, UART, TIMER, etc.). |
| 13.4 | `dbgprobe.svd.list_registers` with `peripheral: "P0"` (or `"GPIO"`) | Returns registers: OUT, IN, DIR, PIN_CNF[0..31], etc. |
| 13.5 | `dbgprobe.svd.list_fields` with `register: "P0.PIN_CNF[3]"` | Returns fields: DIR, INPUT, PULL, DRIVE, SENSE. |
| 13.6 | `dbgprobe.svd.read` with `target: "P0.OUT"` | Returns raw value + decoded PIN0/PIN1/... fields. |
| 13.7 | `dbgprobe.svd.read` with `target: "P0.PIN_CNF[3].PULL"` | Returns field value + enum name ("Disabled", "PullUp", etc.). |
| 13.8 | `dbgprobe.svd.set_field` with `field: "P0.PIN_CNF[3].PULL", value: "PullUp"` | Returns old + new values, old + new enum names. Read back to verify. |
| 13.9 | `dbgprobe.svd.update_fields` with `register: "P0.PIN_CNF[3]", fields: {"DIR": "Output", "PULL": "PullUp"}` | Returns old + new values for both fields. |
| 13.10 | `dbgprobe.svd.write` with `register: "P0.OUT", value: 0x01` | Returns `ok: true`. Read back to verify. |
| 13.11 | `dbgprobe.svd.describe` with `target: "P0"` | Returns peripheral description with register list. |
| 13.12 | `dbgprobe.svd.describe` with `target: "P0.PIN_CNF[3].PULL"` | Returns field description with enum values. |
| 13.13 | `dbgprobe.mem.read` at GPIO OUT register address (e.g. `"0x50000504"`), 4 bytes | Response includes `"svd"` key with peripheral name, register name, decoded fields. |
| 13.14 | `dbgprobe.svd.write` on read-only register (e.g. `P0.IN`) | Returns `ok: false`, error code `read_only`. |
| 13.15 | `dbgprobe.svd.read` on write-only register | Returns value with `warning` about write-only. |

---

## 14. Secured device (APPROTECT) flow

This tests the full lock/unlock cycle. **Warning: this erases all flash.**

Enabling APPROTECT on nRF52840 requires an NVMC flash write sequence (not a simple memory write). Steps 14.1–14.2 use the NVMC register sequence to write to UICR.

| # | Step | Expected |
|---|------|----------|
| 14.1 | Connect. Enable NVMC write: write `0x01` to NVMC CONFIG at `"0x4001E504"`. Write `0x00` to UICR APPROTECT at `"0x10001208"`. Disable NVMC write: write `0x00` to `"0x4001E504"`. | All writes succeed. |
| 14.2 | Reset (hard), disconnect, then `dbgprobe.connect` | Returns `ok: false`, error code `device_secured`, message mentions `dbgprobe.erase`. |
| 14.3 | `dbgprobe.erase` (session-less, full chip, no address params) | Returns `ok: true`, `erased: true`. |
| 14.4 | `dbgprobe.connect` again | Returns `ok: true` — device is unlocked. |
| 14.5 | Read UICR APPROTECT at `"0x10001208"` (4 bytes) | Returns `0xFFFFFFFF` (erased/unlocked). |

---

## 15. Session-less flow (no connect)

Full flow without an active session:

| # | Step | Expected |
|---|------|----------|
| 15.1 | `dbgprobe.erase` (session-less, full chip) | Returns `ok: true`. |
| 15.2 | `dbgprobe.flash` (session-less, `.hex` file) | Returns `ok: true`. Board runs firmware. |
| 15.3 | `ps aux \| grep JLinkGDBServer` | No orphaned GDB server processes. |
| 15.4 | `dbgprobe.connect` | Connects successfully — no probe lock-up from prior operations. |
| 15.5 | `dbgprobe.status` | Returns target state (running or halted). |

---

## 16. Error messages and diagnostics

| # | Test | Expected |
|---|------|----------|
| 16.1 | Connect with wrong device string | Error message includes "InitTarget() failed" and probe-vs-target hint. |
| 16.2 | Connect failure includes JLink output | Error response contains `[JLink output]` section with raw JLinkExe stdout. |
| 16.3 | `device_secured` error includes JLink output | Same — raw output attached for debugging. |

---

## 17. RTT (Real-Time Transfer)

Prerequisites: connected session, firmware with RTT enabled (e.g. Zephyr with `CONFIG_USE_SEGGER_RTT=y` and `CONFIG_RTT_CONSOLE=y`).

| # | Test | Expected |
|---|------|----------|
| 17.1 | `dbgprobe.rtt.start` with session_id | Returns `ok: true`, `rtt_port` in response. |
| 17.2 | `dbgprobe.rtt.status` | Returns `ok: true`, `active: true`. |
| 17.3 | `dbgprobe.rtt.read` with default timeout | Returns target printf output as UTF-8 text. |
| 17.4 | `dbgprobe.rtt.read` with `encoding: "hex"` | Returns same data as hex string. |
| 17.5 | `dbgprobe.rtt.write` with `data: "hello"`, `newline: true` | Returns `ok: true`, `bytes_written: 6`. Target receives input. |
| 17.6 | `dbgprobe.rtt.write` with `encoding: "hex"`, `data: "48656c6c6f"` | Returns `ok: true`, `bytes_written: 5`. |
| 17.7 | `dbgprobe.connections.list` while RTT active | Session entry includes `"rtt": {"active": true}`. |
| 17.8 | `dbgprobe.rtt.stop` | Returns `ok: true`. |
| 17.9 | `dbgprobe.rtt.status` after stop | Returns `active: false`. |
| 17.10 | `dbgprobe.rtt.start`, then `dbgprobe.flash` (session-based), then `dbgprobe.rtt.status` | RTT auto-restarts after flash — `active: true`. |
| 17.11 | `dbgprobe.rtt.start` with `address: "0x20000000"` | Returns `ok: true` — address hint sent to GDBServer. |
| 17.12 | `dbgprobe.rtt.read` with no RTT active | Returns error `disconnected`. |
| 17.13 | `dbgprobe.rtt.start` when already active | Returns error `disconnected` (already active). |

---

## Quick smoke test (minimum viable check)

If short on time, run these in order:

1. `dbgprobe.list_probes` — board shows up
2. `dbgprobe.connect` with `device: "nRF52840_xxAA"` — session created
3. `dbgprobe.mem.read` at `"0x10000060"`, 8 bytes — device ID returned
4. `dbgprobe.halt` then `dbgprobe.go` — both succeed
5. `dbgprobe.flash` with session_id and `.elf` file — `elf_attached: true` in response
6. `dbgprobe.elf.lookup` with `symbol: "main"` — address returned
7. `dbgprobe.breakpoint.set` with `symbol: "main"` — breakpoint set
8. `dbgprobe.reset` — succeeds
9. `dbgprobe.disconnect` — session closed
10. `dbgprobe.erase` (session-less, full chip) — erased successfully
