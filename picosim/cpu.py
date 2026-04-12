"""
ARMv6-M (Cortex-M0+) CPU core.

The execution engine is implemented in C++ (_picosim_core.CPUCore).
This module wraps it with the Python interface expected by sim.py:
  - register access via regs[], pc, sp, lr
  - flags N, Z, C, V
  - step() / check_halt() / is_32bit_thumb()
  - mem_read*/mem_write* delegating to the C++ core
  - SVC syscall dispatch
  - peripheral callbacks routed via the Memory object
"""

import os
import sys
import struct

from ._picosim_core import CPUCore as _CPUCore

# ── Constants ──────────────────────────────────────────────────────────────────

MEM_SIZE  = 0x10000   # 64 KiB (16-bit address space)
STACK_TOP = 0x10000   # initial SP


class SimulatorError(Exception):
    pass


# ── CPU ────────────────────────────────────────────────────────────────────────

class CPU:
    def __init__(self, memory, entry, asm_map, sym_map, trace=False):
        self._core   = _CPUCore()
        self.memory  = memory          # Memory object (for peripheral dispatch)
        self.asm_map = asm_map
        self.sym_map = sym_map
        self.trace   = trace
        self.gpio    = None            # GPIO object for display/interactive use

        # Initialise registers
        self._core.set_reg(13, STACK_TOP)    # SP
        self._core.set_reg(14, 0xFFFFFFFF)   # LR sentinel
        self._core.pc = entry

        # Load flat RAM content into the C++ core's memory array
        self._core.load_memory(bytes(memory._ram.data), 0)

        # Wire callbacks: peripheral I/O and SVC
        self._core.peripheral_read  = self._periph_read
        self._core.peripheral_write = self._periph_write
        self._core.svc_handler      = self._svc_dispatch

    # ── peripheral callbacks ──────────────────────────────────────────────────

    def _periph_read(self, addr, nbytes):
        if nbytes == 4: return self.memory.read32(addr)
        if nbytes == 2: return self.memory.read16(addr)
        return self.memory.read8(addr)

    def _periph_write(self, addr, val, nbytes):
        if nbytes == 4: self.memory.write32(addr, val)
        elif nbytes == 2: self.memory.write16(addr, val)
        else: self.memory.write8(addr, val)

    # ── register accessors ────────────────────────────────────────────────────

    @property
    def regs(self):
        """Return the 16 registers as a list (read view; use set_reg to write)."""
        return self._core.regs

    @property
    def pc(self): return self._core.pc
    @pc.setter
    def pc(self, v): self._core.pc = v

    @property
    def sp(self): return self._core.sp
    @sp.setter
    def sp(self, v): self._core.sp = v

    @property
    def lr(self): return self._core.lr
    @lr.setter
    def lr(self, v): self._core.lr = v

    def set_reg(self, n, v):
        self._core.set_reg(n & 15, v)

    # ── flags ─────────────────────────────────────────────────────────────────

    @property
    def N(self): return self._core.N
    @property
    def Z(self): return self._core.Z
    @property
    def C(self): return self._core.C
    @property
    def V(self): return self._core.V

    # ── state ─────────────────────────────────────────────────────────────────

    @property
    def halted(self): return self._core.halted
    @halted.setter
    def halted(self, v): self._core.halted = v

    @property
    def steps(self): return self._core.steps

    # ── peripheral registration ───────────────────────────────────────────────

    def add_peripheral(self, block):
        """Register a MemoryBlock peripheral with the memory system."""
        self.memory.add_block(block)

    # ── memory access — C++ core is authoritative for flat RAM ────────────────

    def mem_read8 (self, addr): return self._core.read8 (addr)
    def mem_read16(self, addr): return self._core.read16(addr)
    def mem_read32(self, addr): return self._core.read32(addr)
    def mem_write8 (self, addr, val): self._core.write8 (addr, val)
    def mem_write16(self, addr, val): self._core.write16(addr, val)
    def mem_write32(self, addr, val): self._core.write32(addr, val)

    # ── fetch helpers (used by sim.py disassembly display) ────────────────────

    def is_32bit_thumb(self, hw):
        return self._core.is_32bit_thumb(hw)

    # ── step ──────────────────────────────────────────────────────────────────

    def step(self):
        if self._core.halted:
            return
        if self.trace:
            insn_addr = self._core.pc
            sym = self.sym_map.get(insn_addr, "")
            sym_str = f" <{sym}>" if sym else ""
            asm = self.asm_map.get(insn_addr, "???")
            print(f"  0x{insn_addr:04X}{sym_str}: {asm}")
        try:
            self._core.step()
        except RuntimeError as e:
            raise SimulatorError(str(e)) from e

    def check_halt(self):
        self._core.check_halt()

    # ── SVC syscall dispatch (called from C++ core) ───────────────────────────

    def _svc_dispatch(self, num):
        if num == 0:
            # write(fd, buf_addr, len)
            fd  = self._core.get_reg(0)
            buf = self._core.get_reg(1) & 0xFFFF
            n   = self._core.get_reg(2)
            data = self._core.get_mem_slice(buf, n)
            os.write(fd, data)
        elif num == 1:
            # exit(code)
            raise SystemExit(self._core.get_reg(0))
        elif num == 2:
            # putchar(c)
            sys.stdout.write(chr(self._core.get_reg(0) & 0xFF))
            sys.stdout.flush()
        elif num == 3:
            # getchar() — read one char without requiring Enter
            try:
                import tty, termios
                fd = sys.stdin.fileno()
                try:
                    old = termios.tcgetattr(fd)
                    tty.setraw(fd)
                    ch = sys.stdin.read(1)
                except (termios.error, AttributeError):
                    ch = sys.stdin.read(1)
                else:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except ImportError:
                try:
                    import msvcrt
                    ch = msvcrt.getwch()
                except ImportError:
                    ch = sys.stdin.read(1)
            self._core.set_reg(0, ord(ch) if ch else 0xFFFFFFFF)
        else:
            raise SimulatorError(f"Unknown SVC #{num}")
