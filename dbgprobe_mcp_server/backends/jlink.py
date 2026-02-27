"""J-Link backend — persistent JLinkGDBServer connection + JLinkExe for flash/erase."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

from dbgprobe_mcp_server.backend import Backend, ConnectConfig, DeviceSecuredError, ProbeInfo
from dbgprobe_mcp_server.gdb_client import GdbClient, GdbConnectionError, GdbProtocolError, GdbTimeoutError

logger = logging.getLogger("dbgprobe_mcp_server")

# ---------------------------------------------------------------------------
# Platform-specific search paths for J-Link executables
# ---------------------------------------------------------------------------

_JLINK_EXE_NAMES: dict[str, list[str]] = {
    "darwin": ["JLinkExe"],
    "linux": ["JLinkExe"],
    "win32": ["JLink.exe", "JLinkExe.exe"],
}

_JLINK_GDB_NAMES: dict[str, list[str]] = {
    "darwin": ["JLinkGDBServerCLExe"],
    "linux": ["JLinkGDBServerCLExe"],
    "win32": ["JLinkGDBServerCL.exe"],
}

_JLINK_RTT_NAMES: dict[str, list[str]] = {
    "darwin": ["JLinkRTTClient"],
    "linux": ["JLinkRTTClient"],
    "win32": ["JLinkRTTClient.exe"],
}

_COMMON_DIRS: dict[str, list[str]] = {
    "darwin": [
        "/Applications/SEGGER/JLink",
        "/opt/SEGGER/JLink",
        os.path.expanduser("~/Applications/SEGGER/JLink"),
    ],
    "linux": [
        "/opt/SEGGER/JLink",
        "/usr/bin",
        "/usr/local/bin",
    ],
    "win32": [
        r"C:\Program Files\SEGGER\JLink",
        r"C:\Program Files (x86)\SEGGER\JLink",
    ],
}


def _platform_key() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "win32"
    return "linux"


def _find_executable(env_var: str, names: list[str], common_dirs: list[str]) -> str | None:
    """Resolve a J-Link executable path.

    Priority:
    1. Environment variable (explicit path)
    2. shutil.which (PATH search)
    3. Common install directories
    """
    env_path = os.environ.get(env_var, "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)
        logger.warning("%s=%s is not a valid file", env_var, env_path)
        return None

    for name in names:
        found = shutil.which(name)
        if found:
            return found

    for d in common_dirs:
        dp = Path(d)
        if not dp.is_dir():
            continue
        for name in names:
            candidate = dp / name
            if candidate.is_file():
                return str(candidate)

    return None


def find_jlink_exe() -> str | None:
    pk = _platform_key()
    return _find_executable(
        "DBGPROBE_JLINK_PATH",
        _JLINK_EXE_NAMES.get(pk, []),
        _COMMON_DIRS.get(pk, []),
    )


def find_jlink_gdbserver() -> str | None:
    pk = _platform_key()
    return _find_executable(
        "DBGPROBE_JLINK_GDBSERVER_PATH",
        _JLINK_GDB_NAMES.get(pk, []),
        _COMMON_DIRS.get(pk, []),
    )


def find_jlink_rttclient() -> str | None:
    pk = _platform_key()
    return _find_executable(
        "DBGPROBE_JLINK_RTTCLIENT_PATH",
        _JLINK_RTT_NAMES.get(pk, []),
        _COMMON_DIRS.get(pk, []),
    )


# ---------------------------------------------------------------------------
# JLinkExe output parsing
# ---------------------------------------------------------------------------

_PROBE_LINE_RE = re.compile(
    r"J-Link\[(\d+)\].*?(?:S/N|Serial\s*number):\s*(\d+)",
    re.IGNORECASE,
)

_EMULATOR_RE = re.compile(
    r"S/N:\s*(\d+).*?Product:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

_SIMPLE_SN_RE = re.compile(r"Serial\s*number\s*:\s*(\d+)", re.IGNORECASE)


def _parse_probe_list(stdout: str) -> list[ProbeInfo]:
    """Parse ShowEmuList output from JLinkExe."""
    probes: list[ProbeInfo] = []
    seen: set[str] = set()

    for line in stdout.splitlines():
        m = _PROBE_LINE_RE.search(line)
        if m:
            sn = m.group(2)
            if sn not in seen:
                seen.add(sn)
                probes.append(
                    ProbeInfo(
                        serial=sn,
                        description=line.strip(),
                        backend="jlink",
                    )
                )

    if not probes:
        for line in stdout.splitlines():
            m = _EMULATOR_RE.search(line)
            if m:
                sn = m.group(1)
                if sn not in seen:
                    seen.add(sn)
                    probes.append(
                        ProbeInfo(
                            serial=sn,
                            description=m.group(2).strip(),
                            backend="jlink",
                        )
                    )

    return probes


_SECURED_KEYWORDS = [
    "device is secured",
    "approtect",
    "read protected",
    "protection enabled",
    "secure element",
    "unlock the device",
]


def _is_device_secured(stdout: str, stderr: str) -> bool:
    """Return True if JLinkExe output indicates the target is secured/read-protected."""
    lower = (stdout + "\n" + stderr).lower()
    return any(kw in lower for kw in _SECURED_KEYWORDS)


def _check_error(stdout: str, stderr: str) -> str | None:
    """Return an error message if JLinkExe output indicates failure."""
    combined = stdout + "\n" + stderr
    lower = combined.lower()
    if _is_device_secured(stdout, stderr):
        return "Target device is secured. Use dbgprobe.erase to mass-erase and unlock."
    if (
        "inittarget() error" in lower
        or "inittarget(): error" in lower
        or "inittarget() returned with error" in lower
    ):
        return (
            "InitTarget() failed. The device string may be wrong — "
            "note that the probe name (e.g. OB-nRF5340) refers to the debugger MCU, "
            "not the target chip. Check the actual target device on the board."
        )
    if "cannot connect" in lower or "could not connect" in lower:
        return "Cannot connect to target. Check wiring, power, and device name."
    if "no j-link found" in lower or "no emulators found" in lower:
        return "No J-Link probe found. Check USB connection."
    if "unknown device" in lower or "unknown command" in lower:
        for line in combined.splitlines():
            if "unknown" in line.lower():
                return line.strip()
    if "error" in lower:
        for line in combined.splitlines():
            ll = line.lower().strip()
            if ll.startswith("error") or "***error" in ll or "error:" in ll:
                return line.strip()
    return None


# ---------------------------------------------------------------------------
# Subprocess runner
# ---------------------------------------------------------------------------


async def _run_jlink_script(
    exe: str,
    script_commands: list[str],
    *,
    device: str | None = None,
    interface: str = "SWD",
    speed_khz: int = 4000,
    serial: str | None = None,
    timeout: float = 30.0,
    extra_args: list[str] | None = None,
    auto_connect: bool = True,
    exit_on_error: bool = True,
    no_gui: bool = True,
) -> tuple[str, str, int]:
    """Run JLinkExe with a temporary command script.

    Returns (stdout, stderr, returncode).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jlink", delete=False, prefix="dbgprobe_") as f:
        f.write("\n".join(script_commands) + "\n")
        script_path = f.name

    try:
        args = [exe]
        if device:
            args += ["-Device", device]
        args += ["-If", interface]
        args += ["-Speed", str(speed_khz)]
        if serial:
            args += ["-SelectEmuBySN", serial]
        if auto_connect:
            args += ["-AutoConnect", "1"]
        if exit_on_error:
            args += ["-ExitOnError", "1"]
        if no_gui:
            args += ["-NoGui", "1"]
        args += ["-CommandFile", script_path]
        if extra_args:
            args += extra_args

        logger.debug("Running: %s", " ".join(args))

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            await _kill_process(proc)
            raise TimeoutError(f"JLinkExe timed out after {timeout}s") from None
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        logger.debug("JLinkExe stdout:\n%s", stdout)
        if stderr.strip():
            logger.debug("JLinkExe stderr:\n%s", stderr)

        return stdout, stderr, proc.returncode or 0
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and wait for it to exit."""
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        pass


async def _run_jlink_list_probes(exe: str, timeout: float = 15.0) -> tuple[str, str, int]:
    """Run JLinkExe ShowEmuList — no device/interface needed."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jlink", delete=False, prefix="dbgprobe_") as f:
        f.write("ShowEmuList\nq\n")
        script_path = f.name

    try:
        args = [exe, "-NoGui", "1", "-CommandFile", script_path]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            await _kill_process(proc)
            raise TimeoutError(f"JLinkExe timed out after {timeout}s") from None
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return stdout, stderr, proc.returncode or 0
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# JLinkBackend
# ---------------------------------------------------------------------------


def _allocate_free_port() -> int:
    """Allocate a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Keyword that JLinkGDBServer prints when it's ready for connections.
_GDBSERVER_READY = "Waiting for GDB connection"

# Error keywords in GDBServer output.
_GDBSERVER_ERRORS = [
    "could not connect",
    "cannot connect",
    "no j-link",
    "no emulators found",
    "device is secured",
    "approtect",
    "unknown device",
]


class JLinkBackend(Backend):
    """J-Link backend — persistent JLinkGDBServer + GDB RSP for session ops,
    JLinkExe one-shot for flash/erase/list_probes."""

    name = "jlink"

    def __init__(self) -> None:
        self._exe: str | None = None
        self._gdbserver_path: str | None = None
        self._rttclient: str | None = None
        self._config: ConnectConfig | None = None

        # GDBServer persistent connection state
        self._gdbserver_proc: asyncio.subprocess.Process | None = None
        self._gdb_client: GdbClient | None = None
        self._gdb_port: int | None = None
        self._target_running: bool = False

        # RTT state
        self._rtt_port: int | None = None
        self._rtt_reader: asyncio.StreamReader | None = None
        self._rtt_writer: asyncio.StreamWriter | None = None
        self._rtt_buf: bytearray = bytearray()
        self._rtt_task: asyncio.Task | None = None
        self._rtt_event: asyncio.Event = asyncio.Event()
        self._rtt_buf_max: int = 64 * 1024  # 64 KB ring buffer
        self._rtt_total_read: int = 0
        self._rtt_total_written: int = 0

    @property
    def exe(self) -> str:
        if self._exe is None:
            raise ConnectionError("J-Link backend not connected. Call connect first.")
        return self._exe

    def _resolve_paths(self) -> dict[str, str | None]:
        self._exe = find_jlink_exe()
        self._gdbserver_path = find_jlink_gdbserver()
        self._rttclient = find_jlink_rttclient()
        return {
            "jlink_exe": self._exe,
            "jlink_gdbserver": self._gdbserver_path,
            "jlink_rttclient": self._rttclient,
        }

    # ------------------------------------------------------------------
    # list_probes — JLinkExe one-shot (session-less, unchanged)
    # ------------------------------------------------------------------

    async def list_probes(self) -> list[ProbeInfo]:
        exe = find_jlink_exe()
        if exe is None:
            raise FileNotFoundError(
                "JLinkExe not found. Install SEGGER J-Link Software or set DBGPROBE_JLINK_PATH."
            )
        stdout, stderr, rc = await _run_jlink_list_probes(exe)
        if rc != 0:
            err = _check_error(stdout, stderr) or f"JLinkExe exited with code {rc}"
            raise RuntimeError(err)
        return _parse_probe_list(stdout)

    # ------------------------------------------------------------------
    # connect — start JLinkGDBServer + GDB RSP handshake
    # ------------------------------------------------------------------

    async def connect(self, config: ConnectConfig) -> dict[str, Any]:
        paths = self._resolve_paths()
        if self._exe is None:
            raise FileNotFoundError(
                "JLinkExe not found. Install SEGGER J-Link Software or set DBGPROBE_JLINK_PATH."
            )
        if self._gdbserver_path is None:
            raise FileNotFoundError(
                "JLinkGDBServerCLExe not found. Install SEGGER J-Link Software "
                "or set DBGPROBE_JLINK_GDBSERVER_PATH."
            )

        self._config = config

        try:
            await self._start_gdbserver(config)
        except DeviceSecuredError:
            self._config = None
            raise
        except Exception:
            self._config = None
            await self._stop_gdbserver()
            raise

        result: dict[str, Any] = {"resolved_paths": paths}
        if self._gdb_port is not None:
            result["gdb_port"] = self._gdb_port
        return result

    async def _start_gdbserver(self, config: ConnectConfig) -> None:
        """Spawn JLinkGDBServer and connect via GDB RSP."""
        port = _allocate_free_port()
        self._gdb_port = port
        rtt_port = _allocate_free_port()
        self._rtt_port = rtt_port

        args = [self._gdbserver_path]  # type: ignore[list-item]
        if config.device:
            args += ["-device", config.device]
        args += ["-if", config.interface]
        args += ["-speed", str(config.speed_khz)]
        args += ["-port", str(port)]
        args += ["-RTTTelnetport", str(rtt_port)]
        args += ["-nogui", "-localhostonly", "1", "-noir"]
        if config.probe_serial:
            args += ["-select", f"USB={config.probe_serial}"]

        logger.debug("Starting GDBServer: %s", " ".join(args))

        self._gdbserver_proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for ready or error
        collected_output = ""
        try:
            deadline = asyncio.get_event_loop().time() + 10.0
            if self._gdbserver_proc.stdout is None:
                raise ConnectionError("JLinkGDBServer stdout not available")
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    line_bytes = await asyncio.wait_for(
                        self._gdbserver_proc.stdout.readline(),
                        timeout=max(remaining, 0.1),
                    )
                except TimeoutError:
                    break
                if not line_bytes:
                    break  # EOF — process exited
                line = line_bytes.decode("utf-8", errors="replace")
                collected_output += line
                logger.debug("GDBServer: %s", line.rstrip())

                if _GDBSERVER_READY.lower() in line.lower():
                    break  # Ready!

                # Check for fatal errors
                lower = line.lower()
                for kw in _GDBSERVER_ERRORS:
                    if kw in lower:
                        # Drain remaining output
                        try:
                            rest = await asyncio.wait_for(
                                self._gdbserver_proc.stdout.read(4096),
                                timeout=1.0,
                            )
                            collected_output += rest.decode("utf-8", errors="replace")
                        except TimeoutError:
                            pass
                        if _is_device_secured(collected_output, ""):
                            raise DeviceSecuredError(
                                f"Target device is secured. Use dbgprobe.erase to mass-erase and unlock."
                                f"\n\n[GDBServer output]\n{collected_output.strip()}"
                            )
                        err_msg = _check_error(collected_output, "")
                        if err_msg:
                            raise ConnectionError(
                                f"{err_msg}\n\n[GDBServer output]\n{collected_output.strip()}"
                            )
                        raise ConnectionError(
                            f"JLinkGDBServer failed to start.\n\n[GDBServer output]\n{collected_output.strip()}"
                        )
            else:
                # Timeout — check if process is still alive
                if self._gdbserver_proc.returncode is not None:
                    raise ConnectionError(
                        f"JLinkGDBServer exited unexpectedly (code {self._gdbserver_proc.returncode})."
                        f"\n\n[GDBServer output]\n{collected_output.strip()}"
                    )
                raise ConnectionError(
                    f"JLinkGDBServer did not become ready within 10s."
                    f"\n\n[GDBServer output]\n{collected_output.strip()}"
                )
        except (DeviceSecuredError, ConnectionError):
            raise
        except Exception as exc:
            raise ConnectionError(
                f"Failed to start JLinkGDBServer: {exc}\n\n[GDBServer output]\n{collected_output.strip()}"
            ) from exc

        # Connect GDB client
        self._gdb_client = GdbClient("127.0.0.1", port)
        try:
            await self._gdb_client.connect(timeout=5.0)
        except GdbConnectionError as exc:
            raise ConnectionError(f"Failed to connect to GDBServer: {exc}") from exc

        self._target_running = False

    # ------------------------------------------------------------------
    # disconnect — close GDB + kill GDBServer
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        if self.rtt_active:
            await self.rtt_stop()
        await self._close_gdb_client()
        await self._stop_gdbserver()
        self._config = None

    async def _close_gdb_client(self) -> None:
        if self._gdb_client is not None:
            try:
                await self._gdb_client.close()
            except Exception:
                pass
            self._gdb_client = None

    async def _stop_gdbserver(self) -> None:
        if self._gdbserver_proc is not None:
            try:
                self._gdbserver_proc.terminate()
                await asyncio.wait_for(self._gdbserver_proc.wait(), timeout=2.0)
            except TimeoutError:
                await _kill_process(self._gdbserver_proc)
            except ProcessLookupError:
                pass
            except Exception:
                pass
            self._gdbserver_proc = None
        self._gdb_port = None

    # ------------------------------------------------------------------
    # Session operations — all through GDB protocol
    # ------------------------------------------------------------------

    def _require_gdb(self) -> GdbClient:
        """Return the live GDB client or raise."""
        if self._config is None:
            raise ConnectionError("Not connected. Call dbgprobe.connect first.")
        if self._gdb_client is None or not self._gdb_client.connected:
            raise ConnectionError("GDB connection lost. Disconnect and reconnect.")
        return self._gdb_client

    async def _read_pc(self, client: GdbClient) -> int | None:
        """Read the PC via GDB 'p' command (register 15)."""
        try:
            return await client.read_register(15)
        except Exception:
            pass
        return None

    async def _get_pc(self, client: GdbClient, sr_registers: dict[int, int]) -> int | None:
        """Get PC from stop reply registers, falling back to register read."""
        pc = sr_registers.get(15)
        if pc is None:
            pc = await self._read_pc(client)
        return pc

    async def halt(self) -> dict[str, Any]:
        client = self._require_gdb()
        try:
            if self._target_running:
                # Try interrupt first; if target already stopped (e.g. hit a
                # breakpoint) before we got here, fall back to query_status.
                try:
                    sr = await client.halt()
                except GdbTimeoutError:
                    sr = await client.query_status()
            else:
                sr = await client.query_status()
            self._target_running = False
            pc = await self._get_pc(client, sr.registers)
            return {"pc": pc, "reason": sr.reason, "signal": sr.signal}
        except (GdbConnectionError, GdbProtocolError, GdbTimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def go(self) -> dict[str, Any]:
        client = self._require_gdb()
        try:
            await client.continue_execution()
            self._target_running = True
            return {}
        except (GdbConnectionError, GdbProtocolError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def step(self) -> dict[str, Any]:
        client = self._require_gdb()
        if self._target_running:
            # Target may have stopped asynchronously (e.g. breakpoint hit) —
            # check before refusing.
            try:
                sr = await asyncio.wait_for(client.wait_stop(timeout=0.1), timeout=0.2)
                self._target_running = False
            except (TimeoutError, GdbTimeoutError):
                raise ConnectionError("Target is running. Call dbgprobe.halt first.") from None
        try:
            sr = await client.step()
            self._target_running = False
            reason = "step" if sr.reason == "halted" else sr.reason
            pc = await self._get_pc(client, sr.registers)
            return {"pc": pc, "reason": reason, "signal": sr.signal}
        except (GdbConnectionError, GdbProtocolError, GdbTimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def status(self) -> dict[str, Any]:
        client = self._require_gdb()
        try:
            if self._target_running:
                # Poll for async stop (non-blocking check)
                try:
                    sr = await asyncio.wait_for(client.wait_stop(timeout=0.1), timeout=0.2)
                    self._target_running = False
                    pc = await self._get_pc(client, sr.registers)
                    return {
                        "state": "halted",
                        "pc": pc,
                        "reason": sr.reason,
                        "signal": sr.signal,
                    }
                except (TimeoutError, GdbTimeoutError):
                    return {"state": "running"}
            else:
                sr = await client.query_status()
                pc = await self._get_pc(client, sr.registers)
                return {
                    "state": "halted",
                    "pc": pc,
                    "reason": sr.reason,
                    "signal": sr.signal,
                }
        except (GdbConnectionError, GdbProtocolError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def reset(self, mode: str) -> dict[str, Any]:
        client = self._require_gdb()
        try:
            if mode == "hard":
                await client.monitor_command("reset 2")
            elif mode == "halt":
                await client.monitor_command("reset")
                await client.monitor_command("halt")
            else:
                await client.monitor_command("reset")

            # After any reset, query the GDB stub to sync state.
            # The stub knows whether the target is halted or running.
            try:
                sr = await client.query_status()
                self._target_running = False
                pc = await self._get_pc(client, sr.registers)
                return {
                    "mode": mode,
                    "pc": pc,
                    "state": "halted",
                }
            except GdbTimeoutError:
                # Target is running (no stop reply from ?)
                self._target_running = True
                return {"mode": mode, "state": "running"}
        except (GdbConnectionError, GdbProtocolError, GdbTimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def mem_read(self, address: int, length: int) -> bytes:
        client = self._require_gdb()
        try:
            return await client.read_memory(address, length)
        except (GdbConnectionError, GdbProtocolError, GdbTimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def mem_write(self, address: int, data: bytes) -> dict[str, Any]:
        client = self._require_gdb()
        try:
            await client.write_memory(address, data)
            return {"address": address, "length": len(data)}
        except (GdbConnectionError, GdbProtocolError, GdbTimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Breakpoints
    # ------------------------------------------------------------------

    async def set_breakpoint(self, address: int, bp_type: str = "sw") -> dict[str, Any]:
        client = self._require_gdb()
        gdb_type = 0 if bp_type == "sw" else 1
        try:
            await client.set_breakpoint(gdb_type, address)
            return {"address": address, "bp_type": bp_type}
        except (GdbConnectionError, GdbProtocolError, GdbTimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def clear_breakpoint(self, address: int) -> dict[str, Any]:
        client = self._require_gdb()
        # Try software first, then hardware
        for gdb_type in (0, 1):
            try:
                await client.clear_breakpoint(gdb_type, address)
                return {"address": address}
            except GdbProtocolError:
                continue
        # Fallback: J-Link monitor command to clear all breakpoints
        try:
            await client.monitor_command("clrbp")
            return {"address": address}
        except Exception:
            pass
        raise ConnectionError(f"Failed to clear breakpoint at 0x{address:08x}")

    async def clear_all_breakpoints(self) -> None:
        client = self._require_gdb()
        try:
            await client.monitor_command("clrbp")
        except (GdbConnectionError, GdbProtocolError, GdbTimeoutError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def list_breakpoints(self) -> list[dict[str, Any]]:
        # GDB RSP doesn't have a "list breakpoints" command — this is tracked
        # in session state (session.breakpoints).  The backend returns an empty
        # list; the handler layer is responsible for the authoritative list.
        return []

    # ------------------------------------------------------------------
    # Monitor — raw GDB monitor commands
    # ------------------------------------------------------------------

    async def monitor(self, command: str) -> str:
        client = self._require_gdb()
        return await client.monitor_command(command)

    # ------------------------------------------------------------------
    # RTT — Real-Time Transfer via JLinkGDBServer telnet port
    # ------------------------------------------------------------------

    @property
    def rtt_active(self) -> bool:
        return self._rtt_task is not None and not self._rtt_task.done()

    def rtt_status(self) -> dict[str, Any]:
        result: dict[str, Any] = {"active": self.rtt_active}
        if self.rtt_active:
            result["bytes_buffered"] = len(self._rtt_buf)
            result["total_read"] = self._rtt_total_read
            result["total_written"] = self._rtt_total_written
        return result

    async def rtt_start(self, address: int | None = None) -> dict[str, Any]:
        if self.rtt_active:
            raise ConnectionError("RTT is already active.")
        if self._rtt_port is None:
            raise ConnectionError("GDBServer not running — no RTT port available.")

        # Optional: set RTT control block address via GDB monitor command
        if address is not None:
            client = self._require_gdb()
            await client.monitor_command(f"exec SetRTTAddr {address:#x}")

        # Connect to GDBServer's RTT telnet port
        try:
            self._rtt_reader, self._rtt_writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._rtt_port),
                timeout=5.0,
            )
        except (OSError, TimeoutError) as exc:
            raise ConnectionError(f"Failed to connect to RTT telnet port {self._rtt_port}: {exc}") from exc

        # Reset state
        self._rtt_buf.clear()
        self._rtt_event.clear()
        self._rtt_total_read = 0
        self._rtt_total_written = 0

        # Start background reader
        self._rtt_task = asyncio.create_task(self._rtt_reader_loop())
        return {"rtt_port": self._rtt_port}

    async def _rtt_reader_loop(self) -> None:
        """Background task: read from RTT telnet socket into ring buffer."""
        try:
            while True:
                if self._rtt_reader is None:
                    break
                data = await self._rtt_reader.read(4096)
                if not data:
                    break  # EOF — server closed connection
                self._rtt_buf.extend(data)
                if len(self._rtt_buf) > self._rtt_buf_max:
                    excess = len(self._rtt_buf) - self._rtt_buf_max
                    del self._rtt_buf[:excess]
                self._rtt_event.set()
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass

    async def rtt_read(self, timeout: float = 0.1) -> bytes:
        if not self.rtt_active:
            raise ConnectionError("RTT is not active. Call rtt.start first.")
        # Wait for data if buffer is empty
        if not self._rtt_buf and timeout > 0:
            self._rtt_event.clear()
            try:
                await asyncio.wait_for(self._rtt_event.wait(), timeout=timeout)
            except TimeoutError:
                pass
        data = bytes(self._rtt_buf)
        self._rtt_buf.clear()
        self._rtt_event.clear()
        self._rtt_total_read += len(data)
        return data

    async def rtt_write(self, data: bytes) -> int:
        if not self.rtt_active:
            raise ConnectionError("RTT is not active. Call rtt.start first.")
        if self._rtt_writer is None:
            raise ConnectionError("RTT writer not available.")
        self._rtt_writer.write(data)
        await self._rtt_writer.drain()
        self._rtt_total_written += len(data)
        return len(data)

    async def rtt_stop(self) -> None:
        if self._rtt_task is not None:
            self._rtt_task.cancel()
            try:
                await self._rtt_task
            except (asyncio.CancelledError, Exception):
                pass
            self._rtt_task = None
        if self._rtt_writer is not None:
            try:
                self._rtt_writer.close()
                await self._rtt_writer.wait_closed()
            except Exception:
                pass
            self._rtt_writer = None
        self._rtt_reader = None
        self._rtt_buf.clear()
        self._rtt_event.clear()

    # ------------------------------------------------------------------
    # flash — teardown GDB → JLinkExe one-shot → restart GDB
    # ------------------------------------------------------------------

    async def flash(
        self,
        path: str,
        addr: int | None = None,
        verify: bool = True,
        reset_after: bool = True,
        config: ConnectConfig | None = None,
    ) -> dict[str, Any]:
        if config is not None:
            self._config = config
        self._require_config()
        if self._exe is None:
            self._resolve_paths()
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Firmware file not found: {path}")

        # 1. Halt target if running
        if self._target_running and self._gdb_client and self._gdb_client.connected:
            try:
                await self._gdb_client.halt()
            except Exception:
                pass
            self._target_running = False

        # 2. Tear down GDB connection — JLinkExe needs exclusive USB access
        had_gdb = self._gdbserver_proc is not None
        had_rtt = self.rtt_active
        if had_rtt:
            await self.rtt_stop()
        await self._close_gdb_client()
        await self._stop_gdbserver()

        # 3. Flash via JLinkExe one-shot
        cfg = self._require_config()
        ext = resolved.suffix.lower()
        commands: list[str] = []

        if ext in (".hex", ".ihex", ".elf"):
            commands.append(f'loadfile "{resolved}"')
        else:
            if addr is None:
                raise ValueError(
                    f"Binary file {resolved.name} requires an explicit address. "
                    "Pass addr parameter or use .hex/.elf format."
                )
            commands.append(f'loadbin "{resolved}",{addr:#x}')

        if verify:
            if ext in (".hex", ".ihex", ".elf"):
                # verifybin only works with raw binary files;
                # loadfile already reports errors for .hex/.elf.
                pass
            elif addr is not None:
                commands.append(f'verifybin "{resolved}",{addr:#x}')

        # If no GDB server will be restarted after flash, handle reset+go
        # in JLinkExe directly.  The "r" command halts the core (via
        # DEMCR.VC_CORERESET), so we must "g" (go) to resume execution
        # before quitting — otherwise the halted core leaves the debug
        # interface locked, preventing subsequent GDB server connections.
        # For session-based flash, GDB server restarts afterward and handles
        # reset+continue there, so skip this.
        if reset_after and not had_gdb:
            commands.append("r")
            commands.append("g")

        commands.append("q")

        stdout, stderr, _rc = await _run_jlink_script(
            self.exe,
            commands,
            device=cfg.device,
            interface=cfg.interface,
            speed_khz=cfg.speed_khz,
            serial=cfg.probe_serial,
        )
        combined = stdout + "\n" + stderr
        lower = combined.lower()
        flash_ok = "o.k." in lower or "flash download" in lower or "programmed" in lower
        if not flash_ok:
            # No positive confirmation — check for specific error
            err_msg = _check_error(stdout, stderr)
            if err_msg:
                raise ConnectionError(f"{err_msg}\n\n[JLink output]\n{stdout.strip()}")
            raise ConnectionError(f"Flash did not complete successfully.\n\n[JLink output]\n{stdout.strip()}")

        # 4. Restart GDBServer + reconnect (only if we had one before)
        if had_gdb and self._config is not None:
            await self._start_gdbserver(self._config)
            if reset_after and self._gdb_client is not None:
                try:
                    await self._gdb_client.monitor_command("reset")
                    await self._gdb_client.continue_execution()
                    self._target_running = True
                except Exception:
                    pass  # best-effort — flash already succeeded
            # Restart RTT if it was active before flash
            if had_rtt:
                try:
                    await self.rtt_start()
                except Exception:
                    logger.debug("Failed to restart RTT after flash", exc_info=True)

        result: dict[str, Any] = {
            "file": str(resolved),
            "verified": verify,
            "reset": reset_after,
            "breakpoints_cleared": True,
        }
        # Include JLinkExe output for diagnostics (warnings, APPROTECT, etc.)
        if stdout.strip():
            result["jlink_output"] = stdout.strip()
        return result

    # ------------------------------------------------------------------
    # erase — via GDB monitor command (session) or JLinkExe (session-less)
    # ------------------------------------------------------------------

    async def erase_via_gdb(
        self,
        start_addr: int | None = None,
        end_addr: int | None = None,
    ) -> dict[str, Any]:
        """Erase flash through the active GDB session (no USB contention)."""
        client = self._require_gdb()

        if start_addr is not None and end_addr is not None:
            cmd = f"flash erase {start_addr:#x} {end_addr:#x}"
        else:
            cmd = "flash erase"

        output = await client.monitor_command(cmd)

        return {"monitor_output": output}

    async def erase(
        self,
        config: ConnectConfig,
        start_addr: int | None = None,
        end_addr: int | None = None,
    ) -> dict[str, Any]:
        """Erase flash via JLinkExe one-shot (session-less)."""
        paths = self._resolve_paths()
        if self._exe is None:
            raise FileNotFoundError(
                "JLinkExe not found. Install SEGGER J-Link Software or set DBGPROBE_JLINK_PATH."
            )

        if start_addr is not None and end_addr is not None:
            commands = [f"erase {start_addr:#x} {end_addr:#x}", "r", "q"]
        else:
            commands = ["erase", "r", "q"]

        stdout, stderr, _rc = await _run_jlink_script(
            self._exe,
            commands,
            device=config.device,
            interface=config.interface,
            speed_khz=config.speed_khz,
            serial=config.probe_serial,
        )

        # Verify erase actually succeeded by looking for positive confirmation.
        combined = stdout + "\n" + stderr
        lower = combined.lower()
        if "erasing done" not in lower and "erase: completed" not in lower:
            err_msg = _check_error(stdout, stderr)
            if err_msg:
                raise ConnectionError(f"{err_msg}\n\n[JLink output]\n{stdout.strip()}")
            raise ConnectionError(f"Erase did not complete successfully.\n\n[JLink output]\n{stdout.strip()}")

        return {"resolved_paths": paths}

    # -- private helpers --

    def _require_config(self) -> ConnectConfig:
        if self._config is None:
            raise ConnectionError("Not connected. Call dbgprobe.connect first.")
        return self._config
