"""
Virtual GPIO device for the ARMv6-M simulator.

Implements the SIO, IO_BANK0, PADS_BANK0, and RESETS peripheral registers
of the RP2040 at their real hardware addresses.

GPIO is a MemoryBlock whose handles() covers four disjoint address ranges:
  0x4000C000 – 0x4000C0FF   RESETS
  0x40014000 – 0x400147FF   IO_BANK0  (function select per pin)
  0x4001C000 – 0x4001C0FF   PADS_BANK0 (pull-up/down, input-enable per pin)
  0xD0000000 – 0xD000003F   SIO GPIO registers

Register GPIO with the CPU's Memory object so reads/writes to those
addresses are routed here automatically.
"""

from .memory import MemoryBlock

NUM_PINS = 30   # RP2040 has GPIO0–GPIO29

# Peripheral base addresses
_SIO_BASE        = 0xD0000000
_IO_BANK0_BASE   = 0x40014000
_PADS_BANK0_BASE = 0x4001C000
_RESETS_BASE     = 0x4000C000

# Address ranges: (base, size) — size rounded up to a convenient power of two
_RANGES = (
    (_SIO_BASE,        0x40),
    (_IO_BANK0_BASE,   0x800),
    (_PADS_BANK0_BASE, 0x100),
    (_RESETS_BASE,     0x100),
)

_PIN_MASK = (1 << NUM_PINS) - 1
_CTRL_MASK = 0x3033331F   # IRQOVER, INOVER, OEOVER, OUTOVER, FUNCSEL

# Pad-register bit positions (PADS_BANK0_GPIOx)
_PADS_PDE = 1 << 2   # pull-down enable
_PADS_PUE = 1 << 3   # pull-up enable
_PADS_IE  = 1 << 6   # input enable


class GPIO(MemoryBlock):
    """Simulated RP2040 GPIO peripheral block.

    Inherits from MemoryBlock so it can be registered directly with a Memory
    object.  handles() is overridden to cover the four disjoint peripheral
    ranges; read32/write32 dispatch to the appropriate sub-handler.
    """

    def __init__(self):
        super().__init__()                  # no single base/size; handles() overrides
        self._oe  = 0                       # output-enable bitmask
        self._out = 0                       # output-level bitmask
        self._ext = [None] * NUM_PINS       # external drive: None=Z, True=1, False=0
        self._pue = 0                       # pull-up enable bitmask
        self._pde = 0                       # pull-down enable bitmask
        self._ctrl = [0x1F] * NUM_PINS      # IO_BANK0 CTRL (FUNCSEL + overrides)
        # RESETS: all peripherals start in reset; bits 5=IO_BANK0, 8=PADS_BANK0
        self._resets = 0x01FFFFFF
        self._resets_done = 0

    # ── address routing ────────────────────────────────────────────────────────

    def handles(self, addr):
        addr &= 0xFFFFFFFF
        for base, size in _RANGES:
            if base <= addr < base + size:
                return True
        return False

    def read32(self, addr):
        addr &= 0xFFFFFFFF
        off = addr - _SIO_BASE
        if 0 <= off < 0x40:
            return self._sio_read(off)
        off = addr - _IO_BANK0_BASE
        if 0 <= off < 0x800:
            return self._io_bank0_read(off)
        off = addr - _PADS_BANK0_BASE
        if 0 <= off < 0x100:
            return self._pads_read(off)
        off = addr - _RESETS_BASE
        if 0 <= off < 0x100:
            return self._resets_read(off)
        return 0

    def write32(self, addr, val):
        addr &= 0xFFFFFFFF
        val  &= 0xFFFFFFFF
        off = addr - _SIO_BASE
        if 0 <= off < 0x40:
            self._sio_write(off, val); return
        off = addr - _IO_BANK0_BASE
        if 0 <= off < 0x800:
            self._io_bank0_write(off, val); return
        off = addr - _PADS_BANK0_BASE
        if 0 <= off < 0x100:
            self._pads_write(off, val); return
        off = addr - _RESETS_BASE
        if 0 <= off < 0x100:
            self._resets_write(off, val); return

    # ── SIO ───────────────────────────────────────────────────────────────────

    # IO_BANK0 CTRL overrides: 0 = normal, 1 = invert, 2 = force 0, 3 = force 1
    @staticmethod
    def _override(mode, value):
        return (value, not value, False, True)[mode & 3]

    def _pin_oe(self, i):
        """Effective output enable of pin i (SIO OE with CTRL.OEOVER applied)."""
        return self._override(self._ctrl[i] >> 12, bool(self._oe & (1 << i)))

    def _pin_out(self, i):
        """Effective output level of pin i (SIO OUT with CTRL.OUTOVER applied)."""
        return self._override(self._ctrl[i] >> 8, bool(self._out & (1 << i)))

    def pin_level(self, i):
        """Logic level on pin i: True/False, or None when floating (Z)."""
        bit = 1 << i
        if self._pin_oe(i):             # output pin — read back what we drive
            return self._pin_out(i)
        ext = self._ext[i]
        if ext is not None:
            return ext
        if self._pue & bit: return True     # pulled high
        if self._pde & bit: return False    # pulled low
        return None                         # truly floating

    def _gpio_in(self):
        """Compute GPIO_IN value.

        For output pins: reflects the driven output level.
        For input pins:  reflects external drive, or pull if floating (Z);
        a floating pin reads 0 (reproducible undefined).
        """
        result = 0
        for i in range(NUM_PINS):
            if self.pin_level(i):
                result |= 1 << i
        return result & _PIN_MASK

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
    # Each GPIO occupies 8 bytes: STATUS at n*8, CTRL at n*8+4.

    def _io_bank0_read(self, off):
        pin = off >> 3
        if pin >= NUM_PINS: return 0
        if (off & 7) == 4: return self._ctrl[pin]  # CTRL
        return 0                                     # STATUS (simplified)

    def _io_bank0_write(self, off, val):
        pin = off >> 3
        if pin < NUM_PINS and (off & 7) == 4:
            self._ctrl[pin] = val & _CTRL_MASK

    # ── PADS_BANK0 ────────────────────────────────────────────────────────────
    # Offset 0x00: VOLTAGE_SELECT (ignored).
    # Offset 0x04 + pin*4: per-pin pad control.

    def _pads_read(self, off):
        if off == 0: return 0
        pin = (off - 4) >> 2
        if not (0 <= pin < NUM_PINS): return 0
        v = _PADS_IE                               # IE on by default
        if self._pue & (1 << pin): v |= _PADS_PUE
        if self._pde & (1 << pin): v |= _PADS_PDE
        return v

    def _pads_write(self, off, val):
        if off == 0: return
        pin = (off - 4) >> 2
        if not (0 <= pin < NUM_PINS): return
        bit = 1 << pin
        if val & _PADS_PUE: self._pue |= bit
        else:               self._pue &= ~bit & _PIN_MASK
        if val & _PADS_PDE: self._pde |= bit
        else:               self._pde &= ~bit & _PIN_MASK

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
        if self._pin_oe(pin):
            raise ValueError(f"GP{pin} is configured as an output")
        self._ext[pin] = None if value is None else bool(value)

    # ── display ───────────────────────────────────────────────────────────────

    def any_configured(self):
        """True if any pin has been configured (output or pull enabled)."""
        return bool(self._oe or self._pue or self._pde or
                    any(e is not None for e in self._ext) or
                    any(c & 0x3300 for c in self._ctrl))

    def pin_states(self):
        """Per-pin snapshot for the GUI: direction, level, pull and function."""
        pins = []
        for i in range(NUM_PINS):
            bit = 1 << i
            level = self.pin_level(i)
            pins.append({
                'pin': i,
                'dir': 'out' if self._pin_oe(i) else 'in',
                'level': None if level is None else int(level),
                'pull': 'up' if self._pue & bit else 'down' if self._pde & bit else None,
                'driven': self._ext[i] is not None,
                'func': self._ctrl[i] & 0x1F,
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
            d = 'O' if self._pin_oe(i) else 'I'
            level = self.pin_level(i)
            x = 'Z' if level is None else ('1' if level else '0')
            row.append(f"GP{i:<2}:{d}{x}")
            if len(row) == cols:
                lines.append("  " + "  ".join(row))
                row = []
        if row:
            lines.append("  " + "  ".join(row))
        return "\n".join(lines)
