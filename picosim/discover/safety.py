"""
What discovery may and may not do to the chip, checked in code.

Every write the host plans (through the debugger, or by a case's code where
the generator declares it) goes through check_write() before a batch is
uploaded.  A failed check raises SafetyError and the run stops: it is never
a warning.

The rules are the proposal's §7:

  never written   VREG_AND_CHIP_RESET (the core regulator), CLOCKS except the
                  FC0 frequency counter, PLL_SYS, PLL_USB, XOSC, ROSC, PSM,
                  WATCHDOG, SYSCFG except PROC_IN_SYNC_BYPASS, SSI, IO_QSPI,
                  PADS_QSPI, XIP_CTRL except FLUSH and its counters, USB,
                  DMA, PIO, AIRCR (unless a run allows its one planned test);
  allowlist       IO_BANK0, PADS_BANK0, RESETS (allowed peripherals' bits,
                  through the SET/CLR/XOR aliases only), SIO GPIO, divider,
                  interpolators and spinlocks, TIMER, PWM, UART, SPI, I2C, ADC,
                  RTC, BUSCTRL's performance counters, the PPB, SRAM, ROM and
                  the known holes;
  pins            no pin is ever made an output, or given to a peripheral,
                  unless it is in the run's --pins list; GP23, GP24, GP25 and
                  GP29 (the Pico W's wireless chip) are never written at all.

Reads: check_read() refuses registers whose read has a side effect
(read_side_effects()) unless the case asks for exactly that, and flash/XIP
addresses outside a flashed state, where they may hang.
"""

from . import formats as F

PICO_W_PINS = frozenset({23, 24, 25, 29})
N_PINS = 30


class SafetyError(Exception):
    pass


def _aliases(base, size=0x1000):
    """A peripheral's window and its XOR/SET/CLR aliases."""
    return [(base + k * 0x1000, base + k * 0x1000 + size) for k in range(4)]


# ── never written ───────────────────────────────────────────────────────────
# (name, base, exceptions: offsets that may be written)
NEVER = [
    ('VREG_AND_CHIP_RESET', 0x40064000, ()),
    ('CLOCKS', 0x40008000, tuple(range(0x80, 0xA0, 4))),       # FC0_* (measuring only)
    ('PLL_SYS', 0x40028000, ()),
    ('PLL_USB', 0x4002C000, ()),
    ('XOSC', 0x40024000, ()),
    ('ROSC', 0x40060000, ()),
    ('PSM', 0x40010000, ()),
    ('WATCHDOG', 0x40058000, ()),
    ('SYSCFG', 0x40004000, (0x08, 0x0C)),                     # PROC_IN_SYNC_BYPASS(_HI)
    ('IO_QSPI', 0x40018000, ()),
    ('PADS_QSPI', 0x40020000, ()),
    ('TBMAN', 0x4006C000, ()),
    ('SYSINFO', 0x40000000, ()),
]
NEVER_RANGES = [
    ('SSI', 0x18000000, 0x18100000, ()),
    ('XIP_CTRL', 0x14000000, 0x14100000, (0x04, 0x0C, 0x10)),  # FLUSH, CTR_HIT, CTR_ACC
    ('XIP / flash', 0x10000000, 0x14000000, ()),
    ('XIP SRAM', 0x15000000, 0x15004000, ()),
    ('DMA', 0x50000000, 0x50100000, ()),
    ('USB DPRAM', 0x50100000, 0x50110000, ()),
    ('USB', 0x50110000, 0x50120000, ()),
    ('PIO0', 0x50200000, 0x50300000, ()),
    ('PIO1', 0x50300000, 0x50400000, ()),
    ('SIO FIFO', 0xD0000050, 0xD0000060, ()),                 # would talk to core 1's boot ROM
    ('XIP_AUX', 0x50400000, 0x50400010, ()),                 # the XIP stream FIFO (not a hole)
]
AIRCR = 0xE000ED0C

# ── allowed ─────────────────────────────────────────────────────────────────
ALLOW = [
    ('IO_BANK0', 0x40014000), ('PADS_BANK0', 0x4001C000), ('RESETS', 0x4000C000),
    ('TIMER', 0x40054000), ('PWM', 0x40050000), ('UART0', 0x40034000), ('UART1', 0x40038000),
    ('SPI0', 0x4003C000), ('SPI1', 0x40040000), ('I2C0', 0x40044000), ('I2C1', 0x40048000),
    ('ADC', 0x4004C000), ('RTC', 0x4005C000), ('BUSCTRL', 0x40030000),
]
BUSCTRL_COUNTERS = range(0x08, 0x28)
SIO_ALLOWED = [(0xD0000010, 0xD0000040), (0xD0000060, 0xD0000100),   # GPIO; divider + interpolators
               (0xD0000100, 0xD0000180)]                             # spinlocks
RAM = [(0x20000000, 0x20042000), (0x21000000, 0x21040000)]           # striped and non-striped SRAM
ROM = (0x00000000, 0x00004000)
PPB = (0xE0000000, 0xE0100000)
HOLES = [0x00004000, 0x0FFFFFFC, 0x20042000, 0x20080000, 0x21040000, 0x30000000, 0x40070000,
         0x60000000, 0xA0000000, 0xD0000180, 0xE0100000, 0xFFFFFFFC]

# RESETS bits that may be put through reset: adc 0, busctrl 1, i2c0 3, i2c1 4,
# io_bank0 5, pads_bank0 8, pwm 14, rtc 15, spi0 16, spi1 17, timer 21,
# uart0 22, uart1 23.  Never: dma, io_qspi, jtag, pads_qspi, pio, plls,
# syscfg, sysinfo, tbman, usbctrl.
RESETS_ALLOWED = sum(1 << b for b in (0, 1, 3, 4, 5, 8, 14, 15, 16, 17, 21, 22, 23))

IO_BANK0, PADS_BANK0, RESETS = 0x40014000, 0x4001C000, 0x4000C000
PADS_NEVER = {0x00: 'VOLTAGE_SELECT (1.8 V pads on a 3.3 V supply can be damaged)',
              0x7C: "the SWCLK pad (the debugger's own pin)", 0x80: "the SWD pad (the debugger's own pin)"}
SIO_OE, SIO_OE_SET, SIO_OE_CLR, SIO_OE_XOR = 0xD0000020, 0xD0000024, 0xD0000028, 0xD000002C
FUNCSEL_SIO, FUNCSEL_NULL = 5, 31


def _in(addr, lo, hi):
    return lo <= addr < hi


def _peripheral(addr):
    """(name, base, alias, offset) for an APB/AHB peripheral window, else None."""
    for name, base, *_ in NEVER + [(n, b) for n, b in ALLOW]:
        for k, (lo, hi) in enumerate(_aliases(base)):
            if lo <= addr < hi:
                return name, base, k, addr - lo
    return None


def check_write(addr, width=4, value=0, pins=frozenset(), allow_aircr=False, why=''):
    """Raise SafetyError unless writing `value` (`width` bytes) to `addr` is
    allowed.  pins: the GPIOs this run may drive."""
    pins = frozenset(pins)
    where = f"0x{addr:08X}" + (f" ({why})" if why else '')
    bad_pins = pins & PICO_W_PINS
    if bad_pins:
        raise SafetyError(f"--pins names GP{', GP'.join(map(str, sorted(bad_pins)))}, which the Pico W's "
                          "wireless chip uses: those pins are read-only in every campaign")
    if any(_in(addr, lo, hi) for lo, hi in RAM) or _in(addr, *ROM) or addr in HOLES:
        return
    for name, lo, hi, ok in NEVER_RANGES:
        if _in(addr, lo, hi):
            if addr - lo in ok:
                return
            raise SafetyError(f"refused: a write to {where} is in {name}, which discovery never writes")
    if _in(addr, *PPB):
        if addr & ~3 == AIRCR and not allow_aircr:
            raise SafetyError(f"refused: a write to AIRCR {where} (it can reset the chip)")
        return
    if 0xD0000000 <= addr < 0xD0000180:
        if not any(_in(addr, lo, hi) for lo, hi in SIO_ALLOWED):
            raise SafetyError(f"refused: a write to SIO {where} is outside GPIO, divider, interpolators, spinlocks")
        _check_sio_gpio(addr, value, pins, where)
        return
    p = _peripheral(addr)
    if p is None:
        if 0x40000000 <= addr < 0x60000000 or 0xD0000000 <= addr < 0xE0000000:
            raise SafetyError(f"refused: a write to {where}, which is not on the allowlist")
        return                                    # an unmapped area: a hole, which may be probed
    name, base, alias, off = p
    for n, b, ok in NEVER:
        if b == base:
            if off & ~3 in ok:
                return
            raise SafetyError(f"refused: a write to {where} is in {name}, which discovery never writes")
    if name == 'RESETS':
        if off & ~3 != 0:
            raise SafetyError(f"refused: a write to RESETS {where}: only RESETS.RESET may be written")
        if alias == 0:
            raise SafetyError(f"refused: a plain write to RESETS.RESET {where}; use the SET/CLR/XOR aliases")
        if value & ~RESETS_ALLOWED:
            raise SafetyError(f"refused: RESETS bits 0x{value & ~RESETS_ALLOWED:08X} at {where} "
                              "would reset a block discovery must not touch")
        return
    if name == 'BUSCTRL' and off not in BUSCTRL_COUNTERS:
        raise SafetyError(f"refused: a write to BUSCTRL {where}: only its performance counters may be written")
    if name == 'IO_BANK0' and off < 0xF0:
        pin, reg = off // 8, (off % 8) & ~3
        if reg == 4:
            _check_ctrl(pin, alias, value, pins, where)
    if name == 'PADS_BANK0' and off & ~3 in PADS_NEVER:
        raise SafetyError(f"refused: a write to {where}: {PADS_NEVER[off & ~3]}")
    if name == 'PADS_BANK0' and 4 <= off < 4 + 4 * N_PINS:
        pin = (off - 4) // 4
        if pin in PICO_W_PINS:
            raise SafetyError(f"refused: a write to GP{pin}'s pad {where}: a Pico W wireless-chip pin")


def _check_ctrl(pin, alias, value, pins, where):
    if pin in PICO_W_PINS:
        raise SafetyError(f"refused: a write to GP{pin}_CTRL {where}: a Pico W wireless-chip pin")
    if pin in pins:
        return
    if alias:                     # XOR/SET/CLR: the result depends on the old value; allow only harmless bits
        if value & 0x1F or (value >> 12) & 3:
            raise SafetyError(f"refused: {where} could select a function or force the output on for GP{pin}, "
                              "which is not in --pins")
        return
    funcsel, oeover = value & 0x1F, (value >> 12) & 3
    if funcsel not in (FUNCSEL_SIO, FUNCSEL_NULL) or oeover in (1, 3):
        raise SafetyError(f"refused: {where} would let GP{pin} be driven (FUNCSEL {funcsel}, OEOVER {oeover}); "
                          "it is not in --pins")


def _check_sio_gpio(addr, value, pins, where):
    if addr & ~3 in (SIO_OE, SIO_OE_SET, SIO_OE_XOR):
        mask = sum(1 << p for p in pins)
        if value & ((1 << N_PINS) - 1) & ~mask:
            bad = [p for p in range(N_PINS) if value >> p & 1 and p not in pins]
            raise SafetyError(f"refused: {where} would make GP{', GP'.join(map(str, bad))} an output; "
                              "only --pins may be driven")


# ── reads ───────────────────────────────────────────────────────────────────

def read_side_effects():
    """Registers whose read changes something, from the SVD's readAction,
    the SDK headers' RF/RWF fields, and the hand list (proposal §3.5).
    Returns {address: why}."""
    out = {}
    try:
        from . import svd
        for r in svd.registers():
            if r.get('read_action') or r.get('rf'):
                out[r['addr']] = r.get('read_action') or 'a field is read-to-clear (RF/RWF)'
    except Exception:                    # no SDK: the hand list still applies
        pass
    for base in (0x40044000, 0x40048000):                     # I2C0, I2C1: IC_CLR_* (0x40-0x68) and IC_DATA_CMD
        for off in range(0x40, 0x6C, 4):
            out.setdefault(base + off, 'I2C IC_CLR_*: reading clears interrupts (SVD says RO)')
        out.setdefault(base + 0x10, 'I2C IC_DATA_CMD: reading pops the receive FIFO')
    for interp in (0xD0000080, 0xD00000C0):                   # INTERP POP_LANE0/1/FULL
        for off in (0x14, 0x18, 0x1C):
            out.setdefault(interp + off, 'INTERP POP: reading advances the interpolator (SVD says RO)')
    out.setdefault(0xD0000070, 'DIV_QUOTIENT: reading clears DIRTY')
    out.setdefault(0xD0000058, 'SIO FIFO_RD: reading pops core 1\'s FIFO')
    for off in range(0x100, 0x180, 4):
        out.setdefault(0xD0000000 + off, 'SPINLOCK: reading claims it')
    for base in (0x40034000, 0x40038000):
        out.setdefault(base, 'UART DR: reading pops the receive FIFO')
    for base in (0x4003C000, 0x40040000):
        out.setdefault(base + 0x08, 'SPI DR: reading pops the receive FIFO')
    out.setdefault(0x4005400C, 'TIMER TIMELR: reading latches TIMEHR')
    out.setdefault(0x4004C00C, 'ADC FIFO: reading pops it')
    out.setdefault(0xE000E010, 'SYST_CSR: reading clears COUNTFLAG')
    for off in range(0, 0x10, 4):
        out.setdefault(0x50400000 + off, 'XIP_AUX: reading pops the XIP stream FIFO')
    out.setdefault(0x1400001C, 'XIP STREAM_FIFO: reading pops it')
    return out


_SIDE = None


def check_read(addr, flashed=False, side_effect_ok=False, why=''):
    global _SIDE
    if _SIDE is None:
        _SIDE = read_side_effects()
    where = f"0x{addr:08X}" + (f" ({why})" if why else '')
    if not flashed and 0x10000000 <= addr < 0x14000000:
        raise SafetyError(f"refused: a read of {where}: flash (XIP) is only probed in campaign F")
    if not side_effect_ok and addr & ~3 in _SIDE:
        raise SafetyError(f"refused: a read of {where} has a side effect ({_SIDE[addr & ~3]})")


# ── cases ───────────────────────────────────────────────────────────────────

def check_case(case, layout, pins=frozenset(), sites=None, allow_aircr=False):
    """The checks every case passes before upload: its PSP has room for the
    frame inside the PSP area, its code fits in a sled, and every write it
    declares is allowed."""
    lay = F.LAYOUTS[layout] if isinstance(layout, str) else layout
    psp = case['psp']
    in_scratch = lay['SCRATCH'] + 64 <= psp <= lay['SCRATCH'] + lay['SCRATCH_MAX'] - 64   # stack cases
    if psp % 4 or not (lay['PSP_LO'] + 64 <= psp <= lay['PSP_HI'] or in_scratch):
        raise SafetyError(f"case {case['id']}: PSP 0x{psp:08X} is outside the PSP area "
                          f"0x{lay['PSP_LO'] + 64:08X}-0x{lay['PSP_HI']:08X}")
    sites = sites or [(lay['SLED'], lay['SLED'] + lay['SLED_SIZE'])]
    slot, n = case['slot'], case.get('code_len', len(case.get('code', b'')))
    if slot % 2 or not any(lo + 16 <= slot and slot + n + 16 <= hi for lo, hi in sites):
        raise SafetyError(f"case {case['id']}: its code at 0x{slot:08X} (+{n}) is not inside a sled")
    for w in case.get('writes', ()):
        a, width, v = w[0], w[1] if len(w) > 1 else 4, w[2] if len(w) > 2 else 0
        check_write(a, width, v, pins, allow_aircr, why=f"case {case['id']}")
    for a in case.get('reads', ()):
        check_read(a, case.get('flashed', False), case.get('side_effect_ok', False), why=f"case {case['id']}")
