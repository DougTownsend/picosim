"""The path from the chip's results to the facts (2026-11-11): every result
names its own case before a batch is saved, every run's raw files are
indexed and backed up, a lost probe stops a run, campaigns rebuild their
facts from finished runs with no board (--offline), and B keeps the facts of
the stages it finished when a later one stops."""

import json
import os
import shutil
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from picosim.discover import campaign as CA
from picosim.discover import facts as FX
from picosim.discover import formats as F
from picosim.discover import runner as R

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAY = os.path.join(os.path.dirname(HERE), 'hwfacts', 'replay')


def h(*hws):
    return b''.join(x.to_bytes(2, 'little') for x in hws)


class CheckIdsTest(unittest.TestCase):
    """A read-back that went wrong must never be saved as results."""

    def run_with(self, out, ids):
        run = R.Run.__new__(R.Run)
        run.t = SimpleNamespace()
        b = SimpleNamespace(records=[{'id': i} for i in ids])
        return run._check_ids(0, out, b)

    @staticmethod
    def res(case_id):
        return F.pack_words(F.RESULT, {'id': case_id})

    def test_matching_ids_pass(self):
        self.run_with([self.res(7), self.res(8)], [7, 8])

    def test_zeros_are_refused(self):
        with self.assertRaisesRegex(R.RunError, 'reads as case 0, not case 8'):
            self.run_with([self.res(7), bytes(F.RESULT_SIZE)], [7, 8])

    def test_a_missing_result_is_refused(self):
        with self.assertRaisesRegex(R.RunError, 'case 8 has no result'):
            self.run_with([self.res(7), None], [7, 8])

    def test_the_link_is_checked_on_a_mismatch(self):
        run = R.Run.__new__(R.Run)

        def lost():
            raise RuntimeError('link lost')
        run.t = SimpleNamespace(check_link=lost)
        with self.assertRaisesRegex(RuntimeError, 'link lost'):
            run._check_ids(0, [bytes(F.RESULT_SIZE)], SimpleNamespace(records=[{'id': 3}]))


class ProbeLostTest(unittest.TestCase):
    def target(self, *lines):
        from picosim.hwtest.target import Target
        t = Target.__new__(Target)
        t.standin = None
        t.ocd = SimpleNamespace(log=['Info : clock speed 5000 kHz'])
        t._ocd_seen, t.lost = len(t.ocd.log), False
        t.ocd.log += list(lines)
        return t

    def test_an_unplugged_probe_stops_the_run(self):
        from picosim.hwtest.target import TargetError
        t = self.target('Error: error writing data: No such device (it may have been disconnected)',
                        'Error: CMSIS-DAP command CMD_CONNECT failed.')
        self.assertTrue(t.link_lost())
        with self.assertRaisesRegex(TargetError, 'probe Pico has gone'):
            t.check_link()

    def test_a_dead_target_keeps_its_own_advice(self):
        from picosim.hwtest.target import TargetError
        t = self.target('Error: Failed to connect multidrop rp2040.dap0')
        with self.assertRaisesRegex(TargetError, "TARGET Pico's USB"):
            t.check_link()

    def test_the_session_log_is_rate_limited(self):
        """OpenOCD's flood after an unplug (570,000 lines on 2026-11-10) must
        not fill the session log; every line still reaches .log."""
        from picosim.hwtest.target import OpenOCD
        o = OpenOCD.__new__(OpenOCD)
        written = []
        o.write_log = written.append
        o.log = []
        flood = ['Error: CMSIS-DAP command CMD_CONNECT failed.\n'] * 5000
        o.proc = SimpleNamespace(stdout=iter(flood), wait=lambda: 1)
        o._read_log()
        self.assertEqual(len(o.log), 5000)
        self.assertLess(len(written), 2 * OpenOCD.LOG_RATE)
        self.assertTrue(any('not written here' in w for w in written))


class RecordRawTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, 'cache')
        self.facts = os.path.join(self.tmp, 'hwfacts')
        self.backup = os.path.join(self.tmp, 'backup')
        os.makedirs(self.facts)
        self.run = os.path.join(self.cache, 'chip1', 'x-B1', '20260101-000000')
        os.makedirs(self.run)
        with open(os.path.join(self.run, 'state.json'), 'w') as f:
            json.dump({'done': [0], 'n_batches': 2, 'n_cases': 9}, f)
        with open(os.path.join(self.run, 'batch-0000.bin'), 'wb') as f:
            f.write(b'\x01' * 256)
        with open(os.path.join(self.run, 'log.txt'), 'w') as f:
            f.write('not a measurement\n')
        self.patches = [mock.patch.object(FX, 'cache_dir', lambda *p: os.path.join(self.cache, *p)),
                        mock.patch.object(FX, 'root', lambda kind: self.facts),
                        mock.patch.dict(os.environ, {'PICOSIM_RAW_BACKUP': self.backup})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def test_index_and_backup(self):
        e = FX.record_raw(self.run, 'openocd')
        self.assertEqual(set(e['files']), {'state.json', 'batch-0000.bin'})       # not log.txt
        self.assertEqual((e['finished'], e['batches_done'], e['batches'], e['cases']), (False, 1, 2, 9))
        copy = os.path.join(self.backup, 'chip1', 'x-B1', '20260101-000000', 'batch-0000.bin')
        self.assertEqual(FX.sha256_file(copy), e['files']['batch-0000.bin'])
        with open(os.path.join(self.facts, 'raw_index.json')) as f:
            idx = json.load(f)
        self.assertIn('chip1/x-B1/20260101-000000', idx)

    def test_a_damaged_backup_is_replaced(self):
        FX.record_raw(self.run, 'openocd')
        copy = os.path.join(self.backup, 'chip1', 'x-B1', '20260101-000000', 'batch-0000.bin')
        with open(copy, 'wb') as f:
            f.write(b'\x00' * 256)
        e = FX.record_raw(self.run, 'openocd')
        self.assertEqual(FX.sha256_file(copy), e['files']['batch-0000.bin'])

    def test_verify_raw(self):
        r = FX.verify_raw('openocd')
        self.assertEqual((r['ok'], r['problems']), (0, []))
        self.assertEqual(r['unindexed'], ['chip1/x-B1/20260101-000000'])
        FX.record_raw(self.run, 'openocd')
        self.assertEqual(FX.verify_raw('openocd'), {'ok': 1, 'problems': [], 'unindexed': []})
        with open(os.path.join(self.run, 'batch-0000.bin'), 'r+b') as f:
            f.write(b'\x02')                                          # the capture changed after indexing
        os.remove(os.path.join(self.backup, 'chip1', 'x-B1', '20260101-000000', 'state.json'))
        r = FX.verify_raw('openocd')
        self.assertEqual(r['ok'], 0)
        self.assertIn('cache: batch-0000.bin differs', r['problems'][0])
        self.assertIn('backup: state.json missing', r['problems'][0])

    def test_a_run_outside_the_cache_is_not_backed_up(self):
        """A test's run in a temporary folder must never reach the real
        backup; its key is still label/stage/run."""
        other = os.path.join(self.tmp, 'elsewhere', 'chip1', 'y-D1', '20260101-000001')
        shutil.copytree(self.run, other)
        e = FX.record_raw(other, 'openocd')
        self.assertFalse(os.path.exists(self.backup))
        idx = json.load(open(os.path.join(self.facts, 'raw_index.json')))
        self.assertIn('chip1/y-D1/20260101-000001', idx)
        self.assertEqual(e['batches_done'], 1)

    def test_the_standin_is_indexed_but_not_backed_up(self):
        FX.record_raw(self.run, 'standin')
        self.assertFalse(os.path.exists(self.backup))


class PartialFactsTest(unittest.TestCase):
    """B writes encodings.json after each stage: on 2026-11-10 B1 and B2
    finished on the chip, B3 stopped, and no encodings.json was written."""

    def test_finished_stages_keep_their_facts(self):
        from picosim.discover.cases import encodings as E
        recs, _ = FX.read_replay(os.path.join(REPLAY, 'encodings-B1.json'))
        recs = recs[:40]
        state = {'layout': 'hi', 'scratch_words': 0, 'flags': 6, 'campaign': 'encodings-B1', 'run_id': 'r',
                 'chip': {'chip_id': '0x10002927'}, 'created': '2026-11-10'}
        written, statuses = [], []

        def stage(name, s, cases, **kw):
            if s == 'B1':
                return recs, dict(state)
            raise R.RunError(f"{name}-{s} stopped before the end")

        ctx = SimpleNamespace(
            opts=SimpleNamespace(stages='B1,B2'), label='chip1', kind='standin', limit=None, offline=False,
            stop_flag=None, log=lambda s: None, stage=stage,
            status=lambda c, st, **kw: statuses.append((st, kw)),
            provenance=lambda st, **kw: {'chip': st['chip']},
            write_facts=lambda name, kind, prov, fs, **t: written.append((name, t)) or f'/x/{name}')
        with mock.patch.object(FX, 'write_replay'), self.assertRaises(R.RunError):
            E.run(ctx)
        self.assertEqual(len(written), 1)
        self.assertEqual(list(written[0][1]['stages']), ['B1'])
        self.assertEqual(written[0][1]['stages']['B1']['cases'], 40)
        # B2's status still points at B1's facts, should B2 stop
        b2 = [kw for st, kw in statuses if kw.get('step') == 'B2']
        self.assertEqual(b2[0].get('facts'), '/x/encodings.json')


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn (pip install unicorn)")
class OfflineTest(unittest.TestCase):
    """--offline: facts from finished runs on disk; the board is never touched."""

    @classmethod
    def setUpClass(cls):
        from picosim.hwtest.target import Target
        cls.tmp = tempfile.mkdtemp()
        cls.t = Target('standin')
        cls.cases = [R.make_case(i, h(0x1888 + (i % 7)), regs=[i, 3 * i, 5 * i + 1, i ^ 0x55]) for i in range(10)]
        base = os.path.join(cls.tmp, 'camp-S1')
        done = R.Run.start(cls.t, 'camp-S1', cls.cases, root=cls.tmp, run_id='20260101-000000', max_cases=4)
        done.go()
        done.close()
        part = R.Run.start(cls.t, 'camp-S1', cls.cases, root=cls.tmp, run_id='20260102-000000', max_cases=4)
        part.go(max_batches=1)                                  # newer, but unfinished
        part.close()
        cls.base, cls.done = base, done.path

    @classmethod
    def tearDownClass(cls):
        cls.t.close()
        shutil.rmtree(cls.tmp)

    def ctx(self):
        tmp = self.tmp

        class Ctx(CA.Context):
            root = property(lambda s: tmp)
        return Ctx(CA.OfflineTarget('Open On-Chip Debugger 0.12.0'), SimpleNamespace(), log=lambda s: None)

    def test_uses_the_newest_finished_run(self):
        c = self.ctx()
        self.assertTrue(c.offline)
        recs, state = c.stage('camp', 'S1', self.cases)
        self.assertEqual(state['run_id'], '20260101-000000')
        self.assertEqual([x for x, _ in recs], [x for x, _ in R.load(self.done)[0]])
        p = c.provenance(state)
        self.assertIn('rebuilt_offline', p)
        self.assertEqual(p['openocd'], 'Open On-Chip Debugger 0.12.0')

    def test_changed_cases_need_the_board(self):
        other = [dict(x, r0=x['r0'] + 1) for x in self.cases]
        with self.assertRaisesRegex(R.RunError, 'needs the board'):
            self.ctx().stage('camp', 'S1', other)

    def test_raw_files_are_not_changed(self):
        before = {n: FX.sha256_file(os.path.join(self.done, n)) for n in os.listdir(self.done)}
        self.ctx().stage('camp', 'S1', self.cases)
        after = {n: FX.sha256_file(os.path.join(self.done, n)) for n in os.listdir(self.done)}
        self.assertEqual(before, after)

    def test_the_offline_target_touches_nothing(self):
        t = CA.OfflineTarget()
        with self.assertRaises(R.RunError):
            t.rsp.read32(0x40000000)
        with self.assertRaises(R.RunError):
            t.reset_halt()


class InterpMeasuredVariantTest(unittest.TestCase):
    """interp_model.MEASURED_VARIANT reproduces what chip1 read (G,
    2026-11-10, hwfacts/peripherals/sio.json); the datasheet reading stays
    the default."""

    def test_chip_cases(self):
        from picosim.discover import interp_model as M
        from picosim.discover.cases import peripherals as G
        doc = json.load(open(os.path.join(os.path.dirname(HERE), 'hwfacts', 'peripherals', 'sio.json')))
        rows = [r for r in doc['interp_differences'] if 'cfg' in r]
        self.assertGreaterEqual(len(rows), 40)
        for r in rows:
            cfg = [int(v, 16) for v in r['cfg']]
            pop = int(r['pop'], 16)
            with self.subTest(case=r['id']):
                got = G.interp_predict(r['interp'], cfg, pop, M.MEASURED_VARIANT)
                for k, v in r['read'].items():
                    self.assertEqual(got[k], int(v, 16), k)
                old = G.interp_predict(r['interp'], cfg, pop)
                for k, v in r['predicted'].items():
                    self.assertEqual(old[k], int(v, 16), k)

    def test_force_msb_written_back(self):
        from picosim.discover import interp_model as M
        for variant, accum in ((None, 0x5), (M.MEASURED_VARIANT, 0x30000005)):
            m = M.Interp(0, variant)
            m.write(M.CTRL_LANE0, M.ctrl(force_msb=3))
            m.write(M.ACCUM0, 5)
            self.assertEqual(m.read(M.POP_LANE0), 0x30000005)
            self.assertEqual(m.read(M.ACCUM0), accum)


class RepeatUnsetMemoryTest(unittest.TestCase):
    """A repeat runs the batches in another order, so a register loaded from
    memory the agent never set differs between runs without saying anything
    about the chip.  check.unset_fields names such registers (real B1
    records from the chip); everything else is still compared."""

    def setUp(self):
        self.recs, meta = FX.read_replay(os.path.join(REPLAY, 'encodings-B1.json'))
        self.lay = F.LAYOUTS[meta['layout']]

    def case(self, hw):
        return next((c, r) for c, r in self.recs if int.from_bytes(c['code_bytes'][:2], 'little') == hw)

    def test_ldr_sp_loads_never_set_memory(self):
        from picosim.discover import check
        c, r = self.case(0x9800)                                    # LDR r0,[SP,#0]
        self.assertEqual(check.unset_fields(c, r, self.lay, 0), {'f_r0'})

    def test_an_alu_case_has_none(self):
        from picosim.discover import check
        c, r = self.case(0x1888)                                    # ADDS r0,r1,r2
        self.assertEqual(check.unset_fields(c, r, self.lay, 0), set())

    def test_compare_runs_drops_only_those_registers(self):
        tmp = tempfile.mkdtemp()
        try:
            picked = [self.case(0x9800), self.case(0x1888)]
            paths = []
            for name, bump in (('a', 0), ('b', 1)):
                path = os.path.join(tmp, name)
                os.makedirs(path)
                blob = bytearray(F.HEADER_SIZE + len(picked) * F.CASE_SIZE + 64)
                hdr = {k: 0 for k in F.HEADER}
                hdr['n_cases'] = len(picked)
                blob[:F.HEADER_SIZE] = F.pack_words(F.HEADER, hdr)
                res = b''
                code_at = F.HEADER_SIZE + len(picked) * F.CASE_SIZE
                for i, (c, r) in enumerate(picked):
                    c2 = dict(c, code=self.lay['BATCH'] + code_at + 4 * i, code_len=len(c['code_bytes']))
                    o = F.HEADER_SIZE + i * F.CASE_SIZE
                    blob[o:o + F.CASE_SIZE] = F.pack_case(c2)
                    blob[code_at + 4 * i:code_at + 4 * i + len(c['code_bytes'])] = c['code_bytes']
                    r2 = dict(r, f_r0=(r['f_r0'] + bump) & 0xFFFFFFFF)  # both cases' R0 differ in run b
                    res += F.pack_words(F.RESULT, r2)
                with open(os.path.join(path, 'batch-0000.in'), 'wb') as f:
                    f.write(bytes(blob))
                with open(os.path.join(path, 'batch-0000.bin'), 'wb') as f:
                    f.write(res)
                with open(os.path.join(path, 'state.json'), 'w') as f:
                    json.dump({'layout': 'hi', 'done': [0], 'flags': F.HF_NO_SCRATCH, 'scratch_words': 64}, f)
                paths.append(path)
            unset = []
            d = R.compare_runs(*paths, unset=unset)
            self.assertEqual(d, [(picked[1][0]['id'], ['f_r0'])])    # ADDS: a real disagreement
            self.assertEqual(unset, [picked[0][0]['id']])             # LDR [SP]: leftovers
        finally:
            shutil.rmtree(tmp)


class WindowComparisonTest(unittest.TestCase):
    """What the Testing window lists as a difference (overview.differences)."""

    def test_fact_against_a_prediction(self):
        from picosim.discover.overview import _fact_differs
        self.assertFalse(_fact_differs({'csr': 8, 'quotient': 8}, 8))              # every one as predicted
        self.assertTrue(_fact_differs({'csr': 8, 'quotient': 9}, 8))
        self.assertFalse(_fact_differs([], 'pull-up: LEVEL_HIGH ...'))             # nothing disagrees
        self.assertTrue(_fact_differs([3], 'pull-up: LEVEL_HIGH ...'))
        self.assertFalse(_fact_differs({'2': {}}, 'INOVER applies before GPIO_IN'))  # compared row by row
        self.assertTrue(_fact_differs('0x00000003', '0x00000002'))
        self.assertFalse(_fact_differs('0x0000000A', 10))
        self.assertTrue(_fact_differs(False, 'x'))

    def test_detail_agrees_false_is_listed(self):
        from picosim.discover import overview as O
        doc = {'provenance': {}, 'facts': [
            {'id': 'systick.calib', 'value': False, 'source': 'measured',
             'detail': {'calib': '0x0000270F', 'predicted': '0x00000000', 'from': 'SVD reset value', 'agrees': False}},
            {'id': 'systick.rvr0', 'value': True, 'source': 'measured', 'detail': {'agrees': True}}]}
        d = O.differences(doc)
        self.assertEqual([x['what'] for x in d], ['systick.calib'])
        self.assertIn('0x0000270F', d[0]['detail'])

    def test_a_model_reading_explains_a_difference(self):
        from picosim.discover import overview as O
        doc = {'provenance': {}, 'facts': [], 'sio': {'interp_differences': [
            {'id': 1, 'interp': 0, 'read': {'pop': '0x1'}, 'predicted': {'pop': '0x2'},
             'matches_variant': ['force_writeback=bus', 'MEASURED_VARIANT']},
            {'id': 2, 'interp': 1, 'read': {'pop': '0x1'}, 'predicted': {'pop': '0x2'}, 'matches_variant': []}]}}
        d = O.differences(doc)
        self.assertIn('MEASURED_VARIANT', d[0]['known'])
        self.assertEqual(d[1]['known'], '')


if __name__ == '__main__':
    unittest.main()
