# Manual Test Plan — nRF52840-DK

Hardware: nRF52840-DK with onboard J-Link (OB-nRF5340)
Target device string: `nRF52840_xxAA`

## Prerequisites

- [ ] nRF52840-DK connected via USB
- [ ] J-Link Software installed
- [ ] MCP server installed and registered with correct `DBGPROBE_JLINK_DEVICE=nRF52840_xxAA`
- [ ] MCP server restarted after latest code changes
- [ ] A `.hex` firmware file available (e.g. a blinky example)

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
| 4.1 | `dbgprobe.mem.read` — FICR DEVICEID at `0x10000060`, 8 bytes, format `hex` | Returns `ok: true`, non-zero hex string (unique device ID). |
| 4.2 | `dbgprobe.mem.read` — same address, format `u32` | Returns array of 2 u32 values matching the hex from 4.1. |
| 4.3 | `dbgprobe.mem.read` — same address, format `base64` | Returns base64 string that decodes to same bytes. |
| 4.4 | `dbgprobe.mem.read` — FICR DEVICEADDR at `0x100000A0`, 8 bytes | Returns device BLE address bytes. |
| 4.5 | `dbgprobe.mem.write` — write `"deadbeef"` to RAM at `0x20000000`, then read it back | Read returns `"deadbeef"` at those bytes. |
| 4.6 | `dbgprobe.mem.write` — u32 format: `data_u32: [0x12345678]` to `0x20000000`, read back as hex | Returns `"78563412"` (little-endian). |

---

## 5. Halt / Go / Reset

| # | Test | Expected |
|---|------|----------|
| 5.1 | `dbgprobe.halt` | Returns `ok: true`. |
| 5.2 | `dbgprobe.mem.read` while halted (RAM at `0x20000000`, 4 bytes) | Returns data successfully. |
| 5.3 | `dbgprobe.go` | Returns `ok: true`, target resumes. |
| 5.4 | `dbgprobe.reset` with mode `soft` | Returns `ok: true`. |
| 5.5 | `dbgprobe.reset` with mode `hard` | Returns `ok: true`. |
| 5.6 | `dbgprobe.reset` with mode `halt` | Returns `ok: true`, target is halted after reset. Verify with a mem.read. |

---

## 6. Flash

| # | Test | Expected |
|---|------|----------|
| 6.1 | `dbgprobe.flash` with a `.hex` file, defaults (verify=true, reset_after=true) | Returns `ok: true`. Board runs the new firmware. |
| 6.2 | `dbgprobe.flash` with `verify: true, reset_after: false` | Returns `ok: true`. Board does NOT restart (stays halted or at old PC). |
| 6.3 | `dbgprobe.flash` with nonexistent path | Returns error. |

---

## 7. Disconnect

| # | Test | Expected |
|---|------|----------|
| 7.1 | `dbgprobe.disconnect` with valid session_id | Returns `ok: true`. |
| 7.2 | `dbgprobe.connections.list` after disconnect | Session no longer listed. |
| 7.3 | `dbgprobe.mem.read` with disconnected session_id | Returns error (session not found). |

---

## 8. Erase (full chip)

| # | Test | Expected |
|---|------|----------|
| 8.1 | `dbgprobe.erase` with `device: "nRF52840_xxAA"` (no address params) | Returns `ok: true`, `erased: true`. |
| 8.2 | Connect after erase, read flash at `0x00000000` (4 bytes) | All `0xFF` (erased flash). |

---

## 9. Erase (range)

| # | Test | Expected |
|---|------|----------|
| 9.1 | Flash firmware first, then `dbgprobe.erase` with `start_addr: 0x00000000, end_addr: 0x00001000` | Returns `ok: true`, `erased: true`, `start_addr` and `end_addr` in response. |
| 9.2 | Read back erased range — `0x00000000`, 4 bytes | Returns `0xFFFFFFFF`. |
| 9.3 | Read outside erased range (higher address in flash) | Returns non-FF data (firmware still there). |
| 9.4 | `dbgprobe.erase` with only `start_addr` (no `end_addr`) | Returns `ok: false`, error `invalid_params`. |
| 9.5 | `dbgprobe.erase` with `start_addr > end_addr` | Returns `ok: false`, error `invalid_params`. |

---

## 10. Secured device (APPROTECT) flow

This tests the full lock/unlock cycle. **Warning: this erases all flash.**

| # | Step | Expected |
|---|------|----------|
| 10.1 | Connect, write `0x00` to UICR APPROTECT at `0x10001208`, reset | Write and reset succeed. |
| 10.2 | Disconnect, then `dbgprobe.connect` | Returns `ok: false`, error code `device_secured`, message mentions `dbgprobe.erase`. |
| 10.3 | `dbgprobe.erase` (full chip, no address params) | Returns `ok: true`, `erased: true`. |
| 10.4 | `dbgprobe.connect` again | Returns `ok: true` — device is unlocked. |
| 10.5 | Read UICR APPROTECT at `0x10001208` (4 bytes) | Returns `0xFFFFFFFF` (erased/unlocked). |

---

## 11. Error messages and diagnostics

| # | Test | Expected |
|---|------|----------|
| 11.1 | Connect with wrong device string | Error message includes "InitTarget() failed" and probe-vs-target hint. |
| 11.2 | Connect failure includes JLink output | Error response contains `[JLink output]` section with raw JLinkExe stdout. |
| 11.3 | `device_secured` error includes JLink output | Same — raw output attached for debugging. |

---

## Quick smoke test (minimum viable check)

If short on time, run these in order:

1. `dbgprobe.list_probes` — board shows up
2. `dbgprobe.connect` with `device: "nRF52840_xxAA"` — session created
3. `dbgprobe.mem.read` at `0x10000060`, 8 bytes — device ID returned
4. `dbgprobe.halt` then `dbgprobe.go` — both succeed
5. `dbgprobe.reset` — succeeds
6. `dbgprobe.disconnect` — session closed
7. `dbgprobe.erase` (full chip) — erased successfully
