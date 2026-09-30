"""
Program loading shared by the CLI and the GUI server.

load_program() assembles a .s file (or takes an .elf), merges it with the
simulator OS image and returns a ready-to-run CPU plus its symbol tables.
"""

import os
import struct
from dataclasses import dataclass, field

from .cpu import CPU, SimulatorError, MEM_SIZE


class LoadError(Exception):
    pass


@dataclass
class Program:
    path: str
    cpu: CPU
    asm_map: dict
    sym_map: dict
    label_map: dict = field(default_factory=dict)
    main_addr: int = 0


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def build_os_elf(verbose=False):
    """Compile os.s → os.elf if missing or stale.  Returns path to os.elf."""
    from .sim import assemble
    sd     = _script_dir()
    os_s   = os.path.join(sd, 'os.s')
    os_ld  = os.path.join(sd, 'os.ld')
    os_elf = os.path.join(sd, 'os.elf')
    if not os.path.exists(os_s):
        raise LoadError(f"OS source '{os_s}' not found.")
    need_build = (
        not os.path.exists(os_elf) or
        os.path.getmtime(os_s) > os.path.getmtime(os_elf) or
        (os.path.exists(os_ld) and os.path.getmtime(os_ld) > os.path.getmtime(os_elf))
    )
    if need_build:
        if verbose:
            print("Compiling OS...")
        ok, msg = assemble(os_s, os_elf, os_ld)
        if not ok:
            raise LoadError(f"OS build failed:\n{msg}")
    return os_elf


def load_program(path, trace=False, verbose=False):
    """Assemble (if needed), load and link `path` with the OS; return a Program."""
    from .sim import assemble, load_elf
    from .memory import FlatRAM, Memory
    from .gpio import GPIO

    os_elf_path = build_os_elf(verbose)

    input_file = path
    if input_file.endswith('.s'):
        if not os.path.exists(input_file):
            raise LoadError(f"File not found: {input_file}")
        elf_out     = input_file[:-2] + '.elf'
        ld_file     = os.path.join(_script_dir(), 'link.ld')
        peripherals = os.path.join(_script_dir(), 'peripherals.s')
        # --just-symbols lets the linker resolve putchar/getchar from the OS
        just_syms = f'-Wl,--just-symbols={os_elf_path}'
        if verbose:
            print(f"Assembling '{input_file}'...")
        ok, msg = assemble(input_file, elf_out, ld_file,
                           extra_s_files=[peripherals], extra_ld_flags=[just_syms])
        if not ok:
            raise LoadError(f"Assembler/linker error:\n{msg}")
        input_file = elf_out

    try:
        os_memory, os_entry, os_asm, os_syms = load_elf(os_elf_path)
    except (SimulatorError, FileNotFoundError) as e:
        raise LoadError(f"loading OS ELF: {e}")
    try:
        user_memory, user_entry, user_asm, user_syms = load_elf(input_file)
    except (SimulatorError, FileNotFoundError) as e:
        raise LoadError(f"loading '{input_file}': {e}")

    # Merge memories: OS owns 0x0000-0x2FFF, user owns 0x3000-0xFFFF
    memory = bytearray(MEM_SIZE)
    memory[0x0000:0x3000] = os_memory[0x0000:0x3000]
    memory[0x3000:      ] = user_memory[0x3000:      ]

    # Write user main()'s Thumb address into the OS pointer slot at 0x2FFC
    struct.pack_into('<I', memory, 0x2FFC, user_entry | 1)

    asm_map = {**os_asm,  **user_asm}
    sym_map = {**os_syms, **user_syms}

    if verbose:
        print(f"Loaded '{input_file}'  main=0x{user_entry:04X}  "
              f"{len(asm_map)} instructions disassembled")

    cpu = CPU(Memory(FlatRAM(memory)), os_entry, asm_map, sym_map, trace=trace)
    gpio = GPIO()
    cpu.add_peripheral(gpio)
    cpu.gpio = gpio

    # label_map: name → addr, restricted to addresses that have disassembly.
    label_map = {name: addr for addr, name in sym_map.items() if addr in asm_map}

    return Program(path, cpu, asm_map, sym_map, label_map, user_entry)
