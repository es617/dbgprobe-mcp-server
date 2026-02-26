# nRF52840-DK Example

Step-by-step walkthrough using the Debug Probe MCP Server with a Nordic nRF52840-DK development board and its onboard J-Link debugger.

Just tell the agent what you want in plain language — it picks the right tools automatically.

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

> List attached debug probes.

The agent will find the J-Link on your board and report its serial number and description.

## Step 2: Connect

> Connect to the nRF52840.

The device string `nRF52840_xxAA` is picked up from the environment variable. The agent establishes a debug session over SWD.

If the device is secured (APPROTECT enabled), the agent will tell you and suggest a mass-erase to unlock it. See [Handling a secured device](#handling-a-secured-device) below.

## Step 3: Flash firmware

> Flash examples/nrf52840-dk/firmware.hex to the board.

`.hex` and `.elf` files auto-detect the target address. `.bin` files require an explicit address.

The agent programs the image, verifies it, and resets the target to start running.

## Step 4: Load SVD (optional)

The SVD file gives the agent named access to all peripheral registers instead of raw addresses.

> Load the SVD file from /opt/nordic/ncs/v3.2.2/modules/hal/nordic/nrfx/bsp/stable/mdk/nrf52840.svd

After loading, you can refer to peripherals by name:

> Read the GPIO P0 OUT register.

## Step 5: Inspect memory

> Read 8 bytes from address 0x10000060 — that's the FICR Device ID.

The agent reads the memory and returns the unique 64-bit device ID.

## Step 6: Halt, inspect, resume

> Halt the CPU, read 4 bytes from 0x20000000, then resume.

The agent halts the target, reads the memory, and resumes execution — all in one go.

## Step 7: Reset

> Reset the target.

You can also be specific about the reset mode:

| Mode | What to ask |
|---|---|
| Soft (default) | "Reset the target" |
| Hard | "Do a hard reset" |
| Halt after reset | "Reset and halt at the first instruction" |

## Step 8: RTT (Real-Time Transfer)

If your firmware uses SEGGER RTT for logging:

> Start RTT and show me the output.

The agent connects to the RTT channel and streams target output. You can also send data:

> Send "hello" to the target via RTT.

When done:

> Stop RTT.

## Step 9: Disconnect

> Disconnect from the board.

---

## Handling a secured device

If the device has read protection enabled (Nordic APPROTECT), the agent will report it on connect. To unlock:

> Erase the device to unlock it.

This mass-erases all flash and unlocks the device. After erasing, reconnect and re-flash your firmware.

You can also erase a specific address range:

> Erase flash from 0x40000 to 0x80000.

**Warning:** Full chip erase destroys all flash contents including any stored calibration data, keys, or application state.

> **Note:** Requires J-Link Software v6.32 or newer. Older versions (pre-2017) don't support
> APPROTECT recovery — update your J-Link Software or use `nrfjprog --recover` as a fallback.

### Locking a device (enabling APPROTECT)

To enable read protection (useful for testing the secured device flow):

> Write 0x00 to UICR APPROTECT register at 0x10001208, then reset.

After the reset, the device is secured.

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

## SVD file

The SVD file for the nRF52840 is included in the Nordic nRF Connect SDK:

```
/opt/nordic/ncs/v3.2.2/modules/hal/nordic/nrfx/bsp/stable/mdk/nrf52840.svd
```

---

## Firmware

Place your firmware image in this directory (`.hex`, `.elf`, or `.bin`). The README examples assume `firmware.hex`.
