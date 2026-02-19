# nRF52840-DK Example

Step-by-step walkthrough using the Debug Probe MCP Server with a Nordic nRF52840-DK development board and its onboard J-Link debugger.

## Prerequisites

- nRF52840-DK connected via USB
- [SEGGER J-Link Software](https://www.segger.com/downloads/jlink/) installed
- Debug Probe MCP Server installed and registered with your MCP client

```bash
pip install dbgprobe-mcp-server

claude mcp add dbgprobe \
  -e DBGPROBE_JLINK_DEVICE=nRF52840_xxAA \
  -- dbgprobe_mcp
```

## Step 1: Discover the probe

List attached J-Link probes to confirm the board is visible:

```
> List attached debug probes.
```

Tool call: `dbgprobe.list_probes`

```json
{
  "ok": true,
  "probes": [{
    "serial": "683456789",
    "description": "J-Link EDU Mini",
    "backend": "jlink"
  }],
  "count": 1
}
```

## Step 2: Connect

Establish a debug session. The device string `nRF52840_xxAA` tells J-Link which target chip to expect.

```
> Connect to the nRF52840.
```

Tool call: `dbgprobe.connect`

```json
{
  "device": "nRF52840_xxAA",
  "interface": "swd",
  "speed_khz": 4000
}
```

If the device is secured (APPROTECT enabled), you'll get:

```json
{
  "ok": false,
  "error": {
    "code": "device_secured",
    "message": "Target device is secured. Use dbgprobe.erase to mass-erase and unlock."
  }
}
```

See [Handling a secured device](#handling-a-secured-device) below.

## Step 3: Flash firmware

Flash a firmware image to the board. `.hex` and `.elf` files auto-detect the target address. `.bin` files require an explicit `addr`.

```
> Flash the firmware.
```

Tool call: `dbgprobe.flash`

```json
{
  "session_id": "p1a2b3c4",
  "path": "examples/nrf52840-dk/firmware.hex"
}
```

The tool programs the image, verifies it, and resets the target to start running.

## Step 4: Inspect memory

Read the Device ID from the FICR (Factory Information Configuration Registers):

```
> Read 8 bytes from address 0x10000060 (FICR DEVICEID).
```

Tool call: `dbgprobe.mem.read`

```json
{
  "session_id": "p1a2b3c4",
  "address": 268435552,
  "length": 8,
  "format": "hex"
}
```

Returns the unique 64-bit device ID as a hex string.

## Step 5: Halt, inspect, resume

Halt the CPU, read registers or memory, then resume:

```
> Halt the CPU, read 4 bytes from 0x20000000, then resume.
```

Tool calls: `dbgprobe.halt` -> `dbgprobe.mem.read` -> `dbgprobe.go`

## Step 6: Reset

Reset the target in different modes:

```
> Reset the target.
```

| Mode | What it does |
|---|---|
| `soft` (default) | Software reset, target resumes running |
| `hard` | Hardware reset via the reset pin |
| `halt` | Reset and halt at the first instruction |

## Step 7: Disconnect

Close the session when done:

```
> Disconnect.
```

Tool call: `dbgprobe.disconnect`

---

## Handling a secured device

If `dbgprobe.connect` returns `device_secured`, the chip has read protection enabled (Nordic APPROTECT). You can mass-erase to unlock it:

```
> Erase the device to unlock it.
```

Tool call: `dbgprobe.erase`

```json
{
  "device": "nRF52840_xxAA",
  "interface": "swd",
  "speed_khz": 4000
}
```

This erases all flash contents and unlocks the device. After erasing, retry `dbgprobe.connect` and re-flash your firmware.

You can also erase a specific address range instead of the full chip:

```json
{
  "device": "nRF52840_xxAA",
  "start_addr": 262144,
  "end_addr": 524288
}
```

**Warning:** Full chip erase destroys all flash contents including any stored calibration data, keys, or application state.

> **Note:** Requires J-Link Software v6.32 or newer. Older versions (pre-2017) don't support
> APPROTECT recovery — update your J-Link Software or use `nrfjprog --recover` as a fallback.

### Locking a device (enabling APPROTECT)

To enable read protection on the nRF52840 (useful for testing the secured device flow):

```
> Write 0x00 to UICR APPROTECT register at 0x10001208, then reset.
```

Tool calls: `dbgprobe.mem.write` (address `0x10001208`, data `"00"`) -> `dbgprobe.reset`

After the reset, the device is secured. The next `dbgprobe.connect` will return `device_secured`.

---

## Useful memory addresses (nRF52840)

| Address | Region | Description |
|---|---|---|
| `0x00000000` | Flash | Application code start |
| `0x10000000` | FICR | Factory Information Configuration Registers (read-only) |
| `0x10001000` | UICR | User Information Configuration Registers |
| `0x10001208` | UICR | APPROTECT — write `0x00` to enable read protection |
| `0x20000000` | RAM | SRAM start (256 KB) |
| `0x10000060` | FICR | DEVICEID[0..1] — unique 64-bit device ID |
| `0x100000A0` | FICR | DEVICEADDR[0..1] — device address (BLE) |

---

## Firmware

Place your firmware image in this directory (`.hex`, `.elf`, or `.bin`). The README examples assume `firmware.hex`.
