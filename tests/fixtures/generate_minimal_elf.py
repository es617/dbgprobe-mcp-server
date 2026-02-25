#!/usr/bin/env python3
"""Generate a minimal ARM ELF with known symbols for testing.

Run once to create tests/fixtures/minimal.elf.
The ELF has:
  - Entry point: 0x08000000
  - main      FUNC  @ 0x080000ED  size=42
  - loop      FUNC  @ 0x08000117  size=16
  - idle      FUNC  @ 0x08000127  size=8
  - some_var  OBJECT @ 0x20000000  size=4
  - .text section @ 0x080000ED
  - .bss  section @ 0x20000000
"""

from __future__ import annotations

import struct
from pathlib import Path

# ELF constants
ELFMAG = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1  # little-endian
EV_CURRENT = 1
ET_EXEC = 2
EM_ARM = 40
SHT_NULL = 0
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_NOBITS = 8
STB_GLOBAL = 1
STT_FUNC = 2
STT_OBJECT = 1
SHN_ABS = 0xFFF1

ENTRY_POINT = 0x0800_0000

# Symbols we want in the ELF
SYMBOLS = [
    # (name, address, size, type, section_index)
    ("main", 0x0800_00ED, 42, STT_FUNC, 1),
    ("loop", 0x0800_0117, 16, STT_FUNC, 1),
    ("idle", 0x0800_0127, 8, STT_FUNC, 1),
    ("some_var", 0x2000_0000, 4, STT_OBJECT, 2),
]


def _build_strtab(names: list[str]) -> tuple[bytes, dict[str, int]]:
    """Build a string table and return (bytes, name->offset map)."""
    data = b"\x00"
    offsets: dict[str, int] = {}
    for name in names:
        offsets[name] = len(data)
        data += name.encode("ascii") + b"\x00"
    return data, offsets


def _pack_sym(name_idx: int, value: int, size: int, info: int, shndx: int) -> bytes:
    """Pack an Elf32_Sym."""
    return struct.pack("<IIIBBH", name_idx, value, size, info, 0, shndx)


def build_elf() -> bytes:
    # Section names
    section_names = ["", ".text", ".bss", ".symtab", ".strtab", ".shstrtab"]
    shstrtab, shstr_offsets = _build_strtab(section_names)

    # Symbol string table
    sym_names = [s[0] for s in SYMBOLS]
    strtab, str_offsets = _build_strtab(sym_names)

    # Symbol table: null entry + our symbols
    symtab = _pack_sym(0, 0, 0, 0, 0)  # null symbol
    for name, addr, size, stype, shndx in SYMBOLS:
        info = (STB_GLOBAL << 4) | stype
        symtab += _pack_sym(str_offsets[name], addr, size, info, shndx)

    # .text section content (just some NOPs, 0x00 bytes)
    text_data = b"\x00" * 64

    # Layout:
    # ELF header: 52 bytes
    # .text data starts at offset 52
    # .strtab data
    # .symtab data
    # .shstrtab data
    # Section headers

    ehdr_size = 52
    shentsize = 40

    text_offset = ehdr_size
    text_size = len(text_data)

    strtab_offset = text_offset + text_size
    strtab_size = len(strtab)

    symtab_offset = strtab_offset + strtab_size
    symtab_size = len(symtab)

    shstrtab_offset = symtab_offset + symtab_size
    shstrtab_size = len(shstrtab)

    shoff = shstrtab_offset + shstrtab_size
    # Align to 4 bytes
    if shoff % 4:
        shoff += 4 - (shoff % 4)

    num_sections = 6  # null, .text, .bss, .symtab, .strtab, .shstrtab
    shstrtab_idx = 5

    # ELF header
    e_ident = ELFMAG + bytes([ELFCLASS32, ELFDATA2LSB, EV_CURRENT]) + b"\x00" * 9
    ehdr = e_ident + struct.pack(
        "<HHIIIIIHHHHHH",
        ET_EXEC,  # e_type
        EM_ARM,  # e_machine
        EV_CURRENT,  # e_version
        ENTRY_POINT,  # e_entry
        0,  # e_phoff (no program headers)
        shoff,  # e_shoff
        0x05000000,  # e_flags (EABI5)
        ehdr_size,  # e_ehsize
        0,  # e_phentsize
        0,  # e_phnum
        shentsize,  # e_shentsize
        num_sections,  # e_shnum
        shstrtab_idx,  # e_shstrndx
    )

    # Section headers
    def _shdr(name_off, sh_type, flags, addr, offset, size, link=0, info=0, addralign=1, entsize=0):
        return struct.pack(
            "<IIIIIIIIII",
            name_off,
            sh_type,
            flags,
            addr,
            offset,
            size,
            link,
            info,
            addralign,
            entsize,
        )

    shdrs = b""
    # 0: null
    shdrs += _shdr(0, SHT_NULL, 0, 0, 0, 0)
    # 1: .text
    shdrs += _shdr(shstr_offsets[".text"], SHT_PROGBITS, 0x6, 0x080000ED, text_offset, text_size, addralign=4)
    # 2: .bss
    shdrs += _shdr(shstr_offsets[".bss"], SHT_NOBITS, 0x3, 0x20000000, 0, 4, addralign=4)
    # 3: .symtab (link=strtab index=4, info=first global symbol index=1)
    shdrs += _shdr(
        shstr_offsets[".symtab"],
        SHT_SYMTAB,
        0,
        0,
        symtab_offset,
        symtab_size,
        link=4,
        info=1,
        addralign=4,
        entsize=16,
    )
    # 4: .strtab
    shdrs += _shdr(shstr_offsets[".strtab"], SHT_STRTAB, 0, 0, strtab_offset, strtab_size)
    # 5: .shstrtab
    shdrs += _shdr(shstr_offsets[".shstrtab"], SHT_STRTAB, 0, 0, shstrtab_offset, shstrtab_size)

    # Assemble
    padding_before_shdrs = shoff - (shstrtab_offset + shstrtab_size)
    blob = ehdr + text_data + strtab + symtab + shstrtab + (b"\x00" * padding_before_shdrs) + shdrs
    return blob


if __name__ == "__main__":
    out = Path(__file__).parent / "minimal.elf"
    data = build_elf()
    out.write_bytes(data)
    print(f"Wrote {len(data)} bytes to {out}")

    # Verify it parses
    from io import BytesIO

    from elftools.elf.elffile import ELFFile

    elf = ELFFile(BytesIO(data))
    print(f"Entry: 0x{elf.header.e_entry:08x}")
    for section in elf.iter_sections():
        print(f"  Section: {section.name} type={section['sh_type']} addr=0x{section['sh_addr']:08x}")
        if hasattr(section, "iter_symbols"):
            for sym in section.iter_symbols():
                if sym.name:
                    print(
                        f"    {sym.name}: 0x{sym.entry.st_value:08x} size={sym.entry.st_size} type={sym.entry.st_info.type}"
                    )
