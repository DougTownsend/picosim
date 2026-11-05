"""discover/safety.py: every never-write block and the Pico W pins are
refused, --pins is honoured, reads with side effects are refused, and every
campaign's dry run passes the checks."""

import unittest

from picosim.discover import safety as S
from picosim.discover.safety import SafetyError, check_write, check_read


class WriteTest(unittest.TestCase):
    def refused(self, addr, value=0, **kw):
        with self.assertRaises(SafetyError, msg=f"0x{addr:08X}"):
            check_write(addr, 4, value, **kw)

    def test_never_written_blocks_and_their_aliases(self):
        for name, base, ok in S.NEVER:
            for alias in range(4):
                self.refused(base + alias * 0x1000 + 0x40 if 0x40 not in ok else base + 0x100)
        for name, lo, hi, ok in S.NEVER_RANGES:
            self.refused(lo if 0 not in ok else lo + 0x20)
        self.refused(0x4006400C)                       # VREG
        self.refused(S.AIRCR, 0x05FA0004)
        check_write(S.AIRCR, 4, 0x05FA0004, allow_aircr=True)

    def test_measuring_exceptions(self):
        check_write(0x40008094, 4, 0x01)               # CLOCKS FC0_SRC
        self.refused(0x40008000)                       # CLK_GPOUT0_CTRL
        check_write(0x40004008)                        # SYSCFG PROC_IN_SYNC_BYPASS
        self.refused(0x40004000)
        check_write(0x14000004)                        # XIP FLUSH
        self.refused(0x14000000)

    def test_allowed(self):
        for a in (0x20000000, 0x20041FFC, 0x21000000, 0x00000100, 0xE000E010, 0xE000ED08,
                  0x40054000, 0x4003400C, 0xD0000060, 0xD0000014, 0x40030008, 0x60000000):
            check_write(a)

    def test_resets_only_through_aliases_and_only_allowed_blocks(self):
        check_write(0x4000E000, 4, 1 << 22)            # SET uart0
        check_write(0x4000F000, 4, (1 << 5) | (1 << 8))
        self.refused(0x4000C000, 0)                    # a plain write
        self.refused(0x4000E000, 1 << 6)               # io_qspi
        self.refused(0x4000E000, 1 << 24)              # usbctrl
        self.refused(0x4000E000, 1 << 7)               # jtag

    def test_pico_w_pins_are_never_written(self):
        for pin in (23, 24, 25, 29):
            self.refused(0x40014000 + 8 * pin + 4, 5)
            self.refused(0x4001C004 + 4 * pin, 0x56)
            self.refused(0x4001E004 + 4 * pin, 1 << 3)      # SET alias: a pull-up
            with self.assertRaises(SafetyError):
                check_write(0x20000000, pins={pin})
        check_write(0x4001C004 + 4 * 22, 0x5A)            # a pull-up on another pin is fine

    def test_debug_pads_and_voltage_select(self):
        for off in (0x00, 0x7C, 0x80):
            for alias in range(4):
                self.refused(0x4001C000 + alias * 0x1000 + off, 0)

    def test_outputs_only_on_pins(self):
        self.refused(0xD0000024, 1 << 21)                  # OE_SET
        self.refused(0xD0000020, 1)                        # OE
        self.refused(0xD000002C, 1 << 3)                   # OE_XOR
        check_write(0xD0000024, 4, 1 << 21, pins={21})
        check_write(0xD0000028, 4, 0x3FFFFFFF)             # OE_CLR is always safe
        self.refused(0x40014000 + 8 * 2 + 4, 2)            # FUNCSEL 2 (UART) on GP2
        self.refused(0x40014000 + 8 * 2 + 4, 5 | (3 << 12))   # OEOVER: force on
        check_write(0x40014000 + 8 * 2 + 4, 4, 5)          # SIO, which drives only with OE
        check_write(0x40014000 + 8 * 2 + 4, 4, 2, pins={2})
        self.refused(0x40017000 + 8 * 2 + 4, 0x1A)         # CLR alias could leave FUNCSEL 5 from 31

    def test_reads(self):
        for a in (0x40044040, 0xD0000094, 0xD0000070, 0xD0000100, 0x40034000, 0x4005400C, 0xD0000058):
            with self.assertRaises(SafetyError, msg=hex(a)):
                check_read(a)
        check_read(0xD0000070, side_effect_ok=True)
        with self.assertRaises(SafetyError):
            check_read(0x10000000)
        check_read(0x10000000, flashed=True)
        check_read(0x40014000)

    def test_case_checks(self):
        from picosim.discover import formats as F
        lay = F.LAYOUTS['hi']
        good = {'id': 1, 'psp': lay['PSP_DEFAULT'], 'slot': lay['SLOT'], 'code_len': 2}
        S.check_case(good, lay)
        for bad in ({'psp': 0x20000000}, {'psp': lay['PSP_LO']}, {'slot': lay['SLED'] + lay['SLED_SIZE'] - 2},
                    {'slot': lay['SLOT'] + 1}, {'writes': [(0x40058000, 4, 0)]}, {'reads': [0xD0000058]}):
            with self.assertRaises(SafetyError, msg=bad):
                S.check_case({**good, **bad}, lay)


if __name__ == '__main__':
    unittest.main()


class DryRunTest(unittest.TestCase):
    """Every campaign's cases pass the safety checks with no --pins (the
    default: nothing is ever driven)."""

    def test_every_campaign(self):
        import argparse
        from picosim.discover import formats as F
        from picosim.discover import runner as R
        from picosim.discover.campaign import Context
        from picosim.discover.cases import ORDER, module
        opts = argparse.Namespace(target='standin', pins=frozenset(), stages='B1,B4,B5', limit=None)
        ctx = Context(None, opts)
        for name in ORDER:
            try:
                m = module(name)
            except ImportError:
                continue                       # a campaign still being written
            for stage, cases, kw in m.dry_cases(ctx):
                lay = F.LAYOUTS[kw.get('layout', 'hi')]
                sites = kw.get('sites')
                n = 0
                for c in cases:
                    S.check_case({**c, 'code_len': len(R._code_bytes(c))}, lay, frozenset(), sites)
                    n += 1
                self.assertGreater(n, 0, f"{name} {stage}")
