"""Tests for dbgprobe_mcp_server.svd — SVD parsing and register resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dbgprobe_mcp_server.svd import (
    SvdTarget,
    decode_register,
    encode_field,
    extract_field,
    parse_svd,
    parse_target,
    register_at_address,
    resolve_enum_value,
    resolve_field,
    resolve_register,
)

FIXTURES = Path(__file__).parent / "fixtures"
MINIMAL_SVD = str(FIXTURES / "minimal.svd")


# ---------------------------------------------------------------------------
# parse_svd
# ---------------------------------------------------------------------------


class TestParseSvd:
    def test_parse_success(self):
        svd = parse_svd(MINIMAL_SVD)
        assert svd.device_name == "TestDevice"
        assert "GPIO" in svd.peripherals
        assert "TIMER0" in svd.peripherals

    def test_parse_path_absolute(self):
        svd = parse_svd(MINIMAL_SVD)
        assert os.path.isabs(svd.path)

    def test_peripheral_count(self):
        svd = parse_svd(MINIMAL_SVD)
        assert len(svd.peripherals) == 2

    def test_gpio_registers(self):
        svd = parse_svd(MINIMAL_SVD)
        gpio = svd.peripherals["GPIO"]
        assert gpio.base_address == 0x5000_0000
        reg_names = set(gpio.registers.keys())
        assert "OUT" in reg_names
        assert "IN" in reg_names
        assert "DIR" in reg_names
        assert "OUTSET" in reg_names
        # Indexed registers expanded
        assert "PIN_CNF[0]" in reg_names
        assert "PIN_CNF[1]" in reg_names
        assert "PIN_CNF[2]" in reg_names
        assert "PIN_CNF[3]" in reg_names

    def test_register_addresses(self):
        svd = parse_svd(MINIMAL_SVD)
        gpio = svd.peripherals["GPIO"]
        assert gpio.registers["OUT"].address == 0x5000_0504
        assert gpio.registers["IN"].address == 0x5000_0510
        assert gpio.registers["PIN_CNF[0]"].address == 0x5000_0700
        assert gpio.registers["PIN_CNF[1]"].address == 0x5000_0704
        assert gpio.registers["PIN_CNF[3]"].address == 0x5000_070C

    def test_register_access(self):
        svd = parse_svd(MINIMAL_SVD)
        gpio = svd.peripherals["GPIO"]
        assert gpio.registers["OUT"].access == "read-write"
        assert gpio.registers["IN"].access == "read-only"
        assert gpio.registers["OUTSET"].access == "write-only"

    def test_register_reset_value(self):
        svd = parse_svd(MINIMAL_SVD)
        gpio = svd.peripherals["GPIO"]
        assert gpio.registers["OUT"].reset_value == 0
        assert gpio.registers["PIN_CNF[0]"].reset_value == 2

    def test_fields(self):
        svd = parse_svd(MINIMAL_SVD)
        gpio = svd.peripherals["GPIO"]
        pin_cnf = gpio.registers["PIN_CNF[0]"]
        assert "DIR" in pin_cnf.fields
        assert "INPUT" in pin_cnf.fields
        assert "PULL" in pin_cnf.fields
        assert "DRIVE" in pin_cnf.fields
        assert "SENSE" in pin_cnf.fields

    def test_field_bits(self):
        svd = parse_svd(MINIMAL_SVD)
        pull = svd.peripherals["GPIO"].registers["PIN_CNF[0]"].fields["PULL"]
        assert pull.bit_offset == 2
        assert pull.bit_width == 2

    def test_field_enums(self):
        svd = parse_svd(MINIMAL_SVD)
        pull = svd.peripherals["GPIO"].registers["PIN_CNF[0]"].fields["PULL"]
        assert pull.enumerated_values["Disabled"] == 0
        assert pull.enumerated_values["PullDown"] == 1
        assert pull.enumerated_values["PullUp"] == 3
        assert pull.enumerated_names[3] == "PullUp"

    def test_addr_to_register_lookup(self):
        svd = parse_svd(MINIMAL_SVD)
        assert (0x5000_0504) in svd._addr_to_register
        periph_name, reg_name = svd._addr_to_register[0x5000_0504]
        assert periph_name == "GPIO"
        assert reg_name == "OUT"

    def test_parse_not_found(self):
        with pytest.raises(FileNotFoundError, match="SVD file not found"):
            parse_svd("/nonexistent/path.svd")


# ---------------------------------------------------------------------------
# parse_target
# ---------------------------------------------------------------------------


class TestParseTarget:
    def test_register_only(self):
        t = parse_target("GPIO.OUT")
        assert t == SvdTarget(peripheral="GPIO", register="OUT", field=None)

    def test_indexed_register(self):
        t = parse_target("GPIO.PIN_CNF[3]")
        assert t == SvdTarget(peripheral="GPIO", register="PIN_CNF[3]", field=None)

    def test_register_with_field(self):
        t = parse_target("GPIO.PIN_CNF[3].PULL")
        assert t == SvdTarget(peripheral="GPIO", register="PIN_CNF[3]", field="PULL")

    def test_simple_field(self):
        t = parse_target("GPIO.OUT.PIN0")
        assert t == SvdTarget(peripheral="GPIO", register="OUT", field="PIN0")

    def test_invalid_single_name(self):
        with pytest.raises(ValueError, match="Invalid target"):
            parse_target("GPIO")

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid target"):
            parse_target("")


# ---------------------------------------------------------------------------
# resolve_register / resolve_field
# ---------------------------------------------------------------------------


class TestResolveRegister:
    def test_simple_register(self):
        svd = parse_svd(MINIMAL_SVD)
        target = parse_target("GPIO.OUT")
        reg = resolve_register(svd, target)
        assert reg.name == "OUT"
        assert reg.address == 0x5000_0504

    def test_indexed_register(self):
        svd = parse_svd(MINIMAL_SVD)
        target = parse_target("GPIO.PIN_CNF[2]")
        reg = resolve_register(svd, target)
        assert reg.name == "PIN_CNF[2]"
        assert reg.address == 0x5000_0708

    def test_unknown_peripheral(self):
        svd = parse_svd(MINIMAL_SVD)
        target = parse_target("NOPE.OUT")
        with pytest.raises(ValueError, match="Unknown peripheral"):
            resolve_register(svd, target)

    def test_unknown_register(self):
        svd = parse_svd(MINIMAL_SVD)
        target = parse_target("GPIO.NOPE")
        with pytest.raises(ValueError, match="Unknown register"):
            resolve_register(svd, target)


class TestResolveField:
    def test_success(self):
        svd = parse_svd(MINIMAL_SVD)
        target = parse_target("GPIO.PIN_CNF[0].PULL")
        reg, fld = resolve_field(svd, target)
        assert reg.name == "PIN_CNF[0]"
        assert fld.name == "PULL"
        assert fld.bit_width == 2

    def test_no_field_in_target(self):
        svd = parse_svd(MINIMAL_SVD)
        target = parse_target("GPIO.OUT")
        with pytest.raises(ValueError, match="No field specified"):
            resolve_field(svd, target)

    def test_unknown_field(self):
        svd = parse_svd(MINIMAL_SVD)
        target = parse_target("GPIO.PIN_CNF[0].NOPE")
        with pytest.raises(ValueError, match="Unknown field"):
            resolve_field(svd, target)


# ---------------------------------------------------------------------------
# decode / extract / encode
# ---------------------------------------------------------------------------


class TestDecodeRegister:
    def test_decode_all_fields(self):
        svd = parse_svd(MINIMAL_SVD)
        reg = svd.peripherals["GPIO"].registers["PIN_CNF[0]"]
        # Value: DIR=1, INPUT=0, PULL=3 (PullUp), DRIVE=0, SENSE=0
        # Binary: ...0000_0000_0000_0000_0000_0000_0000_1101 = 0x0D
        raw = 0b0000_0000_0000_0000_0000_0000_0000_1101
        decoded = decode_register(reg, raw)
        assert decoded["DIR"]["value"] == 1
        assert decoded["DIR"]["enum"] == "Output"
        assert decoded["INPUT"]["value"] == 0
        assert decoded["INPUT"]["enum"] == "Connect"
        assert decoded["PULL"]["value"] == 3
        assert decoded["PULL"]["enum"] == "PullUp"
        assert decoded["DRIVE"]["value"] == 0
        assert decoded["DRIVE"]["enum"] == "S0S1"

    def test_decode_no_enum_match(self):
        svd = parse_svd(MINIMAL_SVD)
        reg = svd.peripherals["GPIO"].registers["PIN_CNF[0]"]
        # PULL=2 has no enum
        raw = 0b1000  # PULL=2
        decoded = decode_register(reg, raw)
        assert decoded["PULL"]["value"] == 2
        assert "enum" not in decoded["PULL"]


class TestExtractField:
    def test_extract(self):
        svd = parse_svd(MINIMAL_SVD)
        reg = svd.peripherals["GPIO"].registers["PIN_CNF[0]"]
        fld = reg.fields["PULL"]
        # PULL is bits [2:3], value 3 => PullUp
        raw = 0x0D  # 0b1101 -> PULL bits = 11 = 3
        assert extract_field(reg, fld, raw) == 3


class TestResolveEnumValue:
    def test_int_passthrough(self):
        svd = parse_svd(MINIMAL_SVD)
        fld = svd.peripherals["GPIO"].registers["PIN_CNF[0]"].fields["PULL"]
        assert resolve_enum_value(fld, 3) == 3

    def test_enum_name(self):
        svd = parse_svd(MINIMAL_SVD)
        fld = svd.peripherals["GPIO"].registers["PIN_CNF[0]"].fields["PULL"]
        assert resolve_enum_value(fld, "PullUp") == 3

    def test_hex_string(self):
        svd = parse_svd(MINIMAL_SVD)
        fld = svd.peripherals["GPIO"].registers["PIN_CNF[0]"].fields["PULL"]
        assert resolve_enum_value(fld, "0x1") == 1

    def test_unknown_name(self):
        svd = parse_svd(MINIMAL_SVD)
        fld = svd.peripherals["GPIO"].registers["PIN_CNF[0]"].fields["PULL"]
        with pytest.raises(ValueError, match="Unknown value"):
            resolve_enum_value(fld, "BadName")


class TestEncodeField:
    def test_encode_sets_field(self):
        svd = parse_svd(MINIMAL_SVD)
        reg = svd.peripherals["GPIO"].registers["PIN_CNF[0]"]
        fld = reg.fields["PULL"]
        # Current value: PULL=0 (Disabled), set to 3 (PullUp)
        result = encode_field(reg, fld, 0x00, 3)
        assert result == 0x0C  # 3 << 2 = 0b1100

    def test_encode_preserves_other_fields(self):
        svd = parse_svd(MINIMAL_SVD)
        reg = svd.peripherals["GPIO"].registers["PIN_CNF[0]"]
        fld = reg.fields["PULL"]
        # Current value has DIR=1 (bit 0)
        result = encode_field(reg, fld, 0x01, 3)
        assert result == 0x0D  # DIR=1 + PULL=3<<2

    def test_encode_clears_old_value(self):
        svd = parse_svd(MINIMAL_SVD)
        reg = svd.peripherals["GPIO"].registers["PIN_CNF[0]"]
        fld = reg.fields["PULL"]
        # Current has PULL=3, set to 0
        result = encode_field(reg, fld, 0x0C, 0)
        assert result == 0x00

    def test_encode_out_of_range(self):
        svd = parse_svd(MINIMAL_SVD)
        reg = svd.peripherals["GPIO"].registers["PIN_CNF[0]"]
        fld = reg.fields["PULL"]  # 2 bits, max 3
        with pytest.raises(ValueError, match="out of range"):
            encode_field(reg, fld, 0x00, 4)


# ---------------------------------------------------------------------------
# register_at_address
# ---------------------------------------------------------------------------


class TestRegisterAtAddress:
    def test_found(self):
        svd = parse_svd(MINIMAL_SVD)
        result = register_at_address(svd, 0x5000_0504)
        assert result is not None
        periph, reg = result
        assert periph.name == "GPIO"
        assert reg.name == "OUT"

    def test_indexed_register(self):
        svd = parse_svd(MINIMAL_SVD)
        result = register_at_address(svd, 0x5000_070C)
        assert result is not None
        periph, reg = result
        assert periph.name == "GPIO"
        assert reg.name == "PIN_CNF[3]"

    def test_not_found(self):
        svd = parse_svd(MINIMAL_SVD)
        result = register_at_address(svd, 0xDEAD_BEEF)
        assert result is None
