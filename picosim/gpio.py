"""
Virtual GPIO device for the ARMv6-M simulator.

Implements the SIO GPIO, IO_BANK0, PADS_BANK0, SYSCFG and RESETS registers
of the RP2040 at their real hardware addresses.

GPIO is a MemoryBlock whose handles() covers five disjoint address ranges:
  0x40004000 – 0x400040FF   SYSCFG    (PROC_IN_SYNC_BYPASS and the rest, stored)
  0x4000C000 – 0x4000C0FF   RESETS
  0x40014000 – 0x400147FF   IO_BANK0  (STATUS, CTRL, INTR, INTE/INTF/INTS)
  0x4001C000 – 0x4001C0FF   PADS_BANK0 (pulls, input enable, output disable)
  0xD0000000 – 0xD000003F   SIO GPIO registers
The APB blocks also answer at their atomic aliases, as on the RP2040
(datasheet §2.1.2): a write to the register's address + 0x1000 XORs it with
the value, + 0x2000 sets the value's bits, + 0x3000 clears them.

One pin, as the RP2040 builds it (datasheet §2.19, IO_BANK0 STATUS names):

  output:  SIO OUT/OE  (only when FUNCSEL = 5)  ->  OUTFROMPERI / OEFROMPERI
           -> CTRL.OUTOVER / OEOVER  ->  OUTTOPAD / OETOPAD
           -> pad: drives the pin unless PADS.OD (output disable) is set
  pin:     driven by the chip, else by something outside (set_external),
           else by the pad's pull: up, down, both (the bus keeper holds the
           last level) or none (floating: the pin keeps its last level, as
           measured on a real Pico, Level 5 campaign G)
  input:   INFROMPAD = the pin's level if PADS.IE (input enable), else 0
           -> CTRL.INOVER -> INTOPERI = what GPIO_IN reads
  irq:     INTR's level and edge bits follow CTRL.IRQOVER applied to
           INFROMPAD (measured: INOVER does not reach INTR, IRQOVER does);
           PROCn_INTS = (INTR | PROCn_INTF) & PROCn_INTE.  Nothing interrupts
           the core yet (Stage 4); a program can poll the bits.

Time (GPIO.clock: the core's live RP2040 cycle count, CPUCore.m0_cycles):
  a pull change moves the pin PULL_RISE_CYCLES / PULL_FALL_CYCLES after the
  store that made it; GPIO_IN sees the pin SYNC_CYCLES later again through
  the input synchroniser, unless the pin's SYSCFG PROC_IN_SYNC_BYPASS bit is
  set.  STATUS and INTR see the pin itself.  Times are counted, as on the
  chip and in campaign F's measurement, from the start of the store to the
  start of the load.  A pin driven by the chip or from outside changes at
  once.  The numbers are campaign F's, measured on the chip at 125 MHz.

Register GPIO with the CPU's Memory object so reads/writes to those
addresses are routed here automatically.
"""

from .memory import MemoryBlock

NUM_PINS = 30   # RP2040 has GPIO0–GPIO29

# Peripheral base addresses
_SIO_BASE        = 0xD0000000
_SYSCFG_BASE     = 0x40004000
_IO_BANK0_BASE   = 0x40014000
_PADS_BANK0_BASE = 0x4001C000
_RESETS_BASE     = 0x4000C000

# Address ranges: (base, size) — size rounded up to a convenient power of two
_RANGES = (
    (_SIO_BASE,        0x40),
    (_SYSCFG_BASE,     0x100),
    (_IO_BANK0_BASE,   0x800),
    (_PADS_BANK0_BASE, 0x100),
    (_RESETS_BASE,     0x100),
)
_ALIASED = (_SYSCFG_BASE, _IO_BANK0_BASE, _PADS_BANK0_BASE, _RESETS_BASE)   # + 0x1000 XOR, 0x2000 SET, 0x3000 CLR

_PIN_MASK = (1 << NUM_PINS) - 1
_CTRL_MASK = 0x3033331F   # IRQOVER, INOVER, OEOVER, OUTOVER, FUNCSEL
FUNC_SIO = 5              # FUNCSEL value that connects a pin to the SIO GPIO registers

# Pad-register bit positions (PADS_BANK0_GPIOx)
_PADS_PDE = 1 << 2   # pull-down enable
_PADS_PUE = 1 << 3   # pull-up enable
_PADS_IE  = 1 << 6   # input enable
_PADS_OD  = 1 << 7   # output disable (wins over every output enable)
_PADS_RESET = 0x56   # IE, 4 mA drive, pull-down, Schmitt trigger (datasheet reset value)
_PADS_SWCLK, _PADS_SWD = 0x7C, 0x80                 # the debug pads, after GPIO29's
_PADS_SWCLK_RESET, _PADS_SWD_RESET = 0xDA, 0x5A     # SVD reset values

# Pull and synchroniser timing at 125 MHz (cycles from the start of the PADS
# store to the start of the GPIO_IN load that first sees the new level),
# measured by campaign F on 2026-11-17 (hwfacts/peripherals/gpio_flashed.json):
# GP2 of a Pico W on a breadboard, nothing wired to it, clk_sys 125 MHz.
# With the synchroniser bypassed the load first saw the rise at 5 + 49 and
# the fall at 5 + 67 cycles; with it on, at 5 + 52 and 5 + 70.  A pin with
# more wiring on it (more capacitance) takes longer.  None would mean "at once".
PULL_RISE_CYCLES = 54        # synchroniser bypassed
PULL_FALL_CYCLES = 72
SYNC_CYCLES = 3              # the synchroniser's own delay (datasheet: 2; measured: 3, rise and fall)

# IO_BANK0 GPIOn_STATUS bits
ST_OUTFROMPERI, ST_OUTTOPAD = 1 << 8, 1 << 9
ST_OEFROMPERI, ST_OETOPAD   = 1 << 12, 1 << 13
ST_INFROMPAD, ST_INTOPERI   = 1 << 17, 1 << 19
ST_IRQFROMPAD, ST_IRQTOPROC = 1 << 24, 1 << 26

# INTR / INTE / INTF / INTS: 4 bits per pin, 8 pins per register
LEVEL_LOW, LEVEL_HIGH, EDGE_LOW, EDGE_HIGH = 1, 2, 4, 8
_INTR      = 0x0F0                          # INTR0-3
_INT_GROUPS = {0x100: 'proc0', 0x130: 'proc1', 0x160: 'dormant'}   # INTE at +0, INTF at +0x10, INTS at +0x20

# SYSCFG registers: offset -> (reset value, writable mask)
_SYSCFG = {
    0x00: (0, 0xFFFFFFFF),        # PROC0_NMI_MASK
    0x04: (0, 0xFFFFFFFF),        # PROC1_NMI_MASK
    0x08: (0x10000000, 0xFF000000),   # PROC_CONFIG: DAP_INSTIDs (PROCn_HALTED read 0: the cores run)
    0x0C: (0, _PIN_MASK),         # PROC_IN_SYNC_BYPASS (GPIO0-29)
    0x10: (0, 0x3F),              # PROC_IN_SYNC_BYPASS_HI (QSPI pins)
    0x14: (0x66, 0xEE),           # DBGFORCE (PROCn_SWDO read-only)
    0x18: (0, 0xFF),              # MEMPOWERDOWN (stored; picosim's memory stays powered)
}


def _override(mode, value):
    """IO_BANK0 CTRL overrides: 0 = normal, 1 = invert, 2 = force 0, 3 = force 1."""
    return (bool(value), not value, False, True)[mode & 3]


class GPIO(MemoryBlock):
    """Simulated RP2040 GPIO peripheral block.

    Inherits from MemoryBlock so it can be registered directly with a Memory
    object.  handles() is overridden to cover the peripheral ranges;
    read32/write32 dispatch to the appropriate sub-handler.
    """

    # State a copy of the machine needs (explain.py's forecast): copy_from().
    _STATE = ('_oe', '_out', '_resets', '_resets_done', '_pads_written')
    _LISTS = ('_ext', '_ctrl', '_pads', '_base', '_irq_in', '_edges', '_syscfg')

    def __init__(self):
        super().__init__()                  # no single base/size; handles() overrides
        self._oe  = 0                       # SIO output-enable bitmask
        self._out = 0                       # SIO output-level bitmask
        self._ext = [None] * NUM_PINS       # external drive: None=Z, True=1, False=0
        self._ctrl = [0x1F] * NUM_PINS      # IO_BANK0 CTRL (FUNCSEL + overrides)
        # PADS_BANK0 registers (GPIO0-29, then SWCLK and SWD).  The pulls a pin
        # has come from here; _pads_written marks the pins a program has
        # configured, which the board view draws (an untouched pin is drawn
        # floating, a deliberate compromise: its pad says pull-down).
        self._pads = [_PADS_RESET] * NUM_PINS + [_PADS_SWCLK_RESET, _PADS_SWD_RESET]
        self._pads_written = 0
        self._voltage_select = 0
        # Each pin's level over time: _base before the changes in _hist, a
        # list of (cycle, level), some of them possibly still to come (a pull
        # on its way).  A floating pin and the bus keeper hold the level.
        # At reset every pin is pulled down, so 0.
        self.clock = lambda: 0              # the loader points this at core.m0_cycles
        self._base = [False] * NUM_PINS
        self._hist = [[] for _ in range(NUM_PINS)]
        self._moving = set()                # pins with a history still to fold (only they change with time)
        self._gin = 0                       # GPIO_IN while nothing moves (set by _update)
        self._irq_in = [False] * NUM_PINS   # the level INTR follows (IRQOVER applied)
        self._edges = [0] * NUM_PINS        # latched EDGE_LOW / EDGE_HIGH bits
        self._int = {g: [[0] * 4, [0] * 4] for g in _INT_GROUPS.values()}   # INTE, INTF
        self._syscfg = [_SYSCFG[o][0] for o in sorted(_SYSCFG)]
        # RESETS: as the Pico SDK leaves them before main() runs, IO_BANK0
        # (bit 5) and PADS_BANK0 (bit 8) are already out of reset, so a
        # program that waits for RESET_DONE works in both places.
        out = (1 << 5) | (1 << 8)
        self._resets = 0x01FFFFFF & ~out
        self._resets_done = out

    def copy_from(self, other):
        """Make this GPIO an exact copy of `other` (a scratch machine's)."""
        for k in self._STATE:
            setattr(self, k, getattr(other, k))
        for k in self._LISTS:
            setattr(self, k, list(getattr(other, k)))
        self._hist = [list(h) for h in other._hist]
        self._moving = set(other._moving)
        self._gin = other._gin
        self._voltage_select = other._voltage_select
        self._int = {g: [list(e), list(f)] for g, (e, f) in other._int.items()}

    # ── address routing ────────────────────────────────────────────────────────

    def handles(self, addr):
        addr = self._unalias(addr & 0xFFFFFFFF)[0]
        for base, size in _RANGES:
            if base <= addr < base + size:
                return True
        return False

    @staticmethod
    def _unalias(addr):
        """(register address, alias): alias 0 normal, 1 XOR, 2 SET, 3 CLR."""
        for base in _ALIASED:
            if base <= addr < base + 0x4000:
                return base + (addr & 0xFFF), (addr - base) >> 12
        return addr, 0

    def read32(self, addr):
        addr = self._unalias(addr & 0xFFFFFFFF)[0]   # an alias reads the register
        self._advance(self.clock())
        off = addr - _SIO_BASE
        if 0 <= off < 0x40:
            return self._sio_read(off)
        off = addr - _IO_BANK0_BASE
        if 0 <= off < 0x800:
            return self._io_bank0_read(off)
        off = addr - _PADS_BANK0_BASE
        if 0 <= off < 0x100:
            return self._pads_read(off)
        off = addr - _SYSCFG_BASE
        if 0 <= off < 0x100:
            return self._syscfg_read(off)
        off = addr - _RESETS_BASE
        if 0 <= off < 0x100:
            return self._resets_read(off)
        return 0

    def write32(self, addr, val):
        addr, alias = self._unalias(addr & 0xFFFFFFFF)
        val  &= 0xFFFFFFFF
        off = addr - _IO_BANK0_BASE
        if alias and _INTR <= off < _INTR + 0x10:
            # INTR's edge bits clear when a 1 is written.  The SET and XOR
            # aliases write 1s only to the bits given, so they clear just
            # those; the CLR alias writes no 1s and clears nothing.
            if alias != 3:
                self._intr_write(off, val)
            self._update()
            return
        if alias:
            old = self.read32(addr)
            val = (old ^ val, old | val, old & ~val & 0xFFFFFFFF)[alias - 1]
        off = addr - _SIO_BASE
        if 0 <= off < 0x40:
            self._sio_write(off, val)
        elif 0 <= addr - _IO_BANK0_BASE < 0x800:
            self._io_bank0_write(addr - _IO_BANK0_BASE, val)
        elif 0 <= addr - _PADS_BANK0_BASE < 0x100:
            self._pads_write(addr - _PADS_BANK0_BASE, val)
        elif 0 <= addr - _SYSCFG_BASE < 0x100:
            self._syscfg_write(addr - _SYSCFG_BASE, val)
        elif 0 <= addr - _RESETS_BASE < 0x100:
            self._resets_write(addr - _RESETS_BASE, val)
        self._update()

    # ── one pin, output side ──────────────────────────────────────────────────
    # FUNCSEL picks which peripheral drives the pin.  Only SIO (5) is
    # simulated; any other function (including the reset value 0x1F, "null")
    # drives nothing, so SIO_OE / SIO_OUT have no effect until a program
    # writes 5 to the pin's CTRL register — exactly as on the RP2040.

    def _sio(self, i):
        return (self._ctrl[i] & 0x1F) == FUNC_SIO

    def _oe_from_peri(self, i):
        return self._sio(i) and bool(self._oe >> i & 1)

    def _out_from_peri(self, i):
        return self._sio(i) and bool(self._out >> i & 1)

    def _pin_oe(self, i):
        """OETOPAD: the output enable after CTRL.OEOVER."""
        return _override(self._ctrl[i] >> 12, self._oe_from_peri(i))

    def _pin_out(self, i):
        """OUTTOPAD: the output level after CTRL.OUTOVER."""
        return _override(self._ctrl[i] >> 8, self._out_from_peri(i))

    def _drives(self, i):
        """The pad drives the pin: OETOPAD, unless the pad's OD bit is set."""
        return self._pin_oe(i) and not self._pads[i] & _PADS_OD

    # ── one pin, the pin itself and the input side ─────────────────────────────

    def _pull(self, i):
        pad = self._pads[i]
        up, down = bool(pad & _PADS_PUE), bool(pad & _PADS_PDE)
        return 'keeper' if up and down else 'up' if up else 'down' if down else None

    def _level_at(self, i, t):
        """The pin's level at cycle t."""
        v = self._base[i]
        for when, level in self._hist[i]:
            if when > t:
                break
            v = level
        return v

    def _target(self, i):
        """(level, delay): where the pin is going and how long it takes:
        driven by the chip or from outside at once, by a pull after the
        measured time; floating or kept, it stays (None)."""
        if self._drives(i):
            return self._pin_out(i), 0
        if self._ext[i] is not None:
            return self._ext[i], 0
        pull = self._pull(i)
        if pull == 'up':
            return True, PULL_RISE_CYCLES or 0
        if pull == 'down':
            return False, PULL_FALL_CYCLES or 0
        return None, 0

    def _synced(self, i):
        return not self._syscfg[3] >> i & 1          # PROC_IN_SYNC_BYPASS (offset 0x0C)

    def _in_from_pad(self, i, t=None):
        """INFROMPAD at cycle t (now): the pin through the input buffer (0 when IE is off)."""
        t = self.clock() if t is None else t
        return bool(self._pads[i] & _PADS_IE) and bool(self._level_at(i, t))

    def _in_to_peri(self, i, t=None):
        """INTOPERI: INFROMPAD after CTRL.INOVER (STATUS)."""
        return _override(self._ctrl[i] >> 16, self._in_from_pad(i, t))

    def _gpio_in_bit(self, i, now):
        """GPIO_IN's bit: INTOPERI through the processor's synchroniser."""
        return self._in_to_peri(i, now - SYNC_CYCLES if self._synced(i) else now)

    def _fold(self, i, now):
        """Fold pin i's changes that can no longer be looked at into _base.
        Nothing looks further back than the synchroniser (GPIO_IN sees the
        level SYNC_CYCLES ago; STATUS, INTR and the board view see it now)
        and time only goes forward, so a change at or before
        now - SYNC_CYCLES - 1 is history: the level at every time anything
        will ask about is the same with it folded.  The one rule for both
        _advance and _update, so a long run that never reads a GPIO
        register keeps only the changes still on their way (PERF-01)."""
        h = self._hist[i]
        cut = now - SYNC_CYCLES - 1
        n = 0
        while n < len(h) and h[n][0] <= cut:
            n += 1
        if n:
            self._base[i] = h[n - 1][1]
            del h[:n]

    def _advance(self, now):
        """Latch the INTR edges of every change up to `now`, and fold the
        history that can no longer be looked at into _base."""
        for i in list(self._moving):
            h = self._hist[i]
            self._fold(i, now)
            self._latch(i, now)
            if not h:
                # Settled: GPIO_IN's cached bit was computed while the pin
                # was still on its way, so refresh it before dropping the pin.
                self._gin = self._gin & ~(1 << i) | (int(self._gpio_in_bit(i, now)) << i)
                self._moving.discard(i)

    def _latch(self, i, now):
        irq = _override(self._ctrl[i] >> 28, self._in_from_pad(i, now))
        if irq != self._irq_in[i]:
            self._edges[i] |= EDGE_HIGH if irq else EDGE_LOW
            self._irq_in[i] = irq

    def _update(self):
        """After any change: where each pin is going, and the edges INTR latches."""
        now = self.clock()
        for i in range(NUM_PINS):
            h = self._hist[i]
            if h:
                self._fold(i, now)                   # keeps the history bounded (see _fold)
            self._latch(i, now)                      # what happened before this change
            level, delay = self._target(i)
            if h and h[-1][0] > now:                 # a change still on its way ...
                if level == h[-1][1] and h[-1][0] <= now + delay:
                    continue                         # ... to the same level: it goes on as it was
                h.pop()                              # ... overtaken (or the pull was taken away)
            if level is not None and level != self._level_at(i, now):
                h.append((now + delay, level))
            if h:
                self._moving.add(i)
            self._latch(i, now)
        self._gin = self._gpio_in_all(now)

    def pin_level(self, i):
        """The board view's level on pin i: True/False, or None when floating (Z)."""
        now = self.clock()
        if self._drives(i) or self._ext[i] is not None:
            return self._level_at(i, now)
        if not self._pads_written >> i & 1 or self._pull(i) is None:
            return None
        return self._level_at(i, now)

    def _gpio_in_all(self, now):
        result = 0
        for i in range(NUM_PINS):
            if self._gpio_in_bit(i, now):
                result |= 1 << i
        return result

    def _gpio_in(self):
        """GPIO_IN: every pin's INTOPERI, through the synchroniser."""
        if not self._moving:
            return self._gin
        now = self.clock()
        result = self._gin
        for i in self._moving:
            result = result & ~(1 << i) | (int(self._gpio_in_bit(i, now)) << i)
        return result

    # ── SIO ───────────────────────────────────────────────────────────────────

    def _sio_read(self, off):
        if off == 0x04: return self._gpio_in()
        if off == 0x10: return self._out & _PIN_MASK
        if off == 0x20: return self._oe  & _PIN_MASK
        return 0

    def _sio_write(self, off, val):
        m = _PIN_MASK
        if   off == 0x10: self._out  =  val & m            # GPIO_OUT
        elif off == 0x14: self._out |=  val & m            # GPIO_OUT_SET
        elif off == 0x18: self._out &= ~val & m            # GPIO_OUT_CLR
        elif off == 0x1C: self._out ^=  val & m            # GPIO_OUT_XOR
        elif off == 0x20: self._oe   =  val & m            # GPIO_OE
        elif off == 0x24: self._oe  |=  val & m            # GPIO_OE_SET
        elif off == 0x28: self._oe  &= ~val & m            # GPIO_OE_CLR
        elif off == 0x2C: self._oe  ^=  val & m            # GPIO_OE_XOR

    # ── IO_BANK0 ──────────────────────────────────────────────────────────────
    # GPIOn_STATUS at n*8, GPIOn_CTRL at n*8+4 (n = 0..29); INTR0-3 at 0x0F0;
    # PROC0 INTE/INTF/INTS at 0x100/0x110/0x120, PROC1 at 0x130.., DORMANT_WAKE
    # at 0x160..; each of these is 4 registers of 8 pins.

    def status(self, i):
        """GPIOn_STATUS: the pin's signals before and after each override."""
        irq_from_pad = bool(self._ints('proc0', i))
        s = 0
        s |= ST_OUTFROMPERI if self._out_from_peri(i) else 0
        s |= ST_OUTTOPAD if self._pin_out(i) else 0
        s |= ST_OEFROMPERI if self._oe_from_peri(i) else 0
        s |= ST_OETOPAD if self._pin_oe(i) else 0
        s |= ST_INFROMPAD if self._in_from_pad(i) else 0
        s |= ST_INTOPERI if self._in_to_peri(i) else 0
        s |= ST_IRQFROMPAD if irq_from_pad else 0
        s |= ST_IRQTOPROC if _override(self._ctrl[i] >> 28, irq_from_pad) else 0
        return s

    def intr(self, i):
        """Pin i's 4 INTR bits: LEVEL_LOW, LEVEL_HIGH, then the latched edges."""
        return (LEVEL_HIGH if self._irq_in[i] else LEVEL_LOW) | self._edges[i]

    def _ints(self, group, i):
        inte, intf = self._int[group]
        sh = 4 * (i % 8)
        return (self.intr(i) | intf[i // 8] >> sh & 0xF) & inte[i // 8] >> sh & 0xF

    @staticmethod
    def _pins_of(reg):
        return range(8 * reg, min(8 * reg + 8, NUM_PINS))

    def _io_bank0_read(self, off):
        if off < 8 * NUM_PINS:
            pin = off >> 3
            return self._ctrl[pin] if off & 7 == 4 else self.status(pin) if off & 7 == 0 else 0
        if _INTR <= off < _INTR + 0x10:
            reg = (off - _INTR) >> 2
            return sum(self.intr(i) << 4 * (i % 8) for i in self._pins_of(reg))
        for base, group in _INT_GROUPS.items():
            if base <= off < base + 0x30:
                kind, reg = (off - base) >> 4, (off >> 2) & 3
                if kind < 2:
                    return self._int[group][kind][reg]
                return sum(self._ints(group, i) << 4 * (i % 8) for i in self._pins_of(reg))
        return 0

    def _intr_write(self, off, val):
        for i in self._pins_of((off - _INTR) >> 2):
            self._edges[i] &= ~(val >> 4 * (i % 8)) & (EDGE_LOW | EDGE_HIGH)

    def _io_bank0_write(self, off, val):
        if off < 8 * NUM_PINS:
            if off & 7 == 4:
                self._ctrl[off >> 3] = val & _CTRL_MASK
            return
        if _INTR <= off < _INTR + 0x10:
            self._intr_write(off, val)
            return
        for base, group in _INT_GROUPS.items():
            if base <= off < base + 0x20:          # INTE, INTF (INTS is read-only)
                reg = (off >> 2) & 3
                mask = 0xFFFFFFFF if reg < 3 else 0x00FFFFFF
                self._int[group][(off - base) >> 4][reg] = val & mask

    # ── PADS_BANK0 ────────────────────────────────────────────────────────────
    # Offset 0x00: VOLTAGE_SELECT.  0x04 + pin*4: per-pin pad control
    # (OD, IE, DRIVE, PUE, PDE, SCHMITT, SLEWFAST); then SWCLK and SWD.

    def _pads_read(self, off):
        if off == 0: return self._voltage_select
        pin = (off - 4) >> 2
        if not (0 <= pin < len(self._pads)) or off & 3: return 0
        return self._pads[pin]

    def _pads_write(self, off, val):
        if off == 0:
            self._voltage_select = val & 1
            return
        pin = (off - 4) >> 2
        if not (0 <= pin < len(self._pads)) or off & 3: return
        self._pads[pin] = val & 0xFF
        if pin < NUM_PINS:
            self._pads_written |= 1 << pin

    # ── SYSCFG ────────────────────────────────────────────────────────────────

    def _syscfg_read(self, off):
        keys = sorted(_SYSCFG)
        return self._syscfg[keys.index(off)] if off in _SYSCFG else 0

    def _syscfg_write(self, off, val):
        if off in _SYSCFG:
            reset, mask = _SYSCFG[off]
            i = sorted(_SYSCFG).index(off)
            self._syscfg[i] = (self._syscfg[i] & ~mask) | (val & mask)

    # ── RESETS ────────────────────────────────────────────────────────────────
    # Clearing a bit in RESET brings that peripheral out of reset and
    # immediately marks the corresponding RESET_DONE bit.

    def _resets_read(self, off):
        if off == 0x00: return self._resets
        if off == 0x08: return self._resets_done
        return 0

    def _resets_write(self, off, val):
        if off == 0x00:
            released = self._resets & ~val        # bits newly cleared = unreset
            self._resets = val & 0x01FFFFFF
            self._resets_done |= released & 0x01FFFFFF

    # ── interactive: set external pin state ───────────────────────────────────

    def set_external(self, pin, value):
        """Drive an input pin from outside.

        value: None → Z (floating, pull takes effect)
               True / 1 → logic high
               False / 0 → logic low
        Raises ValueError if pin is out of range or is an output.
        """
        if not (0 <= pin < NUM_PINS):
            raise ValueError(f"GPIO pin {pin} out of range (0–{NUM_PINS-1})")
        if self._drives(pin):
            raise ValueError(f"GP{pin} is configured as an output")
        self._ext[pin] = None if value is None else bool(value)
        self._update()

    # ── display ───────────────────────────────────────────────────────────────

    def any_configured(self):
        """True if any pin has been configured (output, pad or override)."""
        return bool(self._oe or self._pads_written or
                    any(e is not None for e in self._ext) or
                    any(c & 0x30033300 for c in self._ctrl))

    def pin_states(self):
        """Per-pin snapshot for the GUI: direction, level, pull and function,
        and the signals along the pin's path (IO_BANK0 STATUS and INTR)."""
        now = self.clock()
        self._advance(now)
        pins = []
        for i in range(NUM_PINS):
            bit = 1 << i
            level = self.pin_level(i)
            pad = self._pads[i]
            ctrl = self._ctrl[i]
            pins.append({
                'pin': i,
                'dir': 'out' if self._drives(i) else 'in',
                'level': None if level is None else int(level),
                'pull': self._pull(i) if self._pads_written & bit else None,
                'driven': self._ext[i] is not None,
                'func': ctrl & 0x1F,
                # SIO is told to drive the pin, but the pin is not connected to SIO
                'sio_unrouted': bool(self._oe & bit) and not self._sio(i),
                'held': int(self._level_at(i, now)),  # the level a floating or kept pin holds
                'reads': int(self._gpio_in_bit(i, now)),   # GPIO_IN's bit
                'moving': bool(self._hist[i] and self._hist[i][-1][0] > now),   # a pull on its way
                'pad': pad,
                'ctrl': ctrl,
                'status': self.status(i),
                'intr': self.intr(i),
            })
        return pins

    def display(self):
        """Return a multi-line string showing all 30 pin states.

        Format per pin:  GPn:Dx
          D = O (output) or I (input)
          x = 0, 1, or Z
        """
        cols = 6
        lines = []
        row = []
        for i in range(NUM_PINS):
            d = 'O' if self._drives(i) else 'I'
            level = self.pin_level(i)
            x = 'Z' if level is None else ('1' if level else '0')
            row.append(f"GP{i:<2}:{d}{x}")
            if len(row) == cols:
                lines.append("  " + "  ".join(row))
                row = []
        if row:
            lines.append("  " + "  ".join(row))
        return "\n".join(lines)
