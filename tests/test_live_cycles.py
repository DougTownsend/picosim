"""
The core's live RP2040 cycle count (CPUCore.m0_cycles, timing.install).

The count must equal timing.py's m0plus machine exactly, because that
machine is what campaign D checked on a real RP2040 (0 differences over 232
forms and 1,600 pairs; tests/test_hwfacts.py replays it, and
discover.replay.body_cycles now answers with the live count and refuses to
answer if the two ever disagree).  Here:

1. every 16-bit encoding, and the 32-bit ones, get the same timing code in
   the core as timing.pico_row gives;
2. every example and test program, run to its end, gives the same total
   live as timing.cycles over the program's profile;
3. the multi-cycle path (step_cycle) counts the same as step();
4. loads and stores count by where their data went (SIO 1, memory 2, APB
   4 / 5), B<cond> 1 or 2, and SVC is untimed.
"""

import glob
import os
import random
import unittest

from picosim import timing
from picosim._picosim_core import CPUCore
from picosim.loader import load_program, LoadError
from test_cycle_core import Harness

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAMS = sorted(glob.glob(os.path.join(ROOT, 'examples', '*.s')) +
                  glob.glob(os.path.join(ROOT, 'test_asm_files', '*.s')))
MAX_INSNS = 200000


def expected_code(hw1, hw2=0):
    return timing._code(timing.pico_row(hw1, hw2))


def profile_cycles(core):
    """timing.cycles over the per-address profile: (cycles, untimed instructions)."""
    total, untimed = 0, 0
    for e in core.profile():
        t = timing.cycles('m0plus', e['hw1'], e['hw2'], e)
        if t is None:
            untimed += e['execs']
        else:
            total += t
    return total, untimed


class TableTest(unittest.TestCase):
    def setUp(self):
        self.core = CPUCore()
        timing.install(self.core)

    def test_every_16bit_encoding(self):
        bad = [h for h in range(0x10000)
               if not timing.is_wide(h) and self.core.m0_code(h, 0) != expected_code(h)]
        self.assertEqual(bad, [])

    def test_32bit_encodings(self):
        rng = random.Random(1)
        pairs = [(h1, h2) for _, v1, _, v2, _ in timing.WIDE_ROWS
                 for h1 in (v1, v1 | 0x000F, v1 | 0x07FF & ~0) for h2 in (v2, v2 | 0x0FFF, v2 ^ 0x2000)]
        pairs += [(rng.randrange(0xE800, 0x10000), rng.randrange(0x10000)) for _ in range(200000)]
        pairs += [(0xF3BF, 0x8F00 | op << 4 | lo) for op in range(16) for lo in range(16)]
        bad = [(hex(a), hex(b)) for a, b in pairs
               if timing.is_wide(a) and self.core.m0_code(a, b) != expected_code(a, b)]
        self.assertEqual(bad, [])

    def test_off_until_installed(self):
        c = CPUCore()
        self.assertFalse(c.m0_ready)
        self.assertEqual(c.m0_cycles, 0)


class ProgramsTest(unittest.TestCase):
    def test_programs_live_equals_profile(self):
        self.assertTrue(PROGRAMS)
        ran = 0
        for path in PROGRAMS:
            with self.subTest(program=os.path.relpath(path, ROOT)):
                try:
                    h = Harness(path)
                except LoadError:
                    continue
                core = h.core
                self.assertTrue(core.m0_ready)
                for _ in range(MAX_INSNS):
                    if core.halted:
                        break
                    try:
                        core.step()
                        core.check_halt()
                    except Exception:            # noqa: BLE001 - a program that faults ends there
                        break
                total, untimed = profile_cycles(core)
                self.assertEqual((core.m0_cycles, core.m0_untimed), (total, untimed))
                ran += 1
        self.assertGreater(ran, 10)

    def test_step_cycle_counts_the_same(self):
        for name in ('03_count_loop.s', '08_button_led.s'):
            path = os.path.join(ROOT, 'examples', name)
            fast, slow = Harness(path), Harness(path)
            for _ in range(3000):
                if fast.core.halted:
                    break
                fast.core.step(); fast.core.check_halt()
                while True:
                    info = slow.core.step_cycle()
                    if info['last'] or slow.core.halted:
                        break
                slow.core.check_halt()
                self.assertEqual(fast.core.m0_cycles, slow.core.m0_cycles, name)
                self.assertEqual(fast.core.m0_last, slow.core.m0_last, name)


class RulesTest(unittest.TestCase):
    """A few instructions by hand, against Table 81 and the APB measurement."""

    def run_body(self, body, steps):
        src = os.path.join(ROOT, 'tests', '_live_cycles_tmp.s')
        with open(src, 'w') as f:
            f.write(".syntax unified\n.cpu cortex-m0plus\n.thumb\nmain:\n" + body + "\n")
        try:
            h = Harness(src)
        finally:
            os.remove(src)
        core = h.core
        # run to main, then count only the body
        main = h.prog.label_map['main']
        while core.pc != main:
            core.step()
        start = core.m0_cycles
        out = []
        for _ in range(steps):
            core.step()
            out.append(core.m0_last)
        return core.m0_cycles - start, out

    def test_loads_and_stores_by_region(self):
        body = ("ldr r0, =0xD0000004\nldr r1, [r0]\n"         # LDR literal 2, SIO load 1
                "ldr r0, =0x20001000\nstr r1, [r0]\nldr r1, [r0]\n"   # 2, SRAM store 2, load 2
                "ldr r0, =0x4001C004\nldr r1, [r0]\nstr r1, [r0]\n")  # 2, APB load 4, store 5
        total, each = self.run_body(body, 8)
        self.assertEqual(each, [2, 1, 2, 2, 2, 2, 4, 5])
        self.assertEqual(total, 20)

    def test_conditional_branch_and_bl(self):
        body = ("movs r0, #0\ncmp r0, #0\nbne 1f\nbeq 1f\nnop\n1: nop\n")
        total, each = self.run_body(body, 5)
        self.assertEqual(each, [1, 1, 1, 2, 1])               # not taken 1, taken 2

    def test_svc_is_untimed(self):
        c = CPUCore()
        timing.install(c)
        self.assertEqual(c.m0_code(0xDF00, 0) >> 8, 0)        # SVC
        self.assertEqual(c.m0_code(0xBE00, 0) >> 8, 0)        # BKPT
        self.assertEqual(c.m0_code(0xF000, 0xF800), 1 << 8 | 3)   # BL


if __name__ == '__main__':
    unittest.main()
