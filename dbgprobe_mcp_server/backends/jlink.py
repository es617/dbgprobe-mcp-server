"""J-Link backend — drives targets via JLinkExe (Commander) subprocess."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from dbgprobe_mcp_server.backend import Backend, ConnectConfig, DeviceSecuredError, ProbeInfo

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


class JLinkBackend(Backend):
    """J-Link backend using JLinkExe subprocess calls."""

    name = "jlink"

    def __init__(self) -> None:
        self._exe: str | None = None
        self._gdbserver: str | None = None
        self._rttclient: str | None = None
        self._config: ConnectConfig | None = None

    @property
    def exe(self) -> str:
        if self._exe is None:
            raise ConnectionError("J-Link backend not connected. Call connect first.")
        return self._exe

    def _resolve_paths(self) -> dict[str, str | None]:
        self._exe = find_jlink_exe()
        self._gdbserver = find_jlink_gdbserver()
        self._rttclient = find_jlink_rttclient()
        return {
            "jlink_exe": self._exe,
            "jlink_gdbserver": self._gdbserver,
            "jlink_rttclient": self._rttclient,
        }

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

    async def connect(self, config: ConnectConfig) -> dict[str, Any]:
        paths = self._resolve_paths()
        if self._exe is None:
            raise FileNotFoundError(
                "JLinkExe not found. Install SEGGER J-Link Software or set DBGPROBE_JLINK_PATH."
            )
        self._config = config

        # Validate connectivity with a simple script.
        commands = ["h", "q"]
        stdout, stderr, _rc = await _run_jlink_script(
            self._exe,
            commands,
            device=config.device,
            interface=config.interface,
            speed_khz=config.speed_khz,
            serial=config.probe_serial,
        )

        if _is_device_secured(stdout, stderr):
            self._config = None
            raise DeviceSecuredError(
                f"Target device is secured. Use dbgprobe.erase to mass-erase and unlock."
                f"\n\n[JLink output]\n{stdout.strip()}"
            )
        err_msg = _check_error(stdout, stderr)
        if err_msg:
            self._config = None
            raise ConnectionError(f"{err_msg}\n\n[JLink output]\n{stdout.strip()}")

        return {"resolved_paths": paths}

    async def disconnect(self) -> None:
        self._config = None

    async def reset(self, mode: str) -> dict[str, Any]:
        cfg = self._require_config()
        if mode == "halt":
            commands = ["r", "h", "q"]
        elif mode == "hard":
            commands = ["RSetType 2", "r", "g", "q"]
        else:
            commands = ["r", "g", "q"]

        stdout, stderr, _rc = await self._run(commands, cfg)
        err_msg = _check_error(stdout, stderr)
        if err_msg:
            raise ConnectionError(err_msg)
        return {"mode": mode}

    async def halt(self) -> dict[str, Any]:
        cfg = self._require_config()
        stdout, stderr, _rc = await self._run(["h", "q"], cfg)
        err_msg = _check_error(stdout, stderr)
        if err_msg:
            raise ConnectionError(err_msg)
        return {}

    async def go(self) -> dict[str, Any]:
        cfg = self._require_config()
        stdout, stderr, _rc = await self._run(["g", "q"], cfg)
        err_msg = _check_error(stdout, stderr)
        if err_msg:
            raise ConnectionError(err_msg)
        return {}

    async def flash(
        self,
        path: str,
        addr: int | None = None,
        verify: bool = True,
        reset_after: bool = True,
    ) -> dict[str, Any]:
        cfg = self._require_config()
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Firmware file not found: {path}")

        ext = resolved.suffix.lower()
        commands: list[str] = []

        if ext in (".hex", ".ihex", ".elf"):
            commands.append(f"loadfile {resolved}")
        else:
            if addr is None:
                raise ValueError(
                    f"Binary file {resolved.name} requires an explicit address. "
                    "Pass addr parameter or use .hex/.elf format."
                )
            commands.append(f"loadbin {resolved},{addr:#x}")

        if verify:
            if ext in (".hex", ".ihex", ".elf"):
                commands.append(f"verifybin {resolved},0")
            # For raw binary, verifybin needs the address
            elif addr is not None:
                commands.append(f"verifybin {resolved},{addr:#x}")

        if reset_after:
            commands.append("r")
            commands.append("g")

        commands.append("q")

        stdout, stderr, _rc = await self._run(commands, cfg)
        err_msg = _check_error(stdout, stderr)
        if err_msg:
            raise ConnectionError(err_msg)
        return {"file": str(resolved), "verified": verify, "reset": reset_after}

    async def mem_read(self, address: int, length: int) -> bytes:
        cfg = self._require_config()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin", prefix="dbgprobe_memread_") as f:
            tmp_path = f.name

        try:
            commands = [
                f"savebin {tmp_path},{address:#x},{length:#x}",
                "q",
            ]
            stdout, stderr, _rc = await self._run(commands, cfg)
            err_msg = _check_error(stdout, stderr)
            if err_msg:
                raise ConnectionError(err_msg)

            return Path(tmp_path).read_bytes()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def mem_write(self, address: int, data: bytes) -> dict[str, Any]:
        cfg = self._require_config()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin", prefix="dbgprobe_memwrite_") as f:
            f.write(data)
            tmp_path = f.name

        try:
            commands = [
                f"loadbin {tmp_path},{address:#x}",
                "q",
            ]
            stdout, stderr, _rc = await self._run(commands, cfg)
            err_msg = _check_error(stdout, stderr)
            if err_msg:
                raise ConnectionError(err_msg)

            return {"address": address, "length": len(data)}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def erase(
        self,
        config: ConnectConfig,
        start_addr: int | None = None,
        end_addr: int | None = None,
    ) -> dict[str, Any]:
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
            # Erase didn't confirm success — check for specific errors.
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

    async def _run(self, commands: list[str], cfg: ConnectConfig) -> tuple[str, str, int]:
        return await _run_jlink_script(
            self.exe,
            commands,
            device=cfg.device,
            interface=cfg.interface,
            speed_khz=cfg.speed_khz,
            serial=cfg.probe_serial,
        )
