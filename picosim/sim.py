#!/usr/bin/env python3
"""
ARMv6-M (Cortex-M0+) Simulator
The RP2040's memory map (memmap.py): picosim's OS in the first 256 bytes of
flash at 0x10000000, the program after it at 0x10000100, its variables and
stack in SRAM at 0x20000000.
Loads ELF files compiled with arm-none-eabi-gcc -mthumb -mcpu=cortex-m0plus.
"""

import contextlib
import os
import signal
import sys
import struct
import time
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
import capstone

from .cpu import CPU, SimulatorError, stop_reason, STOP_LABELS
from .memmap import Image, span, fmt, OS_BASE, OS_END


# ── ELF loader ─────────────────────────────────────────────────────────────────

def load_elf(path):
    """
    Parse an ELF file and return:
      - memory (a memmap.Image: ROM, flash and SRAM)
      - entry point address
      - dict mapping address -> disassembly string
      - dict mapping address -> symbol name
    """
    memory = Image()
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

            if memsz and span(vaddr, memsz) is None:
                raise SimulatorError(
                    f"The program has {memsz} bytes at {fmt(vaddr)}, which is not inside one of "
                    f"ROM, flash (0x10000000, 2 MB) or SRAM (0x20000000, 264 KB)")
            if filesz:
                memory[vaddr:vaddr + filesz] = data
            # bss (memsz > filesz) is already zero in SRAM

        # ── disassemble all executable sections ──────────────────────────────
        cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        cs.detail = False

        for idx, section in enumerate(elf.iter_sections()):
            if not (section['sh_flags'] & 0x4):   # SHF_EXECINSTR
                continue
            sh_addr = section['sh_addr']
            sh_size = section['sh_size']
            if sh_size == 0 or span(sh_addr, sh_size) is None:
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


def elf_names(path):
    """{name: address} of every label the ELF at `path` defines in memory,
    code or data (not .equ constants or the $t/$d mapping symbols)."""
    out = {}
    with open(path, 'rb') as f:
        for section in ELFFile(f).iter_sections():
            if not isinstance(section, SymbolTableSection):
                continue
            for sym in section.iter_symbols():
                shndx = sym['st_shndx']
                if not sym.name or sym.name.startswith('$') or shndx in ('SHN_ABS', 'SHN_UNDEF'):
                    continue
                addr = sym['st_value']
                if sym['st_info']['type'] == 'STT_FUNC':
                    addr &= 0xFFFFFFFE
                if span(addr, 1) is not None:
                    out.setdefault(sym.name, addr)
    return out


# ── Display ────────────────────────────────────────────────────────────────────

REG_NAMES = ['r0','r1','r2','r3','r4','r5','r6','r7',
             'r8','r9','r10','r11','r12','sp','lr','pc']


def _char(v):
    """' = 'H'' when v is a printable ASCII code, else ''."""
    return f" = '{chr(v)}'" if 32 <= v < 127 else ""


def print_state(cpu, asm_map, sym_map, source=None, file_name=''):
    # registers
    for i in range(0, 16, 4):
        parts = []
        for j in range(i, min(i+4, 16)):
            parts.append(f"{REG_NAMES[j]:>3}: {cpu.regs[j]:08X}")
        print("  " + "   ".join(parts))
    # flags
    print(f"  NZCV: {cpu.N}{cpu.Z}{cpu.C}{cpu.V}")
    # the instruction that runs next
    pc = cpu.pc
    label = sym_map.get(pc, "")
    asm = asm_map.get(pc, "???")
    print(f"  PC {fmt(pc)}: {asm}")
    print(f"  PC Label: {label if label else 'N/A'}")
    if source and pc in source:
        n, text = source[pc]
        where = 'os.s (picosim\'s start-up code and OS)' if OS_BASE <= pc < OS_END else file_name
        print(f"  Next line: {where} line {n}: {text}")
    # GPIO (only shown when pins have been configured)
    if cpu.gpio is not None and cpu.gpio.any_configured():
        print(cpu.gpio.display())


# ── Interactive shell ──────────────────────────────────────────────────────────

HELP = """\
  s / step [n]      step n instructions (default 1)
  r / run           run until halt, breakpoint or Ctrl+C
  p / print [rN]    print registers (or one register in hex, decimal and as a character)
  x <addr> [n]      examine memory: n words at a hex address or any label (code or data)
  b <addr|label>    set a breakpoint on an instruction
  db <addr|label>   delete breakpoint
  lb                list breakpoints
  d [addr] [n]      disassemble n instructions at addr
  reg <rN> <val>    set register (e.g.  reg r0 42)
  gpio <pin> 0|1|z  drive an input pin high/low/floating
  check             check the lesson's challenge (@ Expect lines) once the program has finished
  reset             start the same build over from the beginning (breakpoints kept)
  reload            assemble the file again (after you edit it) and start over
  q / quit          quit
  <enter>           repeat last command
  Ctrl+C            while running: stop at the next instruction and come back here"""


class CommandError(ValueError):
    """A command the student can fix; the message says how."""


class Session:
    """One interactive prompt: the loaded program, its breakpoints, and how
    to start over (reset: the same build; reload: build the file again)."""

    def __init__(self, prog, trace=False):
        self.prog = prog
        self.trace = trace
        self.breakpoints = {}        # address -> what was typed for it ('loop', '10000104')
        self.loaded_at = time.strftime('%H:%M:%S')
        self._interrupted = False

    @property
    def cpu(self):
        return self.prog.cpu

    def show(self):
        p = self.prog
        print_state(p.cpu, p.asm_map, p.sym_map, p.source, os.path.basename(p.path))

    # ── names and addresses ───────────────────────────────────────────────────

    def _own_labels(self):
        return sorted((a, n) for n, a in self.prog.label_map.items() if a >= OS_END)

    def data_addr(self, text):
        """A hex address or any label, code or data (for x)."""
        try:
            return int(text, 16)
        except ValueError:
            pass
        if text in self.prog.names:
            return self.prog.names[text]
        raise CommandError(f"'{text}' is not a hex address or a label in this program")

    def code_addr(self, text):
        """A hex address or label of an instruction (for b, db and d)."""
        p = self.prog
        try:
            a = int(text, 16)
        except ValueError:
            if text in p.label_map:
                return p.label_map[text]
            if text in p.names:
                a = p.names[text]
                raise CommandError(f"'{text}' is data at {fmt(a)}, not an instruction. Breakpoints and "
                                 f"d need code; to see the data type: x {text}")
            own = ', '.join(n for _, n in self._own_labels())
            raise CommandError(f"no label named '{text}'" + (f" (your program's code labels: {own})"
                                                           if own else ""))
        if a not in p.asm_map:
            raise CommandError(f"no instruction starts at {fmt(a)}")
        return a

    # ── running ───────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def _ctrl_c(self):
        """While the program runs, Ctrl+C only asks it to stop: the run loop
        stops after the instruction it is in, so the machine is never left
        half way through one."""
        self._interrupted = False
        def ask(sig, frame):
            self._interrupted = True
        try:
            old = signal.signal(signal.SIGINT, ask)
        except ValueError:          # not the main thread
            old = None
        try:
            yield
        finally:
            if old is not None:
                signal.signal(signal.SIGINT, old)

    def execute(self, count=None):
        """Run `count` instructions (None: until halt, a breakpoint or Ctrl+C)."""
        cpu = self.cpu
        n = 0
        with self._ctrl_c():
            try:
                while not cpu.halted and (count is None or n < count):
                    cpu.step()
                    cpu.check_halt()
                    n += 1
                    if cpu.halted:
                        print("  Program halted.")
                    elif self._interrupted:
                        print(f"  Stopped (Ctrl+C) at {fmt(cpu.pc)} after {n:,} instructions. "
                              f"You can step, look around, or type r to carry on.")
                    elif count is None and cpu.pc in self.breakpoints:
                        print(f"  Breakpoint hit at {fmt(cpu.pc)}")
                    else:
                        continue
                    break
            except (SimulatorError, RuntimeError) as e:
                print(f"  ERROR: {_error(e)}")
                cpu.halted = True
            except SystemExit as e:
                print(f"  Program exited with code {e.code}")
                cpu.halted = True
        self.show()

    def check(self):
        """The lesson's challenge (its @ Challenge and @ Expect lines), checked
        against the finished program, as the GUI does when a program halts."""
        from . import challenge
        path = self.prog.path
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                ch = challenge.parse(f.read()) if path.endswith('.s') else None
        except (OSError, ValueError) as e:
            print(f"  Cannot read the challenge: {e}")
            return
        if ch is None:
            print("  This program has no challenge (@ Challenge and @ Expect lines) to check.")
            return
        print(f"  Challenge: {ch['task']}")
        if not self.cpu.halted:
            print("  The program has not finished yet: type r to run it to the end, then check.")
            return
        cpu = self.cpu
        results = challenge.run(ch, cpu.regs, ''.join(cpu.printed), self.prog.names,
                                cpu.mem_read32)
        for r in results:
            print(f"  {'pass' if r['ok'] else 'FAIL'}  {r['text']}: {r['got']}")
        if all(r['ok'] for r in results):
            print("  Challenge passed.")
        elif ch.get('hint'):
            print(f"  Hint: {ch['hint']}")

    def reset(self):
        self.prog = self.prog.clone()
        self.cpu.trace = self.trace
        print(f"  Started over: the build loaded at {self.loaded_at}, from its first "
              f"instruction. Breakpoints are kept.")
        self.show()

    def reload(self):
        from .loader import load_program, LoadError
        print(f"  Building '{self.prog.path}' again...")
        try:
            new = load_program(self.prog.path, trace=self.trace)
        except LoadError as e:
            print(f"  {e}")
            print(f"  That edit did not build, so nothing changed: you are still on the build "
                  f"loaded at {self.loaded_at}, where you were.")
            return
        self.prog = new
        self.loaded_at = time.strftime('%H:%M:%S')
        old, self.breakpoints, lost = self.breakpoints, {}, []
        for spec in old.values():
            try:
                self.breakpoints[self.code_addr(spec)] = spec
            except ValueError:
                lost.append(spec)
        print(f"  Rebuilt at {self.loaded_at} and started over from the first instruction.")
        if self.breakpoints:
            print("  Breakpoints kept: " + ', '.join(f"{s} ({fmt(a)})" for a, s in
                                                      sorted(self.breakpoints.items())))
        if lost:
            print("  Removed (no longer an instruction in the new build): " + ', '.join(lost))
        self.show()

    # ── the prompt ────────────────────────────────────────────────────────────

    def loop(self):
        print("ARMv6-M Simulator  (type 'h' for help)")
        if OS_BASE <= self.cpu.pc < OS_END and self.prog.main_addr >= OS_END:
            print("  The next instruction is in _start: picosim's start-up code, which runs\n"
                  "  first (as on a Pico) and then calls your main. To go to your own\n"
                  "  program, type  b main  then  r.")
        self.show()
        last_cmd = ""
        while True:
            try:
                prompt = "\nsim(halted)> " if self.cpu.halted else "\nsim> "
                raw = input(prompt).strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print("\n  (Type q to quit.)")
                last_cmd = ""
                continue
            cmd = raw if raw else last_cmd
            last_cmd = cmd
            parts = cmd.split()
            if not parts:
                continue
            op = parts[0].lower()
            if op in ('q', 'quit', 'exit'):
                break
            try:
                self.command(op, parts)
            except CommandError as e:
                print(f"  {str(e)[0].upper()}{str(e)[1:]}.")
            except ValueError as e:
                print(f"  Bad argument ({e}).  Type 'h' for help.")
            except (SimulatorError, RuntimeError) as e:
                print(f"  ERROR: {e}")

    def command(self, op, parts):
        cpu = self.cpu
        if op in ('h', 'help', '?'):
            print(HELP)

        elif op in ('s', 'step'):
            if cpu.halted:
                print("  Program is halted.  Type reset to start over, reload after an edit, "
                      "or q to quit.")
                return
            self.execute(int(parts[1]) if len(parts) > 1 else 1)

        elif op in ('r', 'run'):
            if cpu.halted:
                print("  Program is halted.  Type reset to start over, reload after an edit, "
                      "or q to quit.")
                return
            self.execute()

        elif op in ('p', 'print'):
            if len(parts) > 1:
                r = parts[1].lower()
                if r not in REG_NAMES:
                    raise CommandError(f"no register named '{parts[1]}'")
                v = cpu.regs[REG_NAMES.index(r)]
                signed = v - (1 << 32) if v & 0x80000000 else v
                dec = f"{v}" + (f" (or {signed} signed)" if signed < 0 else "")
                print(f"  {r} = 0x{v:08X} = {dec}{_char(v)}")
            else:
                self.show()

        elif op in ('x', 'mem'):
            if len(parts) < 2:
                print("  Usage: x <addr_hex|label> [count]"); return
            base = self.data_addr(parts[1])
            count = int(parts[2]) if len(parts) > 2 else 8
            names = {}
            for n, a in self.prog.names.items():
                names.setdefault(a, []).append(n)
            for i in range(count):
                a = (base + i * 4) & 0xFFFFFFFF
                v = cpu.mem_read32(a)    # memory, or a simulated I/O register
                here = names.get(a) or ([self.prog.sym_map[a]] if a in self.prog.sym_map else [])
                sym_str = f" <{', '.join(sorted(here))}>" if here else ""
                print(f"  {fmt(a)}{sym_str}: 0x{v:08X}  ({v})")

        elif op == 'b':
            if len(parts) < 2:
                print("  Usage: b <addr_hex|label>"); return
            a = self.code_addr(parts[1])
            self.breakpoints[a] = parts[1]
            lbl = self.prog.sym_map.get(a, "")
            print(f"  Breakpoint set at {fmt(a)}" + (f" <{lbl}>" if lbl else ""))

        elif op == 'db':
            if len(parts) < 2:
                print("  Usage: db <addr_hex|label>"); return
            a = self.code_addr(parts[1])
            self.breakpoints.pop(a, None)
            print(f"  Breakpoint removed at {fmt(a)}")

        elif op == 'lb':
            if not self.breakpoints:
                print("  No breakpoints.")
            for a, spec in sorted(self.breakpoints.items()):
                lbl = self.prog.sym_map.get(a, "")
                print(f"  {fmt(a)}" + (f" <{lbl}>" if lbl else ""))

        elif op in ('d', 'dis', 'disasm'):
            p = self.prog
            base = self.code_addr(parts[1]) if len(parts) > 1 else cpu.pc
            count = int(parts[2]) if len(parts) > 2 else 10
            shown = 0
            a = base
            while shown < count and span(a, 2) is not None:
                sym = p.sym_map.get(a, "")
                if sym: print(f"  <{sym}>:")
                asm = p.asm_map.get(a)
                if asm:
                    marker = "=>" if a == cpu.pc else "  "
                    print(f"  {marker} {fmt(a)}: {asm}")
                    shown += 1
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

        elif op == 'check':
            self.check()

        elif op == 'reset':
            self.reset()

        elif op == 'reload':
            self.reload()

        else:
            print(f"  Unknown command '{op}'.  Type 'h' for help.")


def run_interactive(prog, trace=False):
    Session(prog, trace).loop()


# ── Assembler / linker helpers ─────────────────────────────────────────────────

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


BUILD_TIMEOUT = 60          # seconds the assembler and linker may take

# Variables that make GCC load its programs or libraries from somewhere else;
# the build never needs them, so they are not passed on.
_GCC_ENV = ('GCC_EXEC_PREFIX', 'COMPILER_PATH', 'LIBRARY_PATH', 'DEPENDENCIES_OUTPUT',
            'SUNPRO_DEPENDENCIES', 'GCC_COMPARE_DEBUG')


def toolchain():
    """The full path of arm-none-eabi-gcc (PICOSIM_GCC, else the one on PATH), or None."""
    import shutil
    gcc = os.environ.get('PICOSIM_GCC') or shutil.which('arm-none-eabi-gcc')
    return os.path.abspath(gcc) if gcc and os.path.isfile(gcc) else None


def _run_bounded(cmd, timeout):
    """Run `cmd` in its own process group; on timeout or Ctrl+C kill the whole
    group (gcc starts as and ld) and raise.  Returns the CompletedProcess."""
    import subprocess, signal
    env = {k: v for k, v in os.environ.items() if k not in _GCC_ENV}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=env, start_new_session=(os.name == 'posix'))
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException:
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def assemble(s_file, elf_file, ld_file, extra_s_files=None, extra_ld_flags=None,
             timeout=BUILD_TIMEOUT):
    """Assemble and link a .s file.  Returns (ok, message).  Line-number debug
    info (-g) is always included, so the GUI can show each instruction's
    source line (see source_lines).

    The linker writes a private temporary file that then replaces elf_file in
    one step, so another picosim process building the same file at the same
    time (Testing in one window, a test run in another) never reads it half
    written, and a failed build leaves the last good elf_file as it was.
    `.include` finds files next to the source file.  The build is stopped
    after `timeout` seconds."""
    import subprocess, threading
    gcc = toolchain()
    if gcc is None:
        return False, ("arm-none-eabi-gcc not found on PATH.\n"
                       "Install it (installation.md, step 5), then check with: "
                       "picosim --run examples/01_hello.s")
    cmd = [
        gcc,
        f'-Wa,-I{os.path.dirname(os.path.abspath(s_file))}',   # .include next to the source
        '-mcpu=cortex-m0plus', '-mthumb',
        '-nostdlib',
        '-g',                   # DWARF line table: address -> source line
        '-Wa,--keep-locals',    # keep .L* labels in the assembler object
        '-Wl,--discard-none',   # prevent the linker from stripping them
        '-T', ld_file,
        '-o', None,
        s_file,
    ]
    tmp = f"{elf_file}.{os.getpid()}-{threading.get_ident()}.tmp"
    cmd[cmd.index(None)] = tmp
    if extra_s_files:
        cmd.extend(extra_s_files)
    if extra_ld_flags:
        cmd.extend(extra_ld_flags)
    try:
        result = _run_bounded(cmd, timeout)
    except subprocess.TimeoutExpired:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, (f"The assembler took longer than {timeout} seconds and was stopped. "
                       f"Is an .include or .incbin reading something very large, or a .rept "
                       f"repeating forever?")
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    combined = (result.stderr + result.stdout).strip().replace(tmp, elf_file)
    if result.returncode == 0:
        os.replace(tmp, elf_file)
    elif os.path.exists(tmp):
        os.remove(tmp)
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


# ── Entry point ────────────────────────────────────────────────────────────────

RUN_CHUNK = 100_000     # instructions per native run() call: Ctrl+C is seen between calls


def _error(e):
    """An error that stopped the program, with what it means on a real Pico."""
    r = stop_reason(e)
    return f"{e}" + (f"\n  [{STOP_LABELS[r]}]" if r in STOP_LABELS else "")


def _status(msg):
    """A message from picosim itself (not the program): always on stderr, so
    stdout carries only what the program prints."""
    print(msg, file=sys.stderr, flush=True)


def _batch(cpu, count=None, trace=False):
    """Run without the prompt.  Returns (instructions run, why it stopped:
    'halt', 'count' or 'ctrl-c').  Faults raise SimulatorError/RuntimeError;
    the program's exit raises SystemExit.  Without tracing it uses the C++
    run loop, in chunks so that Ctrl+C stops it between two instructions."""
    stop = {'ctrl-c': False}
    def ask(sig, frame):
        stop['ctrl-c'] = True
    old = signal.signal(signal.SIGINT, ask)
    n = 0
    try:
        while not cpu.halted and (count is None or n < count):
            if stop['ctrl-c']:
                return n, 'ctrl-c'
            if trace:
                cpu.step()
                cpu.check_halt()
                n += 1
            else:
                left = RUN_CHUNK if count is None else min(RUN_CHUNK, count - n)
                n += cpu._core.run(left)
        return n, 'halt' if cpu.halted else 'count'
    finally:
        signal.signal(signal.SIGINT, old)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="ARMv6-M (Cortex-M0+) Simulator — the RP2040 memory map",
        epilog="Output: stdout is only what the program prints; picosim's own messages, the "
               "--trace rows and errors go to stderr. Exit status: the program's exit code "
               "(0 when main returns), 1 for a build error or a fault, 130 when stopped with "
               "Ctrl+C.")
    parser.add_argument("input", nargs="?", help=".s assembly file or .elf binary to load")
    parser.add_argument("--run", "-r", action="store_true",
                        help="run to completion without interactive prompt")
    parser.add_argument("--steps", "-n", type=int, default=0,
                        help="execute exactly N steps then stop and print the registers")
    parser.add_argument("--trace", "-t", action="store_true",
                        help="print each instruction (to stderr) as it runs; works with --run "
                             "and -n")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="no status messages (Assembling..., Loaded...)")
    parser.add_argument("--elf", "-o", metavar="PATH",
                        help="also save the linked program (ELF) to PATH")
    parser.add_argument("--time", action="store_true",
                        help="run to completion and report how fast this computer simulated "
                             "it (instructions per second of host time; not Pico cycles)")
    parser.add_argument("--uf2", action="store_true",
                        help="build a .uf2 image for the Raspberry Pi Pico and exit")
    parser.add_argument("--flash", action="store_true",
                        help="build and upload a .uf2 image to a Raspberry Pi Pico")
    parser.add_argument("--gui", action="store_true",
                        help="open the graphical simulator (requires Godot 4)")
    parser.add_argument("--cycle-table", action="store_true",
                        help="print the teaching multi-cycle datapath's cycles per instruction "
                             "(Markdown) and exit")
    args = parser.parse_args()

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
        prog = load_program(args.input, trace=args.trace, verbose=not args.quiet)
    except LoadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if args.elf:
        import shutil
        shutil.copyfile(prog.elf, args.elf)
        if not args.quiet:
            _status(f"Saved the linked program to '{args.elf}'")
    cpu = prog.cpu

    if not (args.time or args.run or args.steps):
        run_interactive(prog, args.trace)
        return

    t_start = time.perf_counter()
    try:
        n, why = _batch(cpu, args.steps or None, args.trace)
    except (RuntimeError, SimulatorError) as e:
        print(f"ERROR: {_error(e)}", file=sys.stderr); sys.exit(1)
    except SystemExit as e:
        sys.exit(e.code)
    elapsed = time.perf_counter() - t_start
    if why == 'ctrl-c':
        _status(f"Stopped (Ctrl+C) at {fmt(cpu.pc)} after {n:,} instructions.")
        sys.exit(130)
    if args.time:
        ips = n / elapsed if elapsed > 0 else float('inf')
        _status(f"Executed {n:,} instructions in {elapsed:.4f}s of host time ({ips:,.0f} insn/s "
                f"on this computer: the simulator's speed, not the Pico's)")
    if args.steps:
        print_state(cpu, prog.asm_map, prog.sym_map, prog.source, os.path.basename(prog.path))


if __name__ == "__main__":
    main()
