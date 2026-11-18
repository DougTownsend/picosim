"""`discover check` and `discover status` (review items RES-02 and RES-05).

check: known and new differences are counted case by case whatever order
the cases come in; a validation that compares nothing, or finds anything
new, fails; known-only differences pass; the report names its evidence and
its input datasets.  status: this computer's runs are labelled local, next
to the captures published in hwfacts/ and how to replay them.  No board,
no stand-in."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from picosim.discover import check
from picosim.discover import facts as FX
from picosim.discover import formats as F
from picosim.discover.replay import HWFACTS

LAY = F.LAYOUTS['hi']
ADDS = bytes.fromhex('8818')                    # adds r0, r1, r2
TABLE = [{'class': 'decoder', 'name': r'^ADDS', 'detail': r'^known', 'reason': 'understood: a test entry'}]


def case(i, code=ADDS, kind='agent'):
    c = {'id': i, 'kind': F.KINDS.get(kind, 0), 'code_bytes': code, 'code_len': len(code), 'batch': 0,
         'xpsr': 0x01000000, 'psp': 0x20040000, 'primask': 0}
    c.update({f'r{n}': n for n in range(13)})
    return c, {'ipsr': 0}


def run(order, outcomes, table=TABLE, source='fixture'):
    """check_records on cases whose comparison is `outcomes[id]`."""
    recs = [case(i) for i in order]
    with mock.patch.object(check, 'compare', lambda c, r, *a, **k: outcomes[c['id']]):
        return check.check_records(recs, LAY, 0, table=table, source=source)


KNOWN = ('differs', 'decoder', 'known: r0 differs')
NEW = ('differs', 'decoder', 'r0: chip 0x1, picosim 0x2')
MATCH = ('match', None, '')


class VerdictTest(unittest.TestCase):
    def test_known_and_new_in_one_group_in_either_order(self):
        outcomes = {1: KNOWN, 2: NEW, 3: MATCH}
        a, b = run([1, 2, 3], outcomes), run([2, 1, 3], outcomes)
        for res in (a, b):
            self.assertEqual((res.differs_known, res.differs_new), (1, 1))
            self.assertEqual(res.counts['differs'], 2)
            self.assertEqual(len(res.new), 1)
            g = res.groups[res.new[0]]
            self.assertEqual(g['example']['id'], 2)              # the new case itself, findable again
            self.assertEqual(g['example']['source'], 'fixture')
            self.assertEqual(res.verdict(), check.NEW)
            self.assertEqual(res.exit_code(), 1)
            text = check.report(res)
            self.assertIn('**FAIL**', text)
            self.assertIn('**new**', text)
            self.assertIn('case 2 of fixture', text)
        self.assertEqual(a.summary()['counts'], b.summary()['counts'])
        self.assertEqual(a.summary()['new'][0]['example']['id'], b.summary()['new'][0]['example']['id'])

    def test_known_only_passes(self):
        res = run([1, 3, 4], {1: KNOWN, 3: MATCH, 4: KNOWN})
        self.assertEqual((res.differs_known, res.differs_new, res.new), (2, 0, []))
        self.assertEqual(res.verdict(), check.PASS)
        self.assertEqual(res.exit_code(), 0)
        text = check.report(res)
        self.assertIn('**PASS**', text)
        self.assertIn('2 known differences', text)
        self.assertIn('understood: a test entry', text)

    def test_nothing_comparable_fails(self):
        res = run([1, 2], {1: ('skipped', None, 'lockup'), 2: (check.PENDING[0], 'faults', 'x')})
        self.assertEqual(res.compared, 0)
        self.assertEqual(res.verdict(), check.EMPTY)
        self.assertEqual(res.exit_code(), 3)
        self.assertIn('NOTHING COMPARED', check.report(res))

    def test_calibration_is_not_a_comparison(self):
        code = bytes.fromhex('3e683d68')             # ldr r6,[r7]; ldr r5,[r7]: an empty timed body
        recs = [case(1, code, 'timing')]
        with mock.patch.object(check, 'compare', lambda c, r, *a, **k: MATCH):
            res = check.check_records(recs, LAY, 0, table=[], source='d')
        self.assertEqual((res.counts['match'], res.calibration, res.compared), (1, 1, 0))
        self.assertEqual(res.verdict(), check.EMPTY)

    def test_summary_is_plain_data(self):
        res = run([1, 2, 3], {1: KNOWN, 2: NEW, 3: MATCH})
        s = json.loads(json.dumps(res.summary()))
        self.assertEqual(s['verdict'], 'new differences')
        self.assertEqual(s['counts']['compared'], 3)
        self.assertEqual(s['by_source']['fixture']['new'], 1)
        self.assertEqual(s['first_case_by_status']['differs (new)']['id'], 2)
        self.assertEqual(s['known'][0]['reason'], 'understood: a test entry')


class ReplayInputTest(unittest.TestCase):
    def test_an_empty_stand_in_cache(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty)
        res, files = check.check_replays(folder=empty, standin=True)
        self.assertEqual((files, res.total), ([], 0))
        self.assertEqual(res.exit_code(), 3)
        self.assertIn('--target standin', res.next_step)
        text = check.report(res)
        self.assertNotIn('against the RP2040', text)
        self.assertIn('stand-in', text.splitlines()[0])
        self.assertIn('--target standin', text)

    def test_the_cli_on_an_empty_stand_in_cache(self):
        from picosim.discover.__main__ import main
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty)
        out = io.StringIO()
        with mock.patch.object(check, 'replay_folder', lambda standin=False: empty), \
                contextlib.redirect_stdout(out):
            code = main(['check', '--standin', '--json', os.path.join(empty, 's.json')])
        self.assertEqual(code, 3)
        self.assertIn('verdict: nothing compared', out.getvalue())
        self.assertIn('--target standin', out.getvalue())
        with open(os.path.join(empty, 's.json')) as f:
            self.assertEqual(json.load(f)['exit_code'], 3)

    def test_the_cli_exit_status(self):
        from picosim.discover.__main__ import main
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for outcomes, want in (({1: KNOWN, 2: NEW}, 1), ({1: KNOWN, 2: MATCH}, 0)):
            res = run([1, 2], outcomes)
            with mock.patch.object(check, 'check_replays', return_value=(res, [])), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['check', '--report', os.path.join(tmp, 'r.md')]), want)

    @unittest.skipUnless(os.path.exists(os.path.join(HWFACTS, 'replay', 'timing-D3.json')), "no D3 replay")
    def test_a_recorded_replay_names_its_datasets(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for n in ('timing-D3.json', 'timing-D3.bin.gz'):
            shutil.copy2(os.path.join(HWFACTS, 'replay', n), tmp)
        with open(os.path.join(tmp, 'timing-D3.json')) as f:
            meta = json.load(f)
        res, files = check.check_replays(folder=tmp)
        self.assertEqual(res.evidence, 'recorded')
        self.assertEqual(len(res.sources), 1)
        src = res.sources[0]
        self.assertEqual((src['name'], src['run_id'], src['sha256'], src['cases']),
                         ('timing-D3', meta['run_id'], meta['sha256'], meta['cases']))
        self.assertEqual(sum(res.by_source['timing-D3'].get(k, 0) for k in
                             ('match', 'differs', 'skipped', check.NOT_COMPARED) + check.PENDING), meta['cases'])
        text = check.report(res, sources=files)
        self.assertIn('recorded RP2040 captures', text)
        self.assertIn(f"| timing-D3.json | {meta['run_id']} | {meta['sha256'][:12]} | {meta['cases']} |", text)
        self.assertGreater(res.compared, 0)


class StatusTest(unittest.TestCase):
    def status(self, *args, cache=None, hwfacts=None):
        from picosim.discover.__main__ import main
        cache = cache or tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cache, True)
        patches = [mock.patch('picosim.discover.campaign.cache_dir', lambda *p: os.path.join(cache, *p)),
                   mock.patch('picosim.discover.build.cache_dir', lambda *p: os.path.join(cache, *p))]
        if hwfacts is not None:
            real = FX.published
            patches.append(mock.patch.object(FX, 'published', lambda folder=None: real(hwfacts)))
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(out))
            self.assertEqual(main(['status', *args]), 0)
        return out.getvalue()

    @unittest.skipUnless(os.path.exists(os.path.join(HWFACTS, 'identity.json')), "no published facts")
    def test_a_clean_profile_still_shows_the_published_evidence(self):
        text = self.status()
        self.assertIn('Local runs on this computer', text)
        self.assertIn('local: not run here', text)
        self.assertIn('published: identity.json', text)
        self.assertIn('replay files', text)
        self.assertIn('python3 -m picosim.discover check --report', text)
        n = len(check.replay_manifests())
        self.assertIn(f"{n} replay files ({sum(m['cases'] for m in check.replay_manifests())} cases)", text)

    def test_a_checkout_without_evidence(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty)
        text = self.status(hwfacts=empty)
        self.assertIn('published: none in this checkout', text)
        self.assertNotIn('check --report', text)

    def test_the_stand_in_is_never_published(self):
        text = self.status('--standin')
        self.assertIn('local: not run here', text)
        self.assertNotIn('published:', text)
        self.assertIn('nothing from it is published', text)

    def test_published_reads_facts_and_replays(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        os.makedirs(os.path.join(tmp, 'replay'))
        with open(os.path.join(tmp, 'timing.json'), 'w') as f:
            json.dump({'kind': 'timing', 'provenance': {'campaign': 'D', 'date': '2026-11-10 14:35:19'},
                       'facts': [{'id': 'a'}, {'id': 'b'}]}, f)
        with open(os.path.join(tmp, 'raw_index.json'), 'w') as f:
            json.dump({'runs': {}}, f)
        with open(os.path.join(tmp, 'replay', 'timing-D2.json'), 'w') as f:
            json.dump({'name': 'timing-D2', 'campaign': 'timing-D2', 'cases': 7, 'run_id': 'r1', 'sha256': 'ab'}, f)
        p = FX.published(tmp)
        self.assertEqual(list(p), ['timing'])
        self.assertEqual(p['timing']['facts'][0]['facts'], 2)
        self.assertEqual(p['timing']['replays'], [{'name': 'timing-D2', 'cases': 7, 'run_id': 'r1', 'sha256': 'ab'}])


if __name__ == '__main__':
    unittest.main()
