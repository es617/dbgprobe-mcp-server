"""Backend interface and registry for debug probe backends."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("dbgprobe_mcp_server")


class DeviceSecuredError(ConnectionError):
    """Raised when the target device is secured/read-protected (e.g. APPROTECT)."""


@dataclass
class ProbeInfo:
    """Information about a discovered debug probe."""

    serial: str
    description: str
    backend: str
    extra: dict[str, Any] | None = None


@dataclass
class ConnectConfig:
    """Resolved configuration for a probe session."""

    backend: str
    device: str | None
    interface: str
    speed_khz: int
    probe_serial: str | None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "backend": self.backend,
            "device": self.device,
            "interface": self.interface,
            "speed_khz": self.speed_khz,
            "probe_serial": self.probe_serial,
        }
        if self.extra:
            d["extra"] = self.extra
        return d


class Backend(ABC):
    """Abstract base class for debug probe backends.

    Each session gets its own Backend instance.  Implementations should store
    resolved paths / config in ``__init__`` or ``connect``.
    """

    name: str  # e.g. "jlink", "openocd"

    @abstractmethod
    async def list_probes(self) -> list[ProbeInfo]:
        """Enumerate attached probes for this backend."""

    @abstractmethod
    async def connect(self, config: ConnectConfig) -> dict[str, Any]:
        """Validate connectivity and store config.

        Returns a dict of extra info to include in the connect response
        (e.g. resolved executable paths).
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Release resources."""

    @abstractmethod
    async def reset(self, mode: str) -> dict[str, Any]:
        """Reset the target.  *mode* is one of ``soft``, ``hard``, ``halt``."""

    @abstractmethod
    async def halt(self) -> dict[str, Any]:
        """Halt the CPU."""

    @abstractmethod
    async def go(self) -> dict[str, Any]:
        """Resume execution."""

    @abstractmethod
    async def flash(
        self,
        path: str,
        addr: int | None = None,
        verify: bool = True,
        reset_after: bool = True,
        config: ConnectConfig | None = None,
    ) -> dict[str, Any]:
        """Program a firmware image.

        *config* is optional — when provided (session-less flash), the
        backend uses it for tool/probe parameters instead of requiring a
        prior ``connect()`` call.
        """

    @abstractmethod
    async def mem_read(self, address: int, length: int) -> bytes:
        """Read *length* bytes starting at *address*."""

    @abstractmethod
    async def mem_write(self, address: int, data: bytes) -> dict[str, Any]:
        """Write *data* starting at *address*."""

    @abstractmethod
    async def erase(
        self,
        config: ConnectConfig,
        start_addr: int | None = None,
        end_addr: int | None = None,
    ) -> dict[str, Any]:
        """Erase the target flash (session-less).

        With no addresses: full chip erase (may unlock secured devices).
        With *start_addr* and *end_addr*: erase only that address range.
        """

    # -- Optional methods (concrete defaults) --------------------------------
    # Backends that support persistent debug connections (e.g. GDBServer)
    # override these.  Test mocks and simple backends need not implement them.

    async def step(self) -> dict[str, Any]:
        """Single-step one instruction."""
        raise NotImplementedError("step() not supported by this backend")

    async def status(self) -> dict[str, Any]:
        """Query target state (running/halted, PC, stop reason)."""
        raise NotImplementedError("status() not supported by this backend")

    async def set_breakpoint(self, address: int, bp_type: str = "sw") -> dict[str, Any]:
        """Set a software or hardware breakpoint at *address*."""
        raise NotImplementedError("set_breakpoint() not supported by this backend")

    async def clear_breakpoint(self, address: int) -> dict[str, Any]:
        """Clear a breakpoint at *address*."""
        raise NotImplementedError("clear_breakpoint() not supported by this backend")

    async def clear_all_breakpoints(self) -> None:
        """Clear all breakpoints on the target."""
        raise NotImplementedError("clear_all_breakpoints() not supported by this backend")

    async def list_breakpoints(self) -> list[dict[str, Any]]:
        """List active breakpoints."""
        raise NotImplementedError("list_breakpoints() not supported by this backend")

    async def erase_via_gdb(
        self,
        start_addr: int | None = None,
        end_addr: int | None = None,
    ) -> dict[str, Any]:
        """Erase flash through an active debug session."""
        raise NotImplementedError("erase_via_gdb() not supported by this backend")


class BackendRegistry:
    """Factory that maps backend names to classes."""

    def __init__(self) -> None:
        self._backends: dict[str, type[Backend]] = {}

    def register(self, name: str, cls: type[Backend]) -> None:
        self._backends[name] = cls

    def create(self, name: str) -> Backend:
        cls = self._backends.get(name)
        if cls is None:
            available = ", ".join(sorted(self._backends)) or "(none)"
            raise ValueError(f"Unknown backend {name!r}. Available: {available}")
        return cls()

    @property
    def available(self) -> list[str]:
        return sorted(self._backends)


# Global singleton
registry = BackendRegistry()
