"""
A stand-in RP2040 for Testing without hardware: a GDB server whose CPU is
Unicorn (an emulator built from QEMU, independent of picosim's C++ core).

It lets the whole Testing pipeline run with nothing plugged in, and checks
picosim's instruction behaviour against a second implementation of ARMv6-M.
It is NOT the real chip:

  * peripherals (RESETS, IO_BANK0, PADS_BANK0, SIO GPIO) are picosim's own
    model (gpio.py), with the RP2040's atomic SET/CLR/XOR register aliases;
    the SIO divider, interpolators, spinlocks and FIFO_ST are campaign G's
    datasheet model (discover/interp_model.py);
  * SysTick counts cycles from RP2040 datasheet Table 81 (the model picosim
    uses), so its benchmark numbers agree with picosim by construction;
  * until VTOR points into SRAM, a HardFault stops in a fault loop without
    stacking a frame (what Testing's traces expect).  Once it does (the
    discovery agent sets it), exceptions follow ARMv6-M: frames, EXC_RETURN,
    Handler mode, SVCall/PendSV/SysTick/NVIC priorities, PRIMASK, WFI/WFE,
    tail-chaining by re-checking what is pending, and lockup on a fault in
    the HardFault handler or a failed stacking.  Entry and exit cost
    ENTRY_CYCLES and EXIT_CYCLES, guesses until campaign D4 measures them;
  * DEMCR.VC_HARDERR starts set, so a HardFault halts the core until the
    debugger sends "cortex_m vector_catch none", as OpenOCD can leave it;
  * the non-striped SRAM alias (0x21000000) is served by callbacks, which
    Unicorn cannot execute from: code there faults (campaign D's code site
    S3 reads "not measured"; only the chip can time it).

Like the real chip, it refuses debugger access to the SIO (0xD0000000),
which only the cores can reach.

Only real hardware can confirm timing and peripheral behaviour.

    srv = StandIn(); srv.start()      # then connect an RSP client to srv.port
"""

import socket
import threading
import time

try:
    import unicorn as U
    import unicorn.arm_const as A
except ImportError:          # optional: pip install unicorn
    U = A = None

from ..gpio import GPIO
from ..timing import pico_row, is_wide, APB_LOAD_EXTRA, APB_STORE_EXTRA

ROM_END = 0x4000
SRAM = (0x20000000, 0x42000)
FAULT_LOOP = 0x000000C0             # "HardFault handler": a b . in ROM
RESET_PC = 0x000000EE
PERIPH = {                           # base -> size (with the atomic aliases)
    0x4000C000: 0x4000,              # RESETS
    0x40014000: 0x4000,              # IO_BANK0
    0x4001C000: 0x4000,              # PADS_BANK0
}
SYSINFO = (0x40000000, 0x1000)
CHIP_ID = 0x20002927                 # a placeholder: revision 2, part 0x0002, manufacturer 0x927
NONSTRIPED = (0x21000000, 0x40000)     # SRAM0-3 one bank after another (datasheet 2.6.2)
SIO = (0xD0000000, 0x1000)
SIO_MODEL = (0x050, 0x180)           # SIO offsets modelled by discover/interp_model.py, not gpio.py
PPB = (0xE000E000, 0x1000)
SYST_CSR, SYST_RVR, SYST_CVR, SYST_CALIB = 0xE000E010, 0xE000E014, 0xE000E018, 0xE000E01C
NVIC_ISER, NVIC_ICER, NVIC_ISPR, NVIC_ICPR, NVIC_IPR = 0xE000E100, 0xE000E180, 0xE000E200, 0xE000E280, 0xE000E400
CPUID, ICSR, VTOR, AIRCR, SCR, CCR = 0xE000ED00, 0xE000ED04, 0xE000ED08, 0xE000ED0C, 0xE000ED10, 0xE000ED14
SHPR2, SHPR3, SHCSR, MPU_TYPE = 0xE000ED1C, 0xE000ED20, 0xE000ED24, 0xE000ED90
DHCSR, DEMCR = 0xE000EDF0, 0xE000EDFC
CPUID_VALUE = 0x410CC601             # Cortex-M0+ r0p1, as the Pico SDK expects
VC_HARDERR = 1 << 10

# The exception model (ARMv6-M B1.5): used once VTOR points into SRAM, as the
# discovery agent sets it.  Until then a fault stops in a "fault loop" with no
# frame stacked, which is what Testing's traces expect.
NMI, HARDFAULT, SVCALL, PENDSV, SYSTICK = 2, 3, 11, 14, 15
ENTRY_CYCLES = 15       # Arm's published Cortex-M0+ interrupt latency (zero wait states); D4 measures it
EXIT_CYCLES = 13        # not published: a stand-in guess; D4 measures it
EXC_RETURNS = {0xFFFFFFF1: (False, False), 0xFFFFFFF9: (True, False), 0xFFFFFFFD: (True, True)}   # (to Thread, PSP)
QEMU_EXCEPTION_EXIT, QEMU_SVC = 8, 2

REGS = ['r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8', 'r9', 'r10', 'r11', 'r12',
        'sp', 'lr', 'pc', 'xpsr', 'msp', 'psp', 'primask', 'control']

# sizes as OpenOCD describes a Cortex-M0+ (PRIMASK 1 bit, CONTROL 3 bits)
BITS = {'primask': 1, 'control': 3}
TARGET_XML = ('<?xml version="1.0"?><target version="1.0"><architecture>arm</architecture>'
              '<feature name="org.gnu.gdb.arm.m-profile">' +
              ''.join(f'<reg name="{n}" bitsize="{BITS.get(n, 32)}" regnum="{i}"/>' for i, n in enumerate(REGS)) +
              '</feature></target>')


class HangUp(Exception):
    """What OpenOCD does with some bad requests: drop the connection."""


def available():
    return U is not None


def _v6m_defined(hw1, hw2):
    """Is this an ARMv6-M instruction? (from discover/v6m.py, written from
    the ARM ARM, not from picosim's decoder)"""
    from ..discover import v6m
    if is_wide(hw1):
        return v6m.decode32(hw1, hw2).cls != 'undefined'
    return v6m.classify(hw1) != 'undefined'


def _reg(name):
    return getattr(A, 'UC_ARM_REG_' + name.upper())


class Chip:
    """The stand-in RP2040: Unicorn plus peripherals, a SysTick and (once
    VTOR points into SRAM) ARMv6-M exceptions."""

    def __init__(self, rom=None):
        uc = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_THUMB | U.UC_MODE_MCLASS)
        uc.ctl_set_cpu_model(A.UC_CPU_ARM_CORTEX_M0)
        uc.mem_map(0, ROM_END, U.UC_PROT_READ | U.UC_PROT_EXEC)
        if rom:
            uc.mem_write(0, bytes(rom[:ROM_END]))
        else:
            uc.mem_write(FAULT_LOOP, b'\xfe\xe7')       # b .
            uc.mem_write(RESET_PC, b'\xfe\xe7')
        uc.mem_map(*SRAM)
        for base, size in PERIPH.items():
            uc.mmio_map(base, size,
                        lambda u, off, sz, ud, b=base: self._periph_read(b + off, sz), None,
                        lambda u, off, sz, v, ud, b=base: self._periph_write(b + off, sz, v), None)
        uc.mmio_map(SYSINFO[0], SYSINFO[1], self._sysinfo_read, None, lambda *a: None, None)
        uc.mmio_map(SIO[0], SIO[1], self._sio_read, None, self._sio_write, None)
        uc.mmio_map(NONSTRIPED[0], NONSTRIPED[1], self._ns_read, None, self._ns_write, None)
        uc.mmio_map(PPB[0], PPB[1], self._ppb_read, None, self._ppb_write, None)
        uc.hook_add(U.UC_HOOK_MEM_READ | U.UC_HOOK_MEM_WRITE, self._check_align)
        uc.hook_add(U.UC_HOOK_INTR, self._intr)
        self.uc = uc
        self.demcr = VC_HARDERR          # the debug domain keeps it over a reset
        self.reset()

    def reset(self):
        self.gpio = GPIO()
        self.sio = None                  # divider, interpolators, spinlocks (_sio_model)
        self.cycles = 0
        self.syst = {'csr': 0, 'rvr': 0, 'cvr': 0}
        for r in REGS[:13]:
            self.uc.reg_write(_reg(r), 0)
        self.uc.reg_write(A.UC_ARM_REG_CONTROL, 0)
        self.uc.reg_write(A.UC_ARM_REG_PRIMASK, 0)
        self.uc.reg_write(A.UC_ARM_REG_SP, SRAM[0] + SRAM[1])
        self.uc.reg_write(A.UC_ARM_REG_LR, 0xFFFFFFFF)
        self.uc.reg_write(A.UC_ARM_REG_PC, RESET_PC)
        self.uc.reg_write(A.UC_ARM_REG_XPSR, 0x01000000)
        self.ipsr = 0
        self.active = []                 # exceptions being handled, oldest first
        self.pending = set()             # exception numbers
        self.npriv = 0                   # CONTROL.nPRIV while a handler runs
        self.vtor = 0
        self.iser = 0
        self.ipr = [0] * 8
        self.shpr2 = self.shpr3 = self.scr = 0
        self.lockup = False
        self.sleeping = False
        self.event = False
        self.tbit = 1                    # EPSR.T: Unicorn always starts in Thumb, so it is kept here
        self.fault_reason = None

    def exc_model(self):
        return SRAM[0] <= self.vtor < SRAM[0] + SRAM[1]

    # ── peripherals ─────────────────────────────────────────────────────────

    def _periph_read(self, addr, size):
        base = addr & ~0x3FFF
        reg = base | (addr & 0xFFF)
        v = self.gpio.read32(reg & ~3)
        return (v >> (8 * (addr & 3))) & ((1 << (8 * size)) - 1)

    def _periph_write(self, addr, size, value):
        """RP2040 datasheet §2.1.2: +0x1000 XOR, +0x2000 SET, +0x3000 CLR."""
        alias = (addr >> 12) & 3
        reg = (addr & ~0x3FFF) | (addr & 0xFFF)
        reg &= ~3
        if alias:
            old = self.gpio.read32(reg)
            value = (old ^ value, old | value, old & ~value)[alias - 1]
        self.gpio.write32(reg, value & 0xFFFFFFFF)

    def _sysinfo_read(self, uc, off, size, ud):
        return {0: CHIP_ID, 4: 0x2}.get(off & ~3, 0)       # CHIP_ID, PLATFORM (ASIC)

    @staticmethod
    def _striped(off):
        """Where a non-striped SRAM0-3 offset is in the striped window."""
        bank, word = off >> 16, (off & 0xFFFF) >> 2
        return SRAM[0] + 16 * word + 4 * bank + (off & 3)

    def _ns_read(self, uc, off, size, ud):
        return int.from_bytes(self.uc.mem_read(self._striped(off), size), 'little')

    def _ns_write(self, uc, off, size, value, ud):
        self.uc.mem_write(self._striped(off), (value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little'))

    def _sio_read(self, uc, off, size, ud):
        if SIO_MODEL[0] <= off < SIO_MODEL[1]:      # divider, interpolators, spinlocks, FIFO_ST
            return self._sio_model().read(off & ~3, self.cycles)
        return self.gpio.read32(SIO[0] + (off & ~3)) if off != 0 else 0   # CPUID = 0 (core 0)

    def _sio_write(self, uc, off, size, value, ud):
        if SIO_MODEL[0] <= off < SIO_MODEL[1]:
            self._sio_model().write(off & ~3, value, self.cycles)
            return
        self.gpio.write32(SIO[0] + (off & ~3), value)

    def _sio_model(self):
        """The datasheet model of the SIO blocks gpio.py does not have
        (picosim/discover/interp_model.py), made on first use after a reset."""
        if self.sio is None:
            from ..discover.interp_model import SioModel
            self.sio = SioModel()
        return self.sio

    def _tick(self, n):
        """n processor clocks pass: SysTick counts them down."""
        self.cycles += n
        s = self.syst
        if not s['csr'] & 1:
            return
        while n > 0:
            if s['cvr'] == 0:
                if s['rvr'] == 0:
                    return                       # reload 0: the counter stays stopped
                s['cvr'] = s['rvr']
                n -= 1
                continue
            d = min(n, s['cvr'])
            s['cvr'] -= d
            n -= d
            if s['cvr'] == 0:
                s['csr'] |= 1 << 16              # COUNTFLAG
                if s['csr'] & 2:                 # TICKINT
                    self.pending.add(SYSTICK)

    def _prio(self, n):
        if n == NMI:
            return -2
        if n == HARDFAULT:
            return -1
        if n == SVCALL:
            return (self.shpr2 >> 24) & 0xC0
        if n == PENDSV:
            return (self.shpr3 >> 16) & 0xC0
        if n == SYSTICK:
            return (self.shpr3 >> 24) & 0xC0
        irq = n - 16
        return (self.ipr[irq // 4] >> (8 * (irq % 4))) & 0xC0

    def _exec_prio(self):
        p = min([256] + [self._prio(n) for n in self.active])
        if self.uc.reg_read(A.UC_ARM_REG_PRIMASK) & 1:
            p = min(p, 0)
        return p

    def _waiting(self):
        """Pending exceptions that are enabled, whatever their priority."""
        return [n for n in self.pending if n < 16 or (self.iser >> (n - 16)) & 1]

    def _to_take(self):
        cur = self._exec_prio()
        c = [n for n in self._waiting() if self._prio(n) < cur]
        return min(c, key=lambda n: (self._prio(n), n)) if c else None

    def _icsr(self):
        w = self._waiting()
        nxt = min(w, key=lambda n: (self._prio(n), n)) if w else 0
        return (self.ipsr | (nxt << 12) | ((any(n >= 16 for n in self.pending)) << 22)
                | ((SYSTICK in self.pending) << 26) | ((PENDSV in self.pending) << 28)
                | ((NMI in self.pending) << 31))

    def _ppb_read(self, uc, off, size, ud):
        a, s = PPB[0] + (off & ~3), self.syst
        if a == SYST_CSR:
            v = s['csr']
            if uc is not None:
                s['csr'] &= ~(1 << 16)           # a read by the core clears COUNTFLAG
            return v
        if a == SYST_RVR:
            return s['rvr']
        if a == SYST_CVR:
            return s['cvr']
        if a in (NVIC_ISER, NVIC_ICER):
            return self.iser
        if a in (NVIC_ISPR, NVIC_ICPR):
            return sum(1 << (n - 16) for n in self.pending if n >= 16)
        if NVIC_IPR <= a < NVIC_IPR + 32:
            return self.ipr[(a - NVIC_IPR) // 4]
        return {CPUID: CPUID_VALUE, ICSR: self._icsr(), VTOR: self.vtor, AIRCR: 0xFA050000,
                SCR: self.scr, CCR: 0x204, SHPR2: self.shpr2, SHPR3: self.shpr3,
                SHCSR: (SVCALL in self.pending) << 15, MPU_TYPE: 0x800,
                DHCSR: 0x00030003 | (self.sleeping << 18) | (self.lockup << 19),
                DEMCR: self.demcr}.get(a, 0)

    def _ppb_write(self, uc, off, size, value, ud):
        a, s = PPB[0] + (off & ~3), self.syst
        if a == SYST_CSR:
            s['csr'] = (s['csr'] & (1 << 16)) | (value & 7)
        elif a == SYST_RVR:
            s['rvr'] = value & 0xFFFFFF
        elif a == SYST_CVR:
            s['cvr'] = 0
            s['csr'] &= ~(1 << 16)
        elif a == NVIC_ISER:
            self.iser |= value
        elif a == NVIC_ICER:
            self.iser &= ~value
        elif a in (NVIC_ISPR, NVIC_ICPR):
            for i in range(32):
                if value >> i & 1:
                    (self.pending.add if a == NVIC_ISPR else self.pending.discard)(16 + i)
        elif NVIC_IPR <= a < NVIC_IPR + 32:
            self.ipr[(a - NVIC_IPR) // 4] = value & 0xC0C0C0C0
        elif a == ICSR:
            for bit, n, add in ((31, NMI, True), (28, PENDSV, True), (27, PENDSV, False),
                                (26, SYSTICK, True), (25, SYSTICK, False)):
                if value >> bit & 1:
                    (self.pending.add if add else self.pending.discard)(n)
        elif a == VTOR:
            self.vtor = value & 0xFFFFFF00
        elif a == SCR:
            self.scr = value & 0x16
        elif a == SHPR2:
            self.shpr2 = value & 0xC0000000
        elif a == SHPR3:
            self.shpr3 = value & 0xC0C00000
        elif a == DEMCR:
            self.demcr = value & 0x01000401
        elif a == AIRCR and (value >> 16) == 0x05FA and value & 4:
            self.reset()                         # SYSRESETREQ

    # ── faults ──────────────────────────────────────────────────────────────

    def _check_align(self, uc, access, addr, size, value, ud):
        if SIO[0] <= addr < SIO[0] + SIO[1]:
            self._sio_access = True
        elif 0x40000000 <= addr < 0x50000000:
            self._apb_access = 'w' if access == U.UC_MEM_WRITE else 'r'
        if size > 1 and addr % size:
            self._fault = f"unaligned {size}-byte access at 0x{addr:08X}"
            if access == U.UC_MEM_WRITE:
                self._undo = (addr, bytes(uc.mem_read(addr, size))) if self._mapped(addr) else None
            uc.emu_stop()

    def _mapped(self, addr):
        return addr < ROM_END or SRAM[0] <= addr < SRAM[0] + SRAM[1]

    def _ram(self, addr, n):
        return SRAM[0] <= addr and addr + n <= SRAM[0] + SRAM[1]

    def _intr(self, uc, intno, ud):
        self._intno = intno
        uc.emu_stop()

    # ── execution ───────────────────────────────────────────────────────────

    def pc(self):
        return self.uc.reg_read(A.UC_ARM_REG_PC)

    def step(self):
        """Run one instruction (or take one exception).  Returns 'ok',
        'bkpt' (stopped on a BKPT, not executed), 'fault' (without the
        exception model: stopped in the fault loop), 'catch' (HardFault
        entered with DEMCR.VC_HARDERR set: a debug halt), 'sleep' (WFI/WFE
        with nothing to wake it) or 'lockup'."""
        uc = self.uc
        exc = self.exc_model()
        if self.lockup:
            return 'lockup'
        if self.ipsr and not exc:
            return 'fault'
        if exc:
            n = self._to_take()
            if n is not None:
                self.sleeping = False
                return self._take(n, self.pc())
            if self.sleeping:
                if not self._waiting():
                    return 'sleep'
                self.sleeping = False            # a pending interrupt wakes it, even masked
        pc = self.pc()
        try:
            hw1 = int.from_bytes(uc.mem_read(pc, 2), 'little')
            hw2 = int.from_bytes(uc.mem_read(pc + 2, 2), 'little') if is_wide(hw1) else 0
        except U.UcError:
            return self._take_fault(f"fetch from 0x{pc:08X}", pc)
        if (hw1 & 0xFF00) == 0xBE00:
            return 'bkpt'
        if not _v6m_defined(hw1, hw2):
            # QEMU's Cortex-M0 still runs some ARMv7-M encodings (Thumb-2
            # data processing, CBZ, IT); the RP2040 does not
            return self._take_fault(f"undefined instruction 0x{hw1:04X}" + (f" 0x{hw2:04X}" if is_wide(hw1) else ''), pc)
        if (hw1 & 0xFF0F) == 0xBF00 and (hw1 >> 4) & 0xF not in (0, 2, 3, 4):
            # YIELD and the unallocated hints: NOPs on ARMv6-M (QEMU raises
            # an exception for YIELD instead)
            uc.reg_write(A.UC_ARM_REG_PC, pc + 2)
            self._tick(1)
            return 'ok'
        if exc:
            if not self.tbit:
                self.fault_reason = "INVSTATE: executing with EPSR.T = 0"
                return self._take(HARDFAULT, pc)
            r = self._special(pc, hw1, hw2)
            if r:
                return r
        taken = self._cond_passes(hw1) if (hw1 & 0xF000) == 0xD000 and (hw1 >> 8) & 0xF < 0xE else None
        ctx = uc.context_save()
        self._fault, self._undo, self._intno = None, None, None
        self._sio_access = False
        self._apb_access = None
        try:
            uc.emu_start(pc | 1, 0xFFFFFFFF, count=1)
        except U.UcError as e:
            self._fault = self._fault or str(e)
        if exc and self._intno == QEMU_EXCEPTION_EXIT and not self._fault:
            # a branch to an EXC_RETURN value (bit 0 is the Thumb bit QEMU took off)
            target = self.pc() | ((uc.reg_read(A.UC_ARM_REG_XPSR) >> 24) & 1)
            self.tbit = 1
            self._tick(self._cost(hw1, hw2, taken))
            if self.ipsr:
                return self._return(target)
            return 'ok'                          # in Thread mode, just a branch: the fetch faults
        if exc and self._fault and 'INSN_INVALID' in self._fault and self.pc() != pc \
                and not (uc.reg_read(A.UC_ARM_REG_XPSR) >> 24) & 1:
            # a BX/BLX/POP to an even address: QEMU stops on the next fetch,
            # but the branch itself completed (the fault comes on that fetch)
            self.tbit = 0
            self._tick(self._cost(hw1, hw2, taken))
            return 'ok'
        if self._fault or self._intno is not None:
            uc.context_restore(ctx)
            if self._undo:
                uc.mem_write(*self._undo)
            return self._take_fault(self._fault or f"exception {self._intno}", pc)
        # Unicorn keeps the stack pointers' bottom bits; on ARMv6-M they always read 0
        for r in (A.UC_ARM_REG_PSP, A.UC_ARM_REG_MSP):
            v = uc.reg_read(r)
            if v & 3:
                uc.reg_write(r, v & ~3)
        self.tbit = (uc.reg_read(A.UC_ARM_REG_XPSR) >> 24) & 1    # a BX/BLX/POP to an even address clears it
        self._tick(self._cost(hw1, hw2, taken))
        return 'ok'

    def _take_fault(self, why, pc):
        if not self.exc_model():
            return self._enter_fault(why)
        self.fault_reason = why
        return self._take(HARDFAULT, pc)

    def _special(self, pc, hw1, hw2):
        """Instructions whose effect depends on state Unicorn does not have
        (Handler mode, the NVIC, sleep).  None: let Unicorn run it."""
        uc = self.uc
        if (hw1 & 0xFF00) == 0xDF00:                       # SVC
            if self._prio(SVCALL) >= self._exec_prio():
                self.fault_reason = "SVC at a priority that cannot take SVCall"
                return self._take(HARDFAULT, pc + 2)
            return self._take(SVCALL, pc + 2)
        if hw1 in (0xBF20, 0xBF30):                        # WFE, WFI
            uc.reg_write(A.UC_ARM_REG_PC, pc + 2)
            self._tick(2)                                  # Table 81: 2, not counting the wait
            if hw1 == 0xBF20 and self.event:
                self.event = False
            elif not self._waiting():
                s = self.syst
                if s['csr'] & 3 == 3 and s['rvr']:
                    self._tick(s['cvr'] or s['rvr'] + 1)   # asleep until SysTick fires
                else:
                    self.sleeping = True
                    return 'sleep'
            return 'ok'
        if hw1 == 0xBF40:                                  # SEV
            self.event = True
            uc.reg_write(A.UC_ARM_REG_PC, pc + 2)
            self._tick(1)
            return 'ok'
        if not self.ipsr:
            return None
        if hw1 == 0xF3EF and (hw2 & 0xF000) == 0x8000:     # MRS in Handler mode
            sysm, rd = hw2 & 0xFF, (hw2 >> 8) & 0xF
            flags = uc.reg_read(A.UC_ARM_REG_XPSR) & 0xF0000000
            v = {0: flags, 1: flags | self.ipsr, 2: flags, 3: flags | self.ipsr, 5: self.ipsr,
                 6: 0, 7: self.ipsr, 20: self.npriv}.get(sysm)
            if v is None:
                return None
            uc.reg_write(_reg(f'r{rd}'), v)
        elif (hw1 & 0xFFF0) == 0xF380 and hw2 == 0x8814:  # MSR CONTROL in Handler mode: SPSEL ignored
            self.npriv = uc.reg_read(_reg(f'r{hw1 & 0xF}')) & 1
        else:
            return None
        uc.reg_write(A.UC_ARM_REG_PC, pc + 4)
        self._tick(3)
        return 'ok'

    def _lock(self, why):
        self.fault_reason = why
        self.lockup = True
        self.uc.reg_write(A.UC_ARM_REG_PC, 0xFFFFFFFE)
        return 'lockup'

    def _take(self, n, ret):
        """Exception entry (ARMv6-M B1.5.6): stack 8 words, LR = EXC_RETURN,
        Handler mode on MSP, PC = the vector."""
        uc = self.uc
        if n == HARDFAULT and self.ipsr in (HARDFAULT, NMI):
            return self._lock(f"a fault in the HardFault handler ({self.fault_reason})")
        thread = self.ipsr == 0
        ctl = uc.reg_read(A.UC_ARM_REG_CONTROL)
        psp = thread and bool(ctl & 2)
        sp = uc.reg_read(A.UC_ARM_REG_PSP if psp else A.UC_ARM_REG_MSP) & ~3
        frame = (sp - 32) & ~7
        realign = 1 if frame != sp - 32 else 0
        x = uc.reg_read(A.UC_ARM_REG_XPSR)
        xpsr = (x & 0xF0000000) | (self.tbit << 24) | self.ipsr | (realign << 9)
        words = [uc.reg_read(_reg(r)) for r in ('r0', 'r1', 'r2', 'r3', 'r12', 'lr')] + [ret & ~1, xpsr]
        if not self._ram(frame, 32):
            return self._lock(f"stacking at 0x{frame:08X} failed")
        uc.mem_write(frame, b''.join(w.to_bytes(4, 'little') for w in words))
        uc.reg_write(A.UC_ARM_REG_PSP if psp else A.UC_ARM_REG_MSP, frame)
        if thread:
            self.npriv = ctl & 1
        uc.reg_write(A.UC_ARM_REG_CONTROL, 0)        # Handler mode: MSP, privileged
        uc.reg_write(A.UC_ARM_REG_LR, 0xFFFFFFF1 if not thread else (0xFFFFFFFD if psp else 0xFFFFFFF9))
        self.pending.discard(n)
        self.active.append(n)
        self.ipsr = n
        va = self.vtor + 4 * n
        if not self._ram(va, 4):
            return self._lock(f"vector fetch at 0x{va:08X} failed")
        vec = int.from_bytes(uc.mem_read(va, 4), 'little')
        uc.reg_write(A.UC_ARM_REG_PC, vec & ~1)
        uc.reg_write(A.UC_ARM_REG_XPSR, (x & 0xF0000000) | ((vec & 1) << 24))
        self.tbit = vec & 1
        self._tick(ENTRY_CYCLES)
        if n == HARDFAULT and self.demcr & VC_HARDERR:
            return 'catch'
        return 'ok'

    def _return(self, exc_return):
        """Exception return (ARMv6-M B1.5.8)."""
        uc = self.uc
        self._tick(EXIT_CYCLES)
        kind = EXC_RETURNS.get(exc_return)
        if kind is None or kind[0] != (len(self.active) == 1):
            self.fault_reason = f"exception return with EXC_RETURN 0x{exc_return:08X}"
            return self._take(HARDFAULT, self.pc())
        to_thread, psp = kind
        r = A.UC_ARM_REG_PSP if psp else A.UC_ARM_REG_MSP
        sp = uc.reg_read(r)
        if not self._ram(sp, 32):
            return self._lock(f"unstacking at 0x{sp:08X} failed")
        w = [int.from_bytes(uc.mem_read(sp + 4 * i, 4), 'little') for i in range(8)]
        self.active.pop()
        self.ipsr = self.active[-1] if self.active else 0
        uc.reg_write(A.UC_ARM_REG_CONTROL, (self.npriv | (2 if psp else 0)) if to_thread else 0)
        uc.reg_write(r, sp + 32 + (4 if w[7] & 0x200 else 0))
        for name, v in zip(('r0', 'r1', 'r2', 'r3', 'r12', 'lr'), w):
            uc.reg_write(_reg(name), v)
        uc.reg_write(A.UC_ARM_REG_XPSR, (w[7] & 0xF0000000) | (w[7] & (1 << 24)))
        uc.reg_write(A.UC_ARM_REG_PC, w[6] & ~1)
        self.tbit = (w[7] >> 24) & 1
        return 'ok'

    def _cond_passes(self, hw1):
        """Would B<cond> (encoding hw1) be taken with the current flags?"""
        f = self.uc.reg_read(A.UC_ARM_REG_XPSR) >> 28
        n, z, c, v = bool(f & 8), bool(f & 4), bool(f & 2), bool(f & 1)
        base = ((z, c, n, v, c and not z, n == v, n == v and not z)[((hw1 >> 8) & 0xF) >> 1])
        return base != bool((hw1 >> 8) & 1)

    def _cost(self, hw1, hw2, taken):
        group, row, rule, n, pc_write = pico_row(hw1, hw2)
        if rule == 'fixed':
            return n
        if rule == 'ldst':
            if self._sio_access:
                return 1                            # the SIO is single-cycle
            if self._apb_access:                    # picosim's (measured) APB cost
                return 2 + (APB_LOAD_EXTRA if self._apb_access == 'r' else APB_STORE_EXTRA)
            return 2
        if rule == 'bcond':
            return 2 if taken else 1
        return 1

    def _enter_fault(self, why):
        self.fault_reason = why
        self.ipsr = 3                                  # HardFault
        self.uc.reg_write(A.UC_ARM_REG_PC, FAULT_LOOP)
        return 'fault'

    def xpsr(self):
        v = self.uc.reg_read(A.UC_ARM_REG_XPSR)
        return (v & 0xF0000000) | (self.tbit << 24) | self.ipsr

    # ── debugger access ─────────────────────────────────────────────────────

    def read_reg(self, n):
        if n == 16:
            return self.xpsr()
        name = REGS[n] if n < len(REGS) else None
        if name is None:
            return 0
        if name == 'control' and self.ipsr and self.exc_model():
            return self.npriv
        return self.uc.reg_read(_reg(name)) & 0xFFFFFFFF

    def write_reg(self, n, v):
        if n >= len(REGS):
            return
        if n == 16:
            self.ipsr = v & 0x3F
            self.tbit = 1
            self.uc.reg_write(A.UC_ARM_REG_XPSR, (v & ~0x3F) | 0x01000000)
            return
        if n == 15:
            self.lockup = self.sleeping = False      # a new PC from the debugger leaves lockup
        self.uc.reg_write(_reg(REGS[n]), v & 0xFFFFFFFF)

    def read_mem(self, addr, n):
        if self._plain(addr, n):
            return bytes(self.uc.mem_read(addr, n))
        return bytes(self._debug_byte(a) for a in range(addr, addr + n))

    def _plain(self, addr, n):
        return (addr + n <= ROM_END) or (SRAM[0] <= addr and addr + n <= SRAM[0] + SRAM[1])

    def _debug_byte(self, a):
        if self._plain(a, 1):
            return self.uc.mem_read(a, 1)[0]
        w = a & ~3
        if any(b <= w < b + s for b, s in PERIPH.items()):
            v = self._periph_read(w, 4)
        elif SYSINFO[0] <= w < SYSINFO[0] + SYSINFO[1]:
            v = self._sysinfo_read(None, w - SYSINFO[0], 4, None)
        elif NONSTRIPED[0] <= w < NONSTRIPED[0] + NONSTRIPED[1]:
            v = self._ns_read(None, w - NONSTRIPED[0], 4, None)
        elif SIO[0] <= w < SIO[0] + SIO[1]:
            raise ValueError(a)      # the SIO is on the core's I/O port: not reachable by a debugger
        elif PPB[0] <= w < PPB[0] + PPB[1]:
            v = self._ppb_read(None, w - PPB[0], 4, None)
        else:
            raise ValueError(a)
        return (v >> (8 * (a & 3))) & 0xFF

    def write_mem(self, addr, data):
        if self._plain(addr, len(data)):
            self.uc.mem_write(addr, bytes(data))
            # Unicorn keeps translated code; new code there must not run the old
            self.uc.ctl_remove_cache(addr, addr + len(data))
            return
        if addr % 4 or len(data) % 4:
            raise ValueError(addr)
        for i in range(0, len(data), 4):
            a, v = addr + i, int.from_bytes(data[i:i + 4], 'little')
            if any(b <= a < b + s for b, s in PERIPH.items()):
                self._periph_write(a, 4, v)
            elif SIO[0] <= a < SIO[0] + SIO[1]:
                raise ValueError(a)  # as on the RP2040: the debugger cannot reach the SIO
            elif PPB[0] <= a < PPB[0] + PPB[1]:
                self._ppb_write(None, a - PPB[0], 4, v, None)
            elif NONSTRIPED[0] <= a < NONSTRIPED[0] + NONSTRIPED[1]:
                self._ns_write(None, a - NONSTRIPED[0], 4, v, None)
            else:
                raise ValueError(a)


class StandIn:
    """A GDB server for one client at a time, on 127.0.0.1:port."""

    def __init__(self, port=0):
        if U is None:
            raise RuntimeError("the stand-in needs Unicorn: pip install unicorn")
        self.chip = Chip()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', port))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.bps = set()
        self.polling = True
        self.held_stop = None
        self.ack = True
        self._stop = False

    def start(self):
        threading.Thread(target=self.serve, daemon=True).start()
        return self

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    def serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.ack = True
            try:
                self._session(conn)
            except (OSError, ConnectionError):
                pass
            finally:
                conn.close()

    def _session(self, conn):
        self.buf = b''
        self.conn = conn
        while True:
            data = conn.recv(65536)
            if not data:
                return
            self.buf += data
            try:
                self._drain()
            except HangUp:
                return                     # like OpenOCD: drop the connection

    def _drain(self, running=False):
        """Answer every whole packet in self.buf.  While the core runs (as
        OpenOCD does, commands are answered then too) a ^C stops the drain:
        returns True."""
        while True:
            self.buf = self.buf.lstrip(b'+-')
            if self.buf.startswith(b'\x03'):
                self.buf = self.buf[1:]
                if running:
                    return True
                continue
            i = self.buf.find(b'$')
            j = self.buf.find(b'#', i)
            if i < 0 or j < 0 or len(self.buf) < j + 3:
                return False
            pkt = self.buf[i + 1:j].decode('latin-1')
            self.buf = self.buf[j + 3:]
            if self.ack:
                self.conn.sendall(b'+')
            try:
                reply = self.handle(pkt)
            except (HangUp, ConnectionError):
                raise
            except Exception:          # a bad request: answer it, keep serving
                reply = 'E02'
            if reply is not None:
                if pkt.startswith('qRcmd') or pkt == 'c':
                    self._send('O')      # OpenOCD's keep-alive, which a client must skip
                self._send(reply)
            if pkt == 'QStartNoAckMode':
                self.ack = False

    def _send(self, text):
        b = text.encode('latin-1')
        self.conn.sendall(b'$' + b + b'#%02x' % (sum(b) & 0xFF))

    def _interrupted(self):
        """While the core runs: answer what the client sent.  True on a ^C."""
        self.conn.setblocking(False)
        try:
            data = self.conn.recv(65536)
        except (BlockingIOError, OSError):
            data = None
        finally:
            self.conn.setblocking(True)
        if data == b'':
            raise ConnectionError              # the client hung up
        if data:
            self.buf += data
        return self._drain(running=True)

    def _halted(self, reply):
        """OpenOCD learns of a halt only by polling the core: with its
        polling off ('poll off'), the stop reply waits until polling is
        back on or the client interrupts.  A 'poll on' polls at once, so
        its stop reply goes out before the command's OK (_monitor)."""
        if self.polling:
            return reply
        self.held_stop = reply
        while self.held_stop and not self._stop:
            if self._interrupted():
                break
            time.sleep(0.005)
        reply, self.held_stop = self.held_stop, None
        return reply

    def handle(self, p):
        chip = self.chip
        if p.startswith('qSupported'):
            return 'PacketSize=4000;qXfer:features:read+;QStartNoAckMode+;hwbreak+'
        if p == 'QStartNoAckMode':
            return 'OK'
        if p.startswith('qXfer:features:read:target.xml:'):
            off, n = (int(x, 16) for x in p.rsplit(':', 1)[1].split(','))
            part = TARGET_XML[off:off + n]
            return ('l' if off + n >= len(TARGET_XML) else 'm') + part
        if p == '?':
            return 'S05'
        if p == 'g':
            return ''.join(chip.read_reg(i).to_bytes(4, 'little').hex() for i in range(17))
        if p.startswith('p'):
            return chip.read_reg(int(p[1:], 16)).to_bytes(4, 'little').hex()
        if p.startswith('P'):
            n, v = p[1:].split('=')
            n = int(n, 16)
            name = REGS[n] if n < len(REGS) else ''
            if len(v) * 4 != ((BITS.get(name, 32) + 7) // 8) * 8:
                raise HangUp(f"gdb sent {len(v) * 4} bits for a {BITS.get(name, 32)}-bit register ({name})")
            chip.write_reg(n, int.from_bytes(bytes.fromhex(v), 'little'))
            return 'OK'
        if p.startswith('m'):
            a, n = (int(x, 16) for x in p[1:].split(','))
            try:
                return chip.read_mem(a, n).hex()
            except (ValueError, U.UcError):
                return 'E01'
        if p.startswith('M'):
            head, data = p[1:].split(':')
            a, n = (int(x, 16) for x in head.split(','))
            try:
                chip.write_mem(a, bytes.fromhex(data))
            except (ValueError, U.UcError):
                return 'E01'
            return 'OK'
        if p == 's':
            chip.step()
            return 'S05'
        if p == 'c':
            return self._cont()
        if p[:2] in ('Z1', 'z1', 'Z0', 'z0'):
            a = int(p.split(',')[1], 16)
            (self.bps.add if p[0] == 'Z' else self.bps.discard)(a)
            return 'OK'
        if p.startswith('qRcmd,'):
            return self._monitor(bytes.fromhex(p[6:]).decode())
        if p in ('D', 'k'):
            return 'OK'
        return ''

    def _monitor(self, cmd):
        """The OpenOCD commands the discovery runner sends.  The reply is
        console text (hex), as OpenOCD's is."""
        chip, words = self.chip, cmd.split()
        said = ''
        if words and words[0] == 'reset':
            chip.reset()
        elif words[:2] == ['cortex_m', 'vector_catch']:
            if words[2:] == ['none']:
                chip.demcr &= ~0x401
            elif words[2:] == ['all']:
                chip.demcr |= 0x401
            said = f"hard_err: {'catch' if chip.demcr & VC_HARDERR else 'ignore'}\n"
        elif words[:1] == ['poll']:
            if words[1:]:
                self.polling = words[1] == 'on'
                if self.polling and self.held_stop:
                    self._send(self.held_stop)       # as OpenOCD: polled before the OK
                    self.held_stop = None
            else:
                said = f"background polling: {'on' if self.polling else 'off'}\n"
        elif words == ['version']:
            said = "Open On-Chip Debugger 0.12.0 (picosim stand-in)\n"
        elif words[:2] == ['adapter', 'speed']:
            said = "adapter speed: 5000 kHz\n"
        return said.encode().hex() if said else 'OK'

    def _cont(self):
        chip = self.chip
        first = True
        n = 0
        while True:
            if not first and chip.pc() in self.bps:
                return self._halted('S05')
            first = False
            r = chip.step()
            if r in ('lockup', 'sleep'):
                return self._stuck()
            if r != 'ok':
                return self._halted('S05')
            n += 1
            if n % 4096 == 0 and self._interrupted():
                return 'S02'

    def _stuck(self):
        """Locked up or asleep for good: the core runs on (doing nothing)
        until the debugger interrupts it, as a real one would."""
        while not self._stop:
            if self._interrupted():
                return 'S02'
            time.sleep(0.005)
        raise ConnectionError
