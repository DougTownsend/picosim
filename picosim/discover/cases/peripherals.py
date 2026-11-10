"""
Campaign G: how the peripherals picosim models (or will model) behave.

Now (decision D6: core, GPIO, SIO and SysTick), in the reset-halt state
with IO_BANK0 and PADS_BANK0 released, run through the agent:

  sio         the divider (edge cases with divide by 0 for both signs,
              seeded random cases, which alias decides signedness, DIRTY
              and READY, writing a result register), the interpolators
              (seeded random configurations and the datasheet's own
              examples, against interp_model.Interp; BASE_1AND0, ACCUMx_ADD
              and the CTRL write masks), FIFO_ST with core 1 parked in the
              boot ROM (read only: the FIFO is never written), and every
              spinlock (claimed by a read, SPINLOCK_ST, released by a write,
              and only if this case claimed it)
  div-timing  the result timing: start a division, wait k = 0..10 NOPs,
              then read CSR, QUOTIENT or REMAINDER first
  gpio        with no wiring: every pin's level with a pull-up, pull-down,
              none and both (bus keeper), seen in GPIO_IN; INOVER (invert,
              force low/high) seen in GPIO_IN, which drives nothing
  gpio-sync   a pull change seen in GPIO_IN after k NOPs (the pull's own
  gpio-bypass rise time plus the input synchroniser), with SYSCFG
              PROC_IN_SYNC_BYPASS off and on for that pin: the difference is
              the synchroniser
  gpio-drive  only for --pins: output -> GPIO_IN latency through the pin's
              own pad, and OEOVER/OUTOVER forcing the pad
  systick     COUNTFLAG (set by a wrap, cleared by a core read and by a CVR
              write), RVR = 0, small reloads (the period), the write masks,
              CALIB and the external clock source

and, through the debugger: each pin's IO_BANK0 STATUS and INTR (edge and
level bits) as pulls change, INOVER and IRQOVER seen in STATUS/INTR, and
whether a debugger read of SYST_CSR clears COUNTFLAG.

GPIO pins: GP23, GP24, GP25 and GP29 (the Pico W's wireless chip) are never
written; no pin is made an output unless it is in --pins.  Registers whose
read has a side effect (QUOTIENT, POP, spinlocks, SYST_CSR, TIMELR, UART
DR) are read here on purpose: those cases say so (side_effect_ok).

TIMER and UART need clk_peri and the watchdog tick, which are off after
`reset halt`; campaign F calls flashed_part() while its flashed firmware is
halted (see there).

Output: peripherals/sio.json, peripherals/gpio.json, peripherals/systick.json
(and from flashed_part: peripherals/timer.json, peripherals/uart.json,
peripherals/gpio_flashed.json).  Each fact that disagrees with
interp_model, the datasheet or the SDK headers counts as a difference;
what picosim does not model is listed as such.
"""

import random
import time

from .. import formats as F
from .. import safety
from .. import interp_model as M
from ..campaign import release, IO_PADS, R_make

NAME, TITLE, AGENT_BASED = 'peripherals', 'Peripherals: SIO divider, interpolators, spinlocks, GPIO, SysTick', True
SEED = 0x6A11
LAY = F.LAYOUTS['hi']
M32 = 0xFFFFFFFF

SIO = 0xD0000000
GPIO_IN = 0x004
INTERP_BASE = (SIO + M.INTERP0, SIO + M.INTERP1)
SPIN = SIO + M.SPINLOCK0
SYST = 0xE000E010                          # CSR; RVR +4, CVR +8, CALIB +0xC
IO_BANK0, PADS_BANK0 = 0x40014000, 0x4001C000
SYSCFG_BYPASS = 0x4000400C                 # SYSCFG PROC_IN_SYNC_BYPASS (GPIO0-29)
RESET_DONE = 0x4000C008
SYSCFG_RESET_BIT = 18
PAD_DOWN, PAD_UP, PAD_NONE, PAD_KEEP = 0x56, 0x5A, 0x52, 0x5E   # IE, 4 mA, Schmitt, and the pulls
SURVEY_PINS = tuple(p for p in range(30) if p not in safety.PICO_W_PINS)
TEST_PINS = (2, 3)                         # away from the saved button (GP19) and LED (GP21)
SYNC_PIN = 2

N_DIV_RANDOM = 200
N_INTERP_RANDOM = 150                      # per interpolator
DIV_WAIT = 10                              # NOPs before reading results in the value cases
DIV_K = range(0, 11)
SYNC_K = range(0, 25)
SYNC_REPEATS = 2
SETTLE = 100                               # delay loop iterations (3 cycles each) for a pull to settle
SENTINEL = 0x5E5E5E5E
DIV_EDGE = (0, 1, M32, 0x80000000, 0x7FFFFFFF, 7, 0xFFFFFFF9, 2)    # 0, 1, -1, INT_MIN, INT_MAX, 7, -7, 2


def pad_addr(pin):
    return PADS_BANK0 + 4 + 4 * pin


def ctrl_addr(pin, alias=0):
    return IO_BANK0 + alias * 0x1000 + 8 * pin + 4


def status_addr(pin):
    return IO_BANK0 + 8 * pin


def intr_addr(pin):
    return IO_BANK0 + 0xF0 + 4 * (pin // 8)


def _x(v, digits=8):
    return None if v is None else f"0x{v & M32:0{digits}X}"


# ── building case code ─────────────────────────────────────────────────────

_SIDE = None


def _side():
    global _SIDE
    if _SIDE is None:
        _SIDE = set(safety.read_side_effects())
    return _SIDE


class Code:
    """Thumb code built an instruction at a time.  Registers start with the
    case's values, so every load and store knows its address: they are
    declared as the case's reads and writes for the safety checks, and a
    case that reads a register with a side effect says so.  With
    declare=False (flashed_part's routines) an access through a register
    whose value is not known is allowed and not declared."""

    def __init__(self, regs=(), declare=True):
        self.init = [v & M32 for v in regs]
        self.known = {i: v for i, v in enumerate(self.init)}
        self.declare = declare
        self.hw, self.reads, self.writes = [], [], []
        self.labels, self.fix = {}, []

    def emit(self, *hws):
        self.hw.extend(h & 0xFFFF for h in hws)
        return self

    def _addr(self, rn, off):
        v = self.known.get(rn)
        if v is None:
            if self.declare:
                raise ValueError(f"r{rn} holds no known value: the access could not be declared")
            return None
        return (v + off) & M32

    @staticmethod
    def _ldst(op, rt, rn, off):
        if off % 4 or not 0 <= off < 128 or rt > 7 or rn > 7:
            raise ValueError(f"no 16-bit form for r{rt}, [r{rn}, #{off}]")
        return op | (off // 4) << 6 | rn << 3 | rt

    def ldr(self, rt, rn, off=0):
        a = self._addr(rn, off)
        if a is not None:
            self.reads.append(a)
        self.known.pop(rt, None)
        return self.emit(self._ldst(0x6800, rt, rn, off))

    def str(self, rt, rn, off=0):
        a = self._addr(rn, off)
        if a is not None:
            self.writes.append((a, 4, self.known.get(rt, M32)))
        return self.emit(self._ldst(0x6000, rt, rn, off))

    def nop(self, n=1):
        return self.emit(*[0xBF00] * n)

    def movs(self, rd, imm):
        self.known[rd] = imm & 0xFF
        return self.emit(0x2000 | rd << 8 | (imm & 0xFF))

    def mov(self, rd, rm):
        if rm in self.known:
            self.known[rd] = self.known[rm]
        else:
            self.known.pop(rd, None)
        return self.emit(0x4600 | (rd & 8) << 4 | rm << 3 | (rd & 7))

    def _alu(self, rd, op, value):
        if value is None:
            self.known.pop(rd, None)
        else:
            self.known[rd] = value & M32
        return self.emit(op)

    def adds_i(self, rd, imm):
        k = self.known.get(rd)
        return self._alu(rd, 0x3000 | rd << 8 | imm, None if k is None else k + imm)

    def subs_i(self, rd, imm):
        k = self.known.get(rd)
        return self._alu(rd, 0x3800 | rd << 8 | imm, None if k is None else k - imm)

    def adds_r(self, rd, rn, rm):
        return self._alu(rd, 0x1800 | rm << 6 | rn << 3 | rd, None)

    def subs_r(self, rd, rn, rm):
        return self._alu(rd, 0x1A00 | rm << 6 | rn << 3 | rd, None)

    def lsls(self, rd, rm, imm):
        k = self.known.get(rm)
        return self._alu(rd, imm << 6 | rm << 3 | rd, None if k is None else k << imm)

    def orrs(self, rd, rm):
        return self._alu(rd, 0x4300 | rm << 3 | rd, None)

    def tst(self, rn, rm):
        return self.emit(0x4200 | rm << 3 | rn)

    def cmp_i(self, rn, imm):
        return self.emit(0x2800 | rn << 8 | imm)

    def cmp_r(self, rn, rm):
        return self.emit(0x4280 | rm << 3 | rn)

    def label(self, name):
        self.labels[name] = len(self.hw)
        return self

    def _branch(self, kind, label, cond=0):
        self.fix.append((len(self.hw), kind, label, cond))
        return self.emit(0)

    def beq(self, label):
        return self._branch('cond', label, 0)

    def bne(self, label):
        return self._branch('cond', label, 1)

    def b(self, label):
        return self._branch('b', label)

    def delay(self, reg, n):
        """About 3n cycles: movs reg, #n; 1: subs reg, #1; bne 1b."""
        name = f"_delay{len(self.hw)}"
        return self.movs(reg, n).label(name).subs_i(reg, 1).bne(name)

    def ldr_lit(self, rt, value):
        self.known[rt] = value & M32
        self.fix.append((len(self.hw), 'lit', rt, value & M32))
        return self.emit(0)

    def bkpt(self, n):
        return self.emit(0xBE00 | n)

    def wfi(self):
        return self.emit(0xBF30)

    def bx(self, rm):
        return self.emit(0x4700 | rm << 3)

    def assemble(self):
        """The code (at a 4-byte aligned address), then any literal pool."""
        hw = list(self.hw)
        n = len(hw)
        pool = []
        for i, kind, a, b in self.fix:
            if kind == 'lit':
                if b not in pool:
                    pool.append(b)
                p = (2 * n + 3) // 4 * 4 + 4 * pool.index(b)
                off = p - ((2 * i + 4) & ~3)
                if not 0 <= off < 1024:
                    raise ValueError("literal out of reach")
                hw[i] = 0x4800 | a << 8 | off // 4
                continue
            d = 2 * self.labels[a] - (2 * i + 4)
            if kind == 'cond':
                if not -256 <= d < 256:
                    raise ValueError("conditional branch out of reach")
                hw[i] = 0xD000 | b << 8 | (d // 2) & 0xFF
            else:
                hw[i] = 0xE000 | (d // 2) & 0x7FF
        out = b''.join(x.to_bytes(2, 'little') for x in hw)
        if pool:
            out += bytes(-len(out) % 4) + b''.join(v.to_bytes(4, 'little') for v in pool)
        return out

    def case(self, id, **meta):
        if any(k == 'lit' for _, k, _, _ in self.fix):
            raise ValueError("an agent case cannot have a literal pool (it runs into the sled)")
        side = _side()
        return R_make(id, self.assemble(), regs=self.init, reads=list(self.reads), writes=list(self.writes),
                      side_effect_ok=any(a & ~3 in side for a in self.reads), **meta)


def reg(r, n):
    """Register n of a result as the case left it (frame or R4-R11)."""
    if n < 4:
        return r[f'f_r{n}']
    if n == 12:
        return r['f_r12']
    if n == 14:
        return r['f_lr']
    return r[f'r{n}']


def _rng(tag):
    return random.Random(f"G|{tag}|{SEED}")


# ── SIO: the divider ───────────────────────────────────────────────────────

def _div_value_case(n, p, q, dend_signed, dsor_signed, why):
    c = Code([p, q, SIO])
    c.str(0, 2, M.DIV_SDIVIDEND if dend_signed else M.DIV_UDIVIDEND)
    c.str(1, 2, M.DIV_SDIVISOR if dsor_signed else M.DIV_UDIVISOR)
    c.nop(DIV_WAIT)
    c.ldr(4, 2, M.DIV_CSR).ldr(3, 2, M.DIV_REMAINDER).ldr(0, 2, M.DIV_QUOTIENT).ldr(5, 2, M.DIV_CSR)
    c.ldr(6, 2, M.DIV_UDIVIDEND).ldr(7, 2, M.DIV_UDIVISOR)
    return c.case(n, part='div', p=p, q=q, signed=dsor_signed, dend_signed=dend_signed, why=why)


def div_cases(start=0):
    n = start
    out = []
    for signed in (False, True):
        for p in DIV_EDGE:
            for q in DIV_EDGE:
                out.append(_div_value_case(n, p, q, signed, signed, 'edge'))
                n += 1
    rng = _rng('div')
    for _ in range(N_DIV_RANDOM):
        p = rng.getrandbits(32) if rng.random() < 0.7 else rng.getrandbits(rng.randint(1, 16))
        q = rng.choice([rng.getrandbits(32), rng.getrandbits(rng.randint(1, 16)), (-rng.getrandbits(8)) & M32,
                        rng.getrandbits(rng.randint(1, 31))])
        s = rng.random() < 0.5
        out.append(_div_value_case(n, p, q, s, s, 'random'))
        n += 1
    for dend_s, dsor_s in ((True, False), (False, True)):        # which alias decides signedness?
        for p, q in ((0xFFFFFF9C, 7), (100, 0xFFFFFFF9)):           # -100 / 7, 100 / -7
            out.append(_div_value_case(n, p, q, dend_s, dsor_s, 'mixed aliases'))
            n += 1
    # DIRTY and READY
    c = Code([100, 7, SIO, 0x12345678])
    c.ldr(4, 2, M.DIV_QUOTIENT)                      # whatever was there: DIRTY now clear
    c.ldr(4, 2, M.DIV_CSR).mov(8, 4)                 # r8: ready, clean (1)
    c.str(0, 2, M.DIV_UDIVIDEND)                     # one operand written: a calculation starts
    c.ldr(4, 2, M.DIV_CSR).mov(9, 4)                 # r9: dirty, busy (2)
    c.nop(DIV_WAIT)
    c.ldr(4, 2, M.DIV_CSR).mov(10, 4)                # r10: dirty, ready (3)
    c.ldr(4, 2, M.DIV_REMAINDER)
    c.ldr(4, 2, M.DIV_CSR).mov(11, 4)                # r11: REMAINDER read: still dirty (3)
    c.ldr(4, 2, M.DIV_QUOTIENT)
    c.ldr(5, 2, M.DIV_CSR)                           # r5: QUOTIENT read: clean (1)
    c.ldr(6, 2, M.DIV_QUOTIENT)
    c.ldr(7, 2, M.DIV_CSR)                           # r7: a second QUOTIENT read: still clean (1)
    out.append(c.case(n, part='div-dirty', check='flags'))
    n += 1
    c = Code([100, 7, SIO, 0x12345678])
    c.str(0, 2, M.DIV_UDIVIDEND).str(1, 2, M.DIV_UDIVISOR).nop(2)
    c.str(3, 2, M.DIV_QUOTIENT)                      # a result written mid-calculation: it stops
    c.ldr(4, 2, M.DIV_CSR)                           # r4: ready and dirty (3)
    c.ldr(5, 2, M.DIV_QUOTIENT)                      # r5: what was written
    c.ldr(6, 2, M.DIV_CSR)                           # r6: clean (1)
    c.nop(DIV_WAIT)
    c.ldr(0, 2, M.DIV_QUOTIENT)                      # r0: still what was written (nothing finished later)
    c.ldr(7, 2, M.DIV_REMAINDER)                     # r7: the remainder register, untouched (no prediction)
    out.append(c.case(n, part='div-dirty', check='result write'))
    return out


DIV_TIMING_PAIRS = ((0x7FFFFFFF, 7, True), (0xFFF0BDC0, 7, True), (0xFFFFFFFF, 0x10, False))   # INT_MAX/7, -1000000/7


def div_timing_cases():
    """Start a division, wait k NOPs, read CSR, QUOTIENT or REMAINDER first
    (the result registers first hold a sentinel, written just before)."""
    out, n = [], 0
    for k in DIV_K:
        for first in ('csr', 'quotient', 'remainder'):
            for i, (p, q, s) in enumerate(DIV_TIMING_PAIRS):
                c = Code([p, q, SIO, SENTINEL])
                c.str(3, 2, M.DIV_QUOTIENT).str(3, 2, M.DIV_REMAINDER)
                c.str(0, 2, M.DIV_SDIVIDEND if s else M.DIV_UDIVIDEND)
                c.str(1, 2, M.DIV_SDIVISOR if s else M.DIV_UDIVISOR)
                c.nop(k)
                order = {'csr': (M.DIV_CSR, M.DIV_REMAINDER, M.DIV_QUOTIENT),
                         'quotient': (M.DIV_QUOTIENT, M.DIV_REMAINDER, M.DIV_CSR),
                         'remainder': (M.DIV_REMAINDER, M.DIV_QUOTIENT, M.DIV_CSR)}[first]
                for rt, off in zip((4, 5, 6), order):
                    c.ldr(rt, 2, off)
                c.nop(DIV_WAIT)
                c.ldr(7, 2, M.DIV_REMAINDER).ldr(0, 2, M.DIV_QUOTIENT)       # the final results
                out.append(c.case(n, part='div-timing', k=k, first=first, pair=i, p=p, q=q, signed=s))
                n += 1
    return out


# ── SIO: FIFO_ST and the spinlocks ─────────────────────────────────────────

def fifo_spin_cases(start):
    n = start
    c = Code([SIO])
    c.ldr(4, 0, M.FIFO_ST).nop(4).ldr(5, 0, M.FIFO_ST)
    out = [c.case(n, part='fifo')]
    n += 1
    for lock in range(32):
        c = Code([SPIN + 4 * lock, SIO, 0])
        c.ldr(3, 1, M.SPINLOCK_ST)                     # r3: before
        c.ldr(4, 0)                                    # r4: claim (1 << lock)
        c.ldr(5, 1, M.SPINLOCK_ST)                     # r5: claimed
        c.ldr(6, 0)                                    # r6: claim again (0)
        c.cmp_i(4, 0).beq('skip')
        c.str(2, 0)                                    # release, only if this case claimed it
        c.label('skip')
        c.ldr(7, 1, M.SPINLOCK_ST)                     # r7: after
        out.append(c.case(n, part='spinlock', lock=lock))
        n += 1
    return out


# ── SIO: the interpolators ─────────────────────────────────────────────────

POPS = (M.POP_LANE0, M.POP_LANE1, M.POP_FULL)


def _interp_case(n, num, cfg, pop, **meta):
    """Configure interpolator `num` (cfg: ctrl0, ctrl1, accum0, accum1,
    base0, base1, base2), read everything, POP once, read again."""
    base = INTERP_BASE[num]
    c = Code(list(cfg) + [base])
    for rt, off in zip(range(7), (M.CTRL_LANE0, M.CTRL_LANE1, M.ACCUM0, M.ACCUM1, M.BASE0, M.BASE1, M.BASE2)):
        c.str(rt, 7, off)
    c.ldr(0, 7, M.PEEK_LANE0).ldr(1, 7, M.PEEK_LANE1).ldr(2, 7, M.PEEK_FULL).ldr(3, 7, M.CTRL_LANE0)
    c.ldr(4, 7, M.ACCUM0_ADD).mov(8, 4)
    c.ldr(4, 7, M.ACCUM1_ADD).mov(9, 4)
    c.ldr(4, 7, M.CTRL_LANE1).mov(10, 4)
    c.ldr(4, 7, pop).mov(11, 4)
    c.ldr(4, 7, M.ACCUM0).mov(12, 4)
    c.ldr(4, 7, M.ACCUM1).mov(14, 4)
    c.ldr(5, 7, M.PEEK_LANE0).ldr(6, 7, M.PEEK_LANE1)
    return c.case(n, part='interp', interp=num, cfg=list(cfg), pop=pop, **meta)


INTERP_FIELDS = (('peek0', 0), ('peek1', 1), ('peek_full', 2), ('ctrl0', 3), ('add0', 8), ('add1', 9),
                 ('ctrl1', 10), ('pop', 11), ('accum0_after', 12), ('accum1_after', 14),
                 ('peek0_after', 5), ('peek1_after', 6))


def interp_predict(num, cfg, pop, variant=None):
    """What _interp_case should read, from the model."""
    m = M.Interp(num, variant)
    for off, v in zip((M.CTRL_LANE0, M.CTRL_LANE1, M.ACCUM0, M.ACCUM1, M.BASE0, M.BASE1, M.BASE2), cfg):
        m.write(off, v)
    out = {}
    for name, off in (('peek0', M.PEEK_LANE0), ('peek1', M.PEEK_LANE1), ('peek_full', M.PEEK_FULL),
                      ('ctrl0', M.CTRL_LANE0), ('add0', M.ACCUM0_ADD), ('add1', M.ACCUM1_ADD),
                      ('ctrl1', M.CTRL_LANE1), ('pop', pop), ('accum0_after', M.ACCUM0),
                      ('accum1_after', M.ACCUM1), ('peek0_after', M.PEEK_LANE0), ('peek1_after', M.PEEK_LANE1)):
        out[name] = m.read(off)
    return out


def _random_ctrl(rng, num, lane):
    lsb = rng.randrange(32)
    msb = rng.randrange(lsb, 32)
    return M.ctrl(shift=rng.randrange(32) if rng.random() < 0.7 else 0, lsb=lsb, msb=msb,
                  signed=rng.random() < 0.4, cross_input=rng.random() < 0.2, cross_result=rng.random() < 0.2,
                  add_raw=rng.random() < 0.2, force_msb=rng.randrange(4) if rng.random() < 0.2 else 0,
                  blend=num == 0 and lane == 0 and rng.random() < 0.15,
                  clamp=num == 1 and lane == 0 and rng.random() < 0.2)


def _random_word(rng):
    r = rng.random()
    if r < 0.5:
        return rng.getrandbits(32)
    if r < 0.8:
        return rng.getrandbits(rng.randint(1, 16))
    return (-rng.getrandbits(rng.randint(1, 16))) & M32


# The datasheet's examples (RP2040 datasheet §2.3.1.6, pico-examples interp):
# (name, interp, cfg, pop, {field: published value})
def datasheet_examples():
    ex = []
    d = M.ctrl()
    ex.append(('times table', 0, (d, d, 0, 0, 9, 0, 0), M.POP_LANE0, {'pop': 9, 'peek0_after': 18}))
    for i in range(8):
        want = [0xD, 0xC0, 0xB00, 0xA000, 0x40000, 0x300000, 0x2000000, 0x10000000][i]
        ex.append((f"moving mask nibble {i}", 0, (M.ctrl(lsb=4 * i, msb=4 * i + 3), d, 0x1234ABCD, 0, 0, 0, 0),
                   M.POP_LANE0, {'add0': want}))
        want = [0xFFFFFFFD, 0xFFFFFFC0, 0xFFFFFB00, 0xFFFFA000, 0x40000, 0x300000, 0x2000000, 0x10000000][i]
        ex.append((f"moving mask signed nibble {i}", 0,
                   (M.ctrl(lsb=4 * i, msb=4 * i + 3, signed=True), d, 0x1234ABCD, 0, 0, 0, 0), M.POP_LANE0,
                   {'add0': want}))
    x = M.ctrl(cross_result=True)
    ex.append(('cross lanes', 0, (x, x, 123, 456, 1, 0, 0), M.POP_LANE1,
               {'peek0': 124, 'pop': 456, 'peek0_after': 457, 'peek1_after': 124}))
    for i, want in enumerate((500, 582, 666, 748, 832, 914, 998)):
        ex.append((f"simple blend 1, step {i}", 0, (M.ctrl(blend=True), d, 0, 255 * i // 6, 500, 1000, 0),
                   M.POP_LANE1, {'peek1': want}))
    for i, want in enumerate((-1000, -672, -336, -8, 328, 656, 992)):
        ex.append((f"simple blend 2 signed, step {i}", 0,
                   (M.ctrl(blend=True), M.ctrl(signed=True), 0, 255 * i // 6, (-1000) & M32, 1000, 0),
                   M.POP_LANE1, {'peek1': want & M32}))
    for i, want in enumerate((0xFFFFFC18, 0xD5FFFD60, 0xAAFFFEB0, 0x80FFFFF8, 0x56000148, 0x2C000290, 0x010003E0)):
        ex.append((f"simple blend 2 unsigned, step {i}", 0,
                   (M.ctrl(blend=True), d, 0, 255 * i // 6, (-1000) & M32, 1000, 0), M.POP_LANE1, {'peek1': want}))
    for a, want in zip(range(-1024, 1025, 256), (0, 0, 0, 0, 0, 64, 128, 192, 255)):
        ex.append((f"clamp {a}", 1, (M.ctrl(clamp=True, shift=2, lsb=0, msb=29, signed=True), d, a & M32, 0, 0, 255, 0),
                   M.POP_LANE0, {'peek0': want}))
    return ex


def interp_cases(start):
    out, n = [], start
    for name, num, cfg, pop, want in datasheet_examples():
        out.append(_interp_case(n, num, cfg, pop, example=name, published=want))
        n += 1
    for num in (0, 1):
        rng = _rng(f"interp{num}")
        for _ in range(N_INTERP_RANDOM):
            c0, c1 = _random_ctrl(rng, num, 0), _random_ctrl(rng, num, 1)
            words = [_random_word(rng) for _ in range(5)]
            if c0 & M.CLAMP and rng.random() < 0.8:          # mostly a sensible range: BASE0 <= BASE1
                lo, hi = sorted(words[2:4], key=M.s32 if c0 & M.SIGNED else int)
                words[2:4] = [lo, hi]
            out.append(_interp_case(n, num, [c0, c1] + words, rng.choice(POPS)))
            n += 1
    # BASE_1AND0 (sign extension per lane), ACCUMx_ADD (how wide is the add?), CTRL write masks
    for num in (0, 1):
        base = INTERP_BASE[num]
        for s0 in (False, True):
            for s1 in (False, True):
                for v in (0x80017FFF, 0x7FFF8001):
                    c = Code([M.ctrl(signed=s0), M.ctrl(signed=s1), v, 0, 0, 0, 0, base])
                    c.str(0, 7, M.CTRL_LANE0).str(1, 7, M.CTRL_LANE1).str(2, 7, M.BASE_1AND0)
                    c.ldr(4, 7, M.BASE0).ldr(5, 7, M.BASE1).ldr(6, 7, M.BASE_1AND0)
                    out.append(c.case(n, part='interp-base10', interp=num, signed=(s0, s1), value=v))
                    n += 1
        for v in (0xFFFFFFFF, 0x01000001, 0x00800000, 5):
            c = Code([M.ctrl(), M.ctrl(), v, 0x10, 0, 0, 0, base])
            c.str(0, 7, M.CTRL_LANE0).str(1, 7, M.CTRL_LANE1).str(3, 7, M.ACCUM0).str(3, 7, M.ACCUM1)
            c.str(2, 7, M.ACCUM0_ADD).str(2, 7, M.ACCUM1_ADD)
            c.ldr(4, 7, M.ACCUM0).ldr(5, 7, M.ACCUM1)
            out.append(c.case(n, part='interp-add', interp=num, value=v))
            n += 1
        c = Code([M32, M32, 0, base])
        c.str(2, 3, M.ACCUM0).str(2, 3, M.ACCUM1).str(0, 3, M.CTRL_LANE0).str(1, 3, M.CTRL_LANE1)
        c.ldr(4, 3, M.CTRL_LANE0).ldr(5, 3, M.CTRL_LANE1)
        c.movs(2, 0).str(2, 3, M.CTRL_LANE0).str(2, 3, M.CTRL_LANE1)
        out.append(c.case(n, part='interp-mask', interp=num))
        n += 1
    return out


def sio_cases():
    out = div_cases(0)
    out += fifo_spin_cases(len(out))
    out += interp_cases(len(out))
    return out


# ── GPIO ───────────────────────────────────────────────────────────────────

def gpio_cases():
    """Pull levels on every pin but the Pico W's four, and the input
    overrides on TEST_PINS.  Nothing is driven."""
    out, n = [], 0
    for pin in SURVEY_PINS:
        c = Code([SIO, pad_addr(pin), PAD_UP, PAD_DOWN, PAD_NONE, PAD_KEEP])
        c.ldr(6, 1)                                      # r6: the pad as it was (restored at the end)
        for value_reg, hi in ((2, 8), (3, 9), (4, 10)):  # up, down, then none (after down)
            c.str(value_reg, 1).delay(7, SETTLE).ldr(7, 0, GPIO_IN).mov(hi, 7)
        c.str(2, 1).delay(7, SETTLE).str(4, 1).delay(7, SETTLE).ldr(7, 0, GPIO_IN).mov(11, 7)   # none after up
        c.str(5, 1).delay(7, SETTLE).ldr(7, 0, GPIO_IN).mov(12, 7)                          # keeper after up
        c.str(3, 1).delay(7, SETTLE).str(5, 1).delay(7, SETTLE).ldr(7, 0, GPIO_IN).mov(14, 7)   # keeper after down
        c.str(6, 1)
        out.append(c.case(n, part='pulls', pin=pin))
        n += 1
    for pin in TEST_PINS:
        c = Code([SIO, ctrl_addr(pin, 2), ctrl_addr(pin, 3), 0x10000, 0x30000, pad_addr(pin), PAD_UP])
        c.ldr(7, 5).mov(12, 7)                           # r12: the pad as it was
        c.ldr(7, 0, GPIO_IN).mov(8, 7)                   # r8: as it is (pulled down: 0)
        c.str(3, 1).delay(7, 4).ldr(7, 0, GPIO_IN).mov(9, 7)            # r9: INOVER = 1, invert
        c.str(4, 2).str(4, 1).delay(7, 4).ldr(7, 0, GPIO_IN).mov(10, 7)  # r10: INOVER = 3, high
        c.str(4, 2).lsls(7, 3, 1).str(7, 1)                              # INOVER = 2, low ...
        c.str(6, 5).delay(7, SETTLE).ldr(7, 0, GPIO_IN).mov(11, 7)       # r11: ... with the pull-up on
        c.str(4, 2).delay(7, 4).ldr(7, 0, GPIO_IN).mov(14, 7)            # lr: normal again, pulled up
        c.mov(7, 12).str(7, 5)                                           # the pad back
        out.append(c.case(n, part='inover', pin=pin))
        n += 1
    return out


def sync_cases(bypass):
    """A pull-up (then a pull-down) on SYNC_PIN, and GPIO_IN read k NOPs
    later.  bypass: the case sets that pin's PROC_IN_SYNC_BYPASS bit first
    and clears it at the end."""
    out, n = [], 0
    for rep in range(SYNC_REPEATS):
        for k in SYNC_K:
            c = Code([SIO, pad_addr(SYNC_PIN), PAD_UP, PAD_DOWN, SYSCFG_BYPASS, 1 << SYNC_PIN, 0])
            if bypass:
                c.str(5, 4)
            c.str(2, 1).nop(k).ldr(7, 0, GPIO_IN).mov(8, 7)           # r8: rising, k NOPs after the write
            c.delay(7, SETTLE)
            c.str(3, 1).nop(k).ldr(7, 0, GPIO_IN).mov(9, 7)           # r9: falling
            c.delay(7, SETTLE)
            if bypass:
                c.str(6, 4)
            out.append(c.case(n, part='sync', k=k, bypass=bypass, rep=rep))
            n += 1
    return out


def drive_cases(pins):
    """Only for --pins: the pin as an SIO output, GPIO_IN read k NOPs after
    OUT changes; and OEOVER/OUTOVER forcing it."""
    out, n = [], 0
    for pin in sorted(pins):
        for k in range(0, 9):
            c = Code([SIO, ctrl_addr(pin), safety.FUNCSEL_SIO, 1 << pin, safety.FUNCSEL_NULL])
            c.str(3, 0, 0x18).str(2, 1).str(3, 0, 0x24).delay(7, SETTLE)     # OUT low, FUNCSEL SIO, OE on
            c.str(3, 0, 0x14).nop(k).ldr(7, 0, GPIO_IN).mov(8, 7)           # r8: OUT set, k NOPs later
            c.delay(7, SETTLE)
            c.str(3, 0, 0x18).nop(k).ldr(7, 0, GPIO_IN).mov(9, 7)           # r9: OUT clear
            c.str(3, 0, 0x28).str(4, 1)                                     # OE off, FUNCSEL NULL
            out.append(c.case(n, part='drive', pin=pin, k=k))
            n += 1
        c = Code([SIO, ctrl_addr(pin), 0x331F, 0x231F, safety.FUNCSEL_NULL])
        c.str(2, 1).delay(7, SETTLE).ldr(7, 0, GPIO_IN).mov(8, 7)          # OEOVER on, OUTOVER high
        c.str(3, 1).delay(7, SETTLE).ldr(7, 0, GPIO_IN).mov(9, 7)          # OUTOVER low
        c.str(4, 1)
        out.append(c.case(n, part='oeover', pin=pin))
        n += 1
    return out


# ── SysTick ────────────────────────────────────────────────────────────────

RVR_SMALL = (1, 2, 3, 5, 8)


def systick_cases():
    out = []
    CSR, RVR, CVR, CALIB = 0, 4, 8, 12

    def start(c, rvr_reg, csr_reg):                  # r0 = SYST, r1 = 0
        c.str(1, 0, CSR).str(rvr_reg, 0, RVR).str(1, 0, CVR).str(csr_reg, 0, CSR)

    c = Code([SYST, 0, 20, 5])
    start(c, 2, 3)
    c.delay(7, 30).ldr(4, 0, CSR).ldr(5, 0, CSR).ldr(6, 0, CVR).str(1, 0, CSR)
    out.append(c.case(len(out), part='systick', check='countflag'))
    c = Code([SYST, 0, 8, 5])
    start(c, 1, 3)
    c.nop(20).ldr(4, 0, CVR).ldr(5, 0, CSR).str(2, 0, RVR).nop(6).ldr(6, 0, CVR).nop(20).ldr(7, 0, CSR)
    c.str(1, 0, CSR)
    out.append(c.case(len(out), part='systick', check='rvr0'))
    for rvr in RVR_SMALL:
        c = Code([SYST, 0, rvr, 5])
        start(c, 2, 3)
        c.nop(3).ldr(4, 0, CVR).ldr(5, 0, CVR).ldr(6, 0, CVR).ldr(7, 0, CVR).str(1, 0, CSR)
        out.append(c.case(len(out), part='systick', check='wrap', rvr=rvr))
    c = Code([SYST, 0, 10, 5, 0x1234])
    start(c, 2, 3)
    c.delay(7, 20).str(4, 0, CVR).ldr(5, 0, CSR).ldr(6, 0, CVR).str(1, 0, CSR)
    out.append(c.case(len(out), part='systick', check='cvr write'))
    c = Code([SYST])
    c.ldr(4, 0, CALIB)
    out.append(c.case(len(out), part='systick', check='calib'))
    c = Code([SYST, 0, 6])
    c.str(1, 0, CSR).str(2, 0, CSR).ldr(4, 0, CSR).str(1, 0, CSR).ldr(5, 0, CSR)
    out.append(c.case(len(out), part='systick', check='csr bits'))
    c = Code([SYST, 0, M32])
    c.str(1, 0, CSR).str(2, 0, RVR).ldr(4, 0, RVR).str(2, 0, CVR).ldr(5, 0, CVR).str(1, 0, RVR)
    out.append(c.case(len(out), part='systick', check='rvr mask'))
    c = Code([SYST, 0, 0xFFFFFF, 1])
    start(c, 2, 3)
    c.delay(7, 60).ldr(4, 0, CVR).delay(7, 60).ldr(5, 0, CVR).ldr(6, 0, CSR).str(1, 0, CSR)
    out.append(c.case(len(out), part='systick', check='external clock'))
    return out


# ── the stages ─────────────────────────────────────────────────────────────

def _hook(t):
    release(t, IO_PADS)


STAGE_KW = {'flags': F.HF_NO_SCRATCH}


def stages(pins, with_bypass=True):
    out = [('sio', sio_cases(), {}),
           ('div-timing', div_timing_cases(), {'timing': True}),
           ('gpio', gpio_cases(), {}),
           ('gpio-sync', sync_cases(False), {'timing': True})]
    if with_bypass:
        out.append(('gpio-bypass', sync_cases(True), {'timing': True}))
    if pins:
        out.append(('gpio-drive', drive_cases(pins), {'timing': True}))
    out.append(('systick', systick_cases(), {}))
    return out


def dry_cases(ctx):
    return [(name, cases, {}) for name, cases, _ in stages(ctx.pins)]


# ── the debugger's part ────────────────────────────────────────────────────

def _dw(t, addr, value, pins=frozenset()):
    """A debugger write, through the safety check first."""
    safety.check_write(addr, 4, value, pins, why='campaign G (debugger)')
    t.rsp.write32(addr, value)


def _dr(t, addr, side_effect_ok=False):
    safety.check_read(addr, side_effect_ok=side_effect_ok, why='campaign G (debugger)')
    try:
        return t.rsp.read32(addr)
    except Exception:                                    # noqa: BLE001 - recorded as missing
        return None


def _bit(v, b):
    return None if v is None else (v >> b) & 1


def gpio_debugger(t, pins, settle=0.002):
    """Each pin's STATUS and INTR bits as its pulls change, through the
    debugger; then INOVER and IRQOVER on TEST_PINS."""
    rows = {}
    for pin in SURVEY_PINS:
        pad, st, it, sh = pad_addr(pin), status_addr(pin), intr_addr(pin), 4 * (pin % 8)
        orig = _dr(t, pad)
        if orig is None:
            rows[pin] = {'error': 'PADS not readable'}
            continue
        row = {'pad': _x(orig, 2)}

        def snap(tag):
            time.sleep(settle)
            s, i = _dr(t, st), _dr(t, it)
            row[tag] = {'infrompad': _bit(s, 17), 'intoperi': _bit(s, 19),
                        'intr': None if i is None else (i >> sh) & 0xF}
        try:
            _dw(t, it, 0xC << sh, pins)                  # clear this pin's edge bits (write 1 to clear)
            snap('start')
            _dw(t, pad, PAD_UP, pins)
            snap('pull-up')
            _dw(t, pad, PAD_DOWN, pins)
            snap('pull-down')
            _dw(t, it, 0xC << sh, pins)
            snap('edges cleared')
        except Exception as e:                           # noqa: BLE001 - the stand-in lacks some of these
            row['error'] = str(e)[:120]
        finally:
            try:
                _dw(t, pad, orig, pins)
            except Exception:                            # noqa: BLE001
                pass
        rows[pin] = row
    over = {}
    for pin in TEST_PINS:
        st, it, sh = status_addr(pin), intr_addr(pin), 4 * (pin % 8)
        r = {}
        try:
            for tag, setv in (('normal', 0), ('inover invert', 0x10000), ('inover high', 0x30000),
                              ('irqover invert', 0x10000000), ('irqover high', 0x30000000)):
                if setv:
                    _dw(t, ctrl_addr(pin, 2), setv, pins)
                _dw(t, it, 0xC << sh, pins)
                time.sleep(settle)
                s, i = _dr(t, st), _dr(t, it)
                r[tag] = {'infrompad': _bit(s, 17), 'intoperi': _bit(s, 19), 'irqfrompad': _bit(s, 24),
                          'irqtoproc': _bit(s, 26), 'intr': None if i is None else (i >> sh) & 0xF}
                if setv:
                    _dw(t, ctrl_addr(pin, 3), 0x30030000, pins)
        except Exception as e:                           # noqa: BLE001
            r['error'] = str(e)[:120]
        over[pin] = r
    return rows, over


# ── the core outside the agent: a small routine runner ────────────────────

class Mini:
    """Run short routines on the core, outside the agent: code, a vector
    table (every exception goes to a trap that halts on BKPT #0x7E, unless
    a handler is given) and a stack, all in one 4 KB area of SRAM.  save()
    keeps what it overwrites (the area, the registers, VTOR, the NVIC
    enables, SysTick) and restore() puts it back."""

    AREA = 0x20030000
    VEC, TRAP, HANDLER, CODE, BUF, STACK = AREA, AREA + 0xC0, AREA + 0x100, AREA + 0x200, AREA + 0x800, AREA + 0x1000
    DONE, TRAPPED = 0x43, 0x7E

    def __init__(self, t, pins=frozenset(), log=print):
        self.t, self.pins, self.log = t, frozenset(pins), log
        self.saved = None
        safety.check_write(self.AREA, 4, 0, pins, why='routine area')

    def save(self):
        t, r = self.t, self.t.rsp
        s = {'mem': r.read_mem(self.AREA, 0x1000), 'regs': t.regs()}
        for name in ('msp', 'psp', 'primask', 'control'):
            n = r.regnum(name)
            s[name] = (n, r.read_reg(n)) if n is not None else None
        for name, a in (('vtor', 0xE000ED08), ('iser', 0xE000E100), ('syst_csr', SYST), ('syst_rvr', SYST + 4)):
            s[name] = _dr(t, a, side_effect_ok=True)
        self.saved = s
        return s

    def restore(self):
        s, t, r = self.saved, self.t, self.t.rsp
        if not s:
            return
        try:
            r.write_mem(self.AREA, s['mem'])
            for name, a in (('syst_rvr', SYST + 4), ('vtor', 0xE000ED08)):
                if s[name] is not None:
                    _dw(t, a, s[name], self.pins)
            if s['syst_csr'] is not None:
                _dw(t, SYST, s['syst_csr'] & 7, self.pins)
            _dw(t, 0xE000E180, M32, self.pins)                      # NVIC: only what was enabled
            if s['iser']:
                _dw(t, 0xE000E100, s['iser'], self.pins)
            regs = s['regs']
            for i in range(13):
                r.write_reg(i, regs[i])
            r.write_reg(14, regs[14])
            for name in ('msp', 'psp', 'primask', 'control'):
                if s[name]:
                    r.write_reg(*s[name])
            r.write_reg(t.xpsr_n, regs[16])
            r.write_reg(15, regs[15])
        except Exception as e:                                       # noqa: BLE001 - say so, don't hide the results
            self.log(f"restoring the core after the routines failed: {e}")

    def setup(self, handler=None, irq=None):
        """The vector table and the trap; `handler` (bytes) for exception `irq`."""
        r = self.t.rsp
        vec = [self.STACK, self.CODE | 1] + [self.TRAP | 1] * 46
        if handler is not None:
            vec[irq] = self.HANDLER | 1
            r.write_mem(self.HANDLER, handler)
        r.write_mem(self.VEC, b''.join(v.to_bytes(4, 'little') for v in vec))
        r.write_mem(self.TRAP, (0xBE00 | self.TRAPPED).to_bytes(2, 'little') + b'\xfe\xe7')
        _dw(self.t, 0xE000ED08, self.VEC, self.pins)

    def call(self, code, regs=None, primask=1, timeout=2.0):
        """Run `code` (bytes, ending in BKPT #0x43) with R0-R12 from `regs`
        {n: value}.  Returns {'end': 'done'|'trap'|'timeout'|..., 'regs': [R0..R15, xPSR]}."""
        t, r = self.t, self.t.rsp
        r.write_mem(self.CODE, code)
        for n, v in (regs or {}).items():
            r.write_reg(n, v & M32)
        if t.control_n is not None:
            r.write_reg(t.control_n, 0)
        if t.primask_n is not None:
            r.write_reg(t.primask_n, primask)
        r.write_reg(13, self.STACK)
        r.write_reg(t.xpsr_n, 0x01000000)
        r.write_reg(15, self.CODE)
        how = t.run(timeout)
        regs = t.regs()
        pc = regs[15]
        try:
            hw = int.from_bytes(r.read_mem(pc & ~1, 2), 'little')
        except Exception:                                             # noqa: BLE001
            hw = None
        if how == 'timeout':
            end = 'timeout'
        elif hw == 0xBE00 | self.DONE:
            end = 'done'
        elif pc == self.TRAP:
            end = 'trap'
        else:
            end = f"stopped at 0x{pc:08X}"
        return {'end': end, 'regs': regs}


def debugger_countflag(t, pins, log):
    """Does a debugger read of SYST_CSR clear COUNTFLAG?  SysTick wraps
    while a routine runs; then the debugger reads SYST_CSR twice."""
    m = Mini(t, pins, log)
    m.save()
    try:
        m.setup()
        _dw(t, SYST, 0, pins)
        _dw(t, SYST + 4, 50, pins)
        _dw(t, SYST + 8, 0, pins)
        _dw(t, SYST, 5, pins)
        c = Code(declare=False).delay(0, 100).bkpt(Mini.DONE)
        res = m.call(c.assemble())
        a, b = _dr(t, SYST, side_effect_ok=True), _dr(t, SYST, side_effect_ok=True)
        _dw(t, SYST, 0, pins)
        if res['end'] != 'done' or a is None or b is None:
            return {'end': res['end'], 'first': _x(a), 'second': _x(b)}
        return {'end': 'done', 'first': _x(a), 'second': _x(b),
                'countflag_first': _bit(a, 16), 'countflag_second': _bit(b, 16),
                'debugger_read_clears': bool(_bit(a, 16)) and not _bit(b, 16)}
    finally:
        m.restore()


# ── analysis ───────────────────────────────────────────────────────────────

def _by_id(recs):
    return {c['id']: (c, r) for c, r in recs}


def _ok(c, r):
    from .. import runner as R
    return R.ending(c, r, LAY)[0] == 'fell-through'


def _ending(c, r):
    from .. import runner as R
    return R.ending(c, r, LAY)[0]


def first_from(rows):
    """{k: True/False}: the smallest k from which every larger k is True."""
    ks = sorted(k for k, v in rows.items() if v is not None)
    for k in ks:
        if all(rows[j] for j in ks if j >= k):
            return k
    return None


def analyse_div(cases, recs):
    rows, diffs, by = [], [], _by_id(recs)
    for case in cases:
        if case['part'] != 'div' or case['id'] not in by:
            continue
        c, r = by[case['id']]
        p, q, s = case['p'], case['q'], case['signed']
        wq, wr = M.div_model(p, q, s)
        row = {'dividend': _x(p), 'divisor': _x(q), 'signed': s, 'why': case['why'],
               'predicted': [_x(wq), _x(wr)]}
        if case['dend_signed'] != s:
            row['aliases'] = f"dividend {'S' if case['dend_signed'] else 'U'}, divisor {'S' if s else 'U'}"
        if not _ok(c, r):
            row['ended'] = _ending(c, r)
            diffs.append(row)
        else:
            row.update({'quotient': _x(reg(r, 0)), 'remainder': _x(reg(r, 3)), 'csr_before': reg(r, 4),
                        'csr_after_quotient': reg(r, 5), 'dividend_reads': _x(reg(r, 6)),
                        'divisor_reads': _x(reg(r, 7))})
            bad = []
            if (reg(r, 0), reg(r, 3)) != (wq, wr):
                if case['dend_signed'] != s and (reg(r, 0), reg(r, 3)) == M.div_model(p, q, case['dend_signed']):
                    bad.append("signedness follows the dividend's alias, not the divisor's (the last written)")
                else:
                    bad.append('result')
            if reg(r, 4) != 3:
                bad.append('CSR before reading the results is not READY|DIRTY (3)')
            if reg(r, 5) != 1:
                bad.append('CSR after reading QUOTIENT is not READY (1)')
            if (reg(r, 6), reg(r, 7)) != (p, q):
                bad.append('the operands do not read back')
            if bad:
                row['differences'] = bad
                diffs.append(row)
        rows.append(row)
    return rows, diffs


def analyse_div_dirty(cases, recs):
    by, out, diffs = _by_id(recs), {}, 0
    for case in cases:
        if case['part'] != 'div-dirty' or case['id'] not in by:
            continue
        c, r = by[case['id']]
        if not _ok(c, r):
            out[case['check']] = {'ended': _ending(c, r)}
            diffs += 1
            continue
        if case['check'] == 'flags':
            got = {'after QUOTIENT read': reg(r, 8), 'one operand written, next cycle': reg(r, 9),
                   'after the wait': reg(r, 10), 'after REMAINDER read': reg(r, 11),
                   'after QUOTIENT read again': reg(r, 5), 'after a second QUOTIENT read': reg(r, 7)}
            want = {'after QUOTIENT read': 1, 'one operand written, next cycle': 2, 'after the wait': 3,
                    'after REMAINDER read': 3, 'after QUOTIENT read again': 1, 'after a second QUOTIENT read': 1}
        else:
            got = {'CSR after writing QUOTIENT': reg(r, 4), 'QUOTIENT reads': _x(reg(r, 5)),
                   'CSR after reading it': reg(r, 6), 'QUOTIENT after waiting': _x(reg(r, 0)),
                   'REMAINDER after waiting': _x(reg(r, 7))}
            want = {'CSR after writing QUOTIENT': 3, 'QUOTIENT reads': _x(0x12345678), 'CSR after reading it': 1,
                    'QUOTIENT after waiting': _x(0x12345678)}
        bad = [k for k, v in want.items() if got[k] != v]
        out[case['check']] = {'csr_values': got, 'predicted': want, 'differences': bad}
        diffs += bool(bad)
    return out, diffs


def analyse_div_timing(cases, recs):
    by = _by_id(recs)
    valid = {'csr': {}, 'quotient': {}, 'remainder': {}}
    raw = []
    for case in cases:
        if case['id'] not in by:
            continue
        c, r = by[case['id']]
        if not _ok(c, r):
            continue
        wq, wr = M.div_model(case['p'], case['q'], case['signed'])
        first = reg(r, 4)
        good = {'csr': first & 1 == 1, 'quotient': first == wq, 'remainder': first == wr}[case['first']]
        k = case['k']
        prev = valid[case['first']].get(k, True)
        valid[case['first']][k] = prev and good
        raw.append({'k': k, 'first': case['first'], 'pair': case['pair'], 'read': _x(first),
                    'final_ok': (reg(r, 0), reg(r, 7)) == (wq, wr)})
    out = {f"{what}_from_k": first_from(v) for what, v in valid.items()}
    return out, raw


def analyse_spin(cases, recs):
    by, locks, diffs, fifo = _by_id(recs), [], 0, None
    for case in cases:
        if case['id'] not in by:
            continue
        c, r = by[case['id']]
        if case['part'] == 'fifo':
            fifo = {'fifo_st': _x(reg(r, 4)) if _ok(c, r) else _ending(c, r),
                    'again': _x(reg(r, 5)) if _ok(c, r) else None}
            continue
        if case['part'] != 'spinlock':
            continue
        n = case['lock']
        if not _ok(c, r):
            locks.append({'lock': n, 'ended': _ending(c, r)})
            diffs += 1
            continue
        row = {'lock': n, 'st_before': _x(reg(r, 3)), 'claim': _x(reg(r, 4)), 'st_claimed': _x(reg(r, 5)),
               'claim_again': _x(reg(r, 6)), 'st_after': _x(reg(r, 7))}
        bad = []
        if reg(r, 4) != 1 << n:
            bad.append('the claim did not return 1 << n')
        if not reg(r, 5) >> n & 1:
            bad.append('SPINLOCK_ST does not show it claimed')
        if reg(r, 6) != 0:
            bad.append('a second claim succeeded')
        if reg(r, 7) != reg(r, 3):
            bad.append('SPINLOCK_ST is not as before after the release')
        if bad:
            row['differences'] = bad
            diffs += 1
        locks.append(row)
    return fifo, locks, diffs


def analyse_interp(cases, recs):
    by = _by_id(recs)
    rows, examples, extras = [], [], []
    stats = {'cases': 0, 'match': 0, 'differ': 0, 'did_not_run': 0, 'variants': {}}
    ex_diffs = 0
    for case in cases:
        if case['id'] not in by or not case['part'].startswith('interp'):
            continue
        c, r = by[case['id']]
        if not _ok(c, r):
            stats['did_not_run'] += 1
            rows.append({'id': case['id'], 'ended': _ending(c, r)})
            continue
        if case['part'] == 'interp':
            stats['cases'] += 1
            got = {name: reg(r, n) for name, n in INTERP_FIELDS}
            want = interp_predict(case['interp'], case['cfg'], case['pop'])
            bad = [k for k in want if got[k] != want[k]]
            if 'published' in case:
                pub = [k for k, v in case['published'].items() if got[k] != v]
                examples.append({'example': case['example'], 'published': {k: _x(v) for k, v in case['published'].items()},
                                 'read': {k: _x(got[k]) for k in case['published']}, 'agrees': not pub})
                ex_diffs += bool(pub)
            if not bad:
                stats['match'] += 1
                continue
            stats['differ'] += 1
            which = []
            for key, alts in M.ALTERNATIVES.items():
                for alt in alts:
                    w2 = interp_predict(case['interp'], case['cfg'], case['pop'], {key: alt})
                    if all(got[k] == w2[k] for k in w2):
                        which.append(f"{key}={alt}")
            for w in which:
                stats['variants'][w] = stats['variants'].get(w, 0) + 1
            rows.append({'id': case['id'], 'interp': case['interp'], 'cfg': [_x(v) for v in case['cfg']],
                         'pop': f"0x{case['pop']:02X}", 'fields': bad,
                         'read': {k: _x(got[k]) for k in bad}, 'predicted': {k: _x(want[k]) for k in bad},
                         'matches_variant': which})
        elif case['part'] == 'interp-base10':
            v, (s0, s1) = case['value'], case['signed']
            lo, hi = v & 0xFFFF, v >> 16
            w0 = lo | 0xFFFF0000 if s0 and lo & 0x8000 else lo
            w1 = hi | 0xFFFF0000 if s1 and hi & 0x8000 else hi
            got = (reg(r, 4), reg(r, 5))
            extras.append({'test': 'BASE_1AND0', 'interp': case['interp'], 'signed': [s0, s1], 'value': _x(v),
                           'base0': _x(got[0]), 'base1': _x(got[1]), 'reads_as': _x(reg(r, 6)),
                           'agrees': got == (w0, w1)})
        elif case['part'] == 'interp-add':
            v = case['value']
            got = (reg(r, 4), reg(r, 5))
            fits = [w for w in (32, 24, -24)
                    if all(g == _add(0x10, v, w) for g in got)]
            extras.append({'test': 'ACCUMx_ADD', 'interp': case['interp'], 'value': _x(v),
                           'accum0': _x(got[0]), 'accum1': _x(got[1]), 'add_width_fits': fits,
                           'agrees': 32 in fits})
        elif case['part'] == 'interp-mask':
            num = case['interp']
            extras.append({'test': 'CTRL write mask', 'interp': num, 'ctrl0': _x(reg(r, 4)), 'ctrl1': _x(reg(r, 5)),
                           'predicted': [_x(M.CTRL_WRITABLE[(num, 0)]), _x(M.CTRL_WRITABLE[(num, 1)])],
                           'agrees': (reg(r, 4) & ~(M.OVERF0 | M.OVERF1 | M.OVERF), reg(r, 5))
                           == (M.CTRL_WRITABLE[(num, 0)], M.CTRL_WRITABLE[(num, 1)])})
    return stats, rows, examples, ex_diffs, extras


def _add(start, v, width):
    m = M.Interp(0, {'add_width': width})
    m.write(M.ACCUM0, start)
    m.write(M.ACCUM0_ADD, v)
    return m.accum[0]


def picosim_gpio(steps):
    """What picosim's own GPIO model (picosim/gpio.py) reads in GPIO_IN
    after `steps`: [(address, value)] writes, the pin pulled as given."""
    from ...gpio import GPIO
    g = GPIO()
    for a, v in steps:
        g.write32(a, v)
    return g.read32(SIO + GPIO_IN)


def analyse_gpio(cases, recs):
    by = _by_id(recs)
    pulls, inover, diffs, odd = {}, {}, 0, []
    for case in cases:
        if case['id'] not in by:
            continue
        c, r = by[case['id']]
        pin = case.get('pin')
        if not _ok(c, r):
            (pulls if case['part'] == 'pulls' else inover)[pin] = {'ended': _ending(c, r)}
            continue
        if case['part'] == 'pulls':
            b = lambda n: (reg(r, n) >> pin) & 1               # noqa: E731
            row = {'pull-up': b(8), 'pull-down': b(9), 'none after down': b(10), 'none after up': b(11),
                   'keeper after up': b(12), 'keeper after down': b(14), 'pad': _x(reg(r, 6), 2)}
            pulls[pin] = row
            if (row['pull-up'], row['pull-down']) != (1, 0):
                odd.append(pin)
        elif case['part'] == 'inover':
            b = lambda n: (reg(r, n) >> pin) & 1               # noqa: E731
            got = {'normal (pulled down)': b(8), 'INOVER invert': b(9), 'INOVER high': b(10),
                   'INOVER low (pulled up)': b(11), 'normal (pulled up)': b(14)}
            want = {'normal (pulled down)': 0, 'INOVER invert': 1, 'INOVER high': 1,
                    'INOVER low (pulled up)': 0, 'normal (pulled up)': 1}
            pc = ctrl_addr(pin)
            pico = {'normal (pulled down)': picosim_gpio([(pad_addr(pin), PAD_DOWN)]),
                    'INOVER invert': picosim_gpio([(pad_addr(pin), PAD_DOWN), (pc, 0x1001F)]),
                    'INOVER high': picosim_gpio([(pad_addr(pin), PAD_DOWN), (pc, 0x3001F)]),
                    'INOVER low (pulled up)': picosim_gpio([(pad_addr(pin), PAD_UP), (pc, 0x2001F)]),
                    'normal (pulled up)': picosim_gpio([(pad_addr(pin), PAD_UP)])}
            pico = {k: (v >> pin) & 1 for k, v in pico.items()}
            bad = [k for k in want if got[k] != want[k]]
            inover[pin] = {'read': got, 'predicted': want, 'picosim': pico,
                           'picosim_differs': [k for k in want if pico[k] != got[k]], 'differences': bad}
            diffs += bool(bad)
    return pulls, inover, diffs, odd


def analyse_sync(cases_by_stage, recs_by_stage):
    out = {}
    for stage, cases in cases_by_stage.items():
        by = _by_id(recs_by_stage.get(stage, []))
        rise, fall, ended = {}, {}, {}
        for case in cases:
            if case['id'] not in by:
                continue
            c, r = by[case['id']]
            if not _ok(c, r):
                ended[_ending(c, r)] = ended.get(_ending(c, r), 0) + 1
                continue
            k = case['k']
            rise[k] = rise.get(k, True) and bool(reg(r, 8) >> SYNC_PIN & 1)
            fall[k] = fall.get(k, True) and not reg(r, 9) >> SYNC_PIN & 1
        out[stage] = {'rise_seen_from_k': first_from(rise), 'fall_seen_from_k': first_from(fall),
                      'rise': {k: rise[k] for k in sorted(rise)}, 'fall': {k: fall[k] for k in sorted(fall)}}
        if ended:
            out[stage]['ended'] = ended
    return out


def analyse_drive(cases, recs):
    by, out = _by_id(recs), {}
    for case in cases:
        if case['id'] not in by:
            continue
        c, r = by[case['id']]
        pin = case['pin']
        row = out.setdefault(pin, {'rise': {}, 'fall': {}})
        if not _ok(c, r):
            row.setdefault('ended', []).append(_ending(c, r))
            continue
        if case['part'] == 'drive':
            row['rise'][case['k']] = bool(reg(r, 8) >> pin & 1)
            row['fall'][case['k']] = not reg(r, 9) >> pin & 1
        else:
            row['oeover_high'] = reg(r, 8) >> pin & 1
            row['oeover_low'] = reg(r, 9) >> pin & 1
    for row in out.values():
        row['rise_seen_from_k'] = first_from(row['rise'])
        row['fall_seen_from_k'] = first_from(row['fall'])
    return out


def analyse_systick(cases, recs):
    by, out, diffs = _by_id(recs), {}, []
    for case in cases:
        if case['id'] not in by:
            continue
        c, r = by[case['id']]
        chk = case['check'] + (f" {case['rvr']}" if 'rvr' in case else '')
        if not _ok(c, r):
            out[chk] = {'ended': _ending(c, r)}
            diffs.append(chk)
            continue
        g = lambda n: reg(r, n)                               # noqa: E731
        if case['check'] == 'countflag':
            row = {'csr_after_wrap': _x(g(4)), 'csr_read_again': _x(g(5)), 'cvr': g(6),
                   'predicted': 'COUNTFLAG set after a wrap, cleared by that read'}
            ok = bool(g(4) >> 16 & 1) and not g(5) >> 16 & 1
        elif case['check'] == 'rvr0':
            row = {'cvr_with_rvr0': g(4), 'csr_with_rvr0': _x(g(5)), 'cvr_after_rvr8': g(6), 'csr_later': _x(g(7)),
                   'predicted': 'RVR = 0: the counter stays at 0 and never sets COUNTFLAG'}
            ok = g(4) == 0 and not g(5) >> 16 & 1
        elif case['check'] == 'wrap':
            rvr = case['rvr']
            vals = [g(4), g(5), g(6), g(7)]
            steps = {(a - b) % (rvr + 1) for a, b in zip(vals, vals[1:])}
            row = {'rvr': rvr, 'values': vals, 'spacing_mod_period': sorted(steps),
                   'predicted': 'values 0..RVR, period RVR + 1 (a constant spacing)'}
            ok = all(v <= rvr for v in vals) and len(steps) == 1
        elif case['check'] == 'cvr write':
            row = {'csr_after_cvr_write': _x(g(5)), 'cvr': g(6),
                   'predicted': 'a CVR write clears COUNTFLAG (and the count)'}
            ok = not g(5) >> 16 & 1
        elif case['check'] == 'calib':
            try:
                from .. import svd
                want = next((x['reset'] for x in svd.registers() if x['name'] == 'SYST_CALIB'), 0)
            except (OSError, ValueError):                      # no Pico SDK: the header's value
                want = 0
            row = {'calib': _x(g(4)), 'predicted': _x(want), 'from': 'SVD reset value'}
            ok = g(4) == want
        elif case['check'] == 'csr bits':
            row = {'csr_after_writing_6': _x(g(4)), 'csr_after_writing_0': _x(g(5)),
                   'predicted': 'TICKINT and CLKSOURCE read back (6)'}
            ok = g(4) & 7 == 6 and g(5) & 7 == 0
        elif case['check'] == 'rvr mask':
            row = {'rvr_after_all_ones': _x(g(4)), 'cvr_after_write': _x(g(5)),
                   'predicted': 'RVR keeps bits 23:0 (0x00FFFFFF); any CVR write reads back 0'}
            ok = g(4) == 0xFFFFFF and g(5) == 0
        else:                                                   # external clock: no prediction
            row = {'cvr_first': g(4), 'cvr_second': g(5), 'csr': _x(g(6)),
                   'counted': g(4) != 0 or g(5) != g(4),
                   'note': 'CLKSOURCE = 0 (the external reference) right after reset halt'}
            ok = True
        row['agrees'] = ok
        out[chk] = row
        if not ok:
            diffs.append(chk)
    return out, diffs


# ── the campaign ────────────────────────────────────────────────────────────

NOT_MODELLED = {
    'divider': 'not modelled: picosim/gpio.py covers SIO 0x000-0x03F only (no DIV_* registers)',
    'interpolators': 'not modelled (no INTERP0/1 registers)',
    'spinlocks': 'not modelled (no SPINLOCKn / SPINLOCK_ST)',
    'fifo': 'not modelled (no FIFO_ST/FIFO_RD/FIFO_WR; there is no core 1)',
    'gpio_inover': 'not modelled: gpio.py stores INOVER in CTRL but GPIO_IN ignores it',
    'gpio_status_intr': 'not modelled: IO_BANK0 GPIOn_STATUS and INTR0-3 read 0',
    'gpio_input_synchroniser': 'not modelled: GPIO_IN changes at once (no 2-cycle synchroniser, no pull rise time)',
    'syscfg': 'not modelled (no SYSCFG block)',
    'pad_keeper': 'not modelled: pull-up and pull-down together read as a pull-up',
    'systick': 'modelled in the stand-in (hwtest/standin.py), not in picosim\'s core',
}


def run(ctx):
    from .. import facts as FX
    t = ctx.t
    ctx.status(NAME, 'partial', step='debugger')
    t.reset_halt()
    t.vector_catch('none')
    release(t, IO_PADS)
    done = _dr(t, RESET_DONE)
    syscfg_ready = done is not None and bool(done >> SYSCFG_RESET_BIT & 1)
    run_bypass = syscfg_ready or ctx.kind == 'standin'          # the stand-in has no SYSCFG: its cases fault
    pad_rows, overrides = gpio_debugger(t, ctx.pins)
    recs, states = {}, {}
    plan = stages(ctx.pins, run_bypass)
    for name, cases, kw in plan:
        ctx.status(NAME, 'partial', step=name)
        recs[name], states[name] = ctx.stage(NAME, name, cases, on_prepare=_hook, **STAGE_KW, **kw)
    cases = {name: cs for name, cs, _ in plan}
    ctx.status(NAME, 'partial', step='debugger COUNTFLAG')
    try:
        dbg_cf = debugger_countflag(t, ctx.pins, ctx.log)
    except Exception as e:                                         # noqa: BLE001 - recorded, not fatal
        dbg_cf = {'error': str(e)[:200]}

    # SIO
    div_rows, div_diffs = analyse_div(cases['sio'], recs['sio'])
    dirty, dirty_diffs = analyse_div_dirty(cases['sio'], recs['sio'])
    timing, timing_raw = analyse_div_timing(cases['div-timing'], recs['div-timing'])
    fifo, locks, lock_diffs = analyse_spin(cases['sio'], recs['sio'])
    istats, irows, examples, ex_diffs, extras = analyse_interp(cases['sio'], recs['sio'])
    div0 = {f"{'signed' if r['signed'] else 'unsigned'} {r['dividend']} / 0": [r.get('quotient'), r.get('remainder')]
            for r in div_rows if r['divisor'] == _x(0) and r['why'] == 'edge'}
    timing_bad = [k for k, v in timing.items() if v != M.DIV_CYCLES]
    extra_bad = sum(not e['agrees'] for e in extras)
    sio_diffs = (len(div_diffs) + dirty_diffs + len(timing_bad) + lock_diffs + istats['differ']
                 + istats['did_not_run'] + ex_diffs + extra_bad)
    fs = [FX.fact('sio.div.cases', len(div_rows)),
          FX.fact('sio.div.differences', len(div_diffs)),
          FX.fact('sio.div.result_cycles', timing, unit='NOPs after the divisor write',
                  prediction={'value': M.DIV_CYCLES, 'from': 'datasheet §2.3.1.5 (8 cycles)'}),
          FX.fact('sio.div.by_zero', div0, prediction={'value': {k: [_x(x) for x in M.div_model(
              int(k.split()[1], 16), 0, k.startswith('signed'))] for k in div0},
              'from': 'derived: a sign-magnitude divider (the datasheet does not say)'}),
          FX.fact('sio.div.dirty_ready', dirty),
          FX.fact('sio.fifo_st', fifo, prediction={'value': '0x00000002', 'from': 'core 1 waiting in the boot ROM'}),
          FX.fact('sio.spinlock.differences', lock_diffs),
          FX.fact('sio.interp.cases', istats['cases']),
          FX.fact('sio.interp.differences', istats['differ']),
          FX.fact('sio.interp.variants_matched', istats['variants']),
          FX.fact('sio.interp.datasheet_examples_disagree', ex_diffs),
          FX.fact('sio.interp.extras_disagree', extra_bad)]
    for k, v in NOT_MODELLED.items():
        if k in ('divider', 'interpolators', 'spinlocks', 'fifo'):
            fs.append(FX.fact(f"picosim.sio.{k}", v, source='not modelled'))
    prov = ctx.provenance(states.get('sio'), campaign='G')
    paths = [ctx.write_facts('peripherals/sio.json', 'peripherals-sio', prov, fs,
                             divider=div_rows, divider_timing=timing_raw, divider_dirty=dirty,
                             spinlocks=locks, fifo=fifo, interp_summary=istats, interp_differences=irows,
                             interp_examples=examples, interp_extras=extras, picosim=NOT_MODELLED)]

    # GPIO
    pulls, inover, inover_diffs, odd = analyse_gpio(cases['gpio'], recs['gpio'])
    sync_cases_ = {k: cases[k] for k in ('gpio-sync', 'gpio-bypass') if k in cases}
    sync = analyse_sync(sync_cases_, recs)
    if not run_bypass:
        sync['gpio-bypass'] = {'skipped': 'SYSCFG is held in reset after reset halt (RESETS bit 18), and '
                                          'discovery may not release it; flashed_part measures it'}
    drive = analyse_drive(cases['gpio-drive'], recs['gpio-drive']) if 'gpio-drive' in cases else \
        {'skipped': 'no --pins: no pin is driven'}
    sync_diff = None
    a, b = (sync.get('gpio-sync', {}).get('rise_seen_from_k'), sync.get('gpio-bypass', {}).get('rise_seen_from_k'))
    if a is not None and b is not None:
        sync_diff = a - b
    dbg_bad = []
    for pin, row in pad_rows.items():
        if not all(k in row for k in ('pull-up', 'pull-down', 'edges cleared')):
            continue
        up, down, clr = row['pull-up'], row['pull-down'], row['edges cleared']
        want = (up['infrompad'] == 1 and up['intr'] == 0b1010 and down['infrompad'] == 0
                and down['intr'] == 0b1101 and clr['intr'] == 0b0001)
        row['agrees'] = want
        if not want and pin not in odd:
            dbg_bad.append(pin)
    over_bad = [p for p, r in overrides.items()
                if r.get('inover invert', {}).get('intoperi') != 1 or r.get('normal', {}).get('intoperi') != 0]
    gpio_diffs = inover_diffs + len(dbg_bad) + len(over_bad) + (sync_diff is not None and sync_diff != 2)
    fs = [FX.fact('gpio.pins_surveyed', len(pulls)),
          FX.fact('gpio.pins_not_following_pulls', odd,
                  note='connected to something (a jumper, the board), or a pad that differs'),
          FX.fact('gpio.inover_in_gpio_in', {p: v['read'] for p, v in inover.items()},
                  prediction={'value': 'INOVER applies before GPIO_IN', 'from': 'datasheet §2.19.2'}),
          FX.fact('gpio.status_intr_disagree', dbg_bad,
                  prediction={'value': 'pull-up: LEVEL_HIGH|EDGE_HIGH; pull-down: LEVEL_LOW and both edges; '
                                       'after W1C: LEVEL_LOW', 'from': 'datasheet §2.19.6 (INTR0-3)'}),
          FX.fact('gpio.overrides_disagree', over_bad),
          FX.fact('gpio.pull_rise_k', {k: v.get('rise_seen_from_k') for k, v in sync.items()},
                  unit='NOPs after the PADS write'),
          FX.fact('gpio.synchroniser_cycles', sync_diff, unit='cycles',
                  prediction={'value': 2, 'from': 'datasheet §2.3.1.2 (2-flop synchroniser on GPIO inputs)'})]
    for k in ('gpio_inover', 'gpio_status_intr', 'gpio_input_synchroniser', 'syscfg', 'pad_keeper'):
        fs.append(FX.fact(f"picosim.{k}", NOT_MODELLED[k], source='not modelled'))
    prov = ctx.provenance(states.get('gpio'), campaign='G')
    paths.append(ctx.write_facts('peripherals/gpio.json', 'peripherals-gpio', prov, fs, pulls=pulls,
                                 inover=inover, debugger=pad_rows, overrides=overrides, sync=sync, drive=drive,
                                 syscfg_out_of_reset=syscfg_ready, picosim=NOT_MODELLED))

    # SysTick
    st, st_bad = analyse_systick(cases['systick'], recs['systick'])
    fs = [FX.fact(f"systick.{k.replace(' ', '_')}", v.get('agrees'), detail=v) for k, v in st.items()]
    fs.append(FX.fact('systick.debugger_read_clears_countflag', dbg_cf.get('debugger_read_clears'), detail=dbg_cf))
    fs.append(FX.fact('picosim.systick', NOT_MODELLED['systick'], source='not modelled'))
    prov = ctx.provenance(states.get('systick'), campaign='G')
    paths.append(ctx.write_facts('peripherals/systick.json', 'peripherals-systick', prov, fs, systick=st,
                                 debugger_countflag=dbg_cf))
    diffs = sio_diffs + gpio_diffs + len(st_bad)
    ctx.log(f"peripherals: {diffs} differences from the models (SIO {sio_diffs}, GPIO {gpio_diffs}, "
            f"SysTick {len(st_bad)})")
    ctx.status(NAME, 'done', facts=paths, differences=diffs, compared_with='datasheet',   # its models: picosim has none
               flashed='not yet: campaign F runs flashed_part()')
    return paths


# ── the flashed part (called by campaign F) ────────────────────────────────

TIMER = 0x40054000
T_TIMEHW, T_TIMELW, T_TIMEHR, T_TIMELR, T_ALARM0 = 0x00, 0x04, 0x08, 0x0C, 0x10
T_ARMED, T_RAWH, T_RAWL, T_DBGPAUSE, T_INTR, T_INTE = 0x20, 0x24, 0x28, 0x2C, 0x34, 0x38
UART1, UART1_RESET_BIT = 0x40038000, 23
U_DR, U_RSR, U_FR, U_IBRD, U_FBRD, U_LCR_H, U_CR, U_IFLS, U_IMSC, U_RIS, U_ICR = (
    0x00, 0x04, 0x18, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C, 0x44)
BAUDS = (('fast', 1, 0), ('115200 at 125 MHz', 67, 52))          # (name, IBRD, FBRD)
TICKS = 1000
ALARM_REPEATS = 16
FLASHED_SYNC_K = range(0, 64)


def flashed_part(ctx, target, clocks=None):
    """G's clock-dependent part, for campaign F.

    Assumes (campaign F guarantees it): `target` is connected and core 0 is
    halted at the probe firmware's BKPT, after the SDK's runtime init, so
    clk_sys and clk_peri run (125 MHz in the SDK's default set-up), the
    watchdog tick makes 1 µs TIMER ticks, TIMER is out of reset and the SDK
    uses hardware alarm 3 only; UART1 is free (stdio is on UART0 or USB).
    `clocks` (optional): F's FC0 measurements {'clk_sys': f, 'clk_peri': f}
    in one unit (only their ratio is used); without them clk_peri = clk_sys
    is assumed for the predictions.

    It runs routines on the core outside the agent (Mini), in
    0x20030000-0x20030FFF, and puts back what it changes: that SRAM, the
    core registers, VTOR, the NVIC enables, SysTick, TIMER DBGPAUSE/INTE/
    ALARM0 (and the time, moved on by what elapsed), UART1 (back into reset
    if it was), the test pin's pad and PROC_IN_SYNC_BYPASS.  It never raises
    for a measurement that fails: the error is recorded.

    Writes peripherals/timer.json, peripherals/uart.json and
    peripherals/gpio_flashed.json, adds them to G's status, and returns
    {'paths', 'differences', 'timer', 'uart', 'gpio'}."""
    from .. import facts as FX
    t = target
    pins = ctx.pins
    m = Mini(t, pins, ctx.log)
    m.save()
    clocks = clocks or {}
    ratio = (clocks['clk_sys'] / clocks['clk_peri']) if clocks.get('clk_sys') and clocks.get('clk_peri') else 1.0
    out = {}
    try:
        m.setup()
        _dw(t, SYST, 0, pins)
        _dw(t, SYST + 4, 0xFFFFFF, pins)
        _dw(t, SYST + 8, 0, pins)
        _dw(t, SYST, 5, pins)
        for name, fn in (('timer', _flashed_timer), ('uart', _flashed_uart), ('gpio', _flashed_gpio)):
            try:
                out[name] = fn(ctx, t, m, ratio)
            except Exception as e:                               # noqa: BLE001 - recorded, the rest goes on
                out[name] = {'error': str(e)[:300]}
                ctx.log(f"peripherals (flashed) {name}: {e}")
    finally:
        m.restore()
    prov = ctx.provenance(None, campaign='G (flashed, in F)', clocks=clocks or None)
    paths, diffs = [], 0
    for name, kind in (('timer', 'peripherals-timer'), ('uart', 'peripherals-uart'),
                       ('gpio', 'peripherals-gpio-flashed')):
        res = out.get(name, {})
        d = res.pop('_differences', [])
        diffs += len(d)
        fs = [FX.fact(f"{name}.{k.replace(' ', '_')}", v.get('value') if isinstance(v, dict) and 'value' in v else v)
              for k, v in res.items() if not k.startswith('_') and k != 'error']
        if 'error' in res:
            fs.append(FX.fact(f"{name}.error", res['error']))
        fs.append(FX.fact(f"{name}.differences", d))
        fs.append(FX.fact(f"picosim.{name}", 'not modelled' if name != 'gpio' else NOT_MODELLED['gpio_input_synchroniser'],
                          source='not modelled'))
        fname = 'gpio_flashed' if name == 'gpio' else name
        paths.append(ctx.write_facts(f'peripherals/{fname}.json', kind, prov, fs, results=res))
    from ..campaign import read_status
    s = read_status(NAME, ctx.kind, ctx.label)
    extra = {k: v for k, v in s.items() if k not in ('campaign', 'state', 'updated')}
    extra['flashed'] = {'facts': paths, 'differences': diffs, 'when': time.strftime('%Y-%m-%d %H:%M:%S')}
    extra['differences'] = (s.get('differences') or 0) + diffs
    ctx.status(NAME, s.get('state') if s.get('state') == 'done' else 'partial', **extra)
    return {'paths': paths, 'differences': diffs, **out}


def _wait_edge(c, base_reg, off, prev, cur):
    """Poll [base + off] into `cur` until it differs from `prev`."""
    name = f"_edge{len(c.hw)}"
    c.ldr(prev, base_reg, off).label(name).ldr(cur, base_reg, off).cmp_r(cur, prev).beq(name)
    return c


def _flashed_timer(ctx, t, m, ratio):
    pins = ctx.pins
    res, bad = {}, []
    dbgpause = _dr(t, TIMER + T_DBGPAUSE)
    if dbgpause is None:
        return {'error': 'TIMER is not readable through the debugger'}
    inte, alarm0 = _dr(t, TIMER + T_INTE), _dr(t, TIMER + T_ALARM0)
    try:
        a = _dr(t, TIMER + T_RAWL)
        time.sleep(0.05)
        b = _dr(t, TIMER + T_RAWL)
        res['paused_while_halted'] = {'value': a == b, 'dbgpause': _x(dbgpause), 'raw': [a, b],
                                      'predicted': bool(dbgpause & 2)}
        if (a == b) != bool(dbgpause & 2):
            bad.append('DBGPAUSE')
        _dw(t, TIMER + T_DBGPAUSE, 0, pins)
        h0, a, t0 = _dr(t, TIMER + T_RAWH), _dr(t, TIMER + T_RAWL), time.monotonic()
        base_time = (h0 << 32) | a                                      # put back, moved on, at the end
        time.sleep(0.5)
        b, t1 = _dr(t, TIMER + T_RAWL), time.monotonic()
        res['ticks_per_host_second'] = {'value': round(((b - a) & M32) / (t1 - t0)), 'predicted': 1_000_000,
                                        'note': 'host-timed through the debugger: about 0.5 % uncertainty'}
        # the TIMELR/TIMEHR latch: set the time just below a carry into the high word
        near = ((base_time >> 32) + 1) << 32 | 0xFFF80000              # 0.52 s before a carry
        _dw(t, TIMER + T_TIMELW, near & M32, pins)
        _dw(t, TIMER + T_TIMEHW, near >> 32, pins)
        lr = _dr(t, TIMER + T_TIMELR, side_effect_ok=True)              # latches TIMEHR
        time.sleep(0.7)                                                 # past the carry
        rawh, hr = _dr(t, TIMER + T_RAWH), _dr(t, TIMER + T_TIMEHR)
        lr2 = _dr(t, TIMER + T_TIMELR, side_effect_ok=True)
        hr2 = _dr(t, TIMER + T_TIMEHR)
        latched = hr == near >> 32 and rawh == (near >> 32) + 1 and hr2 == rawh
        res['timehr_latched_by_timelr'] = {'value': latched, 'timelr': _x(lr), 'timerawh_after_carry': rawh,
                                           'timehr_after_carry': hr, 'timelr_again': _x(lr2), 'timehr_again': hr2,
                                           'set_to': _x(near, 16),
                                           'predicted': 'TIMEHR keeps the high word latched by the TIMELR read'}
        if not latched:
            bad.append('TIMELR/TIMEHR latch')
        now = base_time + int((time.monotonic() - t0) * 1e6)          # as if it had never been moved
        _dw(t, TIMER + T_TIMELW, now & M32, pins)
        _dw(t, TIMER + T_TIMEHW, now >> 32, pins)
        # core-timed: cycles per tick, SysTick's external reference, alarm -> IRQ latency
        c = Code(declare=False)
        _wait_edge(c, 0, T_RAWL, 3, 4)
        c.ldr(5, 1).adds_r(6, 4, 2)
        c.label('w2').ldr(3, 0, T_RAWL).cmp_r(3, 6).bne('w2').ldr(7, 1).bkpt(Mini.DONE)
        r = m.call(c.assemble(), {0: TIMER, 1: SYST + 8, 2: TICKS}, timeout=2.0)
        if r['end'] == 'done':
            cyc = ((r['regs'][5] - r['regs'][7]) & 0xFFFFFF) / TICKS
            res['cycles_per_tick'] = {'value': round(cyc, 3), 'ticks': TICKS}
        else:
            res['cycles_per_tick'] = {'value': None, 'ended': r['end']}
        # SysTick with CLKSOURCE = 0 counted against TIMER ticks
        c = Code(declare=False)
        c.movs(7, 0).str(7, 1, 0).str(7, 1, 8).movs(7, 1).str(7, 1, 0)  # CSR = 0, CVR = 0, CSR = ENABLE (external)
        _wait_edge(c, 0, T_RAWL, 3, 4)
        c.ldr(5, 1, 8).adds_r(6, 4, 2)
        c.label('w2').ldr(3, 0, T_RAWL).cmp_r(3, 6).bne('w2').ldr(7, 1, 8)
        c.movs(3, 5).str(3, 1, 0).bkpt(Mini.DONE)                        # CSR = processor clock again
        r = m.call(c.assemble(), {0: TIMER, 1: SYST, 2: 100}, timeout=2.0)
        if r['end'] == 'done':
            res['systick_external_ticks_per_timer_tick'] = {
                'value': ((r['regs'][5] - r['regs'][7]) & 0xFFFFFF) / 100,
                'note': 'SYST_CSR.CLKSOURCE = 0 (the external reference), counted over 100 TIMER ticks'}
        else:
            res['systick_external_ticks_per_timer_tick'] = {'value': None, 'ended': r['end']}
        _dw(t, SYST, 0, pins)
        _dw(t, SYST + 4, 0xFFFFFF, pins)
        _dw(t, SYST + 8, 0, pins)
        _dw(t, SYST, 5, pins)
        res['alarm_to_irq'] = _alarm_latency(ctx, t, m, res.get('cycles_per_tick', {}).get('value'))
    finally:
        try:
            _dw(t, TIMER + T_INTE, inte or 0, pins)
            _dw(t, TIMER + T_INTR, 1, pins)
            if alarm0 is not None and (inte or 0) & 1:         # the firmware had alarm 0 armed: arm it again
                _dw(t, TIMER + T_ALARM0, alarm0, pins)
            _dw(t, TIMER + T_DBGPAUSE, dbgpause, pins)
        except Exception as e:                                          # noqa: BLE001
            ctx.log(f"TIMER not fully restored: {e}")
    res['_differences'] = bad
    return res


def _alarm_latency(ctx, t, m, cpt):
    """ALARM0 armed 2 ticks after a TIMERAWL edge; the IRQ handler's first
    instruction reads SysTick.  Latency = (edge stamp - handler stamp) -
    2 ticks; the edge is seen up to one poll loop (about 6 cycles) late."""
    pins = ctx.pins
    h = Code(declare=False).ldr(5, 1).movs(7, 1).str(7, 0, T_INTR).bx(14)     # stamp, clear, return
    m.setup(handler=h.assemble(), irq=16)                                     # IRQ 0 = TIMER_IRQ_0
    out = {}
    for mode in ('spin', 'wfi'):
        vals, ends = [], {}
        for _ in range(ALARM_REPEATS):
            _dw(t, 0xE000E180, M32, pins)                  # NVIC: nothing but IRQ 0
            _dw(t, 0xE000E280, M32, pins)
            _dw(t, TIMER + T_INTR, 1, pins)
            _dw(t, TIMER + T_INTE, 1, pins)
            _dw(t, 0xE000E100, 1, pins)
            c = Code(declare=False).movs(7, 0)
            _wait_edge(c, 0, T_RAWL, 3, 4)
            c.ldr(6, 1).adds_i(4, 2).str(4, 0, T_ALARM0)
            c.label('spin')
            if mode == 'wfi':
                c.wfi()
            c.cmp_i(7, 1).bne('spin').bkpt(Mini.DONE)
            r = m.call(c.assemble(), {0: TIMER, 1: SYST + 8}, primask=0, timeout=1.0)
            ends[r['end']] = ends.get(r['end'], 0) + 1
            if r['end'] == 'done' and cpt:
                vals.append(round(((r['regs'][6] - r['regs'][5]) & 0xFFFFFF) - 2 * cpt, 1))
        _dw(t, 0xE000E180, 1, pins)
        _dw(t, TIMER + T_INTE, 0, pins)
        out[mode] = {'cycles': sorted(vals), 'min': min(vals) if vals else None, 'max': max(vals) if vals else None,
                     'ends': ends}
    m.setup()
    return {'value': {k: v['min'] for k, v in out.items()}, 'detail': out,
            'note': 'cycles from the alarm tick to the handler\'s first load, minus the poll lag (0..~6 cycles)'}


def _uart_regs(t):
    return {o: _dr(t, UART1 + o) for o in (U_IBRD, U_FBRD, U_LCR_H, U_CR, U_IFLS, U_IMSC)}


def _drain(t, n=64):
    got = 0
    for _ in range(n):
        fr = _dr(t, UART1 + U_FR)
        if fr is None or fr & 0x10:
            break
        _dr(t, UART1 + U_DR, side_effect_ok=True)
        got += 1
    return got


def _flashed_uart(ctx, t, m, ratio):
    pins = ctx.pins
    res, bad = {}, []
    done = _dr(t, RESET_DONE)
    if done is None:
        return {'error': 'RESETS is not readable'}
    was_in_reset = not done >> UART1_RESET_BIT & 1
    if was_in_reset and not release(t, 1 << UART1_RESET_BIT):
        return {'error': 'UART1 did not come out of reset'}
    orig = _uart_regs(t)
    if orig[U_CR] is None:
        if was_in_reset:
            _dw(t, 0x4000E000, 1 << UART1_RESET_BIT, pins)            # back into reset, as it was
        return {'error': 'UART1 is not readable through the debugger'}
    try:
        def setup(ibrd, fbrd, cr):
            _dw(t, UART1 + U_CR, 0, pins)
            _dw(t, UART1 + U_IBRD, ibrd, pins)
            _dw(t, UART1 + U_FBRD, fbrd, pins)
            _dw(t, UART1 + U_LCR_H, 0x70, pins)              # 8 bits, FIFOs on (also latches the divisor)
            _dw(t, UART1 + U_IMSC, 0, pins)
            _dw(t, UART1 + U_ICR, 0x7FF, pins)
            _dw(t, UART1 + U_CR, cr, pins)
        # FIFO depth: transmit off, fill until TXFF; then transmit into the receive FIFO, past full
        setup(67, 52, 0x281)                                  # UARTEN, LBE, RXE (TXE off)
        _drain(t)
        tx = 0
        for i in range(40):
            if (_dr(t, UART1 + U_FR) or 0) & 0x20:
                break
            _dw(t, UART1 + U_DR, 0x41 + i, pins)
            tx += 1
        _dw(t, UART1 + U_CR, 0x381, pins)                     # TXE on: they loop back
        time.sleep(0.05)
        for i in range(4):                                    # four more than the receive FIFO holds
            _dw(t, UART1 + U_DR, 0x61 + i, pins)
        time.sleep(0.02)
        fr, ris = _dr(t, UART1 + U_FR), _dr(t, UART1 + U_RIS)
        rx = _drain(t, 64)
        res['tx_fifo_depth'] = {'value': tx, 'predicted': 32}
        res['rx_fifo_depth'] = {'value': rx, 'predicted': 32, 'fr_full': _x(fr), 'overrun_ris': _bit(ris, 10)}
        if tx != 32:
            bad.append('TX FIFO depth')
        if rx != 32 or not _bit(ris, 10):
            bad.append('RX FIFO depth / overrun')
        # one character, timed on the core: received (RXFE clear), sent (BUSY clear), receive timeout (RTRIS)
        for name, ibrd, fbrd in BAUDS:
            div = ibrd + fbrd / 64
            bit = 16 * div * ratio                            # clk_sys cycles per bit
            rows = []
            for _ in range(3):
                setup(ibrd, fbrd, 0x381)
                _drain(t)
                _dw(t, UART1 + U_ICR, 0x7FF, pins)
                c = Code(declare=False)
                c.ldr(6, 1).mov(8, 6).str(2, 0, U_DR)
                c.label('w1').ldr(5, 0, U_FR).tst(5, 3).bne('w1').ldr(6, 1).mov(9, 6)
                c.movs(7, 8).label('w2').ldr(5, 0, U_FR).tst(5, 7).bne('w2').ldr(6, 1).mov(10, 6)
                c.label('w3').ldr(5, 0, U_RIS).tst(5, 4).beq('w3').ldr(6, 1).mov(11, 6).bkpt(Mini.DONE)
                r = m.call(c.assemble(), {0: UART1, 1: SYST + 8, 2: 0x55, 3: 0x10, 4: 0x40}, timeout=2.0)
                if r['end'] != 'done':
                    rows.append({'ended': r['end']})
                    continue
                g = r['regs']
                s0, s1, s2, s3 = g[8], g[9], g[10], g[11]
                rows.append({'received': (s0 - s1) & 0xFFFFFF, 'sent': (s0 - s2) & 0xFFFFFF,
                             'timeout_after_received': (s1 - s3) & 0xFFFFFF})
            okr = [x for x in rows if 'received' in x]
            row = {'ibrd': ibrd, 'fbrd': fbrd, 'cycles_per_bit_predicted': round(bit, 2), 'runs': rows}
            if okr:
                row['received_bits'] = round(min(x['received'] for x in okr) / bit, 2)
                row['sent_bits'] = round(min(x['sent'] for x in okr) / bit, 2)
                row['timeout_bits'] = round(min(x['timeout_after_received'] for x in okr) / bit, 2)
                if not 9 <= row['received_bits'] <= 10.5 or not 9.5 <= row['sent_bits'] <= 11:
                    bad.append(f"character time at {name}")
                if not 31 <= row['timeout_bits'] <= 34:
                    bad.append(f"receive timeout at {name}")
            row['predicted'] = {'received_bits': 9.5, 'sent_bits': 10, 'timeout_bits': 32,
                                'from': 'PL011 TRM: stop bit sampled mid-bit; timeout after 32 bit periods'}
            res[f"character {name}"] = row
        # the flags as one character goes, at the fast rate: FR and SysTick sampled into a buffer
        setup(1, 0, 0x381)
        _drain(t)
        c = Code(declare=False)
        c.ldr(6, 1).str(6, 3, 0).adds_i(3, 4).str(2, 0, U_DR)
        c.label('loop').ldr(5, 0, U_FR).ldr(6, 1).str(5, 3, 0).str(6, 3, 4).adds_i(3, 8).subs_i(4, 1).bne('loop')
        c.bkpt(Mini.DONE)
        r = m.call(c.assemble(), {0: UART1, 1: SYST + 8, 2: 0x55, 3: Mini.BUF, 4: 100}, timeout=2.0)
        if r['end'] == 'done':
            buf = t.rsp.read_mem(Mini.BUF, 4 + 800)
            s0 = int.from_bytes(buf[:4], 'little')
            tl, last = [], None
            for i in range(100):
                fr = int.from_bytes(buf[4 + 8 * i:8 + 8 * i], 'little') & 0xFF
                st = int.from_bytes(buf[8 + 8 * i:12 + 8 * i], 'little')
                if fr != last:
                    tl.append({'cycles': (s0 - st) & 0xFFFFFF, 'fr': _x(fr, 2),
                               'busy': fr >> 3 & 1, 'rxfe': fr >> 4 & 1, 'txfe': fr >> 7 & 1})
                    last = fr
            res['flag_timeline_fast'] = {'value': tl, 'note': 'UARTFR each time it changed after a DR write'}
        else:
            res['flag_timeline_fast'] = {'value': None, 'ended': r['end']}
    finally:
        try:
            _dw(t, UART1 + U_CR, 0, pins)
            _drain(t)
            if was_in_reset:
                _dw(t, 0x4000E000, 1 << UART1_RESET_BIT, pins)        # RESETS SET alias: back into reset
            else:
                for o in (U_IBRD, U_FBRD, U_LCR_H, U_IFLS, U_IMSC, U_CR):
                    if orig[o] is not None:
                        _dw(t, UART1 + o, orig[o], pins)
        except Exception as e:                                         # noqa: BLE001
            ctx.log(f"UART1 not fully restored: {e}")
    res['_differences'] = bad
    return res


def _flashed_gpio(ctx, t, m, ratio):
    """The pull-change latency on SYNC_PIN with the synchroniser on and
    bypassed (SYSCFG is out of reset in the flashed state)."""
    pins = ctx.pins
    pad = pad_addr(SYNC_PIN)
    orig_pad, orig_bypass = _dr(t, pad), _dr(t, SYSCFG_BYPASS)
    if orig_pad is None:
        return {'error': 'PADS_BANK0 is not readable'}
    res, bad = {}, []
    try:
        for bypass in (False, True):
            if bypass:
                if orig_bypass is None:
                    res['bypass'] = {'value': None, 'error': 'SYSCFG is not readable'}
                    break
                _dw(t, SYSCFG_BYPASS, orig_bypass | 1 << SYNC_PIN, pins)
            rise, fall, ends = {}, {}, {}
            for k in FLASHED_SYNC_K:
                _dw(t, pad, PAD_DOWN, pins)
                c = Code(declare=False)
                c.str(2, 1).nop(k).ldr(4, 0, GPIO_IN).delay(7, 250).str(3, 1).nop(k).ldr(5, 0, GPIO_IN)
                c.delay(7, 250).bkpt(Mini.DONE)
                r = m.call(c.assemble(), {0: SIO, 1: pad, 2: PAD_UP, 3: PAD_DOWN}, timeout=1.0)
                ends[r['end']] = ends.get(r['end'], 0) + 1
                if r['end'] == 'done':
                    rise[k] = bool(r['regs'][4] >> SYNC_PIN & 1)
                    fall[k] = not r['regs'][5] >> SYNC_PIN & 1
            res['bypass' if bypass else 'synchronised'] = {
                'value': {'rise_seen_from_k': first_from(rise), 'fall_seen_from_k': first_from(fall)},
                'ends': ends, 'rise': rise, 'fall': fall}
        a = res.get('synchronised', {}).get('value', {}) or {}
        b = res.get('bypass', {}).get('value', {}) or {}
        if a.get('rise_seen_from_k') is not None and b.get('rise_seen_from_k') is not None:
            d = a['rise_seen_from_k'] - b['rise_seen_from_k']
            res['synchroniser_cycles'] = {'value': d, 'predicted': 2}
            if d != 2:
                bad.append('synchroniser cycles')
    finally:
        try:
            _dw(t, pad, orig_pad, pins)
            if orig_bypass is not None:
                _dw(t, SYSCFG_BYPASS, orig_bypass, pins)
        except Exception as e:                                         # noqa: BLE001
            ctx.log(f"GPIO not fully restored: {e}")
    res['_differences'] = bad
    return res
