"""The heat map's usage counts (picosim/heat.py): counting each encoding once
and multiplying by how often it ran must give exactly what clocking the
program cycle by cycle gives."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from picosim import heat
from picosim.loader import load_program
from picosim.timing import is_wide

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'examples')
EXAMPLES = ['01_hello', '02_add', '03_count_loop', '04_memory', '05_blink_led',
            '06_echo', '07_function', '08_button_led']


def run_by_cycles(name, max_cycles=60000, keys='ab\rq'):
    """Load an example and clock it cycle by cycle (programs that never end
    stop at an instruction boundary after max_cycles).  Returns the core
    and {component id: count} gathered straight from the signals."""
    prog = load_program(os.path.join(EX, name + '.s'))
    c = prog.cpu._core
    inp = [ord(k) for k in keys]

    def svc(num):
        if num == 1:
            c.halted = True
        elif num == 3:
            c.set_reg(0, inp.pop(0) if inp else 0xFFFFFFFF)
    c.svc_handler = svc
    counts = {}
    cond = None
    n = 0
    while not c.halted:
        if not c.in_insn:
            cond = None
            if n >= max_cycles:
                break
        info = c.step_cycle()
        n += 1
        for s in info['signals']:
            if s.startswith('COND='):
                cond = s[5:]
        wide = is_wide(c.read16(info['insn_addr']))
        for cid in heat.cycle_components(info['state'], info['signals'], wide, cond):
            counts[cid] = counts.get(cid, 0) + 1
        if info['last']:
            c.check_halt()
    return c, counts


class ExactnessTest(unittest.TestCase):
    def test_profile_totals_equal_cycle_by_cycle_counts(self):
        for name in EXAMPLES:
            with self.subTest(name):
                c, stepped = run_by_cycles(name)
                tot = heat.totals(c.profile())
                for cid in heat.IDS:
                    self.assertEqual(tot.get(cid, 0), stepped.get(cid, 0), f"{name}: {cid}")
                self.assertEqual(tot['_cycles'], c.cycles)
                self.assertEqual(tot['_execs'], c.steps)
                split = sum(tot.get(k, 0) for k in ('mem:fetch', 'mem:ram', 'mem:sio', 'mem:apb', 'mem:ahb'))
                self.assertEqual(split, tot.get('mem', 0))

    def test_io_accesses_are_split_out(self):
        c, _ = run_by_cycles('05_blink_led', 20000)
        tot = heat.totals(c.profile())
        self.assertGreater(tot.get('mem:sio', 0) + tot.get('mem:apb', 0), 0)

    def test_branch_paths_differ(self):
        u = heat.encoding_usage(0xD1F8)            # bne loop
        self.assertIn('PC:w', sum(u['taken'], ()))
        self.assertNotIn('PC:w', sum(u['not_taken'][3:], ()))   # after fetch, no PC load
        self.assertIn('Z:r', sum(u['taken'], ()))

    def test_register_reads_and_writes(self):
        cyc = heat.encoding_usage(0x1842)['always']   # adds r2, r0, r1
        flat = sum(cyc, ())
        self.assertEqual(flat.count('R0:r'), 1)
        self.assertEqual(flat.count('R1:r'), 1)
        self.assertEqual(flat.count('R2:w'), 1)
        self.assertEqual({f for f in flat if f.endswith(':w') and len(f) == 3}, {'N:w', 'Z:w', 'C:w', 'V:w'})
        same = sum(heat.encoding_usage(0x1800)['always'], ())   # adds r0, r0, r0
        self.assertEqual(same.count('R0:r'), 2)

    def test_per_line_sums_to_totals(self):
        c, _ = run_by_cycles('03_count_loop')
        prog = load_program(os.path.join(EX, '03_count_loop.s'))
        rows = heat.per_line(c.profile(), prog.source)
        tot = heat.totals(c.profile())
        for cid in ('alu', 'R4:w', 'mem'):
            self.assertEqual(sum(r['usage'].get(cid, 0) for r in rows), tot[cid])
        user = [r for r in rows if not r['os']]
        self.assertTrue(any(r['text'].startswith('adds') and r['line'] for r in user))
        mine = heat.totals(c.profile(), code_only=True)
        self.assertLess(mine['_cycles'], tot['_cycles'])


class TimelineTest(unittest.TestCase):
    def traced(self, name, max_cycles=60000):
        prog = load_program(os.path.join(EX, name + '.s'))
        c = prog.cpu._core
        c.itrace_enable(1 << 20)
        return prog, c

    def test_buckets_sum_to_totals(self):
        for name in ('03_count_loop', '05_blink_led'):
            prog, c = self.traced(name)
            c.svc_handler = lambda n: setattr(c, 'halted', True) if n == 1 else None
            c.run(3000)                              # the fast path
            tot = heat.totals(c.profile())
            heat.set_table(c)
            self.assertEqual(c.heat_trace_cycles(), c.cycles, name)
            for bucket in (1, 7, 4096):
                rows = heat.timeline(c, 0, c.cycles, bucket, refresh=False)
                self.assertEqual(len(rows), -(-c.cycles // bucket))
                for i, cid in enumerate(heat.IDS):
                    self.assertEqual(sum(r[i] for r in rows), tot.get(cid, 0), f"{name} {cid} /{bucket}")

    def test_one_cycle_buckets_match_stepping(self):
        prog, c = self.traced('03_count_loop')
        c.svc_handler = lambda n: None
        seen = []
        cond = None
        while not c.halted and len(seen) < 400:
            if not c.in_insn:
                cond = None
            info = c.step_cycle()
            cond = next((s[5:] for s in info['signals'] if s.startswith('COND=')), cond)
            ids = heat.cycle_components(info['state'], info['signals'],
                                        heat._wide(c.read16(info['insn_addr'])), cond)
            seen.append(sorted(ids))
            if info['last']:
                c.check_halt()
        rows = heat.timeline(c, 0, len(seen), 1)
        for t, ids in enumerate(seen):
            got = sorted(cid for i, cid in enumerate(heat.IDS) for _ in range(rows[t][i]))
            self.assertEqual(got, ids, f"cycle {t}")

    def test_window_and_cap(self):
        prog, c = self.traced('05_blink_led')
        c.itrace_enable(100)
        c.run(150)
        self.assertEqual(c.itrace_len(), 100)
        self.assertEqual(c.itrace_dropped, 50)
        heat.set_table(c)
        whole = heat.timeline(c, 0, c.heat_trace_cycles(), 10, refresh=False)
        part = heat.timeline(c, 30, 70, 10)
        self.assertEqual(part, whole[3:7])


class PipelineEstimateTest(unittest.TestCase):
    def test_cycles_match_the_performance_window(self):
        from picosim import timing
        for name in ('01_hello', '03_count_loop', '05_blink_led', '07_function'):
            with self.subTest(name):
                c, _ = run_by_cycles(name, 20000)
                prof = c.profile()
                t = heat.totals(prof, model='m0plus')
                m = timing.summarize(prof)['machines']['m0plus']
                untimed_execs = sum(r['execs'] for r in prof
                                    if timing.pico_row(r['hw1'], r['hw2'])[2] is None)
                self.assertEqual(t['_cycles'], m['cycles'] + untimed_execs)
                self.assertEqual(t['p_decode'], c.steps)          # one decode per instruction
                self.assertEqual(t.get('R4:w', 0), heat.totals(prof).get('R4:w', 0))

    def test_pipeline_timeline_sums(self):
        prog = load_program(os.path.join(EX, '03_count_loop.s'))
        c = prog.cpu._core
        c.itrace_enable(1 << 16)
        c.svc_handler = lambda n: None
        c.run(60)
        tot = heat.totals(c.profile(), model='m0plus')
        heat.set_table(c, 'm0plus')
        self.assertEqual(c.heat_trace_cycles(), tot['_cycles'])
        rows = heat.timeline(c, 0, tot['_cycles'], 5, refresh=False, model='m0plus')
        for i, cid in enumerate(heat.PIPE_IDS):
            self.assertEqual(sum(r[i] for r in rows), tot.get(cid, 0), cid)


class ServerHeatTest(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from test_friction import server
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        src = os.path.join(EX, '03_count_loop.s')
        self.path = os.path.join(self.dir, 'p.s')
        shutil.copy(src, self.path)
        self.s = server(self.path)

    def run_to_end(self):
        s = self.s
        s.cmd_run({'cps': 0})
        for _ in range(500):
            if not s.running:
                break
            s.run_slice()

    def last(self, kind):
        return [m for m in self.s.out if m.get('type') == kind][-1]

    def test_views(self):
        self.run_to_end()
        s = self.s
        s.cmd_heat({'view': 'totals'})
        t = self.last('heat')
        vals = dict(zip([r['id'] for r in t['rows']], [v[0] for v in t['values']]))
        self.assertEqual(vals['fsm'], s.core.cycles)
        self.assertFalse(t['estimate'])
        s.cmd_heat({'view': 'timeline', 'bucket': 1, 'start': 0, 'end': 10 ** 9})
        tl = self.last('heat')
        self.assertEqual(tl['cols'][-1][1], s.core.cycles)
        self.assertEqual(tl['bucket'], 1)
        fsm = [r['id'] for r in tl['rows']].index('fsm')
        self.assertEqual(sum(tl['values'][fsm]), s.core.cycles)
        s.cmd_heat({'view': 'lines', 'code_only': True})
        ln = self.last('heat')
        self.assertTrue(all(not c['os'] for c in ln['cols']))
        s.cmd_heat({'view': 'totals', 'model': 'm0plus'})
        self.assertTrue(self.last('heat')['estimate'])

    def test_timeline_caps_columns(self):
        self.run_to_end()
        self.s.cmd_heat({'view': 'timeline', 'bucket': 1, 'start': 0, 'end': 10 ** 9})
        heat.MAX_COLS, old = 50, heat.MAX_COLS
        try:
            self.s.cmd_heat({'view': 'timeline', 'bucket': 1, 'start': 0, 'end': 10 ** 9})
            self.assertLessEqual(len(self.last('heat')['cols']), 50)
        finally:
            heat.MAX_COLS = old

    def test_baseline_compare_and_export(self):
        import csv
        import json
        self.run_to_end()
        s = self.s
        s.cmd_heat({'view': 'compare'})
        self.assertFalse(self.last('heat')['baseline'])
        s.cmd_heat_baseline({'action': 'save'})
        s.cmd_heat({'view': 'compare'})
        cmp = self.last('heat')
        self.assertTrue(cmp['baseline'])
        self.assertTrue(all(r['change'] == 0 for r in cmp['rows']))
        s.cmd_heat({'view': 'compare', 'per': 1000})
        fsm = next(r for r in self.last('heat')['rows'] if r['id'] == 'fsm')
        self.assertAlmostEqual(fsm['after'], 1000.0)
        out = os.path.join(self.dir, 'lines.csv')
        s.cmd_heat_export({'path': out, 'view': 'lines'})
        self.assertTrue(self.last('heat_exported')['ok'])
        with open(out) as f:
            body = [l for l in f if not l.startswith('#')]
        rows = list(csv.DictReader(body))
        self.assertEqual(sum(int(r['alu']) for r in rows), heat.totals(s.core.profile())['alu'])
        out = os.path.join(self.dir, 'all.json')
        s.cmd_heat_export({'path': out, 'view': 'totals'})
        with open(out) as f:
            self.assertEqual(json.load(f)['meta']['cycles'], s.core.cycles)
        s.cmd_heat_baseline({'action': 'clear'})
        s.cmd_heat({'view': 'compare'})
        self.assertFalse(self.last('heat')['baseline'])

    def test_step_back_trace_equals_fresh_run(self):
        s = self.s
        for _ in range(30):
            s.cmd_step_insn({})
        for _ in range(5):
            s.cmd_step_back({})
        from test_friction import server
        fresh = server(self.path)
        for _ in range(25):
            fresh.cmd_step_insn({})
        self.assertEqual(s.core.cycles, fresh.core.cycles)
        self.assertEqual(s.core.itrace(), fresh.core.itrace())
        self.assertEqual(heat.totals(s.core.profile()), heat.totals(fresh.core.profile()))


if __name__ == '__main__':
    unittest.main()
