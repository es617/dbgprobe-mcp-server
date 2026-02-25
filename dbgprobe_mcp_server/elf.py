"""ELF file parsing — symbol resolution for debug sessions."""

from __future__ import annotations

import glob
import logging
import os
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

logger = logging.getLogger("dbgprobe_mcp_server")


@dataclass
class SymbolInfo:
    """A single ELF symbol."""

    name: str
    address: int
    size: int
    sym_type: str  # "FUNC", "OBJECT", "NOTYPE", etc.


@dataclass
class ElfData:
    """Parsed ELF data attached to a debug session."""

    path: str
    entry_point: int
    symbols: dict[str, list[SymbolInfo]]  # name -> list (duplicates possible)
    _sorted_functions: list[SymbolInfo]  # sorted by address, for binary search
    _func_addrs: list[int] = field(default_factory=list)  # parallel address list
    sections: list[dict[str, Any]] = field(default_factory=list)
    attached_at: float = field(default_factory=time.time)


def parse_elf(path: str) -> ElfData:
    """Parse an ELF file and build symbol lookup tables."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"ELF file not found: {path}")

    symbols: dict[str, list[SymbolInfo]] = {}
    functions: list[SymbolInfo] = []
    sections: list[dict[str, Any]] = []

    with open(path, "rb") as f:
        elf = ELFFile(f)
        entry_point = elf.header.e_entry

        # ARM Cortex-M (Thumb): bit 0 of function addresses is set to 1 in the
        # symbol table to indicate Thumb mode.  The actual execution address has
        # bit 0 clear.  Strip it so breakpoints and address lookups use the real
        # PC value the CPU reports.
        is_arm = elf.header.e_machine == "EM_ARM"

        # Collect sections
        for section in elf.iter_sections():
            sections.append(
                {
                    "name": section.name,
                    "address": section["sh_addr"],
                    "size": section["sh_size"],
                    "type": section["sh_type"],
                }
            )

        # Collect symbols from .symtab and .dynsym
        for section in elf.iter_sections():
            if not isinstance(section, SymbolTableSection):
                continue
            for sym in section.iter_symbols():
                name = sym.name
                if not name:
                    continue
                sym_type = sym.entry.st_info.type
                type_str = sym_type.replace("STT_", "") if sym_type.startswith("STT_") else sym_type
                address = sym.entry.st_value
                # Strip Thumb bit from function addresses on ARM targets.
                if is_arm and type_str == "FUNC":
                    address = address & ~1
                info = SymbolInfo(
                    name=name,
                    address=address,
                    size=sym.entry.st_size,
                    sym_type=type_str,
                )
                symbols.setdefault(name, []).append(info)
                if type_str == "FUNC" and info.address != 0:
                    functions.append(info)

    # Sort functions by address for binary search
    functions.sort(key=lambda s: s.address)
    func_addrs = [s.address for s in functions]

    return ElfData(
        path=path,
        entry_point=entry_point,
        symbols=symbols,
        _sorted_functions=functions,
        _func_addrs=func_addrs,
        sections=sections,
    )


def resolve_address(elf: ElfData, addr: int) -> tuple[str, int] | None:
    """Resolve an address to (symbol_name, offset) using binary search.

    Returns None if no function contains the address.
    """
    funcs = elf._sorted_functions
    addrs = elf._func_addrs
    if not funcs:
        return None

    idx = bisect_right(addrs, addr) - 1
    if idx < 0:
        return None

    sym = funcs[idx]
    offset = addr - sym.address
    # If the symbol has a known size, only match within it.
    # If size is 0 (common for assembly labels), accept any non-negative offset.
    if sym.size > 0 and offset >= sym.size:
        return None
    return (sym.name, offset)


def resolve_symbol(elf: ElfData, name: str) -> SymbolInfo | None:
    """Look up a symbol by exact name. Returns the first match or None."""
    entries = elf.symbols.get(name)
    if not entries:
        return None
    return entries[0]


def search_symbols(
    elf: ElfData,
    query: str,
    sym_type: str | None = None,
    limit: int = 50,
) -> list[SymbolInfo]:
    """Search symbols by case-insensitive substring match."""
    query_lower = query.lower()
    results: list[SymbolInfo] = []
    for name, entries in elf.symbols.items():
        if query_lower not in name.lower():
            continue
        for entry in entries:
            if sym_type is not None and entry.sym_type != sym_type:
                continue
            results.append(entry)
            if len(results) >= limit:
                return results
    return results


def find_sibling_elf(flash_path: str) -> str | None:
    """Look for .elf files near a flashed .hex/.bin file.

    Search strategy (returns first match):
    1. Same directory: *.elf
    2. One level down: */*.elf
    3. Parent directory: ../*.elf
    """
    flash_dir = os.path.dirname(os.path.abspath(flash_path))

    # 1. Same directory
    matches = glob.glob(os.path.join(flash_dir, "*.elf"))
    if matches:
        return matches[0]

    # 2. One level down
    matches = glob.glob(os.path.join(flash_dir, "*", "*.elf"))
    if matches:
        return matches[0]

    # 3. Parent directory
    parent = os.path.dirname(flash_dir)
    if parent != flash_dir:  # avoid infinite loop at filesystem root
        matches = glob.glob(os.path.join(parent, "*.elf"))
        if matches:
            return matches[0]

    return None
