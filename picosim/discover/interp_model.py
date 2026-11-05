"""
Reference models of the RP2040 SIO's divider and interpolators, written
from the datasheet (§2.3.1.5, §2.3.1.6) and the Pico SDK's register
descriptions (hardware/regs/sio.h), not from any emulator.

Campaign G compares the chip with these; the stand-in uses them too (as
SioModel), so a stand-in run checks the campaign's own case code and
decoding, not the chip.

Interpolator lane i, as the datasheet draws it (Figure 8):

    input   = CROSS_INPUT ? ACCUM[1-i] : ACCUM[i]
    shifted = input >> SHIFT                      (logical, "SHIFT: Logical
                                                   right-shift applied to
                                                   accumulator before masking")
    sm      = shifted & mask(MASK_LSB..MASK_MSB), sign-extended from MASK_MSB
              when SIGNED                         ("shift and mask value")
    result  = BASE[i] + (ADD_RAW ? input : sm)
    FULL    = BASE2 + sm0 + sm1                   (ADD_RAW does not affect it)

    PEEK/POP_LANEi = result | FORCE_MSB << 28     (FORCE_MSB is not in the
                                                   internal datapath)
    POP: ACCUM0 = CROSS_RESULT0 ? result1 : result0, ACCUM1 likewise
    ACCUMi_ADD read = sm_i (BASE not added); write: ACCUM[i] += value
    BASE_1AND0 write: BASE0 = low 16 bits, BASE1 = high 16 bits, each
                      sign-extended if its lane is SIGNED

INTERP0 lane 0 BLEND: alpha = sm1 & 0xFF;
    LANE1 = (BASE0 * (256 - alpha) + BASE1 * alpha) >> 8, with BASE0/1
            signed or not as lane 1's SIGNED says (this is BASE0 +
            (BASE1 - BASE0) * alpha / 256, rounded down);
    LANE0 = alpha ("does not have BASE0 added (yields only the 8 LSBs of
            lane 1 shift+mask value)");
    FULL  = BASE2 + sm0.
INTERP1 lane 0 CLAMP: LANE0 = sm0 clamped to [BASE0, BASE1], signed or
    not as lane 0's SIGNED says (below BASE0 gives BASE0; otherwise above
    BASE1 gives BASE1).

Where the datasheet can be read two ways the model has a variant, so the
campaign can say which reading the chip follows:
    shift       'logical' (default) or 'rotate'
    blend_lane0 'alpha' (default) or 'sm0' (lane 0 shift+mask, BASE0 not added)
    overf       'lane' (default: the lane's own, possibly crossed, input)
                or 'accum' (ACCUM0/ACCUM1 themselves, as the field names say)
    add_width   32 (default: ACCUMx_ADD adds the whole word) or 24 (only
                bits 23:0, the field the SVD declares) or -24 (bits 23:0
                sign-extended)

The divider (div_model): unsigned results are p // q and p % q; signed
results truncate toward zero, the remainder taking the dividend's sign.
INT_MIN / -1 gives 0x80000000 remainder 0 (the 32-bit wrap).  Divide by 0
is not specified by the datasheet; the prediction is what a sign-magnitude
restoring divider gives: unsigned quotient 0xFFFFFFFF, remainder p; signed
quotient -1 for p >= 0 and +1 for p < 0, remainder p.
"""

M32 = 0xFFFFFFFF

# SIO offsets
DIV_UDIVIDEND, DIV_UDIVISOR, DIV_SDIVIDEND, DIV_SDIVISOR = 0x60, 0x64, 0x68, 0x6C
DIV_QUOTIENT, DIV_REMAINDER, DIV_CSR = 0x70, 0x74, 0x78
INTERP0, INTERP1 = 0x80, 0xC0
FIFO_ST, FIFO_WR, FIFO_RD, SPINLOCK_ST, SPINLOCK0 = 0x50, 0x54, 0x58, 0x5C, 0x100
DIV_CYCLES = 8

# interpolator register offsets (from INTERPn)
ACCUM0, ACCUM1, BASE0, BASE1, BASE2 = 0x00, 0x04, 0x08, 0x0C, 0x10
POP_LANE0, POP_LANE1, POP_FULL = 0x14, 0x18, 0x1C
PEEK_LANE0, PEEK_LANE1, PEEK_FULL = 0x20, 0x24, 0x28
CTRL_LANE0, CTRL_LANE1, ACCUM0_ADD, ACCUM1_ADD, BASE_1AND0 = 0x2C, 0x30, 0x34, 0x38, 0x3C

# CTRL_LANEx fields
SHIFT, MASK_LSB, MASK_MSB = 0, 5, 10
SIGNED, CROSS_INPUT, CROSS_RESULT, ADD_RAW = 1 << 15, 1 << 16, 1 << 17, 1 << 18
FORCE_MSB = 19
BLEND, CLAMP = 1 << 21, 1 << 22
OVERF0, OVERF1, OVERF = 1 << 23, 1 << 24, 1 << 25
# writable bits (sio.h *_BITS without the read-only OVERF flags)
CTRL_WRITABLE = {(0, 0): 0x003FFFFF, (0, 1): 0x001FFFFF, (1, 0): 0x005FFFFF, (1, 1): 0x001FFFFF}

DEFAULT_VARIANT = {'shift': 'logical', 'blend_lane0': 'alpha', 'overf': 'lane', 'add_width': 32}
ALTERNATIVES = {'shift': ('rotate',), 'blend_lane0': ('sm0',), 'overf': ('accum',), 'add_width': (24, -24)}


def s32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


def ctrl(shift=0, lsb=0, msb=31, signed=False, cross_input=False, cross_result=False, add_raw=False,
         force_msb=0, blend=False, clamp=False):
    """A CTRL_LANEx value from its fields (interp_default_config() is ctrl())."""
    return ((shift & 31) | (lsb & 31) << MASK_LSB | (msb & 31) << MASK_MSB | (SIGNED if signed else 0)
            | (CROSS_INPUT if cross_input else 0) | (CROSS_RESULT if cross_result else 0)
            | (ADD_RAW if add_raw else 0) | (force_msb & 3) << FORCE_MSB
            | (BLEND if blend else 0) | (CLAMP if clamp else 0))


# ── the divider ─────────────────────────────────────────────────────────────

def div_model(dividend, divisor, signed):
    """(quotient, remainder) as 32-bit words, as the divider should give them."""
    p, q = dividend & M32, divisor & M32
    if not signed:
        if q == 0:
            return M32, p
        return p // q, p % q
    sp, sq = s32(p), s32(q)
    if sq == 0:
        return (M32 if sp >= 0 else 1), p
    mq, mr = abs(sp) // abs(sq), abs(sp) % abs(sq)
    quo = -mq if (sp < 0) != (sq < 0) else mq
    rem = -mr if sp < 0 else mr
    return quo & M32, rem & M32


# ── the interpolators ──────────────────────────────────────────────────────

class Interp:
    """One interpolator (num 0 or 1) of one core."""

    def __init__(self, num, variant=None):
        self.num = num
        self.v = {**DEFAULT_VARIANT, **(variant or {})}
        self.accum = [0, 0]
        self.base = [0, 0, 0]
        self.ctrl = [0, 0]

    def copy(self, variant=None):
        o = Interp(self.num, variant if variant is not None else self.v)
        o.accum, o.base, o.ctrl = list(self.accum), list(self.base), list(self.ctrl)
        return o

    def _lane(self, i):
        """(sm, raw input, overflow) for lane i."""
        c = self.ctrl[i]
        inp = self.accum[1 - i] if c & CROSS_INPUT else self.accum[i]
        sh = c & 31
        if self.v['shift'] == 'rotate':
            shifted = ((inp >> sh) | (inp << (32 - sh))) & M32 if sh else inp
        else:
            shifted = inp >> sh
        lsb, msb = (c >> MASK_LSB) & 31, (c >> MASK_MSB) & 31
        mask = ((1 << (msb + 1)) - 1) & ~((1 << lsb) - 1) & M32
        sm = shifted & mask
        if c & SIGNED and sm >> msb & 1:
            sm |= M32 & ~((1 << (msb + 1)) - 1)
        src = self.accum[i] if self.v['overf'] == 'accum' else inp
        src = (src >> sh) if self.v['shift'] != 'rotate' else shifted
        overf = bool(src & M32 & ~((1 << (msb + 1)) - 1))
        return sm & M32, inp, overf

    def results(self):
        """{'lane0', 'lane1', 'full'} (the internal 32-bit results, without
        FORCE_MSB), and the raw shift+mask values 'sm0', 'sm1'."""
        sm0, raw0, _ = self._lane(0)
        sm1, raw1, _ = self._lane(1)
        c0, c1 = self.ctrl
        add0 = raw0 if c0 & ADD_RAW else sm0
        add1 = raw1 if c1 & ADD_RAW else sm1
        b0, b1, b2 = self.base
        if self.num == 0 and c0 & BLEND:
            alpha = sm1 & 0xFF
            x0, x1 = (s32(b0), s32(b1)) if c1 & SIGNED else (b0, b1)
            r1 = (x0 * (256 - alpha) + x1 * alpha) >> 8
            r0 = alpha if self.v['blend_lane0'] == 'alpha' else add0
            full = b2 + sm0
        elif self.num == 1 and c0 & CLAMP:
            if c0 & SIGNED:
                x, lo, hi = s32(sm0), s32(b0), s32(b1)
            else:
                x, lo, hi = sm0, b0, b1
            r0 = lo if x < lo else hi if x > hi else x
            r1 = b1 + add1
            full = b2 + sm0 + sm1
        else:
            r0, r1, full = b0 + add0, b1 + add1, b2 + sm0 + sm1
        return {'lane0': r0 & M32, 'lane1': r1 & M32, 'full': full & M32, 'sm0': sm0, 'sm1': sm1}

    def _shown(self, lane, value):
        return value | ((self.ctrl[lane] >> FORCE_MSB) & 3) << 28

    def ctrl_read(self, lane):
        v = self.ctrl[lane] & CTRL_WRITABLE[(self.num, lane)]
        if lane == 0:
            o0, o1 = self._lane(0)[2], self._lane(1)[2]
            v |= (OVERF0 if o0 else 0) | (OVERF1 if o1 else 0) | (OVERF if o0 or o1 else 0)
        return v

    def peek(self):
        r = self.results()
        return {'lane0': self._shown(0, r['lane0']), 'lane1': self._shown(1, r['lane1']), 'full': r['full']}

    def pop(self):
        """The PEEK values, then the accumulators written back (POP)."""
        r = self.results()
        out = {'lane0': self._shown(0, r['lane0']), 'lane1': self._shown(1, r['lane1']), 'full': r['full']}
        c0, c1 = self.ctrl
        self.accum = [r['lane1'] if c0 & CROSS_RESULT else r['lane0'],
                      r['lane0'] if c1 & CROSS_RESULT else r['lane1']]
        return out

    def read(self, off):
        if off in (ACCUM0, ACCUM1):
            return self.accum[off // 4]
        if off in (BASE0, BASE1, BASE2):
            return self.base[(off - BASE0) // 4]
        if off in (POP_LANE0, POP_LANE1, POP_FULL):
            return self.pop()[('lane0', 'lane1', 'full')[(off - POP_LANE0) // 4]]
        if off in (PEEK_LANE0, PEEK_LANE1, PEEK_FULL):
            return self.peek()[('lane0', 'lane1', 'full')[(off - PEEK_LANE0) // 4]]
        if off in (CTRL_LANE0, CTRL_LANE1):
            return self.ctrl_read((off - CTRL_LANE0) // 4)
        if off in (ACCUM0_ADD, ACCUM1_ADD):
            return self.results()['sm0' if off == ACCUM0_ADD else 'sm1']
        return 0                                    # BASE_1AND0 is write-only

    def write(self, off, value):
        value &= M32
        if off in (ACCUM0, ACCUM1):
            self.accum[off // 4] = value
        elif off in (BASE0, BASE1, BASE2):
            self.base[(off - BASE0) // 4] = value
        elif off in (CTRL_LANE0, CTRL_LANE1):
            lane = (off - CTRL_LANE0) // 4
            self.ctrl[lane] = value & CTRL_WRITABLE[(self.num, lane)]
        elif off in (ACCUM0_ADD, ACCUM1_ADD):
            w = self.v['add_width']
            add = value if w == 32 else (value & 0xFFFFFF)
            if w == -24 and add & 0x800000:
                add |= 0xFF000000
            i = (off - ACCUM0_ADD) // 4
            self.accum[i] = (self.accum[i] + add) & M32
        elif off == BASE_1AND0:
            lo, hi = value & 0xFFFF, value >> 16
            if self.ctrl[0] & SIGNED and lo & 0x8000:
                lo |= 0xFFFF0000
            if self.ctrl[1] & SIGNED and hi & 0x8000:
                hi |= 0xFFFF0000
            self.base[0], self.base[1] = lo, hi


# ── the SIO blocks the stand-in lacks ──────────────────────────────────────

class SioModel:
    """The divider (with its 8-cycle timing), both interpolators, the
    spinlocks and FIFO_ST, at their SIO offsets (0x050-0x17F), for the
    stand-in.  `now` is the processor clock count at the access.  While a
    division is running the result registers still read the previous
    results (on the chip they are undefined then)."""

    def __init__(self):
        self.dividend = self.divisor = 0
        self.quotient = self.remainder = 0
        self.old = (0, 0)
        self.done_at = 0
        self.dirty = False
        self.interp = [Interp(0), Interp(1)]
        self.locks = 0

    def _start(self, signed, now):
        self.old = (self.quotient, self.remainder)
        self.quotient, self.remainder = div_model(self.dividend, self.divisor, signed)
        self.done_at = now + 1 + DIV_CYCLES          # the store's own cycle, then 8
        self.dirty = True

    def read(self, off, now):
        busy = now < self.done_at
        if off == FIFO_ST:
            return 0x2                                # TX not full, RX empty: core 1 waiting in the ROM
        if off == SPINLOCK_ST:
            return self.locks
        if off in (DIV_UDIVIDEND, DIV_SDIVIDEND):
            return self.dividend
        if off in (DIV_UDIVISOR, DIV_SDIVISOR):
            return self.divisor
        if off == DIV_QUOTIENT:
            self.dirty = False
            return self.old[0] if busy else self.quotient
        if off == DIV_REMAINDER:
            return self.old[1] if busy else self.remainder
        if off == DIV_CSR:
            return (0 if busy else 1) | (2 if self.dirty else 0)
        if INTERP0 <= off < INTERP0 + 0x80:
            return self.interp[(off - INTERP0) // 0x40].read((off - INTERP0) % 0x40)
        if SPINLOCK0 <= off < SPINLOCK0 + 0x80:
            bit = 1 << ((off - SPINLOCK0) // 4)
            if self.locks & bit:
                return 0
            self.locks |= bit
            return bit
        return 0

    def write(self, off, value, now):
        value &= M32
        if off in (DIV_UDIVIDEND, DIV_SDIVIDEND):
            self.dividend = value
            self._start(off == DIV_SDIVIDEND, now)
        elif off in (DIV_UDIVISOR, DIV_SDIVISOR):
            self.divisor = value
            self._start(off == DIV_SDIVISOR, now)
        elif off in (DIV_QUOTIENT, DIV_REMAINDER):
            if off == DIV_QUOTIENT:
                self.quotient = value
            else:
                self.remainder = value
            self.done_at, self.dirty = 0, True
        elif INTERP0 <= off < INTERP0 + 0x80:
            self.interp[(off - INTERP0) // 0x40].write((off - INTERP0) % 0x40, value)
        elif SPINLOCK0 <= off < SPINLOCK0 + 0x80:
            self.locks &= ~(1 << ((off - SPINLOCK0) // 4))
