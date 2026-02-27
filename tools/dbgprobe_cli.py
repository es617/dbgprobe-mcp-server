#!/usr/bin/env python3
"""Interactive CLI for testing the Debug Probe MCP server over stdio.

Usage:
    python tools/dbgprobe_cli.py

Starts the MCP server as a subprocess and provides a simple REPL
for calling debug probe tools interactively.
"""

import json
import os
import readline  # noqa: F401 — enables arrow keys / history in input()
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# MCP client wrapper
# ---------------------------------------------------------------------------

_id_counter = 0


def _next_id():
    global _id_counter
    _id_counter += 1
    return _id_counter


class McpClient:
    def __init__(self):
        env = {**os.environ}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dbgprobe_mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def recv(self):
        """Read one JSON-RPC response (skip notifications)."""
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [raw] {line}")
                continue
            # Skip notifications (no "id" field)
            if "id" not in msg:
                continue
            return msg

    def call_tool(self, name, arguments=None):
        msg = {
            "jsonrpc": "2.0",
            "id": _next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        self.send(msg)
        resp = self.recv()
        if resp is None:
            print("  [error] No response from server")
            return None
        if "error" in resp:
            print(f"  [rpc error] {resp['error']}")
            return None
        # Extract the tool result text
        content = resp.get("result", {}).get("content", [])
        if content:
            text = content[0].get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return None

    def initialize(self):
        time.sleep(0.5)
        if self.proc.poll() is not None:
            stderr = self.proc.stderr.read()
            print(f"  [error] Server exited with code {self.proc.returncode}")
            if stderr:
                print(f"  [stderr] {stderr.strip()}")
            return None

        self.send(
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "dbgprobe-cli", "version": "0.1"},
                },
            }
        )
        resp = self.recv()
        if resp is None:
            stderr = self.proc.stderr.read()
            print("  [error] No response to initialize")
            if stderr:
                print(f"  [stderr] {stderr.strip()}")
            return None
        # Send initialized notification
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


def pp(data):
    if data is None:
        return
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# State to remember session ID between commands
last_session_id = None


def parse_addr(s):
    """Parse an address string: accept 0x prefix (hex) or plain decimal."""
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s)


def require_session(args_session=None):
    """Return session_id from args or last_session_id, or print error."""
    sid = args_session or last_session_id
    if not sid:
        print("  No session ID. Run 'connect' first.")
        return None
    return sid


# ---------------------------------------------------------------------------
# Commands — Session lifecycle
# ---------------------------------------------------------------------------


def cmd_probes(client, args):
    result = client.call_tool("dbgprobe.probes.list", {})
    pp(result)


def cmd_connect(client, args):
    global last_session_id
    if not args:
        print("  Usage: connect <device> [probe_id]")
        return
    params = {"device": args[0]}
    if len(args) > 1:
        params["probe_id"] = args[1]
    result = client.call_tool("dbgprobe.connect", params)
    if result and result.get("ok"):
        last_session_id = result.get("session_id")
        print(f"  Connected: session_id={last_session_id}")
    else:
        pp(result)


def cmd_disconnect(client, args):
    global last_session_id
    sid = require_session(args[0] if args else None)
    if not sid:
        return
    result = client.call_tool("dbgprobe.disconnect", {"session_id": sid})
    if result and result.get("ok"):
        print(f"  Disconnected: {sid}")
        if sid == last_session_id:
            last_session_id = None
    else:
        pp(result)


def cmd_sessions(client, args):
    result = client.call_tool("dbgprobe.connections.list", {})
    pp(result)


def cmd_status(client, args):
    sid = require_session(args[0] if args else None)
    if not sid:
        return
    result = client.call_tool("dbgprobe.status", {"session_id": sid})
    pp(result)


# ---------------------------------------------------------------------------
# Commands — Execution
# ---------------------------------------------------------------------------


def cmd_halt(client, args):
    sid = require_session()
    if not sid:
        return
    result = client.call_tool("dbgprobe.halt", {"session_id": sid})
    pp(result)


def cmd_go(client, args):
    sid = require_session()
    if not sid:
        return
    result = client.call_tool("dbgprobe.go", {"session_id": sid})
    pp(result)


def cmd_step(client, args):
    sid = require_session()
    if not sid:
        return
    params = {"session_id": sid}
    if args:
        try:
            params["count"] = int(args[0])
        except ValueError:
            print(f"  Invalid count: {args[0]}")
            return
    result = client.call_tool("dbgprobe.step", params)
    pp(result)


def cmd_reset(client, args):
    sid = require_session()
    if not sid:
        return
    params = {"session_id": sid}
    if args:
        mode = args[0].lower()
        if mode not in ("soft", "hard", "halt"):
            print(f"  Invalid reset mode: {mode} (use soft, hard, or halt)")
            return
        params["mode"] = mode
    result = client.call_tool("dbgprobe.reset", params)
    pp(result)


# ---------------------------------------------------------------------------
# Commands — Memory
# ---------------------------------------------------------------------------


def cmd_read(client, args):
    sid = require_session()
    if not sid:
        return
    if not args:
        print("  Usage: read <addr> [len]")
        return
    try:
        addr = parse_addr(args[0])
    except ValueError:
        print(f"  Invalid address: {args[0]}")
        return
    length = 4
    if len(args) > 1:
        try:
            length = int(args[1])
        except ValueError:
            print(f"  Invalid length: {args[1]}")
            return
    result = client.call_tool("dbgprobe.mem.read", {"session_id": sid, "address": addr, "length": length})
    pp(result)


def cmd_write(client, args):
    sid = require_session()
    if not sid:
        return
    if len(args) < 2:
        print("  Usage: write <addr> <hex_data>")
        return
    try:
        addr = parse_addr(args[0])
    except ValueError:
        print(f"  Invalid address: {args[0]}")
        return
    result = client.call_tool(
        "dbgprobe.mem.write",
        {"session_id": sid, "address": addr, "data": args[1]},
    )
    pp(result)


# ---------------------------------------------------------------------------
# Commands — Flash
# ---------------------------------------------------------------------------


def cmd_flash(client, args):
    sid = require_session()
    if not sid:
        return
    if not args:
        print("  Usage: flash <path> [addr]")
        return
    params = {"session_id": sid, "path": args[0]}
    if len(args) > 1:
        try:
            params["address"] = parse_addr(args[1])
        except ValueError:
            print(f"  Invalid address: {args[1]}")
            return
    result = client.call_tool("dbgprobe.flash", params)
    pp(result)


def cmd_erase(client, args):
    sid = require_session()
    if not sid:
        return
    params = {"session_id": sid}
    if len(args) >= 2:
        try:
            params["start"] = parse_addr(args[0])
            params["end"] = parse_addr(args[1])
        except ValueError:
            print("  Invalid address. Usage: erase [start end]")
            return
    result = client.call_tool("dbgprobe.erase", params)
    pp(result)


# ---------------------------------------------------------------------------
# Commands — Breakpoints
# ---------------------------------------------------------------------------


def cmd_bp(client, args):
    sid = require_session()
    if not sid:
        return
    if not args:
        print("  Usage: bp <addr|symbol> [hw]")
        return
    params = {"session_id": sid, "address": args[0]}
    if len(args) > 1 and args[1].lower() == "hw":
        params["type"] = "hw"
    result = client.call_tool("dbgprobe.breakpoint.set", params)
    pp(result)


def cmd_bpc(client, args):
    sid = require_session()
    if not sid:
        return
    if not args:
        print("  Usage: bpc <addr>")
        return
    result = client.call_tool("dbgprobe.breakpoint.clear", {"session_id": sid, "address": args[0]})
    pp(result)


def cmd_bpl(client, args):
    sid = require_session()
    if not sid:
        return
    result = client.call_tool("dbgprobe.breakpoint.list", {"session_id": sid})
    pp(result)


# ---------------------------------------------------------------------------
# Commands — RTT (subcommands)
# ---------------------------------------------------------------------------


def cmd_rtt(client, args):
    if not args:
        print("  Usage: rtt <start|stop|read|write|status> [args...]")
        return
    sub = args[0].lower()
    sid = require_session()
    if not sid:
        return

    if sub == "start":
        params = {"session_id": sid}
        if len(args) > 1:
            try:
                params["address"] = parse_addr(args[1])
            except ValueError:
                print(f"  Invalid address: {args[1]}")
                return
        result = client.call_tool("dbgprobe.rtt.start", params)
        pp(result)

    elif sub == "stop":
        result = client.call_tool("dbgprobe.rtt.stop", {"session_id": sid})
        pp(result)

    elif sub == "read":
        result = client.call_tool("dbgprobe.rtt.read", {"session_id": sid})
        pp(result)

    elif sub == "write":
        if len(args) < 2:
            print("  Usage: rtt write <text>")
            return
        text = " ".join(args[1:])
        result = client.call_tool("dbgprobe.rtt.write", {"session_id": sid, "data": text})
        pp(result)

    elif sub == "status":
        result = client.call_tool("dbgprobe.rtt.status", {"session_id": sid})
        pp(result)

    else:
        print(f"  Unknown rtt subcommand: {sub}")
        print("  Subcommands: start, stop, read, write, status")


# ---------------------------------------------------------------------------
# Commands — SVD (subcommands)
# ---------------------------------------------------------------------------


def cmd_svd(client, args):
    if not args:
        print("  Usage: svd <load|read|write|list> [args...]")
        return
    sub = args[0].lower()
    sid = require_session()
    if not sid:
        return

    if sub == "load":
        if len(args) < 2:
            print("  Usage: svd load <path>")
            return
        result = client.call_tool("dbgprobe.svd.attach", {"session_id": sid, "path": args[1]})
        pp(result)

    elif sub == "read":
        if len(args) < 2:
            print("  Usage: svd read <periph.reg>")
            return
        result = client.call_tool("dbgprobe.svd.read", {"session_id": sid, "register": args[1]})
        pp(result)

    elif sub == "write":
        if len(args) < 3:
            print("  Usage: svd write <periph.reg> <value>")
            return
        result = client.call_tool(
            "dbgprobe.svd.write",
            {"session_id": sid, "register": args[1], "value": args[2]},
        )
        pp(result)

    elif sub == "list":
        result = client.call_tool("dbgprobe.svd.list_peripherals", {"session_id": sid})
        pp(result)

    else:
        print(f"  Unknown svd subcommand: {sub}")
        print("  Subcommands: load, read, write, list")


# ---------------------------------------------------------------------------
# Commands — ELF
# ---------------------------------------------------------------------------


def cmd_elf(client, args):
    sid = require_session()
    if not sid:
        return
    if not args:
        print("  Usage: elf <path>")
        return
    result = client.call_tool("dbgprobe.elf.attach", {"session_id": sid, "path": args[0]})
    pp(result)


# ---------------------------------------------------------------------------
# Commands — Utility
# ---------------------------------------------------------------------------


def cmd_raw(client, args):
    """Send a raw tool call: raw <tool_name> <json_args>"""
    if not args:
        print("  Usage: raw <tool_name> [json_args]")
        return
    tool_name = args[0]
    arguments = {}
    if len(args) > 1:
        try:
            arguments = json.loads(" ".join(args[1:]))
        except json.JSONDecodeError as e:
            print(f"  Invalid JSON: {e}")
            return
    result = client.call_tool(tool_name, arguments)
    pp(result)


# ---------------------------------------------------------------------------
# Command table
# ---------------------------------------------------------------------------

COMMANDS = {
    # Session lifecycle
    "probes": (cmd_probes, "probes — List attached probes"),
    "connect": (cmd_connect, "connect <device> [probe_id] — Connect to target"),
    "disconnect": (cmd_disconnect, "disconnect — Disconnect session"),
    "sessions": (cmd_sessions, "sessions — List open sessions"),
    "status": (cmd_status, "status — Show target status (halted/running, PC)"),
    # Execution
    "halt": (cmd_halt, "halt — Halt the target"),
    "go": (cmd_go, "go — Resume execution"),
    "step": (cmd_step, "step [count] — Single-step (default 1)"),
    "reset": (cmd_reset, "reset [soft|hard|halt] — Reset target (default soft)"),
    # Memory
    "read": (cmd_read, "read <addr> [len] — Read memory (default 4 bytes)"),
    "write": (cmd_write, "write <addr> <hex> — Write memory"),
    # Flash
    "flash": (cmd_flash, "flash <path> [addr] — Flash binary to target"),
    "erase": (cmd_erase, "erase [start end] — Erase flash (full chip if no args)"),
    # Breakpoints
    "bp": (cmd_bp, "bp <addr|symbol> [hw] — Set breakpoint"),
    "bpc": (cmd_bpc, "bpc <addr> — Clear breakpoint"),
    "bpl": (cmd_bpl, "bpl — List breakpoints"),
    # RTT
    "rtt": (cmd_rtt, "rtt <start|stop|read|write|status> — RTT commands"),
    # SVD
    "svd": (cmd_svd, "svd <load|read|write|list> — SVD register commands"),
    # ELF
    "elf": (cmd_elf, "elf <path> — Load ELF symbol file"),
    # Utility
    "raw": (cmd_raw, "raw <tool_name> [json_args] — Call any tool directly"),
}


def cmd_help():
    print("\nAvailable commands:\n")
    for _name, (_, desc) in COMMANDS.items():
        print(f"  {desc}")
    print("\n  help — Show this help")
    print("  quit — Exit\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("Debug Probe MCP CLI — interactive test client")
    print("Type 'help' for commands, 'quit' to exit.\n")

    client = McpClient()
    resp = client.initialize()
    if resp:
        print("  Server initialized.")
    else:
        print("  [error] Failed to initialize server.")
        return

    print("  Session ID memory: auto-tracked from last connect\n")

    try:
        while True:
            try:
                line = input("dbgprobe> ").strip()
            except EOFError:
                break
            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                cmd_help()
            elif cmd in COMMANDS:
                try:
                    COMMANDS[cmd][0](client, args)
                except Exception as e:
                    print(f"  [error] {e}")
            else:
                print(f"  Unknown command: {cmd}. Type 'help' for commands.")
    except KeyboardInterrupt:
        print()
    finally:
        client.close()
        print("  Bye.")


if __name__ == "__main__":
    main()
