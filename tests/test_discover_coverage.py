"""Campaign A's address sweep (discover/coverage.py, stage `sweep`) and
campaign E's address edges, with no board.

  * what is never read: flash, side-effect registers, their aliases and
    possible mirrors, DHCSR, the SIO through the debugger;
  * the sweep on a fake chip: every boundary found to the word, at every
    width, hangs kept from the core, and nothing it must not read ever
    asked for;
  * the physical grid's first round passes every safety check (the
    --dry-run), and has no write;
  * the sweep on the stand-in (a sparse grid; STANDIN_SWEEP_BUDGET_S),
    every debugger and core access checked, its table added to an existing
    memory_map.json;
  * campaign E's edge loads: seeded, read-only, safe, and compared."""

import argparse
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from picosim.discover import coverage as COV
from picosim.discover import safety
from picosim.discover.cases import memmap, fuzz

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False

STANDIN_SWEEP_BUDGET_S = 120         # the stand-in sweep took about 25 s on an M1 Pro (2026-11-18)


class FakeChip:
    """A prober over a made-up memory map: `ok` ranges answer, the rest
    fault; `byte_fault` ranges fault for 8-bit loads only; `hang` addresses
    hang the debugger.  Every address asked for is kept."""

    def __init__(self, ok, byte_fault=(), hang=()):
        self.ok, self.byte_fault, self.hang = ok, byte_fault, set(hang)
        self.dbg_asked, self.core_asked = [], []

    def debugger(self, addrs):
        self.dbg_asked += addrs
        return {a: 'hang' if a in self.hang else '0x00000000' for a in addrs}

    def core(self, name, pairs):
        self.core_asked += pairs
        out = {}
        for a, w in pairs:
            good = any(lo <= a < hi for lo, hi in self.ok)
            if w == 1 and any(lo <= a < hi for lo, hi in self.byte_fault):
                good = False
            out[(a, w)] = 'ok:0x00' if good else 'fault'
        return out


OK = [(0, 0x4000), (0x20000000, 0x20042000), (0x21000000, 0x21040000), (0x40000000, 0x4006A124),
      (0x4006C000, 0x40070000), (0xD0000000, 0xD0001000), (0xE0000000, 0xE0101000)]


def row_at(cov, addr):
    for r in cov['ranges']:
        if COV._int(r['lo']) <= addr < COV._int(r['hi']):
            return r
    raise AssertionError(f"0x{addr:08X} is in no range")


class NeverReadTest(unittest.TestCase):
    def test_what_is_never_read(self):
        for a in (0x10000000, 0x13FFFFFC,                    # flash
                  0x50400000, 0x50480000,                    # XIP_AUX and anything that may mirror it
                  0x40034000, 0x40035000, 0x40037000,        # UART0 DR and its atomic aliases
                  0x1400001C, 0x1400003C, 0x14FFFFFC,        # XIP STREAM_FIFO and its mirrors (every 0x20)
                  0x18000060, 0x18000160,                    # SSI DR0, and where it would repeat
                  0xE000EDF0, 0xE000E010,                    # DHCSR, SYST_CSR
                  0xD0000058, 0xD0000100, 0xD0000300,        # FIFO_RD, a spinlock, one repeated
                  0x50200020, 0x50200220):                   # PIO0 RXF0, where it would repeat
            with self.subTest(addr=hex(a)):
                self.assertIsNotNone(COV.why_not_read(a))
        for a in (0x00000000, 0x00004000, 0x20041FFC, 0x40014000, 0x40068000, 0x60000000, 0xD0000000,
                  0xE000ED00, 0xFFFFFFFC):
            with self.subTest(addr=hex(a)):
                self.assertIsNone(COV.why_not_read(a))

    def test_every_side_effect_register_is_never_read(self):
        for a in safety.read_side_effects():
            self.assertIsNotNone(COV.why_not_read(a), hex(a))


class FakeSweepTest(unittest.TestCase):
    def sweep(self, chip, profile=COV.PHYSICAL):
        cov, sw = COV.run_sweep(chip, profile, log=lambda s: None)
        return cov, sw

    def test_boundaries_to_the_word(self):
        chip = FakeChip(OK)
        cov, sw = self.sweep(chip)
        self.assertEqual(COV.check_ranges(cov['ranges']), [])
        self.assertTrue(cov['complete'])
        for edge in (0x4000, 0x20042000, 0x21040000, 0x4006A124, 0x40070000, 0xD0001000, 0xE0101000):
            a, b = row_at(cov, edge - 4), row_at(cov, edge)
            self.assertEqual(COV._int(b['lo']), edge, hex(edge))
            self.assertNotEqual(a['core']['read32'], b['core']['read32'], hex(edge))
        self.assertEqual(row_at(cov, 0x4006A120)['core'], {'read32': 'ok', 'read16': 'ok', 'read8': 'ok'})
        self.assertEqual(row_at(cov, 0x4006A124)['core']['read32'], 'fault')
        self.assertLessEqual(sw.rounds, 20)

    def test_nothing_never_read_is_asked_for(self):
        chip = FakeChip(OK)
        self.sweep(chip)
        for a in chip.dbg_asked:
            self.assertIsNone(COV.why_not_read(a), hex(a))
            self.assertFalse(COV.SIO_BLOCK[0] <= a < COV.SIO_BLOCK[1], hex(a))
        for a, w in chip.core_asked:
            self.assertIsNone(COV.why_not_read(a), hex(a))
            safety.check_read(a)
        cov = self.sweep(FakeChip(OK))[0]
        for a in (0x10000000, 0x50400000, 0x40034000, 0xE000EDF0):
            self.assertTrue(row_at(cov, a)['core']['read32'].startswith('not read'), hex(a))
        self.assertIn('side effect', row_at(cov, 0x40034000)['core']['read32'])

    def test_a_width_boundary_is_found(self):
        chip = FakeChip(OK, byte_fault=[(0x40014000, 0x40014800)])
        cov, _ = self.sweep(chip)
        self.assertEqual(row_at(cov, 0x40014000)['core']['read8'], 'fault')
        self.assertEqual(row_at(cov, 0x40014000)['core']['read32'], 'ok')
        self.assertEqual(COV._int(row_at(cov, 0x40014000)['lo']), 0x40014000)
        self.assertEqual(COV._int(row_at(cov, 0x40014800)['lo']), 0x40014800)
        self.assertEqual(COV.check_ranges(cov['ranges']), [])

    def test_hangs_never_reach_the_core(self):
        chip = FakeChip(OK, hang=[0x60000000])
        cov, _ = self.sweep(chip)
        self.assertNotIn(0x60000000, [a for a, _ in chip.core_asked])
        self.assertIn('hung', row_at(cov, 0x60000000)['core']['read32'])
        many = FakeChip(OK, hang=[0x60000000 + k * COV.MB for k in range(COV.MAX_HANGS + 1)])
        with self.assertRaises(COV.SweepError):
            self.sweep(many)

    def test_the_estimate(self):
        s = COV.estimate(COV.PHYSICAL)
        self.assertLess(s, 30 * 60)
        self.assertGreater(s, 60)


class DryRunTest(unittest.TestCase):
    def test_the_physical_grid_is_safe_and_read_only(self):
        ctx = mock.Mock(kind='openocd', opts=argparse.Namespace(stages='sweep', sweep=None), log=None)
        (stage, cases, kw), = memmap.dry_cases(ctx)
        self.assertEqual(stage, 'sweep-0')
        self.assertEqual(len(cases), len(COV.grid_addresses(COV.PHYSICAL)))
        from picosim.discover import formats as F
        from picosim.discover import runner as R
        lay = F.LAYOUTS[kw['layout']]
        for c in cases:
            self.assertEqual(c.get('writes', []), [])
            self.assertEqual(c['kind'], 'probe-read')
            safety.check_case({**c, 'code_len': len(R._code_bytes(c))}, lay)
            self.assertIsNone(COV.why_not_read(c['r1']))

    def test_the_other_stages_are_unchanged(self):
        ctx = mock.Mock(kind='openocd', opts=argparse.Namespace(stages=None, sweep=None), log=None)
        stages = {s: len(c) for s, c, _ in memmap.dry_cases(ctx)}
        self.assertEqual((stages['core-probes'], stages['timing']), (924, 68))

    def test_profiles(self):
        def ctx(kind, sweep=None, stages=None):
            return mock.Mock(kind=kind, opts=argparse.Namespace(sweep=sweep, stages=stages))
        self.assertIs(memmap.sweep_profile(ctx('openocd')), COV.PHYSICAL)
        self.assertIs(memmap.sweep_profile(ctx('standin')), COV.SMOKE)
        self.assertIs(memmap.sweep_profile(ctx('standin', 'physical')), COV.PHYSICAL)
        with self.assertRaises(ValueError):
            memmap.sweep_profile(ctx('openocd', 'smoke'))
        self.assertEqual(memmap._stages(ctx('openocd', stages='sweep')), ['sweep'])
        self.assertEqual(memmap._stages(ctx('openocd', stages='B1')), list(memmap.STAGES))
        with self.assertRaises(ValueError):
            memmap._stages(ctx('openocd', stages='timing'))


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn")
class StandinSweepTest(unittest.TestCase):
    def test_the_sweep_on_the_stand_in(self):
        from picosim.discover.campaign import Context
        from picosim.hwtest.target import Target
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        facts_dir = os.path.join(root, 'facts')
        os.makedirs(facts_dir)
        # an existing memory_map.json: the sweep adds its table and keeps the rest
        from picosim.discover import facts as FX
        with mock.patch('picosim.discover.facts.root', lambda kind: facts_dir):
            FX.write('memory_map.json', 'memory_map', {k: 'x' for k in FX.PROVENANCE_KEYS} | {'chip': {'a': 1}},
                     [FX.fact('memmap.probes', 3)], 'standin', probes={'0x00000000': {'why': 'kept'}})
        asked = {'dbg': [], 'core': [], 'writes': []}
        real_dbg, real_core = memmap.TargetProber.debugger, memmap.TargetProber.core

        def dbg(self, addrs):
            asked['dbg'] += addrs
            return real_dbg(self, addrs)

        def core(self, name, pairs):
            asked['core'] += pairs
            return real_core(self, name, pairs)
        t = Target('standin')
        real_w32, real_wmem = t.rsp.write32, t.rsp.write_mem

        def w32(a, v, *k, **kw):
            asked['writes'].append((a, 4))
            return real_w32(a, v, *k, **kw)

        def wmem(a, data, *k, **kw):
            asked['writes'].append((a, len(data)))
            return real_wmem(a, data, *k, **kw)
        t.rsp.write32, t.rsp.write_mem = w32, wmem
        opts = argparse.Namespace(target='standin', pins=frozenset(), limit=None, fresh=False, repeat=False,
                                  chip_label='chip1', stages='sweep', sweep=None)
        try:
            with mock.patch('picosim.discover.campaign.cache_dir', lambda *p: os.path.join(root, *p)), \
                    mock.patch('picosim.discover.facts.root', lambda kind: facts_dir), \
                    mock.patch.object(memmap.TargetProber, 'debugger', dbg), \
                    mock.patch.object(memmap.TargetProber, 'core', core):
                t0 = time.monotonic()
                memmap.run(Context(t, opts, log=lambda s: None))
                took = time.monotonic() - t0
        finally:
            t.close()
        self.assertLess(took, STANDIN_SWEEP_BUDGET_S, f"the stand-in sweep took {took:.0f} s")
        with open(os.path.join(facts_dir, 'memory_map.json')) as f:
            doc = json.load(f)
        cov = doc['coverage']
        self.assertTrue(cov['complete'], cov['problems'])
        self.assertEqual(cov['profile'], 'stand-in smoke')
        self.assertEqual(doc['probes'], {'0x00000000': {'why': 'kept'}})
        self.assertIn('memmap.probes', [f['id'] for f in doc['facts']])
        self.assertIn('coverage', doc['provenance'])
        # the stand-in's ROM and SRAM read; past SRAM5 faults
        self.assertEqual(row_at(cov, 0x100)['core']['read32'], 'ok')
        self.assertEqual(row_at(cov, 0x20042000)['core']['read32'], 'fault')
        self.assertEqual(COV._int(row_at(cov, 0x20042000)['lo']), 0x20042000)
        # never read what it must not; writes only where safety allows (the agent's SRAM)
        for a in asked['dbg'] + [a for a, _ in asked['core']]:
            self.assertIsNone(COV.why_not_read(a), hex(a))
        self.assertTrue(asked['core'])
        for a, n in asked['writes']:
            safety.check_write(a, 4)
            # the agent's SRAM, the PPB (vector catch, SysTick), RESETS' CLR alias (IO and pads out of reset)
            self.assertTrue(0x20000000 <= a < 0x20042000 or 0xE0000000 <= a < 0xE0100000 or a == 0x4000F000,
                            hex(a))


class EdgeCaseTest(unittest.TestCase):
    def test_edge_cases_are_seeded_safe_and_read_only(self):
        a, b = fuzz.edge_cases(7, coverage={}), fuzz.edge_cases(7, coverage={})
        self.assertEqual([(c['r1'], c['access_width']) for c in a], [(c['r1'], c['access_width']) for c in b])
        self.assertEqual(len(a), fuzz.EDGE_CASES_PER_ROUND)
        self.assertNotEqual([c['r1'] for c in a], [c['r1'] for c in fuzz.edge_cases(8, coverage={})])
        from picosim.discover import formats as F
        from picosim.discover import runner as R
        widths, misaligned = set(), 0
        for c in a + fuzz.edge_cases(9, n=400, coverage={}):
            self.assertEqual(c.get('writes', []), [])
            safety.check_case({**c, 'code_len': len(R._code_bytes(c))}, F.LAYOUTS['hi'])
            addr, w = c['target_addr'], c['access_width']
            widths.add(w)
            misaligned += addr % w != 0
            for word in {addr & ~3, (addr + w - 1) & ~3}:
                self.assertIsNone(COV.why_not_read(word), hex(addr))
        self.assertEqual(widths, {1, 2, 4})
        self.assertGreater(misaligned, 0)

    def test_edges_come_from_the_coverage_table(self):
        cov = {'ranges': [{'lo': '0x00000000', 'hi': '0x00004000'}, {'lo': '0x00004000', 'hi': '0x100000000'}]}
        self.assertEqual(COV.edges_from(cov), [0, 0x4000])
        for c in fuzz.edge_cases(1, n=50, coverage=cov):
            a = c['target_addr']
            self.assertTrue(min(abs(a - 0x4000), a, (1 << 32) - a) <= 12, hex(a))

    def record(self, addr, width, chip_end, value=0):
        """A case and result as R.load() gives them, for a load at addr."""
        code = {4: 0x6808, 2: 0x8808, 1: 0x7808}[width].to_bytes(2, 'little')
        slot = 0x20030000
        c = {'r1': addr, 'code_bytes': code, 'slot': slot, 'code_len': 2, 'id': 1}
        r = {'ipsr': 3, 'f_pc': slot + 2 if chip_end == 'ok' else slot, 'f_r0': value}
        return c, r

    def test_compare(self):
        from picosim.discover.replay import rom
        word = int.from_bytes(rom()[0x100:0x104], 'little')
        self.assertIsNone(fuzz.compare_edge(*self.record(0x100, 4, 'ok', word)))
        self.assertIsNone(fuzz.compare_edge(*self.record(0x4000, 4, 'fault')))         # both fault
        self.assertIsNone(fuzz.compare_edge(*self.record(0x40000000, 4, 'ok', 5)))     # not simulated: fine
        self.assertIsNone(fuzz.compare_edge(*self.record(0x101, 2, 'fault')))           # misaligned: both fault
        d = fuzz.compare_edge(*self.record(0x100, 4, 'ok', word ^ 1))
        self.assertIn('the chip reads', d[1])
        d = fuzz.compare_edge(*self.record(0x20041FFC, 4, 'fault'))
        self.assertIn('the chip faults', d[1])
        d = fuzz.compare_edge(*self.record(0x40000000, 4, 'fault'))
        self.assertIn('does not simulate', d[1])
        d = fuzz.compare_edge(*self.record(0x4000, 4, 'ok'))
        self.assertIn('HardFault', d[1])


if __name__ == '__main__':
    unittest.main()
