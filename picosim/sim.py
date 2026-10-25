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
    asm_map = {}      # addr -> "mnemonic  op_str" (or ".word 0x…" for data)
    sym_map = {}      # addr -> name
    marks = {}        # section index -> [(addr, 't' code | 'd' data), ...]

    with open(path, 'rb') as f:
        elf = ELFFile(f)

        if elf.header.e_machine != 'EM_ARM':
            raise SimulatorError(f"Not an ARM ELF file: {path}")

        entry = elf.header.e_entry & 0xFFFFFFFE   # clear Thumb bit

        # ── collect symbols ──────────────────────────────────────────────────
        for section in elf.iter_sections():
            if isinstance(section, SymbolTableSection):
                for sym in section.iter_symbols():
                    # Skip absolute symbols (.equ constants): they have small
                    # numeric values that collide with code addresses.
                    if sym['st_shndx'] == 'SHN_ABS':
                        continue
                    # ARM mapping symbols ($t code, $d data, $a ARM code) say
                    # where code and data are; GAS emits them at the same
                    # addresses as user labels, so they are not label names.
                    if sym.name.startswith('$'):
                        kind = sym.name[1:2]
                        if kind in ('t', 'd') and isinstance(sym['st_shndx'], int):
                            marks.setdefault(sym['st_shndx'], []).append((sym['st_value'], kind))
                        continue
                    if sym.name and sym['st_value']:
                        addr = sym['st_value']
                        if sym['st_info']['type'] == 'STT_FUNC':
                            addr &= 0xFFFFFFFE      # a Thumb function's address has bit 0 set
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
        cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        cs.detail = False

        for idx, section in enumerate(elf.iter_sections()):
            if not (section['sh_flags'] & 0x4):   # SHF_EXECINSTR
                continue
            sh_addr = section['sh_addr']
            sh_size = section['sh_size']
            if sh_size == 0 or sh_addr + sh_size > MEM_SIZE:
                continue
            # code and data regions, from the mapping symbols (all code if none)
            m = sorted(set(marks.get(idx, [])))
            if not m or m[0][0] > sh_addr:
                m.insert(0, (sh_addr, 't'))
            for i, (start, kind) in enumerate(m):
                end = m[i + 1][0] if i + 1 < len(m) else sh_addr + sh_size
                if kind == 't':
                    _disassemble(cs, memory, start, end, asm_map)
                else:
                    _data_rows(memory, start, end, asm_map, sym_map)

    return memory, entry, asm_map, sym_map


def _disassemble(cs, memory, start, end, asm_map):
    """Thumb code in [start, end); a halfword capstone cannot decode becomes
    a .hword row and disassembly carries on after it."""
    a = start & ~1
    while a < end:
        n = 0
        for insn in cs.disasm(bytes(memory[a:end]), a):
            asm_map[insn.address] = f"{insn.mnemonic:<8} {insn.op_str}"
            n = insn.address + insn.size - a
        a += n
        if a + 2 <= end:
            asm_map[a] = f".hword   0x{memory[a] | memory[a + 1] << 8:04X}"
            a += 2
        else:
            break


def _data_rows(memory, start, end, asm_map, labels):
    """Data in a code section (literal pools, strings), split at labels and
    after each string's terminating 0: text becomes .ascii rows of up to 16
    characters, anything else one row per aligned word, halfword or byte."""
    cuts = sorted({start, end} | {a for a in labels if start < a < end})
    for lo, hi in zip(cuts, cuts[1:]):
        a = lo
        while a < hi:
            nul = memory.find(0, a, hi)
            piece_end = hi if nul < 0 else nul + 1
            if _is_text(memory[a:piece_end], 2 if a in labels else 3):
                for r in range(a, piece_end, 16):
                    asm_map[r] = '.ascii   "' + _escape(memory[r:min(r + 16, piece_end)]) + '"'
                a = piece_end
                continue
            n = 4 if a % 4 == 0 and a + 4 <= hi else 2 if a % 2 == 0 and a + 2 <= hi else 1
            v = int.from_bytes(memory[a:a + n], 'little')
            asm_map[a] = {4: f".word    0x{v:08X}", 2: f".hword   0x{v:04X}", 1: f".byte    0x{v:02X}"}[n]
            a += n


def _is_text(b, least):
    """Printable characters (and \\n \\r \\t), at least `least`, ending with at most one 0."""
    body = bytes(b[:-1]) if b and b[-1] == 0 else bytes(b)
    return len(body) >= least and all(32 <= c < 127 or c in (9, 10, 13) for c in body)


def _escape(b):
    esc = {10: '\\n', 13: '\\r', 9: '\\t', 0: '\\0', 34: '\\"', 92: '\\\\'}
    return ''.join(esc.get(c, chr(c)) for c in b)


def source_lines(path, source_name):
    """{address: line number} for the instructions assembled from the file
    named `source_name` (its base name), from the ELF's DWARF line table.
    Empty if the ELF has no line information."""
    out = {}
    want = os.path.basename(source_name).encode()
    with open(path, 'rb') as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return out
        dwarf = elf.get_dwarf_info()
        for cu in dwarf.iter_CUs():
            lp = dwarf.line_program_for_CU(cu)
            if lp is None:
                continue
            files = lp['file_entry']
            ver = lp['version']
            for entry in lp.get_entries():
                st = entry.state
                if st is None or st.end_sequence:
                    continue
                # DWARF 5 numbers files from 0, earlier versions from 1
                i = st.file if ver >= 5 else st.file - 1
                if 0 <= i < len(files) and os.path.basename(files[i].name) == want:
                    out.setdefault(st.address, st.line)
    return out


def elf_symbol(path, names):
    """Address of the first of `names` defined in the ELF at `path`, or None."""
    with open(path, 'rb') as f:
        elf = ELFFile(f)
        found = {}
        for section in elf.iter_sections():
            if isinstance(section, SymbolTableSection):
                for sym in section.iter_symbols():
                    if sym.name in names and sym['st_shndx'] != 'SHN_UNDEF':
                        found[sym.name] = sym['st_value'] & 0xFFFFFFFE
    for n in names:
        if n in found:
            return found[n]
    return None


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
    label = sym_map.get(pc, "")
    asm = asm_map.get(pc, "???")
    print(f"  PC 0x{pc:04X}: {asm}")
    print(f"  PC Label: {label if label else 'N/A'}")
    # GPIO (only shown when pins have been configured)
    if cpu.gpio is not None and cpu.gpio.any_configured():
        print(cpu.gpio.display())


# ── Interactive shell ──────────────────────────────────────────────────────────

_BREAKPOINTS = set()


def run_interactive(cpu, asm_map, sym_map, label_map):
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
        try:
            _command(cpu, asm_map, sym_map, label_map, op, parts)
        except ValueError as e:
            print(f"  Bad argument ({e}).  Type 'h' for help.")
        except (SimulatorError, RuntimeError) as e:
            print(f"  ERROR: {e}")


def _parse_addr(text, label_map):
    """A hex address (0x3000 or 3000) or a label name."""
    try:
        return int(text, 16)
    except ValueError:
        if text in label_map:
            return label_map[text]
        raise ValueError(f"'{text}' is not a hex address or a label")


def _command(cpu, asm_map, sym_map, label_map, op, parts):
    if op in ('h', 'help', '?'):
        print(
            "  s / step [n]      step n instructions (default 1)\n"
            "  r / run           run until halt or breakpoint\n"
            "  p / print         print registers\n"
            "  x <addr> [n]      examine memory: n words at hex addr (or label)\n"
            "  b <addr|label>    set breakpoint at hex addr or label\n"
            "  db <addr|label>   delete breakpoint\n"
            "  lb                list breakpoints\n"
            "  d [addr] [n]      disassemble n instructions at addr\n"
            "  reg <rN> <val>    set register (e.g.  reg r0 42)\n"
            "  gpio <pin> 0|1|z  drive an input pin high/low/floating\n"
            "  q / quit          quit\n"
            "  <enter>           repeat last command"
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
            breakpoints = _BREAKPOINTS
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
            print("  Usage: x <addr_hex|label> [count]"); return
        base = _parse_addr(parts[1], label_map)
        count = int(parts[2]) if len(parts) > 2 else 8
        for i in range(count):
            a = (base + i * 4) & 0xFFFFFFFF
            v = cpu.mem_read32(a)    # RAM, or a simulated I/O register
            sym = sym_map.get(a, "")
            sym_str = f" <{sym}>" if sym else ""
            print(f"  0x{a:04X}{sym_str}: 0x{v:08X}  ({v})")

    elif op == 'b':
        if len(parts) < 2:
            print("  Usage: b <addr_hex|label>"); return
        addr_b = _parse_addr(parts[1], label_map)
        _BREAKPOINTS.add(addr_b)
        lbl = sym_map.get(addr_b, "")
        lbl_str = f" <{lbl}>" if lbl else ""
        print(f"  Breakpoint set at 0x{addr_b:04X}{lbl_str}")

    elif op == 'db':
        bp = _BREAKPOINTS
        if len(parts) < 2:
            print("  Usage: db <addr_hex>"); return
        addr_b = _parse_addr(parts[1], label_map)
        bp.discard(addr_b)
        print(f"  Breakpoint removed at 0x{addr_b:04X}")

    elif op == 'lb':
        bp = _BREAKPOINTS
        if not bp: print("  No breakpoints.")
        for a in sorted(bp): print(f"  0x{a:04X}")

    elif op in ('d', 'dis', 'disasm'):
        base = _parse_addr(parts[1], label_map) if len(parts) > 1 else cpu.pc
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
            print("  Usage: reg <rN> <value>"); return
        rname = parts[1].lower()
        val   = int(parts[2], 0)
        if rname in REG_NAMES:
            cpu.set_reg(REG_NAMES.index(rname), val)
            print(f"  {rname} = 0x{val:08X}")
        else:
            print(f"  Unknown register: {rname}")

    elif op == 'gpio':
        if cpu.gpio is None:
            print("  GPIO not enabled."); return
        if len(parts) < 3:
            print("  Usage: gpio <pin> 0|1|z"); return
        try:
            pin = int(parts[1])
            arg = parts[2].lower()
            if arg == 'z':
                value = None
            elif arg in ('0', '1'):
                value = int(arg)
            else:
                print("  Value must be 0, 1, or z"); return
            cpu.gpio.set_external(pin, value)
            state = 'Z' if value is None else str(value)
            print(f"  GP{pin} = {state}")
        except ValueError as e:
            print(f"  {e}")

    else:
        print(f"  Unknown command '{op}'.  Type 'h' for help.")


# ── Assembler / linker helpers ─────────────────────────────────────────────────

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def assemble(s_file, elf_file, ld_file, extra_s_files=None, extra_ld_flags=None):
    """Assemble and link a .s file.  Returns (ok, message).  Line-number debug
    info (-g) is always included, so the GUI can show each instruction's
    source line (see source_lines)."""
    import subprocess, shutil
    if shutil.which('arm-none-eabi-gcc') is None:
        return False, ("arm-none-eabi-gcc not found on PATH.\n"
                       "See DEPENDENCIES.md for installation instructions.")
    cmd = [
        'arm-none-eabi-gcc',
        '-mcpu=cortex-m0plus', '-mthumb',
        '-nostdlib',
        '-g',                   # DWARF line table: address -> source line
        '-Wa,--keep-locals',    # keep .L* labels in the assembler object
        '-Wl,--discard-none',   # prevent the linker from stripping them
        '-T', ld_file,
        '-o', elf_file,
        s_file,
    ]
    if extra_s_files:
        cmd.extend(extra_s_files)
    if extra_ld_flags:
        cmd.extend(extra_ld_flags)
    result = subprocess.run(cmd, capture_output=True, text=True)
    combined = (result.stderr + result.stdout).strip()
    return result.returncode == 0, combined


def compile_asm(s_file, elf_file, ld_file, extra_s_files=None, extra_ld_flags=None):
    """Assemble and link a .s file.  Returns True on success, False on error."""
    ok, msg = assemble(s_file, elf_file, ld_file, extra_s_files, extra_ld_flags)
    if not ok:
        print("Assembler/linker error:" if 'not found' not in msg else "Error:",
              file=sys.stderr)
        if msg:
            print(msg, file=sys.stderr)
    return ok


def ensure_os_elf():
    """Compile os.s → os.elf if missing or stale.  Returns path to os.elf."""
    from .loader import build_os_elf, LoadError
    try:
        return build_os_elf(verbose=True)
    except LoadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


# ── GUI launcher ───────────────────────────────────────────────────────────────

def _find_godot():
    import shutil
    cands = [os.environ.get('PICOSIM_GODOT'), shutil.which('godot'),
             '/Applications/Godot.app/Contents/MacOS/Godot']
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def run_gui(input_file):
    """Start the backend server and the Godot front end; wait for the GUI to exit."""
    import subprocess, threading
    from .server import Server
    gui_dir = os.path.join(os.path.dirname(_script_dir()), 'gui')
    if not os.path.exists(os.path.join(gui_dir, 'project.godot')):
        print(f"Error: Godot project not found at {gui_dir}", file=sys.stderr)
        return 1
    godot = _find_godot()
    if godot is None:
        print("Error: Godot 4 not found.  Put 'godot' on PATH or set PICOSIM_GODOT.",
              file=sys.stderr)
        return 1
    srv = Server(0)
    srv.path = os.path.abspath(input_file) if input_file else None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proc = subprocess.run([godot, '--path', gui_dir, '--', '--port', str(srv.port)])
    return proc.returncode


def chat_setup():
    """picosim --chat-setup: the teacher's view of Ask picosim's settings."""
    from .chat import config
    from .chat.claude import Assistant
    path = config.write_template()
    cfg = config.load()
    status, why = Assistant(cfg).status()
    print(f"Ask picosim config: {path}")
    for k in ('ai', 'model', 'max_ai_per_hour', 'helpline', 'log_unanswered'):
        print(f"  {k:16} {cfg[k]!r}")
    print(f"AI help: {status}" + (f" ({why})" if why else ''))
    print("The helper gives hints only; there is no setting for full answers.")
    if cfg['ai'] != 'on':
        print('To turn AI help on: set "ai": "on" in the file above, run pip install -e .[ai] '
              '(Python 3.10+), and set ANTHROPIC_API_KEY. Questions and the student\'s program are '
              'then sent to Anthropic: check your school\'s rules first.')
    print(f"Reports and feedback counts are saved next to it: {config.data_file('chat_reports.jsonl')}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="ARMv6-M (Cortex-M0+) Simulator — flat 16-bit address space"
    )
    parser.add_argument("input", nargs="?", help=".s assembly file or .elf binary to load")
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
    parser.add_argument("--flash", action="store_true",
                        help="build and upload a .uf2 image to a Raspberry Pi Pico")
    parser.add_argument("--gui", action="store_true",
                        help="open the graphical simulator (requires Godot 4)")
    parser.add_argument("--cycle-table", action="store_true",
                        help="print the per-instruction cycle table (Markdown) and exit")
    parser.add_argument("--chat-setup", action="store_true",
                        help="Ask picosim (the help chat): show where its teacher config is, create it, "
                             "and say whether AI help can run")
    parser.add_argument("--teacher-config", metavar="PATH",
                        help="use this Ask picosim config file instead of the one in the user data folder")
    args = parser.parse_args()

    if args.teacher_config:
        os.environ['PICOSIM_TEACHER'] = os.path.abspath(os.path.expanduser(args.teacher_config))
    if args.chat_setup:
        chat_setup()
        return

    if args.cycle_table:
        from .cycles import print_table
        print_table()
        return
    if args.gui:
        sys.exit(run_gui(args.input))
    if not args.input:
        parser.error("an input file is required (or use --gui)")

    # ── UF2 build/flash (early exit — does not start the simulator) ──────────
    if args.uf2 or args.flash:
        if not args.input.endswith('.s'):
            print("Error: --uf2/--flash requires an assembly (.s) input file.",
                  file=sys.stderr)
            sys.exit(1)
        from .uf2 import build_uf2, flash_uf2
        if args.flash:
            sys.exit(0 if flash_uf2(args.input) else 1)
        sys.exit(0 if build_uf2(args.input) else 1)

    from .loader import load_program, LoadError
    try:
        prog = load_program(args.input, trace=args.trace, verbose=True)
    except LoadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    cpu, asm_map, sym_map, label_map = prog.cpu, prog.asm_map, prog.sym_map, prog.label_map

    if args.time:
        import time
        t_start = time.perf_counter()
        try:
            insn_count = cpu._core.run()
        except (RuntimeError, SimulatorError) as e:
            print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
        except SystemExit as e:
            sys.exit(e.code)
        elapsed = time.perf_counter() - t_start
        ips = insn_count / elapsed if elapsed > 0 else float('inf')
        print(f"Executed {insn_count:,} instructions in {elapsed:.4f}s  ({ips:,.0f} insn/s)")
    elif args.run:
        try:
            cpu._core.run()
        except (RuntimeError, SimulatorError) as e:
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
        run_interactive(cpu, asm_map, sym_map, label_map)


if __name__ == "__main__":
    main()
