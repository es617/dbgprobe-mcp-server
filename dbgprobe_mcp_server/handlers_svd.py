"""SVD tool definitions and handlers — register-level peripheral access."""

from __future__ import annotations

import struct
from typing import Any

from mcp.types import Tool

from dbgprobe_mcp_server.helpers import _err, _ok, _validate_file_path
from dbgprobe_mcp_server.state import ProbeState
from dbgprobe_mcp_server.svd import (
    SvdData,
    decode_register,
    encode_field,
    extract_field,
    parse_svd,
    parse_target,
    resolve_enum_value,
    resolve_field,
    resolve_register,
)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="dbgprobe.svd.attach",
        description=(
            "Parse an SVD file and attach it to a session. Enables named register "
            "reads/writes, field-level access, and auto-decode on mem.read. "
            "Re-attaching replaces the previous SVD."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Path to the SVD file.",
                },
            },
            "required": ["session_id", "path"],
        },
    ),
    Tool(
        name="dbgprobe.svd.info",
        description=(
            "Get SVD metadata for a session: device name, peripheral count. "
            "Returns null if no SVD is attached."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.svd.read",
        description=(
            "Read a register or field by name. For registers (e.g. 'GPIO.OUT'), "
            "returns the raw value and all decoded fields. For fields (e.g. "
            "'GPIO.PIN_CNF[3].PULL'), returns just the field value and enum name."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "target": {
                    "type": "string",
                    "description": (
                        "Register or field target: 'PERIPHERAL.REGISTER' or "
                        "'PERIPHERAL.REGISTER.FIELD' (e.g. 'GPIO.OUT', 'GPIO.PIN_CNF[3].PULL')."
                    ),
                },
            },
            "required": ["session_id", "target"],
        },
    ),
    Tool(
        name="dbgprobe.svd.write",
        description=(
            "Write a raw value to a full register. No read-modify-write — the "
            "entire register is overwritten. For field-level writes, use svd.set_field."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "register": {
                    "type": "string",
                    "description": (
                        "Register target: 'PERIPHERAL.REGISTER' (e.g. 'GPIO.OUT'). "
                        "Must not include a field name."
                    ),
                },
                "value": {
                    "type": ["integer", "string"],
                    "description": "Value to write (integer or hex string like '0x01').",
                },
            },
            "required": ["session_id", "register", "value"],
        },
    ),
    Tool(
        name="dbgprobe.svd.set_field",
        description=(
            "Read-modify-write a single register field. Reads the current register "
            "value, modifies the specified field, and writes back. Accepts enum "
            "names (e.g. 'PullUp') or integer values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "field": {
                    "type": "string",
                    "description": (
                        "Field target: 'PERIPHERAL.REGISTER.FIELD' (e.g. 'GPIO.PIN_CNF[3].PULL')."
                    ),
                },
                "value": {
                    "type": ["integer", "string"],
                    "description": "New field value — enum name (e.g. 'PullUp') or integer.",
                },
            },
            "required": ["session_id", "field", "value"],
        },
    ),
    Tool(
        name="dbgprobe.svd.update_fields",
        description=(
            "Batch read-modify-write: update multiple fields in one register with "
            "a single read and write. Accepts a dict of field names to values "
            "(enum names or integers)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "register": {
                    "type": "string",
                    "description": ("Register target: 'PERIPHERAL.REGISTER' (e.g. 'GPIO.PIN_CNF[3]')."),
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Dict of field name → value. Values can be enum names (e.g. 'PullUp') or integers."
                    ),
                    "additionalProperties": {"type": ["integer", "string"]},
                },
            },
            "required": ["session_id", "register", "fields"],
        },
    ),
    Tool(
        name="dbgprobe.svd.list_peripherals",
        description="List all peripherals defined in the attached SVD.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="dbgprobe.svd.list_registers",
        description="List all registers for a peripheral.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "peripheral": {
                    "type": "string",
                    "description": "Peripheral name (e.g. 'GPIO').",
                },
            },
            "required": ["session_id", "peripheral"],
        },
    ),
    Tool(
        name="dbgprobe.svd.list_fields",
        description="List all fields for a register.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "peripheral": {
                    "type": "string",
                    "description": "Peripheral name (e.g. 'GPIO').",
                },
                "register": {
                    "type": "string",
                    "description": "Register name (e.g. 'PIN_CNF[3]', 'OUT').",
                },
            },
            "required": ["session_id", "peripheral", "register"],
        },
    ),
    Tool(
        name="dbgprobe.svd.describe",
        description=(
            "Detailed description of a peripheral, register, or field. Includes "
            "description, access type, reset value, and enum values as appropriate."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "target": {
                    "type": "string",
                    "description": (
                        "Target: 'PERIPHERAL', 'PERIPHERAL.REGISTER', or 'PERIPHERAL.REGISTER.FIELD'."
                    ),
                },
            },
            "required": ["session_id", "target"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_svd(state: ProbeState, args: dict[str, Any]) -> tuple[Any, SvdData, dict[str, Any] | None]:
    """Get session + SVD or return an error dict."""
    session = state.get_session(args["session_id"])
    if session.svd is None:
        return None, None, _err("no_svd", "No SVD attached. Use dbgprobe.svd.attach first.")
    return session, session.svd, None


def _svd_summary(svd: SvdData) -> dict[str, Any]:
    """Build a summary dict for an attached SVD."""
    total_regs = sum(len(p.registers) for p in svd.peripherals.values())
    return {
        "path": svd.path,
        "device_name": svd.device_name,
        "peripheral_count": len(svd.peripherals),
        "register_count": total_regs,
    }


def _parse_value(value: int | str) -> int:
    """Parse a value that may be an integer or hex string."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            raise ValueError(f"Invalid value: {value!r}") from None
    raise ValueError(f"Invalid value type: {type(value).__name__}")


async def _mem_read_reg(backend: Any, address: int, size_bits: int) -> int:
    """Read a register from memory and unpack to int."""
    size_bytes = size_bits // 8
    data = await backend.mem_read(address, size_bytes)
    if size_bytes == 1:
        return data[0]
    elif size_bytes == 2:
        return struct.unpack("<H", data)[0]
    else:
        return struct.unpack("<I", data)[0]


async def _mem_write_reg(backend: Any, address: int, size_bits: int, value: int) -> None:
    """Pack an int and write to a register."""
    size_bytes = size_bits // 8
    if size_bytes == 1:
        data = bytes([value & 0xFF])
    elif size_bytes == 2:
        data = struct.pack("<H", value & 0xFFFF)
    else:
        data = struct.pack("<I", value & 0xFFFF_FFFF)
    await backend.mem_write(address, data)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_svd_attach(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    try:
        path = _validate_file_path(args["path"], {".svd", ".xml"})
    except FileNotFoundError as exc:
        return _err("not_found", str(exc))
    except ValueError as exc:
        return _err("invalid_path", str(exc))

    try:
        svd = parse_svd(str(path))
    except FileNotFoundError as exc:
        return _err("not_found", str(exc))
    except Exception as exc:
        return _err("parse_error", f"Failed to parse SVD: {exc}")

    session.svd = svd
    return _ok(session_id=args["session_id"], **_svd_summary(svd))


async def handle_svd_info(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session = state.get_session(args["session_id"])
    if session.svd is None:
        return _ok(svd=None)
    return _ok(svd=_svd_summary(session.svd))


async def handle_svd_read(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    target = parse_target(args["target"])
    reg = resolve_register(svd, target)

    # Warn on write-only reads
    warning = None
    if reg.access == "write-only":
        warning = f"Register {reg.name} is write-only. Read value may be unreliable."

    raw = await _mem_read_reg(session.backend, reg.address, reg.size)

    if target.field is not None:
        # Field-level read
        fld = reg.fields.get(target.field)
        if fld is None:
            return _err("not_found", f"Unknown field: {target.field!r}")
        val = extract_field(reg, fld, raw)
        result = _ok(
            session_id=args["session_id"],
            target=args["target"],
            register=f"{target.peripheral}.{target.register}",
            field=target.field,
            address=reg.address,
            raw_register=raw,
            value=val,
        )
        enum_name = fld.enumerated_names.get(val)
        if enum_name is not None:
            result["enum"] = enum_name
    else:
        # Register-level read
        decoded = decode_register(reg, raw)
        result = _ok(
            session_id=args["session_id"],
            target=args["target"],
            address=reg.address,
            raw=raw,
            fields=decoded,
        )

    if warning:
        result["warning"] = warning
    return result


async def handle_svd_write(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    target = parse_target(args["register"])

    if target.field is not None:
        return _err(
            "invalid_params",
            "svd.write takes a register target (PERIPHERAL.REGISTER), not a field. "
            "Use svd.set_field for field-level writes.",
        )

    reg = resolve_register(svd, target)
    if reg.access == "read-only":
        return _err("read_only", f"Register {reg.name} is read-only.")

    value = _parse_value(args["value"])
    await _mem_write_reg(session.backend, reg.address, reg.size, value)

    return _ok(
        session_id=args["session_id"],
        register=args["register"],
        address=reg.address,
        value=value,
    )


async def handle_svd_set_field(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    target = parse_target(args["field"])

    if target.field is None:
        return _err(
            "invalid_params",
            "svd.set_field requires a field target (PERIPHERAL.REGISTER.FIELD).",
        )

    reg, fld = resolve_field(svd, target)
    if reg.access == "read-only":
        return _err("read_only", f"Register {reg.name} is read-only.")

    new_val = resolve_enum_value(fld, args["value"])
    old_raw = await _mem_read_reg(session.backend, reg.address, reg.size)
    old_field_val = extract_field(reg, fld, old_raw)
    new_raw = encode_field(reg, fld, old_raw, new_val)
    await _mem_write_reg(session.backend, reg.address, reg.size, new_raw)

    result: dict[str, Any] = {
        "session_id": args["session_id"],
        "field": args["field"],
        "address": reg.address,
        "old_value": old_field_val,
        "new_value": new_val,
        "old_raw": old_raw,
        "new_raw": new_raw,
    }
    # Add enum names if available
    old_enum = fld.enumerated_names.get(old_field_val)
    if old_enum is not None:
        result["old_enum"] = old_enum
    new_enum = fld.enumerated_names.get(new_val)
    if new_enum is not None:
        result["new_enum"] = new_enum

    return _ok(**result)


async def handle_svd_update_fields(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    session, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    target = parse_target(args["register"])

    if target.field is not None:
        return _err(
            "invalid_params",
            "svd.update_fields takes a register target (PERIPHERAL.REGISTER).",
        )

    reg = resolve_register(svd, target)
    if reg.access == "read-only":
        return _err("read_only", f"Register {reg.name} is read-only.")

    fields_input = args["fields"]
    if not fields_input:
        return _err("invalid_params", "No fields provided.")

    # Validate all fields exist before reading
    field_updates: list[tuple[Any, int]] = []
    for fname, fval in fields_input.items():
        fld = reg.fields.get(fname)
        if fld is None:
            return _err("not_found", f"Unknown field: {fname!r} in {reg.name}")
        resolved = resolve_enum_value(fld, fval)
        field_updates.append((fld, resolved))

    old_raw = await _mem_read_reg(session.backend, reg.address, reg.size)
    new_raw = old_raw
    changes: dict[str, Any] = {}
    for fld, new_val in field_updates:
        old_val = extract_field(reg, fld, old_raw)
        new_raw = encode_field(reg, fld, new_raw, new_val)
        entry: dict[str, Any] = {"old_value": old_val, "new_value": new_val}
        old_enum = fld.enumerated_names.get(old_val)
        if old_enum is not None:
            entry["old_enum"] = old_enum
        new_enum = fld.enumerated_names.get(new_val)
        if new_enum is not None:
            entry["new_enum"] = new_enum
        changes[fld.name] = entry

    await _mem_write_reg(session.backend, reg.address, reg.size, new_raw)

    return _ok(
        session_id=args["session_id"],
        register=args["register"],
        address=reg.address,
        old_raw=old_raw,
        new_raw=new_raw,
        changes=changes,
    )


async def handle_svd_list_peripherals(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    _, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    peripherals = [
        {
            "name": p.name,
            "base_address": p.base_address,
            "description": p.description,
            "register_count": len(p.registers),
        }
        for p in svd.peripherals.values()
    ]
    return _ok(
        session_id=args["session_id"],
        peripherals=peripherals,
        count=len(peripherals),
    )


async def handle_svd_list_registers(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    _, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    periph_name = args["peripheral"]
    periph = svd.peripherals.get(periph_name)
    if periph is None:
        return _err("not_found", f"Unknown peripheral: {periph_name!r}")
    registers = [
        {
            "name": r.name,
            "address": r.address,
            "size": r.size,
            "access": r.access,
            "field_count": len(r.fields),
        }
        for r in periph.registers.values()
    ]
    return _ok(
        session_id=args["session_id"],
        peripheral=periph_name,
        registers=registers,
        count=len(registers),
    )


async def handle_svd_list_fields(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    _, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    periph_name = args["peripheral"]
    reg_name = args["register"]
    periph = svd.peripherals.get(periph_name)
    if periph is None:
        return _err("not_found", f"Unknown peripheral: {periph_name!r}")
    reg = periph.registers.get(reg_name)
    if reg is None:
        return _err("not_found", f"Unknown register: {periph_name}.{reg_name!r}")
    fields = [
        {
            "name": f.name,
            "bit_offset": f.bit_offset,
            "bit_width": f.bit_width,
            "access": f.access,
            "enum_values": f.enumerated_values if f.enumerated_values else None,
        }
        for f in reg.fields.values()
    ]
    return _ok(
        session_id=args["session_id"],
        peripheral=periph_name,
        register=reg_name,
        fields=fields,
        count=len(fields),
    )


async def handle_svd_describe(state: ProbeState, args: dict[str, Any]) -> dict[str, Any]:
    _, svd, err = _require_svd(state, args)
    if err is not None:
        return err
    target_str = args["target"]

    # Try peripheral-only first (no dot → just peripheral name)
    if "." not in target_str:
        periph = svd.peripherals.get(target_str)
        if periph is None:
            return _err("not_found", f"Unknown peripheral: {target_str!r}")
        return _ok(
            session_id=args["session_id"],
            type="peripheral",
            name=periph.name,
            base_address=periph.base_address,
            description=periph.description,
            register_count=len(periph.registers),
            registers=[r.name for r in periph.registers.values()],
        )

    target = parse_target(target_str)

    if target.field is not None:
        # Field description
        reg, fld = resolve_field(svd, target)
        result: dict[str, Any] = {
            "session_id": args["session_id"],
            "type": "field",
            "name": fld.name,
            "register": f"{target.peripheral}.{target.register}",
            "bit_offset": fld.bit_offset,
            "bit_width": fld.bit_width,
            "bit_range": f"[{fld.bit_offset}:{fld.bit_offset + fld.bit_width - 1}]",
            "access": fld.access or reg.access,
            "description": fld.description,
        }
        if fld.enumerated_values:
            result["enum_values"] = fld.enumerated_values
        return _ok(**result)

    # Register description
    reg = resolve_register(svd, target)
    return _ok(
        session_id=args["session_id"],
        type="register",
        name=reg.name,
        peripheral=target.peripheral,
        address=reg.address,
        size=reg.size,
        access=reg.access,
        reset_value=reg.reset_value,
        description=reg.description,
        field_count=len(reg.fields),
        fields=[f.name for f in reg.fields.values()],
    )


HANDLERS: dict[str, Any] = {
    "dbgprobe.svd.attach": handle_svd_attach,
    "dbgprobe.svd.info": handle_svd_info,
    "dbgprobe.svd.read": handle_svd_read,
    "dbgprobe.svd.write": handle_svd_write,
    "dbgprobe.svd.set_field": handle_svd_set_field,
    "dbgprobe.svd.update_fields": handle_svd_update_fields,
    "dbgprobe.svd.list_peripherals": handle_svd_list_peripherals,
    "dbgprobe.svd.list_registers": handle_svd_list_registers,
    "dbgprobe.svd.list_fields": handle_svd_list_fields,
    "dbgprobe.svd.describe": handle_svd_describe,
}
