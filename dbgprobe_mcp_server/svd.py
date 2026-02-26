"""SVD file parsing — register-level peripheral access for debug sessions."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from cmsis_svd.model import SVDRegisterArray, SVDRegisterCluster, SVDRegisterClusterArray
from cmsis_svd.parser import SVDParser

logger = logging.getLogger("dbgprobe_mcp_server")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SvdFieldInfo:
    """A single field within a register."""

    name: str
    bit_offset: int
    bit_width: int
    access: str | None  # "read-only", "write-only", "read-write"
    description: str
    enumerated_values: dict[str, int]  # name → value ("PullUp" → 3)
    enumerated_names: dict[int, str]  # value → name (3 → "PullUp")


@dataclass
class SvdRegisterInfo:
    """A single register within a peripheral."""

    name: str  # "PIN_CNF[3]" or "OUT"
    address: int  # absolute (base + offset)
    size: int  # bits: 8, 16, or 32
    access: str | None
    reset_value: int | None
    description: str
    fields: dict[str, SvdFieldInfo]


@dataclass
class SvdPeripheralInfo:
    """A peripheral with its register map."""

    name: str
    base_address: int
    description: str
    registers: dict[str, SvdRegisterInfo]


@dataclass
class SvdData:
    """Parsed SVD data attached to a debug session."""

    path: str
    device_name: str
    peripherals: dict[str, SvdPeripheralInfo]
    _addr_to_register: dict[int, tuple[str, str]] = field(default_factory=dict)
    attached_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Target parsing — "GPIO.OUT", "GPIO.PIN_CNF[3].PULL"
# ---------------------------------------------------------------------------

_TARGET_RE = re.compile(
    r"^(?P<periph>[A-Za-z_]\w*)"
    r"\.(?P<reg>[A-Za-z_]\w*(?:\[\d+\])?)"
    r"(?:\.(?P<field>[A-Za-z_]\w*))?$"
)


@dataclass
class SvdTarget:
    """Parsed target string."""

    peripheral: str
    register: str
    field: str | None


def parse_target(target: str) -> SvdTarget:
    """Parse a target string like 'GPIO.OUT' or 'GPIO.PIN_CNF[3].PULL'."""
    m = _TARGET_RE.match(target)
    if m is None:
        raise ValueError(
            f"Invalid target: {target!r}. "
            "Expected PERIPHERAL.REGISTER or PERIPHERAL.REGISTER.FIELD "
            "(e.g. GPIO.OUT, GPIO.PIN_CNF[3].PULL)."
        )
    return SvdTarget(
        peripheral=m.group("periph"),
        register=m.group("reg"),
        field=m.group("field"),
    )


# ---------------------------------------------------------------------------
# SVD parsing
# ---------------------------------------------------------------------------


def _access_str(access: Any) -> str | None:
    """Convert cmsis-svd access enum to string."""
    if access is None:
        return None
    return str(access.value) if hasattr(access, "value") else str(access)


def _build_field(svd_field: Any) -> SvdFieldInfo:
    """Convert a cmsis-svd field to SvdFieldInfo."""
    enum_vals: dict[str, int] = {}
    enum_names: dict[int, str] = {}
    if svd_field.enumerated_values:
        for ev_container in svd_field.enumerated_values:
            for ev in ev_container.enumerated_values:
                if ev.name is not None and ev.value is not None:
                    enum_vals[ev.name] = ev.value
                    enum_names[ev.value] = ev.name
    return SvdFieldInfo(
        name=svd_field.name,
        bit_offset=svd_field.bit_offset,
        bit_width=svd_field.bit_width,
        access=_access_str(svd_field.access),
        description=svd_field.description or "",
        enumerated_values=enum_vals,
        enumerated_names=enum_names,
    )


def _build_register(svd_reg: Any, base_address: int) -> SvdRegisterInfo:
    """Convert a cmsis-svd register to SvdRegisterInfo."""
    fields: dict[str, SvdFieldInfo] = {}
    if svd_reg.fields:
        for f in svd_reg.fields:
            fields[f.name] = _build_field(f)
    return SvdRegisterInfo(
        name=svd_reg.name,
        address=base_address + svd_reg.address_offset,
        size=svd_reg.size or 32,
        access=_access_str(svd_reg.access),
        reset_value=svd_reg.reset_value,
        description=svd_reg.description or "",
        fields=fields,
    )


def _collect_registers(
    reg_list: list,
    base: int,
    periph_name: str,
    registers: dict[str, SvdRegisterInfo],
    addr_to_reg: dict[int, tuple[str, str]],
    cluster_offset: int = 0,
) -> None:
    """Recursively collect registers, handling arrays and clusters."""
    for svd_reg in reg_list:
        if isinstance(svd_reg, SVDRegisterClusterArray):
            for cluster in svd_reg.clusters:
                offset = cluster_offset + (cluster.address_offset or 0)
                _collect_registers(cluster.registers, base, periph_name, registers, addr_to_reg, offset)
        elif isinstance(svd_reg, SVDRegisterCluster):
            offset = cluster_offset + (svd_reg.address_offset or 0)
            _collect_registers(svd_reg.registers, base, periph_name, registers, addr_to_reg, offset)
        elif isinstance(svd_reg, SVDRegisterArray):
            for sub_reg in svd_reg.registers:
                reg_info = _build_register(sub_reg, base + cluster_offset)
                registers[reg_info.name] = reg_info
                addr_to_reg[reg_info.address] = (periph_name, reg_info.name)
        else:
            reg_info = _build_register(svd_reg, base + cluster_offset)
            registers[reg_info.name] = reg_info
            addr_to_reg[reg_info.address] = (periph_name, reg_info.name)


def parse_svd(path: str) -> SvdData:
    """Parse an SVD XML file and build lookup tables."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SVD file not found: {path}")

    parser = SVDParser.for_xml_file(path)
    device = parser.get_device()

    peripherals: dict[str, SvdPeripheralInfo] = {}
    addr_to_reg: dict[int, tuple[str, str]] = {}

    for svd_periph in device.peripherals:
        base = svd_periph.base_address
        registers: dict[str, SvdRegisterInfo] = {}

        if svd_periph.registers:
            _collect_registers(svd_periph.registers, base, svd_periph.name, registers, addr_to_reg)

        peripherals[svd_periph.name] = SvdPeripheralInfo(
            name=svd_periph.name,
            base_address=base,
            description=svd_periph.description or "",
            registers=registers,
        )

    return SvdData(
        path=path,
        device_name=device.name or "unknown",
        peripherals=peripherals,
        _addr_to_register=addr_to_reg,
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def resolve_register(svd: SvdData, target: SvdTarget) -> SvdRegisterInfo:
    """Look up a register by parsed target."""
    periph = svd.peripherals.get(target.peripheral)
    if periph is None:
        raise ValueError(f"Unknown peripheral: {target.peripheral!r}")
    reg = periph.registers.get(target.register)
    if reg is None:
        raise ValueError(f"Unknown register: {target.peripheral}.{target.register!r}")
    return reg


def resolve_field(svd: SvdData, target: SvdTarget) -> tuple[SvdRegisterInfo, SvdFieldInfo]:
    """Look up a register + field by parsed target."""
    reg = resolve_register(svd, target)
    if target.field is None:
        raise ValueError(f"No field specified in target: {target.peripheral}.{target.register}")
    fld = reg.fields.get(target.field)
    if fld is None:
        raise ValueError(f"Unknown field: {target.peripheral}.{target.register}.{target.field!r}")
    return reg, fld


# ---------------------------------------------------------------------------
# Field value helpers
# ---------------------------------------------------------------------------


def decode_register(reg: SvdRegisterInfo, raw_value: int) -> dict[str, Any]:
    """Extract all fields from a raw register value, with enum names."""
    result: dict[str, Any] = {}
    for name, fld in reg.fields.items():
        mask = (1 << fld.bit_width) - 1
        val = (raw_value >> fld.bit_offset) & mask
        entry: dict[str, Any] = {
            "value": val,
            "bit_range": f"[{fld.bit_offset}:{fld.bit_offset + fld.bit_width - 1}]",
        }
        enum_name = fld.enumerated_names.get(val)
        if enum_name is not None:
            entry["enum"] = enum_name
        result[name] = entry
    return result


def extract_field(reg: SvdRegisterInfo, fld: SvdFieldInfo, raw_value: int) -> int:
    """Extract a single field value from a raw register value."""
    mask = (1 << fld.bit_width) - 1
    return (raw_value >> fld.bit_offset) & mask


def resolve_enum_value(fld: SvdFieldInfo, value: int | str) -> int:
    """Resolve a field value — accepts int, enum name string, or hex string."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        # Try enum name first
        if value in fld.enumerated_values:
            return fld.enumerated_values[value]
        # Try numeric string
        try:
            return int(value, 0)
        except ValueError:
            pass
        available = ", ".join(sorted(fld.enumerated_values.keys()))
        raise ValueError(
            f"Unknown value {value!r} for field {fld.name}. Available enum values: {available or '(none)'}"
        )
    raise TypeError(f"Expected int or str, got {type(value).__name__}")


def encode_field(reg: SvdRegisterInfo, fld: SvdFieldInfo, current: int, new_value: int) -> int:
    """Read-modify-write: replace a field in the current register value."""
    mask = (1 << fld.bit_width) - 1
    if new_value < 0 or new_value > mask:
        raise ValueError(
            f"Value {new_value} out of range for field {fld.name} (max {mask}, {fld.bit_width} bits)"
        )
    cleared = current & ~(mask << fld.bit_offset)
    return cleared | (new_value << fld.bit_offset)


def register_at_address(svd: SvdData, addr: int) -> tuple[SvdPeripheralInfo, SvdRegisterInfo] | None:
    """Look up a register by absolute address. Returns None if no match."""
    entry = svd._addr_to_register.get(addr)
    if entry is None:
        return None
    periph_name, reg_name = entry
    periph = svd.peripherals[periph_name]
    reg = periph.registers[reg_name]
    return periph, reg
