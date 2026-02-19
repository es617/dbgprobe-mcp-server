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
  jlink.py          → JLinkBackend (subprocess calls to JLinkExe)
state.py            → DbgProbeSession, ProbeState (session management)
handlers_probe.py   → 9 MCP tool definitions + handlers
server.py           → MCP server wiring, error dispatch, tracing
```

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
3. Register it in `backends/__init__.py`: `registry.register("mybackend", MyBackend)`
4. Add tests in `tests/test_mybackend.py`

The handler layer doesn't need changes — backends are selected by name via the registry.

## Tests

All tests run without debug hardware. They use `unittest.mock` for subprocess calls, `tmp_path` fixtures for filesystem isolation, and `monkeypatch` for environment variables.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_jlink.py -v

# Run a specific test class
python -m pytest tests/test_handlers_probe.py::TestMemRead -v
```

## MCP Inspector

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) lets you call tools without an agent:

```bash
npx @modelcontextprotocol/inspector python -m dbgprobe_mcp_server
```
