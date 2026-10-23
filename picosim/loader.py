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
    # address -> (line number, source line) for the student's file and the OS
    source: dict = field(default_factory=dict)
    image: bytes = b''         # memory as loaded, for clone()
    os_entry: int = 0
    # the program's sections: name ('text', 'data', 'bss') -> (start, end)
    sections: dict = field(default_factory=dict)

    def clone(self):
        """A fresh copy of this program, exactly as it was just after loading
        (no assembling: Step back rebuilds the machine from this)."""
        cpu = _build_cpu(bytearray(self.image), self.os_entry, self.asm_map, self.sym_map)
        return Program(self.path, cpu, self.asm_map, self.sym_map, self.label_map,
                       self.main_addr, self.source, self.image, self.os_entry, self.sections)

    def run_to_main(self, limit=64):
        """Run the built-in OS's start-up (_start up to the call of main), so
        the program shows main as its first instruction.  The instructions
        really run: steps, cycles and the profile count them."""
        c = self.cpu._core
        for _ in range(limit):
            if c.halted or c.pc == self.main_addr:
                break
            c.step()
            c.check_halt()


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
        not _has_lines(os_elf) or       # built before line info was added
        os.path.getmtime(os_s) > os.path.getmtime(os_elf) or
        (os.path.exists(os_ld) and os.path.getmtime(os_ld) > os.path.getmtime(os_elf))
    )
    if need_build:
        if verbose:
            print("Compiling OS...", flush=True)
        ok, msg = assemble(os_s, os_elf, os_ld)
        if not ok:
            raise LoadError(f"OS build failed:\n{msg}")
    return os_elf


def _has_lines(elf_path):
    from .sim import source_lines
    try:
        return bool(source_lines(elf_path, 'os.s'))
    except Exception:
        return False


def _source_map(elf_path, s_path):
    """address -> (line number, the line's text) for the code from `s_path`."""
    from .sim import source_lines
    try:
        with open(s_path, encoding='utf-8', errors='replace') as f:
            text = f.read().split('\n')
        lines = source_lines(elf_path, s_path)
    except Exception:          # no line info: the listing just has no Source column
        return {}
    return {a: (n, text[n - 1].strip()) for a, n in lines.items() if 0 < n <= len(text)}


def _sections(elf_path):
    """{'text': (start, end), 'data': …, 'bss': …} of the program's own
    sections (at 0x3000 and up), for the Memory tab's map."""
    from elftools.elf.elffile import ELFFile
    out = {}
    try:
        with open(elf_path, 'rb') as f:
            for sec in ELFFile(f).iter_sections():
                name = sec.name.lstrip('.')
                a, n = sec['sh_addr'], sec['sh_size']
                if name in ('text', 'data', 'bss') and n and 0x3000 <= a < MEM_SIZE:
                    out[name] = (a, a + n)
    except (OSError, ValueError):
        pass
    return out


def load_program(path, trace=False, verbose=False):
    """Assemble (if needed), load and link `path` with the OS; return a Program."""
    from elftools.common.exceptions import ELFError
    from .sim import assemble, load_elf, elf_symbol
    from .memory import FlatRAM, Memory
    from .gpio import GPIO

    os_elf_path = build_os_elf(verbose)

    input_file = path
    s_source = path if path.endswith('.s') else None
    if input_file.endswith('.s'):
        if not os.path.exists(input_file):
            raise LoadError(f"File not found: {input_file}")
        elf_out     = input_file[:-2] + '.elf'
        ld_file     = os.path.join(_script_dir(), 'link.ld')
        peripherals = os.path.join(_script_dir(), 'peripherals.s')
        # --just-symbols lets the linker resolve putchar/getchar from the OS
        just_syms = f'-Wl,--just-symbols={os_elf_path}'
        if verbose:
            print(f"Assembling '{input_file}'...", flush=True)
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
        # The program starts at main: (asm_main: also works, as in the Pico
        # build).  Without either the linker would quietly start at the first
        # instruction, and the Pico build would fail, so say so here.
        start = elf_symbol(input_file, ('main', 'asm_main'))
    except (SimulatorError, OSError) as e:
        raise LoadError(f"loading '{input_file}': {e}")
    except ELFError as e:
        raise LoadError(f"'{input_file}' is not an ELF program file ({e})")
    if start is None:
        raise LoadError("The program has no main: label. Put main: (or asm_main:) on the line "
                        "before its first instruction; that is where the program starts.")
    user_entry = start

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

    image = bytes(memory)
    cpu = _build_cpu(memory, os_entry, asm_map, sym_map, trace)

    # label_map: name → addr, restricted to addresses that have disassembly.
    label_map = {name: addr for addr, name in sym_map.items() if addr in asm_map}

    source = _source_map(os_elf_path, os.path.join(_script_dir(), 'os.s'))
    if s_source:
        source.update(_source_map(input_file, s_source))

    return Program(path, cpu, asm_map, sym_map, label_map, user_entry, source, image, os_entry,
                   _sections(input_file))


def _build_cpu(memory, os_entry, asm_map, sym_map, trace=False):
    """A CPU on `memory` with the simulated RP2040 peripherals attached."""
    from .memory import FlatRAM, Memory
    from .gpio import GPIO
    cpu = CPU(Memory(FlatRAM(memory)), os_entry, asm_map, sym_map, trace=trace)
    gpio = GPIO()
    cpu.add_peripheral(gpio)
    cpu.gpio = gpio
    return cpu
