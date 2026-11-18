"""No unknown addresses: picosim against campaign A's measured coverage of
the whole 4 GB (hwfacts/memory_map.json "coverage", discover/coverage.py).

For every measured range, at its first and last word, the word after the
first, the one before the last and its middle, at every width, picosim must
do what the chip did:

    chip: a load works     picosim: works, or stops saying the Pico has
                           something there it does not simulate
    chip: a load faults    picosim: stops with a HardFault ("HardFault on the
                           Pico too")
    not read (side effect, flash)   picosim never calls it a HardFault

and the ranges must cover 0x00000000-0xFFFFFFFF with no gap and no unknown
answer.  Before the sweep has run on a board this skips, saying how to run
it; once hwfacts has it, any address it does not cover fails here.

The checker itself is tested with a coverage table made from picosim's own
answers (it must pass) and one with a range flipped (it must fail)."""

import unittest

from picosim import cpu
from picosim.discover import coverage as COV

SWEEP_COMMAND = "python3 -m picosim.discover memmap --stages sweep"


def _probe_addrs(lo, hi):
    mid = (lo + (hi - lo) // 2) & ~3
    return sorted({a for a in (lo, lo + 4, mid, hi - 8, hi - 4) if lo <= a < hi})


def disagreements(ranges, max_report=40, from_table=True):
    """Where picosim's answer and the chip's differ, as text: picosim
    answering from this table, as it does from hwfacts/ (from_table), or
    from its built-in tables only (cpu.PICO_HAS / PICO_NOTHING: what a copy
    of picosim without hwfacts/ says)."""
    cpu.set_measured_coverage([(COV._int(r['lo']), COV._int(r['hi']), (r.get('core') or {}).get('read32') or '')
                               for r in ranges] if from_table else [])
    try:
        return _disagreements(ranges, max_report, from_table)
    finally:
        cpu.set_measured_coverage(None)


def _disagreements(ranges, max_report, from_table):
    out = []
    for r in ranges:
        lo, hi = COV._int(r['lo']), COV._int(r['hi'])
        for w in (4, 2, 1):
            chip = (r.get('core') or {}).get(f'read{8 * w}') or ''
            for a in _probe_addrs(lo, hi):
                sim, what = COV.picosim_access(a, w)
                if chip.startswith('not read'):
                    why = chip.split(':', 1)[1] if ':' in chip else ''
                    exists = why.strip().startswith('side effect') or 'flash' in why
                    # a register (or flash) is there; elsewhere picosim may guess a HardFault, saying
                    # the sweep did not read it
                    ok = sim != cpu.HARDWARE_FAULT if exists else (
                        sim != cpu.HARDWARE_FAULT or 'did not read' in str(what))
                else:
                    ok = COV.agrees(chip, sim)
                if not ok:
                    out.append(f"0x{a:08X} ({8 * w}-bit, range {r['lo']}-{r['hi']}): the chip: {chip}; "
                               f"picosim: {sim}: {str(what).splitlines()[0][:120]}")
                    if len(out) >= max_report:
                        return out
                if from_table and sim in (cpu.HARDWARE_FAULT, cpu.MODEL_LIMIT) and not chip.startswith('not read') \
                        and 'most likely' in str(what):
                    out.append(f"0x{a:08X}: measured, but picosim says 'most likely'")
    return out


class RecordedCoverageTest(unittest.TestCase):
    def setUp(self):
        self.cov = COV.recorded()
        if not self.cov:
            self.skipTest("hwfacts/memory_map.json has no address coverage yet: on a board, run "
                          f"`{SWEEP_COMMAND}` (about {COV.estimate(COV.PHYSICAL) / 60:.0f} min), "
                          "then commit hwfacts/memory_map.json")
        cpu.set_measured_coverage(None)

    def test_the_ranges_cover_the_whole_4gb(self):
        self.assertEqual(COV.check_ranges(self.cov['ranges']), [])
        self.assertTrue(self.cov.get('complete'))

    def test_picosim_agrees_with_the_chip_at_every_edge(self):
        bad = disagreements(self.cov['ranges'])
        self.assertEqual(bad, [], "picosim and the chip disagree:\n" + '\n'.join(bad))

    def test_the_built_in_tables_agree_too(self):
        """cpu.PICO_HAS and PICO_NOTHING (used where hwfacts/ is not
        installed) say what the sweep measured."""
        bad = disagreements(self.cov['ranges'], from_table=False)
        self.assertEqual(bad, [], "update cpu.PICO_HAS / PICO_NOTHING:\n" + '\n'.join(bad))


class CheckerTest(unittest.TestCase):
    """The checker above, on tables made without a board."""

    def table(self, answer):
        """A coverage table from a sweep whose 'chip' answers `answer(addr,
        width)` ('ok' or 'fault')."""
        class Prober:
            def debugger(self, addrs):
                return {a: '0x00000000' for a in addrs}

            def core(self, name, pairs):
                return {(a, w): answer(a, w) for a, w in pairs}
        cov, _ = COV.run_sweep(Prober(), COV.PHYSICAL, log=lambda s: None)
        return cov

    @staticmethod
    def picosim_as_chip(a, w):
        sim = COV.picosim_access(a, w)[0]
        return 'ok' if sim in ('works', cpu.MODEL_LIMIT) else 'fault'

    def test_a_table_that_matches_picosim_passes(self):
        cov = self.table(self.picosim_as_chip)
        self.assertEqual(COV.check_ranges(cov['ranges']), [])
        self.assertEqual(disagreements(cov['ranges']), [])

    def test_a_chip_that_differs_is_caught(self):
        # the 'chip' has nothing at 0x20010000-0x20010FFF (SRAM in picosim), byte loads
        # fault at 0x40014000-0x40014FFF (IO_BANK0, which picosim simulates), and it has
        # something at 0x40070000 (a HardFault in picosim's built-in table)
        def chip(a, w):
            if 0x20010000 <= a < 0x20011000:
                return 'fault'
            if 0x40014000 <= a < 0x40015000 and w == 1:
                return 'fault'
            if 0x40070000 <= a < 0x40074000:
                return 'ok'
            return self.picosim_as_chip(a, w)
        rows = self.table(chip)['ranges']
        bad = disagreements(rows)
        self.assertTrue(any(b.startswith('0x20010000') for b in bad), bad)
        self.assertTrue(any(b.startswith('0x40014000 (8-bit') for b in bad), bad)
        self.assertFalse(any('0x4007' in b for b in bad), bad)        # picosim answers from the table
        static = disagreements(rows, max_report=10 ** 6, from_table=False)
        self.assertTrue(any(b.startswith('0x40070000') for b in static), static)

    def test_a_gap_is_caught(self):
        cov = self.table(self.picosim_as_chip)
        rows = cov['ranges']
        self.assertTrue(COV.check_ranges(rows[:3] + rows[4:]))
        self.assertTrue(COV.check_ranges(rows[:-1]))

    def test_cpu_answers_from_the_measured_table(self):
        try:
            cpu.set_measured_coverage([(0, 0x4000, 'ok'), (0x4000, 0x10000000, 'fault'),
                                       (0x40068000, 0x4006C000, 'fault'),
                                       (0x4006C000, 0x40070000, 'ok'),
                                       (0xF0000000, 0x100000000, 'fault'),
                                       (0x50400000, 0x50500000, 'not read: XIP_AUX: a read pops it')])
            reason, what = cpu.unmapped_reason(0x40068000)
            self.assertEqual(reason, cpu.HARDWARE_FAULT)
            self.assertIn('measured', what)
            self.assertNotIn('most likely', what)
            reason, what = cpu.unmapped_reason(0xF0000000)
            self.assertEqual(reason, cpu.HARDWARE_FAULT)
            self.assertNotIn('most likely', what)
            reason, what = cpu.unmapped_reason(0x50400000)
            self.assertEqual(reason, cpu.MODEL_LIMIT)
            self.assertIn('not read on a Pico', what)
            self.assertIn('pops', what)
        finally:
            cpu.set_measured_coverage(None)
        # without a table: the datasheet's guess, which says so
        try:
            cpu.set_measured_coverage([])
            self.assertIn('most likely', cpu.unmapped_reason(0xF0000000)[1])
            self.assertIn(SWEEP_COMMAND, cpu.unmapped_reason(0xF0000000)[1])
        finally:
            cpu.set_measured_coverage(None)


if __name__ == '__main__':
    unittest.main()
