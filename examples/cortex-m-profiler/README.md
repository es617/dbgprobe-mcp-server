# cortex_m_profiler — Non-invasive Latency Profiler

Measure firmware execution time between any two code addresses using the Cortex-M4 DWT hardware cycle counter. No firmware instrumentation required — works purely through the debug probe.

## How it works

The Cortex-M4 core has a **Data Watchpoint and Trace (DWT)** unit with a 32-bit free-running cycle counter (`DWT_CYCCNT`). The plugin:

1. Enables the cycle counter via debug registers
2. Sets a hardware breakpoint at the **start** address
3. Resumes the target and waits for the breakpoint hit
4. Zeros the cycle counter
5. Sets a hardware breakpoint at the **end** address
6. Resumes and waits for the hit
7. Reads the cycle counter — the delta is the execution time

At 64 MHz (nRF52840), the 32-bit counter overflows after ~67 seconds — more than enough for any single measurement. The CPU frequency is configurable via the `cpu_freq_mhz` parameter (default 64).

### DWT registers used

| Register | Address | Purpose |
|----------|---------|---------|
| `CoreDebug_DEMCR` | `0xE000EDFC` | Bit 24 (TRCENA) — enables DWT/ITM |
| `DWT_CTRL` | `0xE0001000` | Bit 0 (CYCCNTENA) — enables cycle counter |
| `DWT_CYCCNT` | `0xE0001004` | 32-bit cycle count (read/write) |

## Requirements

- A debug probe session (`dbgprobe.connect`)
- ELF file attached (`dbgprobe.elf.attach`) — for symbol name resolution
- Any Cortex-M3/M4/M33 target with DWT support

## Tools

| Tool | Description |
|------|-------------|
| `cortex_m_profiler.dwt_setup` | Enable the DWT cycle counter (idempotent). Returns current CYCCNT value. Optional `cpu_freq_mhz` for time conversion |
| `cortex_m_profiler.dwt_read` | Read current CYCCNT and convert to microseconds. Optional `reset`, `cpu_freq_mhz` |
| `cortex_m_profiler.measure` | Measure CPU cycles between two breakpoint addresses. The main profiling tool. Optional `cpu_freq_mhz` |

### measure parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `session_id` | yes | Debug probe session |
| `start` | yes | Start symbol name or hex address (e.g. `"audio_frontend_process"` or `"0x00012D64"`) |
| `end` | yes | End symbol name or hex address |
| `trigger_symbol` | no | Write 1 to this int32 symbol before resuming (e.g. `"tflm_run_preprocess"`) |
| `timeout_s` | no | Max seconds to wait per breakpoint (default 5) |
| `cpu_freq_mhz` | no | CPU frequency in MHz for time conversion (default 64) |

### measure output

```json
{
  "start": "audio_frontend_process",
  "start_addr": "0x00012D64",
  "end": "micro_speech_infer",
  "end_addr": "0x00012ABC",
  "cycles": 3578762,
  "time_us": 55918.2,
  "time_ms": 55.92,
  "freq_mhz": 64
}
```

## Usage examples

```
# Connect and attach ELF
dbgprobe.connect(device="nRF52840_xxAA")
dbgprobe.elf.attach(path="build/peripheral_uart/zephyr/zephyr.elf")

# Enable cycle counter
cortex_m_profiler.dwt_setup()
→ {"enabled": true, "cyccnt": 169108477, "freq_mhz": 64}

# Measure preprocessing time (trigger preprocess pipeline, measure until inference starts)
cortex_m_profiler.measure(
    start="audio_frontend_process",
    end="micro_speech_infer",
    trigger_symbol="tflm_run_preprocess"
)
→ {"cycles": 3578762, "time_ms": 55.92}

# Measure inference time
cortex_m_profiler.measure(
    start="micro_speech_infer",
    end="micro_speech_get_label",
    trigger_symbol="tflm_run_preprocess"
)
→ {"cycles": 2008059, "time_ms": 31.38}

# Measure full pipeline (preprocess + inference)
cortex_m_profiler.measure(
    start="audio_frontend_process",
    end="micro_speech_get_label",
    trigger_symbol="tflm_run_preprocess"
)
→ {"cycles": 5587951, "time_ms": 87.31}

# Read raw cycle count
cortex_m_profiler.dwt_read(reset=true)
→ {"cyccnt": 42000000, "time_us": 656250.0, "time_ms": 656.25}
```

## Trigger flag protocol

When using `trigger_symbol`, the plugin:
1. Halts the target
2. Sets the start breakpoint
3. Writes 1 to the trigger symbol
4. Resumes the target
5. After measurement completes, waits for the firmware to clear the flag back to 0 before returning

This ensures back-to-back measurements work correctly — the firmware must clear the flag **after** work completes (not before), otherwise a race condition can cause the next trigger write to be overwritten.

## Validated measurements

Compared against firmware-instrumented profiling (RTT output with `k_cycle_get_32()`):

| Measurement | Plugin (DWT) | Firmware (RTT) | Match |
|-------------|-------------|----------------|-------|
| Preprocessing | 55.9 ms | 67 ms* | Yes |
| Inference | 31.4 ms | 31 ms | Yes |
| Full pipeline | 87.3 ms | 98 ms* | Yes |

*Firmware RTT timing includes additional overhead (function call setup, logging, state reset) not captured by the breakpoint-to-breakpoint measurement. The DWT measurements are more precise for the actual computation.
