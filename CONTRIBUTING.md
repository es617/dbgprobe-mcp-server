# Contributing

## Dev setup

```bash
# Clone and install in editable mode with test dependencies
git clone https://github.com/es617/dbgprobe-mcp-server.git
cd dbgprobe-mcp-server
pip install -e ".[test]"

# Run tests (no debug hardware needed)
python -m pytest tests/ -v

# Lint
ruff check . && ruff format --check .
```

## Architecture

The server uses a backend-agnostic architecture. Each probe vendor is implemented as a `Backend` subclass:

```
backend.py          → Backend ABC, BackendRegistry, ProbeInfo, ConnectConfig
backends/
  __init__.py       → registers backends in the global registry
  jlink.py          → JLinkBackend (hybrid: GDBServer + JLinkExe)
gdb_client.py       → Async GDB Remote Serial Protocol client over TCP
state.py            → DbgProbeSession, Breakpoint, ProbeState (session management)
handlers_probe.py   → 15 MCP tool definitions + handlers
server.py           → MCP server wiring, error dispatch, tracing
```

## J-Link hybrid architecture

The J-Link backend uses a **hybrid approach** — two SEGGER tools running concurrently against the same probe:

```
                                    ┌──────────────────────┐
                                    │  JLinkGDBServer      │  persistent process
  MCP Server ──TCP (GDB RSP)──────►│  (GDB stub over USB) │──────┐
       │                            └──────────────────────┘      │
       │                                                          │ USB  ┌────────┐
       │                            ┌──────────────────────┐      ├─────►│ J-Link │──SWD──► Target
       └──subprocess (one-shot)────►│  JLinkExe            │──────┘      └────────┘
                                    │  (flash/erase cmds)  │
                                    └──────────────────────┘
```

### Why two tools?

**JLinkGDBServer** provides a persistent TCP connection speaking GDB Remote Serial Protocol. This gives us:
- Stateful debug sessions (halt, step, continue, breakpoints)
- Low-latency memory reads/writes over an open TCP socket
- Async stop notifications (breakpoint hit, target halted)

**JLinkExe** (Commander) handles flash and erase via one-shot subprocess calls. This gives us:
- Native support for all firmware formats (.hex, .elf, .bin) without parsing
- Battle-tested flash programming with built-in error detection
- Erase commands including full chip erase for secured/APPROTECT devices

### Concurrent probe access

SEGGER's J-Link software stack supports multiple simultaneous connections to the same probe. JLinkExe can flash or erase while JLinkGDBServer holds a persistent connection — no teardown/reconnect needed. This was validated on nRF52840 but may be device-specific; the backend halts the target via GDB before invoking JLinkExe as a safety measure.

### Which tool handles what

| Operation | Tool | Protocol |
|---|---|---|
| `connect` | JLinkGDBServer | Spawns process, TCP + GDB RSP handshake |
| `disconnect` | JLinkGDBServer | Closes TCP, kills process |
| `halt`, `go`, `step`, `status` | GDB RSP | `\x03`, `c`, `s`, `?` packets |
| `mem.read`, `mem.write` | GDB RSP | `m`/`M` packets |
| `breakpoint.set/clear` | GDB RSP | `Z`/`z` packets |
| `reset` | GDB RSP | `qRcmd` (monitor command) |
| `flash` | JLinkExe | `loadfile`/`loadbin` (one-shot subprocess) |
| `erase` | JLinkExe | `erase` (one-shot subprocess, session-less) |
| `list_probes` | JLinkExe | `ShowEmuList` (one-shot subprocess, session-less) |

### GDB client internals

`gdb_client.py` is a zero-dependency async GDB RSP client (~300 lines):

- Background `_read_loop` task reads the TCP stream continuously
- Packets are dispatched to either `_response_event` (command replies) or `_stop_event` (async stop notifications from continue/interrupt)
- Console output (`O` packets) is logged and discarded
- Stop replies distinguish breakpoints from generic halts via extended T-packet fields (`swbreak:`, `hwbreak:`)
- Memory operations are chunked (1024 bytes read, 512 bytes write) to stay within GDB packet limits

### Flash flow

```
flash(path, reset_after=True)
  1. Halt target via GDB (if running)
  2. JLinkExe loadfile <path> (GDBServer stays running)
  3. Check JLinkExe output for success confirmation
  4. Reset + continue via GDB (if reset_after)
  5. Clear session breakpoints (firmware changed)
```

### Breakpoint tracking

GDB RSP has no "list breakpoints" command — the protocol only supports set (`Z`) and clear (`z`). Breakpoints are tracked in `session.breakpoints` (a dict of address → Breakpoint). The handler layer is the authoritative source; the backend just forwards to the GDB stub. Flash clears all tracked breakpoints since the firmware changed.

Software breakpoints (`Z0`) are the default — this matches what GDB uses. JLinkGDBServer handles flash patching internally, so `Z0` works on both flash and RAM. Hardware breakpoints (`Z1`) are available via `bp_type: "hw"` but are limited by FPB slots (typically 4-6 on Cortex-M).

### Continue from breakpoint (remove/re-insert dance)

When the target is halted at an address with an active breakpoint, `go` must remove the breakpoint before sending `c`, then re-insert it after. Without this, the breakpoint re-triggers immediately. This is standard GDB behavior — confirmed by sniffing traffic with `tools/gdb_proxy.py`.

## How tools are registered

Each `handlers_*.py` file exports:

```python
TOOLS: list[Tool] = [...]          # Tool definitions with names, descriptions, schemas
HANDLERS: dict[str, Callable] = {  # Maps tool name → async handler function
    "dbgprobe.tool_name": handle_fn,
}
```

In `server.py`, these are merged inside `build_server()`:

```python
tools = handlers_probe.TOOLS + handlers_introspection.TOOLS + handlers_spec.TOOLS + ...
handlers = {**handlers_probe.HANDLERS, **handlers_introspection.HANDLERS, ...}
```

Plugin handlers are added via `handlers_plugin.make_handlers()`.

## Handler pattern

Every handler has the same signature:

```python
async def handle_something(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
```

- `state` — shared probe state (sessions, connections)
- `args` — parsed tool arguments from the MCP client
- Returns `_ok(key=value)` on success or `_err(code, message)` on failure

The dispatcher in `server.py` catches common exceptions (KeyError, ValueError, ConnectionError, TimeoutError, etc.) and converts them to error responses automatically.

## Adding a new tool

1. Add the `Tool(...)` definition to the appropriate `handlers_*.py` `TOOLS` list
2. Write the handler function following the signature above
3. Add the mapping to the `HANDLERS` dict
4. Add tests in the corresponding `test_*.py`

Tool names follow the convention `dbgprobe.<action>` for core tools (e.g., `dbgprobe.halt`, `dbgprobe.flash`) and `dbgprobe.<category>.<action>` for subsystems (e.g., `dbgprobe.mem.read`, `dbgprobe.spec.attach`).

## Adding a new backend

1. Create `backends/mybackend.py` with a class that extends `Backend`
2. Implement all abstract methods (`list_probes`, `connect`, `disconnect`, `reset`, `halt`, `go`, `flash`, `mem_read`, `mem_write`)
3. Optionally override concrete methods (`step`, `status`, `set_breakpoint`, `clear_breakpoint`, `list_breakpoints`) — these default to `NotImplementedError`
4. Register it in `backends/__init__.py`: `registry.register("mybackend", MyBackend)`
5. Add tests in `tests/test_mybackend.py`

The handler layer doesn't need changes — backends are selected by name via the registry.

## Tests

All tests run without debug hardware. They use `unittest.mock` for subprocess calls, `tmp_path` fixtures for filesystem isolation, and `monkeypatch` for environment variables. The GDB client tests use a mock TCP server (`MockGdbServer`) that speaks the GDB RSP protocol.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_jlink.py -v

# Run a specific test class
python -m pytest tests/test_handlers_probe.py::TestMemRead -v
```

## GDB RSP traffic proxy

`tools/gdb_proxy.py` is a TCP proxy that sits between a GDB client and JLinkGDBServer, logging every packet in both directions. Useful for understanding what GDB does under the hood and comparing it with our GDB client implementation.

### Setup

Terminal 1 — start JLinkGDBServer:
```bash
/Applications/SEGGER/JLink/JLinkGDBServerCLExe \
  -device NRF52840_XXAA -if SWD -speed 4000 -port 2331 \
  -nogui -localhostonly 1
```

Terminal 2 — start the proxy:
```bash
python tools/gdb_proxy.py 3333 2331
```

Terminal 3 — connect GDB through the proxy:
```bash
# Find GDB in your toolchain (Nordic/Zephyr example):
/opt/nordic/ncs/toolchains/e5f4758bcf/opt/zephyr-sdk/arm-zephyr-eabi/bin/arm-zephyr-eabi-gdb \
  build/peripheral_uart/zephyr/zephyr.elf

(gdb) target remote localhost:3333
```

All traffic is printed to the terminal and logged to `/tmp/gdb_rsp_traffic.log`.

### Example: breakpoint behavior

Software breakpoint (GDB default):
```
(gdb) break main
(gdb) continue        # runs, hits breakpoint
(gdb) continue        # GDB sends: z0 (remove) → Z0 (re-insert) → c
```

Hardware breakpoint:
```
(gdb) hbreak main
(gdb) continue        # runs, hits breakpoint
(gdb) continue        # GDB sends: z1 (remove) → Z1 (re-insert) → c
```

Key finding: GDB removes the breakpoint before `c` and re-inserts it after. This is required for hardware breakpoints (FPB re-triggers if armed at current PC) and is standard practice for software breakpoints too.

### GDB RSP quick reference

| Packet | Direction | Meaning |
|---|---|---|
| `$c#63` | GDB→stub | Continue |
| `$s#73` | GDB→stub | Single step |
| `\x03` | GDB→stub | Interrupt (halt) |
| `$?#3f` | GDB→stub | Query status |
| `$Z0,addr,kind` | GDB→stub | Set software breakpoint |
| `$Z1,addr,kind` | GDB→stub | Set hardware breakpoint |
| `$z0,addr,kind` | GDB→stub | Remove software breakpoint |
| `$z1,addr,kind` | GDB→stub | Remove hardware breakpoint |
| `$m addr,len` | GDB→stub | Read memory |
| `$M addr,len:hex` | GDB→stub | Write memory |
| `$g` | GDB→stub | Read all registers |
| `$p reg` | GDB→stub | Read single register |
| `$qRcmd,hex` | GDB→stub | Monitor command |
| `$T05...` | stub→GDB | Stop reply (SIGTRAP) |
| `$S05` | stub→GDB | Stop reply (no thread info) |
| `+` | either | ACK |

`kind`: 2 = Thumb (Cortex-M), 4 = ARM (Cortex-A/R).

## MCP Inspector

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) lets you call tools without an agent:

```bash
npx @modelcontextprotocol/inspector python -m dbgprobe_mcp_server
```
