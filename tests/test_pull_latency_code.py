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


if __name__ == '__main__':
    unittest.main()
