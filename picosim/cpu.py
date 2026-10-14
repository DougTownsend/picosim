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


def unmapped_message(addr, write, insn_addr, access='data'):
    """Why an access to `addr` failed, in words a student can act on.
    access: 'fetch' (running code there), 'data' (a load or store) or 'debug'."""
    if access == 'fetch':
        where = f"The program jumped to 0x{addr:08X} and tried to run code there, where picosim has nothing."
    elif access == 'data':
        what = "writes to" if write else "reads from"
        where = f"The instruction at 0x{insn_addr:X} {what} 0x{addr:08X}, where picosim has nothing."
    else:
        return (f"Nothing is at address 0x{addr:08X} in picosim: RAM is 0x0000-0xFFFF and the "
                "simulated I/O registers are SIO (0xD0000000), IO_BANK0 (0x40014000), "
                "PADS_BANK0 (0x4001C000) and RESETS (0x4000C000).")
    if 0x20000000 <= addr < 0x20042000:
        hint = (" That is the Pico's SRAM; in picosim RAM is at 0x0000-0xFFFF, so use labels "
                "(ldr r0, =my_data) rather than fixed RAM addresses.")
    elif 0x10000000 <= addr < 0x11000000:
        hint = " That is the Pico's flash (XIP), which picosim does not simulate."
    elif 0x40000000 <= addr < 0x60000000 or 0xD0000000 <= addr < 0xE0000000:
        hint = (" picosim simulates only these Pico peripherals: SIO GPIO registers "
                "(0xD0000000), IO_BANK0 (0x40014000), PADS_BANK0 (0x4001C000) and "
                "RESETS (0x4000C000).")
    else:
        hint = " RAM is at 0x0000-0xFFFF; is the address in the register what you meant?"
    return "Bus fault: " + where + hint + " On the Pico this would be a HardFault."


def svc_write_data(core):
    """The bytes SVC #0 (write) prints: R2 bytes from address R1, in RAM."""
    buf, n = core.get_reg(1), core.get_reg(2)
    if buf >= 0x10000 or buf + n > 0x10000:
        raise SimulatorError(f"SVC #0 (write): {n} bytes at 0x{buf:X} run past the end of RAM (0xFFFF)")
    return core.get_mem_slice(buf, n)


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
    # The C++ core calls these for every address at or above 0x10000.  An
    # address no simulated peripheral claims is a bus fault, as on the Pico
    # (where it is a HardFault) — never a silent read of 0.

    def _block(self, addr, write):
        block = self.memory.peripheral(addr)
        if block is None:
            c = self._core
            raise SimulatorError(unmapped_message(addr, write, c.insn_addr, c.access))
        return block

    def _periph_read(self, addr, nbytes):
        b = self._block(addr, False)
        if nbytes == 4: return b.read32(addr)
        if nbytes == 2: return b.read16(addr)
        return b.read8(addr)

    def _periph_write(self, addr, val, nbytes):
        b = self._block(addr, True)
        if nbytes == 4: b.write32(addr, val)
        elif nbytes == 2: b.write16(addr, val)
        else: b.write8(addr, val)

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
            # write(fd, buf_addr, len) — to the terminal only (fd 1 or 2)
            fd  = self._core.get_reg(0)
            if fd not in (1, 2):
                raise SimulatorError(f"SVC #0 (write): file descriptor {fd} is not 1 (stdout) or 2 (stderr)")
            sys.stdout.flush()
            os.write(fd, svc_write_data(self._core))
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
