"""
Campaign F's pull-latency routine (discover/cases/peripherals.py
pull_latency_code) must wait exactly the cycles it says, or the pull
delays picosim takes from it would be off.  Timed here with the core's live
RP2040 counter (CPUCore.m0_cycles, itself checked against the chip by
campaign D's replay).
"""

import unittest

from picosim import timing
from picosim._picosim_core import CPUCore
from picosim.discover.cases import peripherals as P

BASE = 0x20000000


def core_with(code):
    c = CPUCore()
    timing.install(c)
    c.load_memory(code, BASE)
    c.peripheral_read = lambda a, n: 0
    c.peripheral_write = lambda a, v, n: None
    c.pc = BASE
    return c


def run_to(c, addr, limit=2_000_000):
    for _ in range(limit):
        if c.pc == addr:
            return
        c.step()
    raise AssertionError(f"did not reach 0x{addr:08X}")


class WaitCyclesTest(unittest.TestCase):
    def test_wait_is_exact(self):
        for d in list(range(0, 40)) + [100, 255, 256, 257, 1000, 7999, 8000, 250000]:
            code = P.Code(declare=False).wait_cycles(7, d).bkpt(0x43)
            hw_len = 2 * len(code.hw)
            c = core_with(code.assemble())
            run_to(c, BASE + hw_len - 2)                 # the BKPT
            self.assertEqual(c.m0_cycles, d, d)


class PullLatencyCodeTest(unittest.TestCase):
    def test_store_to_load_is_5_plus_d(self):
        for d in (0, 1, 4, 5, 6, 7, 31, 32, 400, 8000):
            c = core_with(P.pull_latency_code(d))
            c.set_reg(0, P.SIO)
            c.set_reg(1, P.pad_addr(P.SYNC_PIN))
            seen = []
            for _ in range(5_000_000):
                hw = c.read16(c.pc)
                if hw == 0xBE00 | P.Mini.DONE:
                    break
                if (hw & 0xF800) in (0x6000, 0x6800) and (hw & 7) in (2, 3, 4, 5):   # str r2/r3, ldr r4/r5
                    seen.append(c.m0_cycles)
                c.step()
            self.assertEqual(len(seen), 4, d)
            self.assertEqual(seen[1] - seen[0], 5 + d, d)     # STR r2 (pull-up) .. LDR r4
            self.assertEqual(seen[3] - seen[2], 5 + d, d)     # STR r3 (pull-down) .. LDR r5
            self.assertGreaterEqual(seen[2] - seen[1], P.FLASHED_SYNC_SETTLE)


class MatchesTheChipTest(unittest.TestCase):
    """F's routine run inside picosim (live counter + gpio.py's constants)
    sees the new level from the same d as the chip did (gpio_flashed.json)."""

    def run_once(self, d, bypass):
        from picosim.gpio import GPIO
        c = core_with(P.pull_latency_code(d))
        g = GPIO()
        g.clock = lambda: c.m0_cycles
        if bypass:
            g.write32(P.SYSCFG_BYPASS, 1 << P.SYNC_PIN)
        g.write32(P.pad_addr(P.SYNC_PIN), P.PAD_DOWN)
        c.peripheral_read = lambda a, n: g.read32(a)
        c.peripheral_write = lambda a, v, n: g.write32(a, v)
        for r, v in {0: P.SIO, 1: P.pad_addr(P.SYNC_PIN), 2: P.PAD_UP, 3: P.PAD_DOWN}.items():
            c.set_reg(r, v)
        for _ in range(3_000_000):
            if c.read16(c.pc) == 0xBE00 | P.Mini.DONE:
                break
            c.step()
        return bool(c.get_reg(4) >> P.SYNC_PIN & 1), not c.get_reg(5) >> P.SYNC_PIN & 1

    def test_thresholds_match_the_chip(self):
        import json
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'hwfacts', 'peripherals', 'gpio_flashed.json')
        with open(path) as f:
            chip = json.load(f)['results']
        for name, bypass in (('synchronised', False), ('bypass', True)):
            for i, edge in ((0, 'rise'), (1, 'fall')):
                sim = next(d for d in range(0, 200) if self.run_once(d, bypass)[i])
                lo, hi = chip_window(chip[name], edge)
                self.assertTrue(lo <= sim <= hi + 1,
                                f"{name} {edge}: picosim first sees it at d={sim}; the chip at d={lo}..{hi}")


def chip_window(res, edge):
    """Where the chip's edge was seen, from F's per-d table: (first d at which
    any repeat saw it, first d from which every repeat did).  The edge is an
    analog pull crossing the pad's threshold somewhere inside a clock cycle,
    so near it the repeats disagree (None) and two runs can differ by a cycle:
    2026-11-17, the synchronised fall was seen always from d=70 in one run and
    from d=69 (sometimes at 67, 68) in the next.  picosim is deterministic: its
    d must lie in that window, or one past it (the earlier run's value)."""
    table = {int(d): v for d, v in res[edge].items()}
    always = res['value'][f'{edge}_seen_from_d']
    first = min((d for d, v in table.items() if v is not False and d <= always), default=always)
    return first, always


if __name__ == '__main__':
    unittest.main()
