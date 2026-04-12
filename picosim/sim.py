#!/usr/bin/env python3
"""
ARMv6-M (Cortex-M0+) Simulator
Flat 16-bit address space, similar in spirit to the LC-3 simulator.
Loads ELF files compiled with arm-none-eabi-gcc -mthumb -mcpu=cortex-m0plus.
"""

import os
import sys
import struct
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
import capstone

from .cpu import CPU, SimulatorError, MEM_SIZE


# ── ELF loader ─────────────────────────────────────────────────────────────────

def load_elf(path):
    """
    Parse an ELF file and return:
      - memory bytes (bytearray, MEM_SIZE long)
      - entry point address
      - dict mapping address -> disassembly string
      - dict mapping address -> symbol name
    """
    memory = bytearray(MEM_SIZE)
    asm_map = {}      # addr -> "mnemonic  op_str"
    sym_map = {}      # addr -> name

    with open(path, 'rb') as f:
        elf = ELFFile(f)

        if elf.header.e_machine != 'EM_ARM':
            raise SimulatorError(f"Not an ARM ELF file: {path}")

        entry = elf.header.e_entry & 0xFFFFFFFE   # clear Thumb bit

        # ── collect symbols ──────────────────────────────────────────────────
        for section in elf.iter_sections():
            if isinstance(section, SymbolTableSection):
                for sym in section.iter_symbols():
                    if sym.name and sym['st_value']:
                        addr = sym['st_value'] & 0xFFFFFFFE
                        sym_map[addr] = sym.name

        # ── load LOAD segments into flat memory ──────────────────────────────
        for seg in elf.iter_segments():
            if seg.header.p_type != 'PT_LOAD':
                continue
            vaddr  = seg.header.p_vaddr
            filesz = seg.header.p_filesz
            memsz  = seg.header.p_memsz
            data   = seg.data()

            if vaddr + memsz > MEM_SIZE:
                raise SimulatorError(
                    f"Segment at 0x{vaddr:04X} size {memsz} overflows "
                    f"16-bit address space (max 0x{MEM_SIZE:X})"
                )
            memory[vaddr:vaddr + filesz] = data
            # bss (memsz > filesz) is already zero from bytearray

        # ── disassemble all executable sections ──────────────────────────────
        cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
        cs.detail = False

        for section in elf.iter_sections():
            if not (section['sh_flags'] & 0x4):   # SHF_EXECINSTR
                continue
            sh_addr = section['sh_addr']
            sh_size = section['sh_size']
            if sh_size == 0:
                continue
            code = bytes(memory[sh_addr:sh_addr + sh_size])
            for insn in cs.disasm(code, sh_addr):
                asm_map[insn.address] = f"{insn.mnemonic:<8} {insn.op_str}"

    return memory, entry, asm_map, sym_map


# ── Display ────────────────────────────────────────────────────────────────────

REG_NAMES = ['r0','r1','r2','r3','r4','r5','r6','r7',
             'r8','r9','r10','r11','r12','sp','lr','pc']

def print_state(cpu, asm_map, sym_map):
    # registers
    for i in range(0, 16, 4):
        parts = []
        for j in range(i, min(i+4, 16)):
            parts.append(f"{REG_NAMES[j]:>3}: {cpu.regs[j]:08X}")
        print("  " + "   ".join(parts))
    # flags
    print(f"  NZCV: {cpu.N}{cpu.Z}{cpu.C}{cpu.V}")
    # current instruction
    pc = cpu.pc
    sym = sym_map.get(pc, "")
    sym_str = f" <{sym}>" if sym else ""
    asm = asm_map.get(pc, "???")
    print(f"  PC 0x{pc:04X}{sym_str}: {asm}")


# ── Interactive shell ──────────────────────────────────────────────────────────

def run_interactive(cpu, asm_map, sym_map):
    print("ARMv6-M Simulator  (type 'h' for help)")
    print_state(cpu, asm_map, sym_map)

    last_cmd = ""
    while True:
        try:
            prompt = "\nsim(halted)> " if cpu.halted else "\nsim> "
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        cmd = raw if raw else last_cmd
        last_cmd = cmd
        parts = cmd.split()
        if not parts:
            continue
        op = parts[0].lower()

        if op in ('q', 'quit', 'exit'):
            break

        elif op in ('h', 'help', '?'):
            print(
                "  s / step [n]    step n instructions (default 1)\n"
                "  r / run         run until halt or breakpoint\n"
                "  p / print       print registers\n"
                "  x <addr> [n]    examine memory: n words at hex addr\n"
                "  b <addr>        set breakpoint at hex addr\n"
                "  db <addr>       delete breakpoint\n"
                "  lb              list breakpoints\n"
                "  d [addr] [n]    disassemble n instructions at addr\n"
                "  reg <rN> <val>  set register (e.g.  reg r0 42)\n"
                "  q / quit        quit\n"
                "  <enter>         repeat last command"
            )

        elif op in ('s', 'step'):
            n = int(parts[1]) if len(parts) > 1 else 1
            for _ in range(n):
                if cpu.halted: break
                try:
                    cpu.step()
                except SimulatorError as e:
                    print(f"  ERROR: {e}")
                    cpu.halted = True
                    break
                except SystemExit as e:
                    print(f"  Program exited with code {e.code}")
                    cpu.halted = True
                    break
                cpu.check_halt()
                if cpu.halted: print("  Program halted.")
            print_state(cpu, asm_map, sym_map)

        elif op in ('r', 'run'):
            if cpu.halted:
                print("  Program is halted.  Use 'q' to quit.")
            else:
                breakpoints = getattr(run_interactive, '_bp', set())
                try:
                    while not cpu.halted:
                        cpu.step()
                        cpu.check_halt()
                        if cpu.pc in breakpoints:
                            print(f"  Breakpoint hit at 0x{cpu.pc:04X}")
                            break
                except SimulatorError as e:
                    print(f"  ERROR: {e}")
                    cpu.halted = True
                except SystemExit as e:
                    print(f"  Program exited with code {e.code}")
                    cpu.halted = True
                print_state(cpu, asm_map, sym_map)

        elif op in ('p', 'print'):
            print_state(cpu, asm_map, sym_map)

        elif op in ('x', 'mem'):
            if len(parts) < 2:
                print("  Usage: x <addr_hex> [count]"); continue
            base = int(parts[1], 16)
            count = int(parts[2]) if len(parts) > 2 else 8
            for i in range(count):
                a = (base + i * 4) & 0xFFFF
                v = cpu.mem_read32(a)
                sym = sym_map.get(a, "")
                sym_str = f" <{sym}>" if sym else ""
                print(f"  0x{a:04X}{sym_str}: 0x{v:08X}  ({v})")

        elif op == 'b':
            if len(parts) < 2:
                print("  Usage: b <addr_hex>"); continue
            if not hasattr(run_interactive, '_bp'):
                run_interactive._bp = set()
            addr_b = int(parts[1], 16)
            run_interactive._bp.add(addr_b)
            print(f"  Breakpoint set at 0x{addr_b:04X}")

        elif op == 'db':
            bp = getattr(run_interactive, '_bp', set())
            if len(parts) < 2:
                print("  Usage: db <addr_hex>"); continue
            addr_b = int(parts[1], 16)
            bp.discard(addr_b)
            print(f"  Breakpoint removed at 0x{addr_b:04X}")

        elif op == 'lb':
            bp = getattr(run_interactive, '_bp', set())
            if not bp: print("  No breakpoints.")
            for a in sorted(bp): print(f"  0x{a:04X}")

        elif op in ('d', 'dis', 'disasm'):
            base = int(parts[1], 16) if len(parts) > 1 else cpu.pc
            count = int(parts[2]) if len(parts) > 2 else 10
            shown = 0
            a = base
            while shown < count and a < MEM_SIZE:
                sym = sym_map.get(a, "")
                if sym: print(f"  <{sym}>:")
                asm = asm_map.get(a)
                if asm:
                    marker = "=>" if a == cpu.pc else "  "
                    print(f"  {marker} 0x{a:04X}: {asm}")
                    shown += 1
                    # advance: peek at instruction size
                    hw = cpu.mem_read16(a)
                    a += 4 if cpu.is_32bit_thumb(hw) else 2
                else:
                    a += 2

        elif op == 'reg':
            if len(parts) < 3:
                print("  Usage: reg <rN> <value>"); continue
            rname = parts[1].lower()
            val   = int(parts[2], 0)
            if rname in REG_NAMES:
                cpu.set_reg(REG_NAMES.index(rname), val)
                print(f"  {rname} = 0x{val:08X}")
            else:
                print(f"  Unknown register: {rname}")

        else:
            print(f"  Unknown command '{op}'.  Type 'h' for help.")


# ── Assembler / linker helpers ─────────────────────────────────────────────────

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def compile_asm(s_file, elf_file, ld_file, extra_ld_flags=None):
    """Assemble and link a .s file.  Returns True on success, False on error."""
    import subprocess, shutil
    if shutil.which('arm-none-eabi-gcc') is None:
        print(
            "Error: arm-none-eabi-gcc not found on PATH.\n"
            "See DEPENDENCIES.md for installation instructions.",
            file=sys.stderr,
        )
        return False
    cmd = [
        'arm-none-eabi-gcc',
        '-mcpu=cortex-m0plus', '-mthumb',
        '-nostdlib',
        '-T', ld_file,
        '-o', elf_file,
        s_file,
    ]
    if extra_ld_flags:
        cmd.extend(extra_ld_flags)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("Assembler/linker error:", file=sys.stderr)
        combined = (result.stderr + result.stdout).strip()
        if combined:
            print(combined, file=sys.stderr)
        return False
    return True


def ensure_os_elf():
    """Compile os.s → os.elf if missing or stale.  Returns path to os.elf."""
    sd     = _script_dir()
    os_s   = os.path.join(sd, 'os.s')
    os_ld  = os.path.join(sd, 'os.ld')
    os_elf = os.path.join(sd, 'os.elf')

    if not os.path.exists(os_s):
        print(f"Error: OS source '{os_s}' not found.", file=sys.stderr)
        sys.exit(1)

    need_build = (
        not os.path.exists(os_elf) or
        os.path.getmtime(os_s) > os.path.getmtime(os_elf) or
        (os.path.exists(os_ld) and os.path.getmtime(os_ld) > os.path.getmtime(os_elf))
    )
    if need_build:
        print("Compiling OS...")
        if not compile_asm(os_s, os_elf, os_ld):
            sys.exit(1)
    return os_elf


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="ARMv6-M (Cortex-M0+) Simulator — flat 16-bit address space"
    )
    parser.add_argument("input", help=".s assembly file or .elf binary to load")
    parser.add_argument("--run", "-r", action="store_true",
                        help="run to completion without interactive prompt")
    parser.add_argument("--steps", "-n", type=int, default=0,
                        help="execute exactly N steps then stop")
    parser.add_argument("--trace", "-t", action="store_true",
                        help="print instruction trace during execution")
    parser.add_argument("--time", action="store_true",
                        help="run to completion and print execution time and instructions/second")
    parser.add_argument("--uf2", action="store_true",
                        help="build a .uf2 image for the Raspberry Pi Pico and exit")
    args = parser.parse_args()

    # ── UF2 build (early exit — does not start the simulator) ────────────────
    if args.uf2:
        if not args.input.endswith('.s'):
            print("Error: --uf2 requires an assembly (.s) input file.", file=sys.stderr)
            sys.exit(1)
        from .uf2 import build_uf2
        sys.exit(0 if build_uf2(args.input) else 1)

    # ── Compile OS first (needed to resolve putchar/getchar in user code) ───────
    os_elf_path = ensure_os_elf()

    # ── Assemble user .s file if needed ──────────────────────────────────────
    input_file = args.input
    if input_file.endswith('.s'):
        base    = input_file[:-2]
        elf_out = base + '.elf'
        ld_file = os.path.join(_script_dir(), 'link.ld')
        # --just-symbols lets the linker resolve putchar/getchar from the OS
        just_syms = f'-Wl,--just-symbols={os_elf_path}'
        print(f"Assembling '{input_file}'...")
        if not compile_asm(input_file, elf_out, ld_file,
                           extra_ld_flags=[just_syms]):
            sys.exit(1)
        input_file = elf_out

    # ── Load OS ELF ───────────────────────────────────────────────────────────
    try:
        os_memory, os_entry, os_asm, os_syms = load_elf(os_elf_path)
    except (SimulatorError, FileNotFoundError) as e:
        print(f"Error loading OS ELF: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Load user ELF ─────────────────────────────────────────────────────────
    try:
        user_memory, user_entry, user_asm, user_syms = load_elf(input_file)
    except (SimulatorError, FileNotFoundError) as e:
        print(f"Error loading '{input_file}': {e}", file=sys.stderr)
        sys.exit(1)

    # ── Merge memories: OS owns 0x0000-0x2FFF, user owns 0x3000-0xFFFF ───────
    memory = bytearray(MEM_SIZE)
    memory[0x0000:0x3000] = os_memory[0x0000:0x3000]
    memory[0x3000:      ] = user_memory[0x3000:      ]

    # Write user main()'s Thumb address into the OS pointer slot at 0x2FFC
    main_thumb_addr = user_entry | 1   # ensure Thumb bit is set for BLX
    struct.pack_into('<I', memory, 0x2FFC, main_thumb_addr)

    asm_map = {**os_asm,  **user_asm}
    sym_map = {**os_syms, **user_syms}

    print(f"Loaded '{input_file}'  main=0x{user_entry:04X}  "
          f"{len(asm_map)} instructions disassembled")

    cpu = CPU(memory, os_entry, asm_map, sym_map, trace=args.trace)

    if args.time:
        import time
        insn_count = 0
        t_start = time.perf_counter()
        try:
            while not cpu.halted:
                cpu.step()
                cpu.check_halt()
                insn_count += 1
        except SimulatorError as e:
            print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
        except SystemExit as e:
            sys.exit(e.code)
        elapsed = time.perf_counter() - t_start
        ips = insn_count / elapsed if elapsed > 0 else float('inf')
        print(f"Executed {insn_count:,} instructions in {elapsed:.4f}s  ({ips:,.0f} insn/s)")
    elif args.run:
        try:
            while not cpu.halted:
                cpu.step()
                cpu.check_halt()
        except SimulatorError as e:
            print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
        except SystemExit as e:
            sys.exit(e.code)
    elif args.steps:
        for _ in range(args.steps):
            if cpu.halted: break
            try:
                cpu.step()
            except SimulatorError as e:
                print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
            except SystemExit as e:
                sys.exit(e.code)
            cpu.check_halt()
        print_state(cpu, asm_map, sym_map)
    else:
        run_interactive(cpu, asm_map, sym_map)


if __name__ == "__main__":
    main()
