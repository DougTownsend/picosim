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
from . import _picosim_core as _core_mod
from . import timing
from .memmap import (STACK_TOP, SRAM_BASE, SRAM_END, FLASH_BASE, FLASH_END, ROM_END, fmt,
                     span)

SIMULATED_IO = ("SIO GPIO registers (0xD0000000), IO_BANK0 (0x40014000), "
                "PADS_BANK0 (0x4001C000) and RESETS (0x4000C000)")


# Why the program stopped, as it bears on the same program on a real Pico
# (RES-03).  SimulatorError.reason and stop_reason() give one of these.
HARDWARE_FAULT = 'hardware-fault'   # the Pico stops here too: a HardFault
MODEL_LIMIT = 'model-limit'         # the Pico has something here that picosim does not simulate
TEACHING_STOP = 'teaching-stop'     # the Pico would go on; picosim stops to show the mistake
STOP_LABELS = {HARDWARE_FAULT: "HardFault on the Pico too",
               MODEL_LIMIT: "picosim limitation (works on the Pico)",
               TEACHING_STOP: "teaching stop (the Pico would go on)"}


class SimulatorError(Exception):
    def __init__(self, *args, reason=None):
        super().__init__(*args)
        self.reason = reason            # HARDWARE_FAULT, MODEL_LIMIT, TEACHING_STOP or None


def stop_reason(exc):
    """What an exception that stopped the program means on a real Pico:
    HARDWARE_FAULT, MODEL_LIMIT or TEACHING_STOP; None when it is not one of
    those (an OS call picosim does not have, a file that will not load)."""
    for e in (exc, getattr(exc, '__cause__', None)):
        if getattr(e, 'reason', None):
            return e.reason
        if isinstance(e, _core_mod.HardwareFault):
            return HARDWARE_FAULT
        if isinstance(e, _core_mod.TeachingStop):
            return TEACHING_STOP
    return None


# What the RP2040 has where picosim has nothing (datasheet 2.2; measured on a
# Pico: hwfacts/memory_map.json).  An access to one of these is a picosim
# limitation: on the Pico it works.  (lo, hi, what is there)
PICO_HAS = (
    (FLASH_END, 0x11000000, "still the 16 MB XIP flash window (the Pico's flash chip is 2 MB; "
                            "picosim's flash ends at 0x101FFFFF)"),
    (0x11000000, 0x14000000, "another view of the flash through the XIP cache (no-allocate or no-cache)"),
    (0x14000000, 0x14004000, "the XIP cache's control registers (XIP_CTRL)"),
    (0x18000000, 0x18004000, "the flash interface's registers (XIP_SSI)"),
    (0x40000000, 0x40068000, "an APB peripheral's registers (clocks, UART, SPI, I2C, ADC, PWM, timer, "
                             "watchdog, RTC and others)"),
    (0x4006C000, 0x40070000, "the TBMAN registers"),
    (0x50000000, 0x50004000, "the DMA controller's registers"),
    (0x50100000, 0x50104000, "the USB controller's RAM (DPRAM)"),
    (0x50110000, 0x50114000, "the USB controller's registers"),
    (0x50200000, 0x50204000, "PIO0's registers"),
    (0x50300000, 0x50304000, "PIO1's registers"),
    (0x50400000, 0x50400010, "the XIP stream FIFO (XIP_AUX)"),
    (0xD0000000, 0xD0001000, "an SIO register picosim does not have (CPUID, the FIFOs, spinlocks, "
                             "the divider, the interpolators)"),
    (0xE0000000, 0xE0100000, "the Cortex-M0+'s own registers (the PPB: SysTick, NVIC, SCB)"),
    (0xE0100000, 0xE0101000, "the system region just past the PPB, where an access worked on a Pico "
                             "(it read 0; measured at 0xE0100000 only)"),
)
# Where the RP2040 has nothing and a load or store HardFaults (datasheet 2.2;
# each range checked at the addresses in hwfacts/memory_map.json "probes").
PICO_NOTHING = (
    (ROM_END, 0x10000000),             # 0x00004000, 0x0FFFFFFC
    (SRAM_END, 0x21000000),            # 0x20042000, 0x20080000
    (0x21040000, 0x40000000),          # 0x21040000, 0x30000000
    (0x40070000, 0x50000000),          # 0x40070000
    (0x60000000, 0xD0000000),          # 0x60000000, 0xA0000000
    (0xFFFFFFFC, 0x100000000),         # 0xFFFFFFFC
)
XIP_SRAM = (0x15000000, 0x15004000)    # the cache's own memory: faults while the cache is on

# Campaign A's sweep (discover/coverage.py) measures every address of the
# 4 GB on a Pico: hwfacts/memory_map.json "coverage", contiguous ranges, each
# with what a 32-bit load did there.  When it is there, unmapped_reason()
# answers from it; until then from the tables above and the datasheet.
_HWFACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hwfacts')
_COVERAGE = None                       # [(lo, hi, read32 result)], sorted; [] when not measured


def measured_coverage():
    """The measured ranges [(lo, hi, what a 32-bit load did: 'ok', 'fault',
    'hang' or 'not read: <why>')], or [] before the sweep has run."""
    global _COVERAGE
    if _COVERAGE is None:
        _COVERAGE = []
        try:
            import json
            with open(os.path.join(_HWFACTS, 'memory_map.json')) as f:
                cov = json.load(f).get('coverage') or {}
            _COVERAGE = [(int(r['lo'], 16), int(r['hi'], 16), (r.get('core') or {}).get('read32') or '')
                         for r in cov.get('ranges') or ()]
            _COVERAGE.sort()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            _COVERAGE = []
    return _COVERAGE


def set_measured_coverage(ranges):
    """Use these ranges (as measured_coverage() gives them) instead of
    hwfacts/; None reads hwfacts/ again.  For tests."""
    global _COVERAGE
    _COVERAGE = None if ranges is None else sorted(ranges)


def _measured(addr):
    import bisect
    cov = measured_coverage()
    if not cov:
        return None
    i = bisect.bisect_right([lo for lo, _, _ in cov], addr) - 1
    if i >= 0 and cov[i][0] <= addr < cov[i][1]:
        return cov[i]
    return None


def _pico_has(addr):
    for lo, hi, what in PICO_HAS:
        if lo <= addr < hi:
            return what
    return None


def unmapped_reason(addr):
    """(reason, what the Pico does there) for an address picosim has nothing
    at: MODEL_LIMIT and what the Pico has there, or HARDWARE_FAULT and a
    sentence on the Pico's HardFault."""
    m = _measured(addr)
    if m is not None:
        lo, hi, r32 = m
        where = f"{fmt(lo)}-{fmt(hi - 1)}"
        has = _pico_has(addr)
        if r32 == 'fault':
            if XIP_SRAM[0] <= addr < XIP_SRAM[1]:
                return HARDWARE_FAULT, ("On the Pico this is a HardFault too: the address is the XIP cache's "
                                        "own memory, which faults while the cache is on, as it is when a "
                                        f"program runs from flash (measured on a Pico: {where}).")
            return HARDWARE_FAULT, ("On the Pico this is a HardFault too: a load anywhere in "
                                    f"{where} faulted on a Pico (measured).")
        if r32 == 'ok':
            return MODEL_LIMIT, has or f"an address where a load worked on a Pico (measured: {where})"
        if r32 == 'hang' or 'hung' in r32:
            return HARDWARE_FAULT, (f"On the Pico a read at {where} hung the bus (measured): a program "
                                    "that reads there stops too, without a HardFault.")
        if r32.startswith('not read'):
            why = r32.split(':', 1)[1].strip() if ':' in r32 else r32
            if has:
                return MODEL_LIMIT, f"{has} (not read on a Pico by the address sweep: {why})"
            return HARDWARE_FAULT, ("On the Pico this is probably a HardFault too: the datasheet lists "
                                    f"nothing here, and the address sweep did not read it ({why}).")
    has = _pico_has(addr)
    if has:
        return MODEL_LIMIT, has
    if XIP_SRAM[0] <= addr < XIP_SRAM[1]:
        return HARDWARE_FAULT, ("On the Pico this is a HardFault too: the address is the XIP cache's "
                                "own memory, which faults while the cache is on, as it is when a program "
                                "runs from flash (measured on a Pico).")
    for lo, hi in PICO_NOTHING:
        if lo <= addr < hi:
            return HARDWARE_FAULT, ("On the Pico this is a HardFault too: the RP2040 has nothing at this "
                                    "address (datasheet 2.2; checked on a Pico).")
    return HARDWARE_FAULT, ("On the Pico this would most likely be a HardFault too: the RP2040 "
                            "datasheet lists nothing at this address (not measured on a Pico; "
                            "`python3 -m picosim.discover memmap --stages sweep` measures it).")


def unmapped_message(addr, write, insn_addr, access='data'):
    """Why an access to `addr` failed, in words a student can act on, and
    whether the Pico would fail there too (unmapped_reason()).
    access: 'fetch' (running code there), 'data' (a load or store) or 'debug'."""
    reason, what = unmapped_reason(addr)
    if access not in ('fetch', 'data'):
        pico = (f" On the Pico this address is {what}; picosim does not simulate it."
                if reason == MODEL_LIMIT else "")
        return (f"Nothing is at address {fmt(addr)} in picosim. Its memory is ROM "
                f"(0x00000000-0x00003FFF), flash (0x10000000-{fmt(FLASH_END - 1)}) and SRAM "
                f"(0x20000000-{fmt(SRAM_END - 1)}, and again without striping at "
                f"0x21000000-0x2103FFFF); the simulated I/O registers are {SIMULATED_IO}." + pico)
    if access == 'fetch':
        where = f"The program jumped to {fmt(addr)} and tried to run code there"
    else:
        what_ = "writes to" if write else "reads from"
        where = f"The instruction at {fmt(insn_addr)} {what_} {fmt(addr)}"
    if reason == MODEL_LIMIT:
        hint = (" The program's flash is at 0x10000000 in picosim."
                if 0x11000000 <= addr < 0x14000000 else
                f" picosim simulates only these Pico peripherals: {SIMULATED_IO}."
                if 0x40000000 <= addr < 0x60000000 or 0xD0000000 <= addr < 0xE0000000 else "")
        return (f"Not simulated: {where}. On the Pico that is {what}, so the access works there, "
                f"but picosim does not simulate it. This stop is a limit of picosim, not a mistake "
                f"in the program.{hint}")
    if SRAM_BASE - 0x100 <= addr < SRAM_BASE and access == 'data' and write:
        hint = (" That is just below SRAM, where the stack would go next: the stack has run out "
                "(too many nested calls or pushes without pops?).")
    elif ROM_END <= addr < FLASH_BASE:
        hint = " That is past the end of the ROM. Is the address in the register what you meant?"
    elif XIP_SRAM[0] <= addr < XIP_SRAM[1]:
        hint = ""
    else:
        hint = (" Memory is ROM at 0x00000000, flash at 0x10000000 and SRAM at 0x20000000; "
                "is the address in the register what you meant?")
    return f"Bus fault: {where}, where picosim has nothing.{hint} {what}"


def svc_write_data(core):
    """The bytes SVC #0 (write) prints: R2 bytes from address R1, in memory."""
    buf, n = core.get_reg(1), core.get_reg(2)
    if n and span(buf, n) is None:
        raise SimulatorError(f"SVC #0 (write): {n} bytes at {fmt(buf)} are not all inside one of "
                             f"ROM, flash or SRAM")
    return core.get_mem_slice(buf, n)


# ── CPU ────────────────────────────────────────────────────────────────────────

class CPU:
    def __init__(self, memory, entry, asm_map, sym_map, trace=False):
        self._core   = _CPUCore()
        timing.install(self._core)     # the live RP2040 cycle count (core.m0_cycles)
        self.memory  = memory          # Memory object (for peripheral dispatch)
        self.asm_map = asm_map
        self.sym_map = sym_map
        self.trace   = trace
        self.gpio    = None            # GPIO object for display/interactive use
        self.printed = []              # what the program printed (for the prompt's check)

        # Initialise registers
        self._core.set_reg(13, STACK_TOP)    # SP
        self._core.set_reg(14, 0xFFFFFFFF)   # LR sentinel
        self._core.pc = entry

        # Copy the program's ROM, flash and SRAM into the C++ core
        memory.image.load_into(self._core)

        # Wire callbacks: peripheral I/O and SVC
        self._core.peripheral_read  = self._periph_read
        self._core.peripheral_write = self._periph_write
        self._core.svc_handler      = self._svc_dispatch

    # ── peripheral callbacks ──────────────────────────────────────────────────
    # The C++ core calls these for every address outside ROM, flash and
    # SRAM.  An address no simulated peripheral claims stops the program —
    # never a silent read of 0: a bus fault where the Pico has nothing too (a
    # HardFault there), a picosim limitation where the Pico has something
    # picosim does not simulate (unmapped_reason).

    def _block(self, addr, write):
        block = self.memory.peripheral(addr)
        if block is None:
            c = self._core
            raise SimulatorError(unmapped_message(addr, write, c.insn_addr, c.access),
                                 reason=unmapped_reason(addr)[0])
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

    # ── memory access — the C++ core holds ROM, flash and SRAM ───────────────

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
            # stderr, so stdout stays only what the program prints
            print(f"  {fmt(insn_addr)}{sym_str}: {asm}", file=sys.stderr, flush=True)
        try:
            self._core.step()
        except RuntimeError as e:
            raise SimulatorError(str(e), reason=stop_reason(e)) from e

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
            data = svc_write_data(self._core)
            self.printed.append(bytes(data).decode('latin-1'))
            os.write(fd, data)
        elif num == 1:
            # exit(code)
            raise SystemExit(self._core.get_reg(0))
        elif num == 2:
            # putchar(c)
            ch = chr(self._core.get_reg(0) & 0xFF)
            self.printed.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()
        elif num == 3:
            # getchar() — read one char without requiring Enter.  At the end
            # of the input (a pipe or file that has run out) it returns -1
            # (0xFFFFFFFF), the C library's EOF.
            try:
                import tty, termios
                fd = sys.stdin.fileno()
                try:
                    old = termios.tcgetattr(fd)
                except (termios.error, AttributeError, ValueError, OSError):
                    ch = sys.stdin.read(1)          # not a terminal: a pipe or a file
                else:
                    try:
                        tty.setraw(fd)
                        ch = sys.stdin.read(1)
                    finally:                        # the terminal always gets its settings back
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
