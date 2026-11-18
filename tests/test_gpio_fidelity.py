"""
picosim's GPIO against the real RP2040 (Level 5 proposal 3, decided
2026-11-12).  The expected values are campaign G's measurements on a real
Pico (hwfacts/peripherals/gpio.json), read from the file, so these tests
follow the chip, not a reading of the datasheet.

Covered: pulls, the floating pin and the bus keeper holding the last level,
INOVER in GPIO_IN, IO_BANK0 STATUS, INTR's level and edge bits (and IRQOVER
reaching them), INTE/INTF/INTS, the pad's IE and OD bits, SYSCFG storage and
the atomic aliases on INTR.
"""

import json
import os
import unittest

from picosim.gpio import GPIO, EDGE_HIGH, EDGE_LOW, LEVEL_HIGH, LEVEL_LOW

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTS = os.path.join(ROOT, 'hwfacts', 'peripherals', 'gpio.json')

IN = 0xD0000004
OE_SET, OUT_SET = 0xD0000024, 0xD0000014
PAD_UP, PAD_DOWN, PAD_NONE, PAD_KEEP = 0x5A, 0x56, 0x52, 0x5E


def pad(p): return 0x4001C004 + 4 * p
def ctrl(p): return 0x40014004 + 8 * p
def status(p): return 0x40014000 + 8 * p
def intr(p): return 0x400140F0 + 4 * (p // 8)
def bit(v, p): return (v >> p) & 1


def settled():
    """A GPIO whose clock moves 1000 cycles at every access: like campaign
    G's debugger accesses, microseconds apart, so every pull has finished
    (gpio.py's measured rise/fall times are under 100 cycles)."""
    g = GPIO()
    t = iter(range(0, 1 << 62, 1000))
    g.clock = lambda: next(t)
    return g


def chip():
    with open(FACTS) as f:
        return json.load(f)


class PullsTest(unittest.TestCase):
    """Campaign G 'pulls': every surveyed pin, the same writes in the same order."""

    def test_pulls_match_the_chip(self):
        measured = chip()['pulls']
        self.assertTrue(measured)
        for key, row in measured.items():
            pin = int(key)
            g, got = settled(), {}
            for name, value in (('pull-up', PAD_UP), ('pull-down', PAD_DOWN), ('none after down', PAD_NONE)):
                g.write32(pad(pin), value)
                got[name] = bit(g.read32(IN), pin)
            g.write32(pad(pin), PAD_UP); g.write32(pad(pin), PAD_NONE)
            got['none after up'] = bit(g.read32(IN), pin)
            g.write32(pad(pin), PAD_KEEP)
            got['keeper after up'] = bit(g.read32(IN), pin)
            g.write32(pad(pin), PAD_DOWN); g.write32(pad(pin), PAD_KEEP)
            got['keeper after down'] = bit(g.read32(IN), pin)
            want = {k: v for k, v in row.items() if k in got}
            self.assertEqual(got, want, f"GP{pin}")


class InoverTest(unittest.TestCase):
    def test_inover_matches_the_chip(self):
        for key, row in chip()['inover'].items():
            pin = int(key)
            seq = {'normal (pulled down)': [(pad(pin), PAD_DOWN)],
                   'INOVER invert': [(pad(pin), PAD_DOWN), (ctrl(pin), 0x1001F)],
                   'INOVER high': [(pad(pin), PAD_DOWN), (ctrl(pin), 0x3001F)],
                   'INOVER low (pulled up)': [(pad(pin), PAD_UP), (ctrl(pin), 0x2001F)],
                   'normal (pulled up)': [(pad(pin), PAD_UP)]}
            for name, writes in seq.items():
                g = settled()
                for a, v in writes:
                    g.write32(a, v)
                self.assertEqual(bit(g.read32(IN), pin), row['read'][name], f"GP{pin} {name}")


class StatusIntrTest(unittest.TestCase):
    def test_status_and_intr_follow_the_pulls_as_measured(self):
        """Through the debugger: STATUS's INFROMPAD/INTOPERI and the pin's
        INTR bits at start, after a pull-up, a pull-down, and clearing edges."""
        for key, row in chip()['debugger'].items():
            if 'error' in row:
                continue
            pin, sh = int(key), 4 * (int(key) % 8)
            g = settled()
            got = {}

            def snap(tag):
                s, i = g.read32(status(pin)), g.read32(intr(pin))
                got[tag] = {'infrompad': bit(s, 17), 'intoperi': bit(s, 19), 'intr': (i >> sh) & 0xF}
            g.write32(intr(pin), 0xC << sh)
            snap('start')
            g.write32(pad(pin), PAD_UP)
            snap('pull-up')
            g.write32(pad(pin), PAD_DOWN)
            snap('pull-down')
            g.write32(intr(pin), 0xC << sh)
            snap('edges cleared')
            want = {k: row[k] for k in got}
            self.assertEqual(got, want, f"GP{pin}")

    def test_overrides_in_status_and_intr_as_measured(self):
        for key, row in chip()['overrides'].items():
            if 'error' in row:
                continue
            pin, sh = int(key), 4 * (int(key) % 8)
            for tag, setv in (('normal', 0), ('inover invert', 0x10000), ('inover high', 0x30000),
                              ('irqover invert', 0x10000000), ('irqover high', 0x30000000)):
                g = settled()
                if setv:
                    g.write32(ctrl(pin) + 0x2000, setv)          # SET alias, as the campaign did
                g.write32(intr(pin), 0xC << sh)
                s, i = g.read32(status(pin)), g.read32(intr(pin))
                got = {'infrompad': bit(s, 17), 'intoperi': bit(s, 19), 'irqfrompad': bit(s, 24),
                       'irqtoproc': bit(s, 26), 'intr': (i >> sh) & 0xF}
                self.assertEqual(got, row[tag], f"GP{pin} {tag}")

    def test_status_output_side(self):
        g = settled()
        g.write32(ctrl(15), 5)
        g.write32(OE_SET, 1 << 15)
        g.write32(OUT_SET, 1 << 15)
        s = g.read32(status(15))
        for b in (8, 9, 12, 13, 17, 19):                        # out/oe from SIO, to the pad; read back
            self.assertEqual(bit(s, b), 1, b)
        g.write32(ctrl(15), 0x205)                              # OUTOVER = force low
        s = g.read32(status(15))
        self.assertEqual((bit(s, 8), bit(s, 9), bit(s, 17)), (1, 0, 0))

    def test_edges_latch_and_clear_by_writing_one(self):
        g = settled()
        sh = 4 * (19 % 8)
        g.set_external(19, True)
        g.set_external(19, False)
        self.assertEqual(g.read32(intr(19)) >> sh & 0xF, LEVEL_LOW | EDGE_LOW | EDGE_HIGH)
        g.write32(intr(19), EDGE_HIGH << sh)                    # clear one edge only
        self.assertEqual(g.read32(intr(19)) >> sh & 0xF, LEVEL_LOW | EDGE_LOW)
        g.write32(intr(19) + 0x3000, EDGE_LOW << sh)            # CLR alias writes no 1s
        self.assertEqual(g.read32(intr(19)) >> sh & 0xF, LEVEL_LOW | EDGE_LOW)
        g.write32(intr(19) + 0x2000, EDGE_LOW << sh)            # SET alias: a 1 to that bit
        self.assertEqual(g.read32(intr(19)) >> sh & 0xF, LEVEL_LOW)

    def test_ints_is_intr_or_intf_masked_by_inte(self):
        g = settled()
        sh = 4 * (2 % 8)
        g.write32(0x40014100, (EDGE_HIGH | LEVEL_LOW) << sh)    # PROC0_INTE0
        self.assertEqual(g.read32(0x40014120) >> sh & 0xF, LEVEL_LOW)
        g.set_external(2, True)
        self.assertEqual(g.read32(0x40014120) >> sh & 0xF, EDGE_HIGH)
        self.assertTrue(bit(g.read32(status(2)), 24))           # IRQFROMPAD
        g.write32(0x40014110, LEVEL_LOW << sh)                  # PROC0_INTF0 forces LEVEL_LOW
        self.assertEqual(g.read32(0x40014120) >> sh & 0xF, EDGE_HIGH | LEVEL_LOW)
        self.assertEqual(g.read32(0x40014150) >> sh & 0xF, 0)  # PROC1_INTS0: nothing enabled
        g.write32(0x40014120, 0xFFFFFFFF)                       # INTS is read-only
        self.assertEqual(g.read32(0x40014100) >> sh & 0xF, EDGE_HIGH | LEVEL_LOW)


class PadTest(unittest.TestCase):
    def test_input_enable_off_reads_zero(self):
        g = settled()
        g.write32(pad(4), PAD_UP & ~0x40)
        self.assertEqual(bit(g.read32(IN), 4), 0)
        g.write32(pad(4), PAD_UP)
        self.assertEqual(bit(g.read32(IN), 4), 1)

    def test_output_disable_wins(self):
        g = settled()
        g.write32(pad(15), 0x40 | 0x80)                         # IE, OD
        g.write32(ctrl(15), 5)
        g.write32(OE_SET, 1 << 15)
        g.write32(OUT_SET, 1 << 15)
        self.assertEqual(g.pin_states()[15]['dir'], 'in')
        self.assertEqual(bit(g.read32(IN), 15), 0)              # floating, held at its reset level
        self.assertTrue(bit(g.read32(status(15)), 13))          # OETOPAD is before the pad's OD

    def test_debug_pads_and_voltage_select_read_back(self):
        g = settled()
        self.assertEqual(g.read32(0x4001C07C), 0xDA)
        self.assertEqual(g.read32(0x4001C080), 0x5A)
        g.write32(0x4001C000, 1)
        self.assertEqual(g.read32(0x4001C000), 1)

    def test_board_view_unchanged_for_untouched_pins(self):
        p = settled().pin_states()[7]
        self.assertEqual((p['dir'], p['level'], p['pull']), ('in', None, None))

    def test_keeper_shown_with_its_level(self):
        g = settled()
        g.write32(pad(9), PAD_UP)
        g.write32(pad(9), PAD_KEEP)
        p = g.pin_states()[9]
        self.assertEqual((p['pull'], p['level'], p['reads']), ('keeper', 1, 1))


class SyscfgTest(unittest.TestCase):
    def test_registers_store_with_their_masks(self):
        g = settled()
        self.assertEqual(g.read32(0x40004008), 0x10000000)      # PROC_CONFIG reset
        self.assertEqual(g.read32(0x40004014), 0x66)            # DBGFORCE reset
        g.write32(0x4000400C, 0xFFFFFFFF)
        self.assertEqual(g.read32(0x4000400C), 0x3FFFFFFF)      # GPIO0-29
        g.write32(0x4000400C + 0x3000, 1 << 3)                  # CLR alias
        self.assertEqual(g.read32(0x4000400C), 0x3FFFFFF7)
        g.write32(0x40004008, 0xFFFFFFFF)
        self.assertEqual(g.read32(0x40004008), 0xFF000000)      # PROCn_HALTED read-only


class CopyTest(unittest.TestCase):
    def test_copy_from_is_independent(self):
        a = settled()
        a.write32(pad(5), PAD_UP)
        a.write32(0x40014100, 0xF0)
        b = settled()
        b.copy_from(a)
        self.assertEqual(b.read32(IN), a.read32(IN))
        b.write32(pad(5), PAD_DOWN)
        b.write32(0x40014100, 0)
        self.assertEqual(bit(a.read32(IN), 5), 1)
        self.assertEqual(a.read32(0x40014100), 0xF0)


if __name__ == '__main__':
    unittest.main()


class PullTimingTest(unittest.TestCase):
    """The time model, with stand-in numbers (the real ones come from
    campaign F) and a clock the test moves by hand."""

    def setUp(self):
        import picosim.gpio as G
        self.G = G
        self.saved = (G.PULL_RISE_CYCLES, G.PULL_FALL_CYCLES, G.SYNC_CYCLES)
        G.PULL_RISE_CYCLES, G.PULL_FALL_CYCLES, G.SYNC_CYCLES = 300, 500, 2
        self.t = 0
        self.g = GPIO()
        self.g.clock = lambda: self.t

    def tearDown(self):
        self.G.PULL_RISE_CYCLES, self.G.PULL_FALL_CYCLES, self.G.SYNC_CYCLES = self.saved

    def at(self, t, pin=2):
        self.t = t
        return bit(self.g.read32(IN), pin)

    def test_rise_and_fall_take_their_time_plus_the_synchroniser(self):
        self.g.write32(pad(2), PAD_UP)                       # at t = 0
        self.assertEqual([self.at(t) for t in (0, 301, 302)], [0, 0, 1])
        self.t = 1000
        self.g.write32(pad(2), PAD_DOWN)
        self.assertEqual([self.at(t) for t in (1000, 1501, 1502)], [1, 1, 0])

    def test_level_stays_after_the_pin_settles(self):
        """Regression (2026-11-17): once a pin stopped moving, GPIO_IN went
        back to a value cached while it was still moving."""
        self.g.write32(pad(2), PAD_UP)
        self.assertEqual([self.at(t) for t in (302, 303, 10_000, 1_000_000)], [1, 1, 1, 1])
        self.g.write32(pad(2), PAD_DOWN)
        self.assertEqual([self.at(t) for t in (1_000_502, 1_000_503, 2_000_000)], [0, 0, 0])

    def test_bypass_removes_the_synchroniser(self):
        self.g.write32(0x4000400C, 1 << 2)                   # PROC_IN_SYNC_BYPASS for GP2
        self.g.write32(pad(2), PAD_UP)
        self.assertEqual([self.at(t) for t in (299, 300)], [0, 1])

    def test_status_and_intr_see_the_pin_itself(self):
        self.g.write32(pad(2), PAD_UP)
        self.t = 300
        self.assertTrue(bit(self.g.read32(status(2)), 17))  # INFROMPAD at once
        self.assertEqual(self.g.read32(intr(2)) >> 8 & 0xF, LEVEL_HIGH | EDGE_HIGH)
        self.assertEqual(self.at(300), 0)                    # GPIO_IN 2 cycles later

    def test_other_writes_do_not_restart_a_pull(self):
        self.g.write32(pad(2), PAD_UP)
        for t in range(0, 300, 7):                           # the program keeps writing SIO
            self.t = t
            self.g.write32(OUT_SET, 0)
        self.assertEqual(self.at(302), 1)

    def test_pull_taken_away_mid_rise_holds_the_old_level(self):
        self.g.write32(pad(2), PAD_UP)
        self.t = 100
        self.g.write32(pad(2), PAD_NONE)
        self.assertEqual(self.at(1000), 0)

    def test_driving_and_outside_are_at_once(self):
        self.g.write32(ctrl(15), 5)
        self.g.write32(OE_SET, 1 << 15)
        self.g.write32(OUT_SET, 1 << 15)
        self.assertEqual(self.at(2, 15), 1)                  # only the synchroniser
        self.g.set_external(19, True)
        self.assertEqual(self.g.pin_states()[19]['level'], 1)

    def test_board_view_says_moving(self):
        self.g.write32(pad(2), PAD_UP)
        self.t = 10
        p = self.g.pin_states()[2]
        self.assertTrue(p['moving'])
        self.assertEqual(p['level'], 0)


class LongRunTest(unittest.TestCase):
    """PERF-01: a long run that only writes GPIO registers keeps only the
    changes still on their way (gpio.py _fold), and what a program sees is
    the same run in one batch or an instruction at a time."""

    PROGRAM = """
        .syntax unified
        .cpu cortex-m0plus
        .thumb
main:
        push    {r4, r5, r6, r7, lr}
        ldr     r0, =0x40014004 + 8 * 15    @ GP15 CTRL: SIO
        movs    r1, #5
        str     r1, [r0]
        ldr     r0, =0xD0000024             @ GPIO_OE_SET
        ldr     r1, =1 << 15
        str     r1, [r0]
        ldr     r6, =results
        movs    r7, #0
loop:
        ldr     r0, =0x4001C00C             @ GP2's pad: pull-up, then pull-down
        movs    r1, #0x5A
        str     r1, [r0]
        mov     r3, r7                      @ wait r7 % 64 rounds
        lsls    r3, r3, #26
        lsrs    r3, r3, #26
1:      subs    r3, #1
        bpl     1b
        ldr     r2, =0xD0000004
        ldr     r4, [r2]                    @ GPIO_IN: has the rise arrived yet?
        movs    r1, #0x56
        str     r1, [r0]
        ldr     r2, =0x400140F0
        ldr     r5, [r2]                    @ INTR0: GP2's level and edges
        str     r1, [r2]                    @ clear GP2's edges (0x56: bits 4 and 6 among others)
        ldr     r2, =0xD000001C             @ GPIO_OUT_XOR: flip GP15
        ldr     r1, =1 << 15
        str     r1, [r2]
        str     r4, [r6]
        str     r5, [r6, #4]
        adds    r6, #8
        adds    r7, #1
        cmp     r7, #150
        bne     loop
blink:
        str     r1, [r2]                    @ then only writes, for a long time
        b       blink
        .data
results: .space 8 * 150
"""

    def load(self):
        import tempfile
        from picosim.loader import load_program
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = os.path.join(d.name, 'long.s')
        with open(path, 'w') as f:
            f.write(self.PROGRAM)
        return load_program(path)

    def test_batched_and_stepped_runs_see_the_same(self):
        prog = self.load()
        a, b = prog.clone(), prog.clone()
        a.cpu._core.run(40000)
        for _ in range(40000):
            b.cpu._core.step()
        ca, cb = a.cpu._core, b.cpu._core
        self.assertEqual(ca.m0_cycles, cb.m0_cycles)
        self.assertEqual(ca.get_memory(), cb.get_memory())
        seen = {ca.read32(0x20000000 + 8 * i) >> 2 & 1 for i in range(150)}
        self.assertEqual(seen, {0, 1}, "some reads come before the rise, some after")
        self.assertEqual(a.cpu.gpio.pin_states(), b.cpu.gpio.pin_states())

    def test_history_stays_short(self):
        prog = self.load()
        c, g = prog.cpu._core, prog.cpu.gpio
        for _ in range(20):
            c.run(5000)                          # no GPIO reads in the blink loop
            self.assertLessEqual(sum(len(h) for h in g._hist), 2)
