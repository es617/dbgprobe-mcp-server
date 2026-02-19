"""Unit tests for dbgprobe_mcp_server.backends.jlink — no hardware required."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dbgprobe_mcp_server.backend import ConnectConfig
from dbgprobe_mcp_server.backends.jlink import (
    JLinkBackend,
    _check_error,
    _is_device_secured,
    _parse_probe_list,
    find_jlink_exe,
)
from dbgprobe_mcp_server.gdb_client import (
    GdbClient,
    GdbConnectionError,
    GdbProtocolError,
    StopReply,
)

# ---------------------------------------------------------------------------
# Probe list parsing
# ---------------------------------------------------------------------------

SHOW_EMU_LIST_OUTPUT = """\
SEGGER J-Link Commander V7.94 (Compiled Jan  5 2024 10:14:20)
DLL version V7.94, compiled Jan  5 2024 10:13:44

J-Link[0]: Connection: USB, Serial number: 683456789, ProductName: J-Link EDU Mini
J-Link[1]: Connection: USB, Serial number: 260012345, ProductName: J-Link PLUS
"""

SHOW_EMU_LIST_SINGLE = """\
J-Link Commander V7.80
DLL version V7.80

J-Link[0]: Connection: USB, Serial number: 12345678, ProductName: J-Link EDU
"""

SHOW_EMU_LIST_EMPTY = """\
SEGGER J-Link Commander V7.94
No emulators found.
"""


class TestParseProbeList:
    def test_two_probes(self):
        probes = _parse_probe_list(SHOW_EMU_LIST_OUTPUT)
        assert len(probes) == 2
        assert probes[0].serial == "683456789"
        assert probes[1].serial == "260012345"
        assert all(p.backend == "jlink" for p in probes)

    def test_single_probe(self):
        probes = _parse_probe_list(SHOW_EMU_LIST_SINGLE)
        assert len(probes) == 1
        assert probes[0].serial == "12345678"

    def test_no_probes(self):
        probes = _parse_probe_list(SHOW_EMU_LIST_EMPTY)
        assert probes == []

    def test_empty_string(self):
        probes = _parse_probe_list("")
        assert probes == []

    def test_no_duplicates(self):
        doubled = SHOW_EMU_LIST_SINGLE + "\n" + SHOW_EMU_LIST_SINGLE
        probes = _parse_probe_list(doubled)
        assert len(probes) == 1


# ---------------------------------------------------------------------------
# Error checking
# ---------------------------------------------------------------------------


class TestIsDeviceSecured:
    @pytest.mark.parametrize(
        "output",
        [
            "Device is secured. Cannot connect.",
            "APPROTECT is enabled on this device.",
            "The device is read protected.",
            "Protection enabled — mass erase required.",
            "Secure element detected, cannot access.",
            "Please unlock the device before connecting.",
        ],
    )
    def test_secured_true(self, output):
        assert _is_device_secured(output, "") is True

    def test_secured_in_stderr(self):
        assert _is_device_secured("", "Device is secured") is True

    @pytest.mark.parametrize(
        "output",
        [
            "Connected OK\nHalting...",
            "All good. Connected to target.",
            "",
        ],
    )
    def test_secured_false(self, output):
        assert _is_device_secured(output, "") is False


class TestCheckError:
    def test_inittarget_error(self):
        err = _check_error(
            "InitTarget() end\n****** Error: J-Link script file function "
            "InitTarget() returned with error code -1",
            "",
        )
        assert err is not None
        assert "InitTarget" in err
        assert "probe name" in err.lower()

    def test_inittarget_success_no_false_positive(self):
        """Normal InitTarget() start/end with 'exit on Error' should NOT trigger."""
        err = _check_error(
            "J-Link Commander will now exit on Error\n"
            "InitTarget() start\n"
            "InitTarget() end - Took 4.23ms\n"
            "Found SW-DP with ID 0x2BA01477\n"
            "Cortex-M4 identified.\n"
            "Script processing completed.\n",
            "",
        )
        assert err is None

    def test_cannot_connect(self):
        err = _check_error("Cannot connect to target", "")
        assert err is not None
        assert "connect" in err.lower()

    def test_no_jlink(self):
        err = _check_error("No J-Link found", "")
        assert err is not None
        assert "J-Link" in err

    def test_no_error(self):
        err = _check_error("All good. Connected to target.", "")
        assert err is None

    def test_error_in_stderr(self):
        err = _check_error("", "ERROR: Something went wrong")
        assert err is not None

    def test_unknown_device(self):
        err = _check_error("Unknown device: FOO_BAR", "")
        assert err is not None

    def test_secured_takes_priority(self):
        err = _check_error("Cannot connect to target. Device is secured.", "")
        assert err is not None
        assert "secured" in err.lower()
        assert "dbgprobe.erase" in err


# ---------------------------------------------------------------------------
# Executable discovery
# ---------------------------------------------------------------------------


class TestFindJLinkExe:
    def test_env_var(self, monkeypatch, tmp_path):
        exe = tmp_path / "JLinkExe"
        exe.touch()
        monkeypatch.setenv("DBGPROBE_JLINK_PATH", str(exe))
        result = find_jlink_exe()
        assert result == str(exe)

    def test_env_var_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DBGPROBE_JLINK_PATH", str(tmp_path / "nope"))
        result = find_jlink_exe()
        assert result is None

    def test_which_fallback(self, monkeypatch):
        monkeypatch.delenv("DBGPROBE_JLINK_PATH", raising=False)
        with patch("shutil.which", return_value="/usr/bin/JLinkExe"):
            result = find_jlink_exe()
            assert result == "/usr/bin/JLinkExe"

    def test_common_dir_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DBGPROBE_JLINK_PATH", raising=False)
        # Create a fake common dir with JLinkExe
        jlink_dir = tmp_path / "SEGGER" / "JLink"
        jlink_dir.mkdir(parents=True)
        exe = jlink_dir / ("JLinkExe" if sys.platform != "win32" else "JLink.exe")
        exe.touch()

        with (
            patch("shutil.which", return_value=None),
            patch(
                "dbgprobe_mcp_server.backends.jlink._COMMON_DIRS",
                {
                    "darwin": [str(jlink_dir)],
                    "linux": [str(jlink_dir)],
                    "win32": [str(jlink_dir)],
                },
            ),
        ):
            result = find_jlink_exe()
            assert result is not None
            assert "JLink" in result

    def test_not_found(self, monkeypatch):
        monkeypatch.delenv("DBGPROBE_JLINK_PATH", raising=False)
        with (
            patch("shutil.which", return_value=None),
            patch(
                "dbgprobe_mcp_server.backends.jlink._COMMON_DIRS",
                {"darwin": [], "linux": [], "win32": []},
            ),
        ):
            result = find_jlink_exe()
            assert result is None


# ---------------------------------------------------------------------------
# Mock GDB client helper
# ---------------------------------------------------------------------------


def _make_mock_gdb_client(**overrides) -> MagicMock:
    """Create a MagicMock that behaves like GdbClient."""
    mock = MagicMock(spec=GdbClient)
    mock.connected = True
    mock.connect = AsyncMock()
    mock.close = AsyncMock()
    mock.send_packet = AsyncMock(return_value="OK")
    mock.send_interrupt = AsyncMock()
    mock.read_memory = AsyncMock(return_value=b"\x00" * 16)
    mock.write_memory = AsyncMock()
    mock.continue_execution = AsyncMock()
    mock.step = AsyncMock(return_value=StopReply(signal=5, reason="halted", registers={15: 0x0800_0100}))
    mock.halt = AsyncMock(return_value=StopReply(signal=2, reason="interrupt", registers={15: 0x0800_0200}))
    mock.query_status = AsyncMock(
        return_value=StopReply(signal=5, reason="halted", registers={15: 0x0800_0300})
    )
    mock.set_breakpoint = AsyncMock()
    mock.clear_breakpoint = AsyncMock()
    mock.monitor_command = AsyncMock(return_value="OK")
    mock.read_registers = AsyncMock(return_value=b"\x00" * 64)
    mock.wait_stop = AsyncMock(return_value=StopReply(signal=5, reason="halted", registers={15: 0x0800_0400}))
    for key, val in overrides.items():
        setattr(mock, key, val)
    return mock


def _make_connected_backend(**gdb_overrides) -> JLinkBackend:
    """Create a JLinkBackend with a mock GDB client already connected."""
    backend = JLinkBackend()
    backend._exe = "/usr/bin/JLinkExe"
    backend._gdbserver_path = "/usr/bin/JLinkGDBServerCLExe"
    backend._config = ConnectConfig(
        backend="jlink",
        device="nRF52840_xxAA",
        interface="SWD",
        speed_khz=4000,
        probe_serial=None,
    )
    backend._gdb_client = _make_mock_gdb_client(**gdb_overrides)
    backend._gdb_port = 2331
    backend._target_running = False
    return backend


# ---------------------------------------------------------------------------
# JLinkBackend — GDB-based session operations
# ---------------------------------------------------------------------------


class TestJLinkBackendListProbes:
    async def test_list_probes(self):
        backend = JLinkBackend()
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value="/usr/bin/JLinkExe"),
            patch(
                "dbgprobe_mcp_server.backends.jlink._run_jlink_list_probes",
                AsyncMock(return_value=(SHOW_EMU_LIST_OUTPUT, "", 0)),
            ),
        ):
            probes = await backend.list_probes()
            assert len(probes) == 2

    async def test_list_probes_no_exe(self):
        backend = JLinkBackend()
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value=None),
            pytest.raises(FileNotFoundError, match="JLinkExe not found"),
        ):
            await backend.list_probes()


class TestJLinkBackendConnect:
    async def test_connect_no_exe(self):
        backend = JLinkBackend()
        cfg = ConnectConfig(
            backend="jlink",
            device=None,
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value="/usr/bin/gdb"),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            pytest.raises(FileNotFoundError, match="JLinkExe not found"),
        ):
            await backend.connect(cfg)

    async def test_connect_no_gdbserver(self):
        backend = JLinkBackend()
        cfg = ConnectConfig(
            backend="jlink",
            device=None,
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value="/usr/bin/JLinkExe"),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            pytest.raises(FileNotFoundError, match="JLinkGDBServerCLExe not found"),
        ):
            await backend.connect(cfg)


class TestJLinkBackendHalt:
    async def test_halt_when_running(self):
        backend = _make_connected_backend()
        backend._target_running = True
        result = await backend.halt()
        assert result["reason"] == "interrupt"
        assert result["signal"] == 2
        assert result["pc"] == 0x0800_0200
        backend._gdb_client.halt.assert_awaited_once()

    async def test_halt_when_halted(self):
        backend = _make_connected_backend()
        backend._target_running = False
        result = await backend.halt()
        assert result["reason"] == "halted"
        backend._gdb_client.query_status.assert_awaited_once()

    async def test_halt_connection_error(self):
        backend = _make_connected_backend()
        backend._target_running = True
        backend._gdb_client.halt = AsyncMock(side_effect=GdbConnectionError("lost"))
        with pytest.raises(ConnectionError, match="lost"):
            await backend.halt()


class TestJLinkBackendGo:
    async def test_go(self):
        backend = _make_connected_backend()
        result = await backend.go()
        assert result == {}
        assert backend._target_running is True
        backend._gdb_client.continue_execution.assert_awaited_once()


class TestJLinkBackendStep:
    async def test_step(self):
        backend = _make_connected_backend()
        result = await backend.step()
        assert result["reason"] == "step"
        assert result["pc"] == 0x0800_0100
        backend._gdb_client.step.assert_awaited_once()

    async def test_step_while_running_raises(self):
        backend = _make_connected_backend()
        backend._target_running = True
        with pytest.raises(ConnectionError, match="running"):
            await backend.step()


class TestJLinkBackendStatus:
    async def test_status_halted(self):
        backend = _make_connected_backend()
        backend._target_running = False
        result = await backend.status()
        assert result["state"] == "halted"
        assert result["reason"] == "halted"

    async def test_status_running(self):
        backend = _make_connected_backend()
        backend._target_running = True
        # wait_stop times out — target is still running
        backend._gdb_client.wait_stop = AsyncMock(side_effect=TimeoutError())
        result = await backend.status()
        assert result["state"] == "running"


class TestJLinkBackendReset:
    async def test_reset_soft(self):
        backend = _make_connected_backend()
        result = await backend.reset("soft")
        assert result["mode"] == "soft"
        assert backend._target_running is True
        backend._gdb_client.monitor_command.assert_awaited_once_with("reset")

    async def test_reset_halt(self):
        backend = _make_connected_backend()
        result = await backend.reset("halt")
        assert result["mode"] == "halt"
        assert backend._target_running is False
        calls = backend._gdb_client.monitor_command.call_args_list
        assert calls[0][0][0] == "reset"
        assert calls[1][0][0] == "halt"

    async def test_reset_hard(self):
        backend = _make_connected_backend()
        result = await backend.reset("hard")
        assert result["mode"] == "hard"
        backend._gdb_client.monitor_command.assert_awaited_once_with("reset 2")


class TestJLinkBackendMemory:
    async def test_mem_read(self):
        backend = _make_connected_backend()
        backend._gdb_client.read_memory = AsyncMock(return_value=b"\xde\xad\xbe\xef")
        data = await backend.mem_read(0x2000_0000, 4)
        assert data == b"\xde\xad\xbe\xef"

    async def test_mem_write(self):
        backend = _make_connected_backend()
        result = await backend.mem_write(0x2000_0000, b"\x01\x02\x03\x04")
        assert result["length"] == 4
        assert result["address"] == 0x2000_0000
        backend._gdb_client.write_memory.assert_awaited_once()


class TestJLinkBackendBreakpoints:
    async def test_set_breakpoint_hw(self):
        backend = _make_connected_backend()
        result = await backend.set_breakpoint(0x0800_0100, "hw")
        assert result["address"] == 0x0800_0100
        assert result["bp_type"] == "hw"
        backend._gdb_client.set_breakpoint.assert_awaited_once_with(1, 0x0800_0100)

    async def test_set_breakpoint_sw(self):
        backend = _make_connected_backend()
        result = await backend.set_breakpoint(0x0800_0100, "sw")
        assert result["bp_type"] == "sw"
        backend._gdb_client.set_breakpoint.assert_awaited_once_with(0, 0x0800_0100)

    async def test_clear_breakpoint(self):
        backend = _make_connected_backend()
        result = await backend.clear_breakpoint(0x0800_0100)
        assert result["address"] == 0x0800_0100

    async def test_clear_breakpoint_failure(self):
        backend = _make_connected_backend()
        backend._gdb_client.clear_breakpoint = AsyncMock(side_effect=GdbProtocolError("nope"))
        with pytest.raises(ConnectionError, match="Failed to clear"):
            await backend.clear_breakpoint(0x0800_0100)

    async def test_list_breakpoints(self):
        backend = _make_connected_backend()
        result = await backend.list_breakpoints()
        assert result == []


class TestJLinkBackendFlash:
    async def test_flash_hex(self, tmp_path):
        backend = _make_connected_backend()
        fw = tmp_path / "firmware.hex"
        fw.write_text(":00000001FF\n")

        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            AsyncMock(return_value=("Programmed OK\n", "", 0)),
        ):
            # Mock _start_gdbserver to avoid actually spawning
            backend._start_gdbserver = AsyncMock()
            result = await backend.flash(str(fw))
            assert result["verified"] is True
            assert result["reset"] is True
            assert result["breakpoints_cleared"] is True
            # GDB client should have been closed and restarted
            backend._start_gdbserver.assert_awaited_once()

    async def test_flash_bin_requires_addr(self, tmp_path):
        backend = _make_connected_backend()
        fw = tmp_path / "firmware.bin"
        fw.write_bytes(b"\x00" * 16)

        with pytest.raises(ValueError, match="requires an explicit address"):
            await backend.flash(str(fw))

    async def test_flash_bin_with_addr(self, tmp_path):
        backend = _make_connected_backend()
        fw = tmp_path / "firmware.bin"
        fw.write_bytes(b"\x00" * 16)

        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            AsyncMock(return_value=("Programmed OK\n", "", 0)),
        ):
            backend._start_gdbserver = AsyncMock()
            result = await backend.flash(str(fw), addr=0x0800_0000)
            assert result["verified"] is True

    async def test_flash_file_not_found(self):
        backend = _make_connected_backend()
        with pytest.raises(FileNotFoundError, match="not found"):
            await backend.flash("/nonexistent/firmware.hex")


class TestJLinkBackendErase:
    async def test_erase_success(self):
        backend = JLinkBackend()
        cfg = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value="/usr/bin/JLinkExe"),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            patch(
                "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
                AsyncMock(return_value=("Erasing device...\nErasing done.\n", "", 0)),
            ) as mock,
        ):
            result = await backend.erase(cfg)
            assert "resolved_paths" in result
            call_args = mock.call_args
            commands = call_args[0][1]
            assert "erase" in commands
            assert "r" in commands
            assert "q" in commands

    async def test_erase_no_confirmation_raises(self):
        backend = JLinkBackend()
        cfg = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value="/usr/bin/JLinkExe"),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            patch(
                "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
                AsyncMock(return_value=("Device is secured. APPROTECT enabled.", "", 0)),
            ),
            pytest.raises(ConnectionError),
        ):
            await backend.erase(cfg)

    async def test_erase_generic_failure_raises(self):
        backend = JLinkBackend()
        cfg = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value="/usr/bin/JLinkExe"),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            patch(
                "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
                AsyncMock(return_value=("Some unexpected output with no confirmation.", "", 0)),
            ),
            pytest.raises(ConnectionError, match="did not complete"),
        ):
            await backend.erase(cfg)

    async def test_erase_range(self):
        backend = JLinkBackend()
        cfg = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value="/usr/bin/JLinkExe"),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            patch(
                "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
                AsyncMock(return_value=("Erasing range...\nErasing done.\n", "", 0)),
            ) as mock,
        ):
            result = await backend.erase(cfg, start_addr=0x00040000, end_addr=0x00080000)
            assert "resolved_paths" in result
            call_args = mock.call_args
            commands = call_args[0][1]
            assert commands[0] == "erase 0x40000 0x80000"
            assert "r" in commands
            assert "q" in commands

    async def test_erase_no_exe(self):
        backend = JLinkBackend()
        cfg = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            pytest.raises(FileNotFoundError, match="JLinkExe not found"),
        ):
            await backend.erase(cfg)


class TestJLinkBackendDisconnect:
    async def test_disconnect(self):
        backend = _make_connected_backend()
        await backend.disconnect()
        assert backend._config is None
        assert backend._gdb_client is None

    async def test_not_connected_raises(self):
        backend = JLinkBackend()
        with pytest.raises(ConnectionError, match="Not connected"):
            await backend.halt()


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------


class TestGlobalRegistry:
    def test_jlink_registered(self):
        import dbgprobe_mcp_server.backends  # noqa: F401
        from dbgprobe_mcp_server.backend import registry

        assert "jlink" in registry.available
        backend = registry.create("jlink")
        assert isinstance(backend, JLinkBackend)
