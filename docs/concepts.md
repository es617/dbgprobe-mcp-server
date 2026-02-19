# Concepts

How the Debug Probe MCP server works, and how the pieces fit together.

---

## Architecture

The server gives an AI agent (like Claude) a set of debug probe tools over the MCP protocol. The agent uses these tools to interact with embedded targets — listing probes, connecting, flashing firmware, reading/writing memory, and controlling execution.

Everything is **stateful**: sessions persist across tool calls. The agent doesn't have to reconnect between each operation.

```
┌─────────────┐       stdio/MCP        ┌─────────────────────┐   subprocess    ┌──────────┐     SWD/JTAG    ┌────────┐
│  AI Agent   │ ◄────────────────────► │  Debug Probe MCP    │ ◄────────────► │ JLinkExe │ ◄──────────────► │ Target │
│ (Claude etc)│   structured JSON      │  Server             │   cmd scripts  │          │    debug probe   │  MCU   │
└─────────────┘                        └─────────────────────┘                └──────────┘                  └────────┘
```

### Backend abstraction

The server is backend-agnostic. A `Backend` abstract class defines the interface that all probe backends implement:

```
Backend (ABC)
  ├── JLinkBackend    ← implemented (v0)
  ├── OpenOCDBackend  ← planned
  └── PyOCDBackend    ← planned
```

Tool names (`dbgprobe.*`) stay the same regardless of backend. The backend is selected via `DBGPROBE_BACKEND` env var or the `backend` argument to `dbgprobe.connect`.

### How J-Link operations work

Each operation (halt, reset, flash, mem.read, etc.) is a **one-shot subprocess call** to JLinkExe:

1. Generate a temporary `.jlink` command script file
2. Run `JLinkExe -Device ... -If SWD -Speed 4000 -CommandFile script.jlink`
3. Parse stdout/stderr for results or errors
4. Clean up temp files

This is simple and reliable — no persistent process to manage. The tradeoff is slightly higher latency per operation compared to a persistent connection.

### Sessions

A session represents a connection to a specific probe + target combination. Created by `dbgprobe.connect`, it stores:

- The backend instance (e.g. JLinkBackend)
- Resolved configuration (device, interface, speed, probe serial)
- Optional attached protocol spec
- Timestamps

Multiple sessions can be open simultaneously (up to 10 by default).

---

## Security model

Plugins can execute arbitrary code, so they are opt-in:

| `DBGPROBE_MCP_PLUGINS` | Effect |
|---|---|
| *(unset)* | Plugins disabled — no loading, no discovery |
| `all` | All plugins in `.dbgprobe_mcp/plugins/` are loaded |
| `name1,name2` | Only named plugins are loaded |

The agent cannot bypass these flags. It can only use the tools the server exposes, and the server enforces the policy.

Path containment is enforced for all filesystem operations:
- **Plugins** must be inside `.dbgprobe_mcp/plugins/`
- **Specs** must be inside the project directory (parent of `.dbgprobe_mcp/`)
- **Traces** always write to `.dbgprobe_mcp/traces/trace.jsonl` (not configurable)

---

## Protocol specs — teaching the agent about your target

Specs are markdown files that describe a target device — register maps, memory layout, boot sequences, and multi-step flows.

```
.dbgprobe_mcp/
  specs/
    nrf52840.md      # target documentation
```

The agent reads specs to understand what a target can do. Without a spec, the agent can still connect and interact with the probe, but it won't know what memory addresses matter or what register values mean.

### How specs help the agent

```
Without spec:                         With spec:
  "I connected to the target.          "This is an nRF52840. The FICR is
   I can read memory but I don't        at 0x10000000. The device ID is
   know what addresses to read."        at offset 0x60. The UICR
                                        customer registers start at
                                        0x10001080."
```

### Creating a spec

Tell the agent about your target — paste a datasheet excerpt, describe the memory map, or just let it explore and document what it finds. The agent generates the spec file, registers it, and references it in future sessions.

---

## Plugins — giving the agent shortcut tools

Plugins add device-specific tools to the server. Instead of the agent composing raw `mem.read`/`mem.write` sequences, a plugin provides high-level operations like `nrf52.read_ficr` or `stm32.read_uid`.

```
.dbgprobe_mcp/
  plugins/
    nrf52.py         # adds nrf52.* tools
    stm32.py         # adds stm32.* tools
```

### What a plugin provides

```python
TOOLS = [...]       # Tool definitions the agent can call
HANDLERS = {...}    # Implementation for each tool
META = {...}        # Optional: matching hints (description)
```

### AI-authored plugins

The agent can create plugins. Using `dbgprobe.plugin.template`, it generates a skeleton, fills in the implementation, and saves it to `.dbgprobe_mcp/plugins/`. After hot-reload, the new tools are available. Review generated plugins before enabling them.

---

## How specs and plugins connect

| | Spec | Plugin |
|---|---|---|
| **What** | Documentation | Code |
| **Purpose** | Teach the agent about the target | Give the agent shortcut tools |
| **Format** | Freeform markdown | Python module |
| **Required?** | No — agent can still use raw tools | No — agent can compose raw operations |
| **Bound to** | A session (via `dbgprobe.spec.attach`) | Global (all sessions) |

They work together:

```
                    ┌──────────────────┐
                    │  Protocol Spec   │──── "What can this target do?"
                    │  (markdown)      │     Agent reads and reasons
                    └────────┬─────────┘
                             │
                     agent reasons about
                     the spec, or creates
                             │
                    ┌────────▼─────────┐
                    │     Plugin       │──── "Shortcut tools for this target"
                    │  (Python module) │     Agent calls directly
                    └──────────────────┘
```

---

## The agent's decision flow

After connecting, the agent follows this flow:

```
Connect to probe
       │
       ▼
Check dbgprobe.spec.list ──── matching spec? ──── yes ──► dbgprobe.spec.attach
       │                                                       │
       │ no                                                    │
       ▼                                                       ▼
Check dbgprobe.plugin.list ◄──────────────────────── Check dbgprobe.plugin.list
       │                                                       │
       │                                                       ▼
       ▼                                             Present options:
  matching plugin? ─── yes ──► use plugin tools       • use plugin tools
       │                                              • follow spec manually
       │ no                                           • extend plugin
       ▼                                              • create new plugin
  Ask user / explore
  with raw debug probe tools
```

The tool descriptions guide it through each step.

---

## J-Link executable auto-detection

The server searches for J-Link executables in this order:

1. **Environment variable** — `DBGPROBE_JLINK_PATH` (or `_GDBSERVER_PATH`, `_RTTCLIENT_PATH`)
2. **PATH** — `shutil.which()` search
3. **Common install directories** — platform-specific locations

| Platform | Search directories |
|---|---|
| macOS | `/Applications/SEGGER/JLink/`, `~/Applications/SEGGER/JLink/` |
| Linux | `/opt/SEGGER/JLink/`, `/usr/bin/`, `/usr/local/bin/` |
| Windows | `C:\Program Files\SEGGER\JLink\`, `C:\Program Files (x86)\SEGGER\JLink\` |

The resolved paths are included in the `dbgprobe.connect` response so the agent (and user) can verify which executables are being used.
