"""Campaign D: its cases, picosim's predictions for them, and stand-in runs
of the specials (D3) and a set of exception scenarios (D4), in which every
store the core makes to a peripheral is checked against the safety rules."""

import argparse
import json
import os
import tempfile
import unittest
from unittest import mock

from picosim.discover import formats as F
from picosim.discover import runner as R
from picosim.discover import safety as S
from picosim.discover.cases import timing as D

try:
    from tests.test_discover_peripherals import audit_writes, check_audit
except ImportError:                     # run from inside tests/
    from test_discover_peripherals import audit_writes, check_audit

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False


def _ctx(root, target, stages):
    from picosim.discover.campaign import Context
    opts = argparse.Namespace(target='standin', pins=frozenset(), limit=None, fresh=True, repeat=False,
                              chip_label='chip1', yes=True, stages=stages)
    c = Context(target, opts, log=lambda s: None)
    c.kind = 'standin'
    return c


class CasesTest(unittest.TestCase):
    def test_every_case_runs_five_times_with_its_own_id(self):
        for stage in D.STAGES:
            entries = D.GENERATORS[stage]()
            cases = D.stage_cases(stage, entries)
            ids = [c['id'] for c in cases]
            with self.subTest(stage=stage):
                self.assertEqual(len(cases), D.RUNS * len(entries))
                self.assertEqual(len(set(ids)), len(ids))
                self.assertEqual({i % D.ID_RUN for i in ids}, set(range(len(entries))))

    def test_calibration_often_enough(self):
        """Every site and alignment gets its own empty body at least every
        CALIB_EVERY forms, so each batch can subtract its own overhead."""
        for stage in ('D1', 'D3'):
            gap = {}
            for e in D.GENERATORS[stage]():
                key = (e['site'], e['align'])
                if e['role'] == 'calib':
                    gap[key] = 0
                else:
                    self.assertIn(key, gap, f"{stage}: {e['name']} before any calibration")
                    gap[key] += 1
                    self.assertLessEqual(gap[key], D.CALIB_EVERY + 1)

    def test_bodies_leave_the_systick_registers_alone(self):
        """A body must not write R6 or R7 (R7 = SYST_CVR, R6 = the first reading;
        R5 is written by the second reading, after the body)
        unless it saves them (the wrap)."""
        from picosim.discover import v6m
        for e in D.GENERATORS['D1']() + D.GENERATORS['D3']():
            if e['wrap'] or e['role'] != 'form':
                continue
            hws, i = e['one'], 0
            while i < len(hws):
                wide = v6m.is_prefix32(hws[i])
                d = v6m.decode32(hws[i], hws[i + 1]) if wide else v6m.decode16(hws[i])
                hw, i = hws[i], i + (2 if wide else 1)
                written = {v for k, v in d.fields.items() if k in ('rd', 'rdn', 'rt')}
                if d.name.startswith(('LDM', 'POP')):
                    written |= {r for r in range(8) if d.fields.get('list', 0) >> r & 1}
                with self.subTest(form=e['name']):
                    self.assertFalse(written & {6, 7}, f"0x{hw:04X} writes {sorted(written)}")

    def test_predictions(self):
        """picosim's Table 81 model for a few bodies, worked by hand."""
        def body(name, stage='D3'):
            entries = D.GENERATORS[stage]()
            i = next(i for i, e in enumerate(entries) if e['name'] == name and e['align'] == 'word')
            c = D.stage_cases(stage, entries, runs=1)[i]
            return D._prediction({**c, 'code_bytes': R._code_bytes(c)})      # as runner.load() gives it
        self.assertEqual(body('(empty)'), 0)                         # the body alone, without the SysTick reads
        self.assertEqual(body('NOP'), D.REPS)
        self.assertEqual(body('DMB'), 3 * D.REPS)
        self.assertEqual(body('SEV+WFE'), 3 * D.REPS)                # SEV 1 + WFE 2, nothing to wait for

    def test_d4_scenarios_are_safe(self):
        lay = F.LAYOUTS[D.LAYOUT]
        cases = D.stage_cases('D4', runs=1)
        for c in cases:
            S.check_case(c, lay, frozenset())
        self.assertEqual(len(cases), len(D.d4_entries()))


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn")
class StandInTest(unittest.TestCase):
    def setUp(self):
        from picosim.hwtest.target import Target
        self.root = tempfile.mkdtemp()
        self.t = Target('standin')
        self.patches = [mock.patch('picosim.discover.campaign.cache_dir', lambda *p: os.path.join(self.root, *p)),
                        mock.patch('picosim.discover.facts.root', lambda kind: os.path.join(self.root, 'facts'))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.t.close()

    def test_specials(self):
        seen = audit_writes(self.t.standin.chip)
        D.run(_ctx(self.root, self.t, 'D3'))
        check_audit(self, seen)
        with open(os.path.join(self.root, 'facts', 'timing.json')) as f:
            rows = [r for r in json.load(f)['specials'] if r['role'] == 'form']
        self.assertEqual(len(rows), 2 * len(D.SPECIALS))
        for r in rows:
            with self.subTest(form=r['name'], align=r['align']):
                # the stand-in charges Table 81, as picosim does: every special agrees, five times out of five
                self.assertEqual(r['status'], 'measured')
                self.assertEqual(r['value'], r['prediction'])
        with open(os.path.join(self.root, 'standin', 'timing', 'status.json')) as f:
            st = json.load(f)
        self.assertEqual((st['state'], st['step']), ('partial', 'only D3'))

    def test_exception_scenarios(self):
        from picosim.hwtest import standin
        want = {'latency SVC', 'latency PendSV', 'latency IRQ26', 'frame PendSV from PSP misaligned',
                'priority PendSV+SysTick, SysTick higher', 'nested: IRQ26 preempts PendSV', 'interrupted LDM r0!,{r1-r7} k=1'}
        entries = [e for e in D.d4_entries() if e['role'] == 'calib' or e['sc']['name'] in want]
        self.assertEqual(len(entries), 1 + len(want), [e['sc']['name'] for e in D.d4_entries()[1:12]])
        seen = audit_writes(self.t.standin.chip)
        ctx = _ctx(self.root, self.t, 'D4')
        recs, _ = ctx.stage(D.NAME, 'D4', D.d4_cases(entries, runs=2), on_prepare=D._hook, **D._kw('D4'))
        check_audit(self, seen)
        fs, table, _ = D.exception_facts(recs, entries)
        got = {f['id']: f['value'] for f in fs}
        for trig in ('svc', 'pendsv', 'irq26'):
            self.assertEqual(got[f'exceptions.entry.latency_{trig}'], standin.ENTRY_CYCLES)
            self.assertEqual(got[f'exceptions.exc_return.latency_{trig}'], '0xFFFFFFFD')
        self.assertEqual(got['exceptions.priority.priority_pendsv_systick_systick_higher'], 15)
        self.assertEqual(got['exceptions.nested.irq_exc_return'], '0xFFFFFFF1')
        frame = next(t for t in table if t['scenario'] == 'frame PendSV from PSP misaligned')
        for run in frame['runs']:
            self.assertTrue(int(str(run['stacked']['xpsr']), 0) & 0x200, "realignment bit")


if __name__ == '__main__':
    unittest.main()
