"""Unit tests for dbgprobe_mcp_server.backend — ABC and registry."""

from __future__ import annotations

import pytest

from dbgprobe_mcp_server.backend import Backend, BackendRegistry, ConnectConfig, DeviceSecuredError, ProbeInfo


class DummyBackend(Backend):
    """Minimal backend for testing."""

    name = "dummy"

    async def list_probes(self):
        return [ProbeInfo(serial="123", description="Dummy", backend="dummy")]

    async def connect(self, config):
        return {}

    async def disconnect(self):
        pass

    async def reset(self, mode):
        return {"mode": mode}

    async def halt(self):
        return {}

    async def go(self):
        return {}

    async def flash(self, path, addr=None, verify=True, reset_after=True, config=None):
        return {"file": path}

    async def mem_read(self, address, length):
        return b"\x00" * length

    async def mem_write(self, address, data):
        return {"address": address, "length": len(data)}

    async def erase(self, config, start_addr=None, end_addr=None):
        return {}


class TestBackendRegistry:
    def test_register_and_create(self):
        reg = BackendRegistry()
        reg.register("dummy", DummyBackend)
        backend = reg.create("dummy")
        assert isinstance(backend, DummyBackend)
        assert backend.name == "dummy"

    def test_create_unknown(self):
        reg = BackendRegistry()
        with pytest.raises(ValueError, match="Unknown backend"):
            reg.create("nope")

    def test_available(self):
        reg = BackendRegistry()
        reg.register("b", DummyBackend)
        reg.register("a", DummyBackend)
        assert reg.available == ["a", "b"]

    def test_available_empty(self):
        reg = BackendRegistry()
        assert reg.available == []


class TestConnectConfig:
    def test_to_dict(self):
        cfg = ConnectConfig(
            backend="jlink",
            device="nRF52840_xxAA",
            interface="SWD",
            speed_khz=4000,
            probe_serial="123456",
        )
        d = cfg.to_dict()
        assert d["backend"] == "jlink"
        assert d["device"] == "nRF52840_xxAA"
        assert d["interface"] == "SWD"
        assert d["speed_khz"] == 4000
        assert d["probe_serial"] == "123456"

    def test_to_dict_no_extra(self):
        cfg = ConnectConfig(
            backend="jlink",
            device=None,
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        d = cfg.to_dict()
        assert "extra" not in d

    def test_to_dict_with_extra(self):
        cfg = ConnectConfig(
            backend="jlink",
            device=None,
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
            extra={"foo": "bar"},
        )
        d = cfg.to_dict()
        assert d["extra"] == {"foo": "bar"}


class TestDeviceSecuredError:
    def test_is_connection_error(self):
        exc = DeviceSecuredError("secured")
        assert isinstance(exc, ConnectionError)

    def test_message(self):
        exc = DeviceSecuredError("Target is secured")
        assert str(exc) == "Target is secured"


class TestProbeInfo:
    def test_fields(self):
        p = ProbeInfo(serial="999", description="My probe", backend="jlink")
        assert p.serial == "999"
        assert p.description == "My probe"
        assert p.backend == "jlink"
        assert p.extra is None


class TestDummyBackend:
    async def test_list_probes(self):
        backend = DummyBackend()
        probes = await backend.list_probes()
        assert len(probes) == 1
        assert probes[0].serial == "123"

    async def test_connect_disconnect(self):
        backend = DummyBackend()
        cfg = ConnectConfig(
            backend="dummy",
            device=None,
            interface="SWD",
            speed_khz=4000,
            probe_serial=None,
        )
        result = await backend.connect(cfg)
        assert isinstance(result, dict)
        await backend.disconnect()

    async def test_reset(self):
        backend = DummyBackend()
        result = await backend.reset("soft")
        assert result["mode"] == "soft"

    async def test_mem_read(self):
        backend = DummyBackend()
        data = await backend.mem_read(0x2000_0000, 16)
        assert len(data) == 16

    async def test_mem_write(self):
        backend = DummyBackend()
        result = await backend.mem_write(0x2000_0000, b"\x01\x02")
        assert result["length"] == 2


class TestOptionalMethods:
    """New concrete methods raise NotImplementedError by default."""

    async def test_step_not_implemented(self):
        backend = DummyBackend()
        with pytest.raises(NotImplementedError, match="step"):
            await backend.step()

    async def test_status_not_implemented(self):
        backend = DummyBackend()
        with pytest.raises(NotImplementedError, match="status"):
            await backend.status()

    async def test_set_breakpoint_not_implemented(self):
        backend = DummyBackend()
        with pytest.raises(NotImplementedError, match="set_breakpoint"):
            await backend.set_breakpoint(0x0800_0000)

    async def test_clear_breakpoint_not_implemented(self):
        backend = DummyBackend()
        with pytest.raises(NotImplementedError, match="clear_breakpoint"):
            await backend.clear_breakpoint(0x0800_0000)

    async def test_list_breakpoints_not_implemented(self):
        backend = DummyBackend()
        with pytest.raises(NotImplementedError, match="list_breakpoints"):
            await backend.list_breakpoints()
