"""Unit tests for dbgprobe_mcp_server.backends.jlink — no hardware required."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from dbgprobe_mcp_server.backend import ConnectConfig
from dbgprobe_mcp_server.backends.jlink import (
    JLinkBackend,
    _check_error,
    _parse_probe_list,
    find_jlink_exe,
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


class TestCheckError:
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
# JLinkBackend (mocked subprocess)
# ---------------------------------------------------------------------------


def _mock_run_jlink_script(stdout: str, stderr: str = "", rc: int = 0):
    """Return an AsyncMock that replaces _run_jlink_script."""
    mock = AsyncMock(return_value=(stdout, stderr, rc))
    return mock


def _mock_run_jlink_list_probes(stdout: str, stderr: str = "", rc: int = 0):
    """Return an AsyncMock that replaces _run_jlink_list_probes."""
    return AsyncMock(return_value=(stdout, stderr, rc))


class TestJLinkBackend:
    async def test_list_probes(self):
        backend = JLinkBackend()
        with (
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_exe", return_value="/usr/bin/JLinkExe"),
            patch(
                "dbgprobe_mcp_server.backends.jlink._run_jlink_list_probes",
                _mock_run_jlink_list_probes(SHOW_EMU_LIST_OUTPUT),
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

    async def test_connect_success(self):
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
                _mock_run_jlink_script("Connected OK\nHalting...\n"),
            ),
        ):
            result = await backend.connect(cfg)
            assert "resolved_paths" in result
            assert result["resolved_paths"]["jlink_exe"] == "/usr/bin/JLinkExe"

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
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_gdbserver", return_value=None),
            patch("dbgprobe_mcp_server.backends.jlink.find_jlink_rttclient", return_value=None),
            pytest.raises(FileNotFoundError, match="JLinkExe not found"),
        ):
            await backend.connect(cfg)

    async def test_connect_failure(self):
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
                _mock_run_jlink_script("Cannot connect to target"),
            ),
            pytest.raises(ConnectionError, match="Cannot connect"),
        ):
            await backend.connect(cfg)

    async def test_halt(self):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            _mock_run_jlink_script("Halted\n"),
        ):
            result = await backend.halt()
            assert isinstance(result, dict)

    async def test_go(self):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            _mock_run_jlink_script("Resumed\n"),
        ):
            result = await backend.go()
            assert isinstance(result, dict)

    async def test_reset_soft(self):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            _mock_run_jlink_script("Reset\n"),
        ) as mock:
            result = await backend.reset("soft")
            assert result["mode"] == "soft"
            # Verify command includes "r" and "g" and "q"
            call_args = mock.call_args
            commands = call_args[0][1]
            assert "r" in commands
            assert "g" in commands

    async def test_reset_halt(self):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            _mock_run_jlink_script("Reset\n"),
        ) as mock:
            result = await backend.reset("halt")
            assert result["mode"] == "halt"
            call_args = mock.call_args
            commands = call_args[0][1]
            assert "h" in commands

    async def test_disconnect(self):
        backend = JLinkBackend()
        backend._config = ConnectConfig(
            backend="jlink",
            device=None,
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        await backend.disconnect()
        assert backend._config is None

    async def test_mem_read(self, tmp_path):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )

        async def fake_run(exe, commands, **kwargs):
            # Write some data to the temp file that savebin creates
            for cmd in commands:
                if cmd.startswith("savebin"):
                    # Extract temp path from savebin command
                    parts = cmd.split(",")
                    path = parts[0].replace("savebin ", "").strip()
                    Path(path).write_bytes(b"\xde\xad\xbe\xef")
            return ("OK\n", "", 0)

        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            side_effect=fake_run,
        ):
            data = await backend.mem_read(0x2000_0000, 4)
            assert data == b"\xde\xad\xbe\xef"

    async def test_mem_write(self):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            _mock_run_jlink_script("OK\n"),
        ):
            result = await backend.mem_write(0x2000_0000, b"\x01\x02\x03\x04")
            assert result["length"] == 4
            assert result["address"] == 0x2000_0000

    async def test_flash_hex(self, tmp_path):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        fw = tmp_path / "firmware.hex"
        fw.write_text(":00000001FF\n")

        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            _mock_run_jlink_script("Programmed OK\n"),
        ):
            result = await backend.flash(str(fw))
            assert result["verified"] is True
            assert result["reset"] is True

    async def test_flash_bin_requires_addr(self, tmp_path):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        fw = tmp_path / "firmware.bin"
        fw.write_bytes(b"\x00" * 16)

        with pytest.raises(ValueError, match="requires an explicit address"):
            await backend.flash(str(fw))

    async def test_flash_bin_with_addr(self, tmp_path):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        fw = tmp_path / "firmware.bin"
        fw.write_bytes(b"\x00" * 16)

        with patch(
            "dbgprobe_mcp_server.backends.jlink._run_jlink_script",
            _mock_run_jlink_script("Programmed OK\n"),
        ):
            result = await backend.flash(str(fw), addr=0x0800_0000)
            assert result["verified"] is True

    async def test_flash_file_not_found(self):
        backend = JLinkBackend()
        backend._exe = "/usr/bin/JLinkExe"
        backend._config = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        with pytest.raises(FileNotFoundError, match="not found"):
            await backend.flash("/nonexistent/firmware.hex")

    async def test_not_connected_raises(self):
        backend = JLinkBackend()
        with pytest.raises(ConnectionError, match="Not connected"):
            await backend.halt()


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------


class TestGlobalRegistry:
    def test_jlink_registered(self):
        # Import backends to trigger registration
        import dbgprobe_mcp_server.backends  # noqa: F401
        from dbgprobe_mcp_server.backend import registry

        assert "jlink" in registry.available
        backend = registry.create("jlink")
        assert isinstance(backend, JLinkBackend)
