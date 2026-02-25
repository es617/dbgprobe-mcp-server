"""Tests for dbgprobe_mcp_server.elf — ELF parsing and symbol resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dbgprobe_mcp_server.elf import (
    find_sibling_elf,
    parse_elf,
    resolve_address,
    resolve_symbol,
    search_symbols,
)

FIXTURES = Path(__file__).parent / "fixtures"
MINIMAL_ELF = str(FIXTURES / "minimal.elf")

# Known symbols in minimal.elf (see generate_minimal_elf.py):
# Raw ELF addresses have Thumb bit set (odd) for FUNC symbols on ARM.
# parse_elf strips bit 0, so resolved addresses are even:
#   main      FUNC  @ 0x080000EC  size=42  (raw 0x080000ED)
#   loop      FUNC  @ 0x08000116  size=16  (raw 0x08000117)
#   idle      FUNC  @ 0x08000126  size=8   (raw 0x08000127)
#   some_var  OBJECT @ 0x20000000  size=4   (unchanged)


class TestParseElf:
    def test_parse_success(self):
        elf = parse_elf(MINIMAL_ELF)
        assert elf.entry_point == 0x0800_0000
        assert "main" in elf.symbols
        assert "loop" in elf.symbols
        assert "idle" in elf.symbols
        assert "some_var" in elf.symbols

    def test_parse_counts(self):
        elf = parse_elf(MINIMAL_ELF)
        total_symbols = sum(len(v) for v in elf.symbols.values())
        assert total_symbols == 4
        # Only FUNC symbols in _sorted_functions (main, loop, idle)
        assert len(elf._sorted_functions) == 3

    def test_parse_path_absolute(self):
        elf = parse_elf(MINIMAL_ELF)
        assert os.path.isabs(elf.path)

    def test_parse_sections(self):
        elf = parse_elf(MINIMAL_ELF)
        section_names = [s["name"] for s in elf.sections]
        assert ".text" in section_names
        assert ".bss" in section_names

    def test_parse_not_found(self):
        with pytest.raises(FileNotFoundError, match="ELF file not found"):
            parse_elf("/nonexistent/path.elf")

    def test_parse_invalid_elf(self, tmp_path):
        from elftools.common.exceptions import ELFError

        bad = tmp_path / "bad.elf"
        bad.write_text("not an elf file")
        with pytest.raises(ELFError):
            parse_elf(str(bad))

    def test_symbol_types(self):
        elf = parse_elf(MINIMAL_ELF)
        assert elf.symbols["main"][0].sym_type == "FUNC"
        assert elf.symbols["some_var"][0].sym_type == "OBJECT"

    def test_symbol_sizes(self):
        elf = parse_elf(MINIMAL_ELF)
        assert elf.symbols["main"][0].size == 42
        assert elf.symbols["loop"][0].size == 16
        assert elf.symbols["idle"][0].size == 8
        assert elf.symbols["some_var"][0].size == 4

    def test_thumb_bit_stripped_from_func_addresses(self):
        """ARM ELF FUNC symbols have Thumb bit (bit 0) set; parse_elf strips it."""
        elf = parse_elf(MINIMAL_ELF)
        # Raw addresses in the ELF are 0x080000ED, 0x08000117, 0x08000127 (all odd).
        # After stripping: 0x080000EC, 0x08000116, 0x08000126 (all even).
        assert elf.symbols["main"][0].address == 0x0800_00EC
        assert elf.symbols["loop"][0].address == 0x0800_0116
        assert elf.symbols["idle"][0].address == 0x0800_0126
        # OBJECT symbols are NOT stripped
        assert elf.symbols["some_var"][0].address == 0x2000_0000


class TestResolveAddress:
    def test_exact_match(self):
        elf = parse_elf(MINIMAL_ELF)
        # main is at 0x080000EC after Thumb bit stripping
        result = resolve_address(elf, 0x0800_00EC)
        assert result == ("main", 0)

    def test_with_offset(self):
        elf = parse_elf(MINIMAL_ELF)
        result = resolve_address(elf, 0x0800_00EC + 6)
        assert result == ("main", 6)

    def test_at_boundary(self):
        """Address at start of loop function."""
        elf = parse_elf(MINIMAL_ELF)
        # loop is at 0x08000116 after Thumb bit stripping
        result = resolve_address(elf, 0x0800_0116)
        assert result == ("loop", 0)

    def test_beyond_function(self):
        """Address past end of all functions returns None."""
        elf = parse_elf(MINIMAL_ELF)
        result = resolve_address(elf, 0x0800_FFFF)
        assert result is None

    def test_before_first_function(self):
        """Address before all functions."""
        elf = parse_elf(MINIMAL_ELF)
        result = resolve_address(elf, 0x0800_0000)
        assert result is None

    def test_between_functions_outside_size(self):
        """Address between main and loop but past main's size."""
        elf = parse_elf(MINIMAL_ELF)
        # main: 0x080000EC size=42 -> ends at 0x08000116 (which is loop start)
        # One byte before loop: 0x08000115 — still within main
        result = resolve_address(elf, 0x0800_0115)
        assert result == ("main", 0x0800_0115 - 0x0800_00EC)


class TestResolveSymbol:
    def test_found(self):
        elf = parse_elf(MINIMAL_ELF)
        sym = resolve_symbol(elf, "main")
        assert sym is not None
        assert sym.address == 0x0800_00EC  # Thumb bit stripped
        assert sym.sym_type == "FUNC"

    def test_not_found(self):
        elf = parse_elf(MINIMAL_ELF)
        sym = resolve_symbol(elf, "nonexistent")
        assert sym is None

    def test_object_symbol(self):
        elf = parse_elf(MINIMAL_ELF)
        sym = resolve_symbol(elf, "some_var")
        assert sym is not None
        assert sym.address == 0x2000_0000
        assert sym.sym_type == "OBJECT"


class TestSearchSymbols:
    def test_substring_match(self):
        elf = parse_elf(MINIMAL_ELF)
        results = search_symbols(elf, "oo")
        names = [s.name for s in results]
        assert "loop" in names

    def test_case_insensitive(self):
        elf = parse_elf(MINIMAL_ELF)
        results = search_symbols(elf, "MAIN")
        assert len(results) == 1
        assert results[0].name == "main"

    def test_type_filter(self):
        elf = parse_elf(MINIMAL_ELF)
        # All symbols matching "" (everything), but only FUNC type
        results = search_symbols(elf, "", sym_type="FUNC")
        for s in results:
            assert s.sym_type == "FUNC"
        names = [s.name for s in results]
        assert "some_var" not in names
        assert "main" in names

    def test_limit(self):
        elf = parse_elf(MINIMAL_ELF)
        results = search_symbols(elf, "", limit=2)
        assert len(results) == 2

    def test_no_match(self):
        elf = parse_elf(MINIMAL_ELF)
        results = search_symbols(elf, "zzzzz_no_match")
        assert results == []


class TestFindSiblingElf:
    def test_same_directory(self, tmp_path):
        hex_file = tmp_path / "firmware.hex"
        hex_file.write_text(":00000001FF\n")
        elf_file = tmp_path / "firmware.elf"
        elf_file.write_bytes(b"\x7fELF")

        result = find_sibling_elf(str(hex_file))
        assert result is not None
        assert result.endswith(".elf")

    def test_one_level_down(self, tmp_path):
        hex_file = tmp_path / "merged.hex"
        hex_file.write_text(":00000001FF\n")
        subdir = tmp_path / "zephyr"
        subdir.mkdir()
        elf_file = subdir / "zephyr.elf"
        elf_file.write_bytes(b"\x7fELF")

        result = find_sibling_elf(str(hex_file))
        assert result is not None
        assert "zephyr.elf" in result

    def test_parent_directory(self, tmp_path):
        subdir = tmp_path / "build"
        subdir.mkdir()
        hex_file = subdir / "firmware.hex"
        hex_file.write_text(":00000001FF\n")
        elf_file = tmp_path / "firmware.elf"
        elf_file.write_bytes(b"\x7fELF")

        result = find_sibling_elf(str(hex_file))
        assert result is not None
        assert result.endswith(".elf")

    def test_no_elf_found(self, tmp_path):
        hex_file = tmp_path / "firmware.hex"
        hex_file.write_text(":00000001FF\n")

        result = find_sibling_elf(str(hex_file))
        assert result is None
