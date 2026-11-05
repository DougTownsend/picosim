"""The Testing window's Hardware facts group (plan S18): the server's
hw_discover_* commands and picosim/discover/overview.py, on the stand-in,
with the discovery cache in a temporary folder."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from picosim.discover import overview as O
from picosim.discover.cases import ORDER

import test_server

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False


class TempCache:
    """Every cache path discovery uses, moved into a temporary folder."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix='discover-gui-')

    def cache_dir(self, *parts):
        d = os.path.join(self.root, *parts)
        os.makedirs(d, exist_ok=True)
        return d

    def __enter__(self):
        self.patches = [mock.patch(f'picosim.discover.{m}.cache_dir', self.cache_dir)
                        for m in ('campaign', 'facts', 'runner')]
        self.patches.append(mock.patch('picosim.discover.overview._cache', self.cache_dir))
        self.patches.append(mock.patch('picosim.discover.flashguard.DEFAULT_STATE_PATH',
                                       os.path.join(self.root, 'flash_state.json')))
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.root, ignore_errors=True)


ENCODINGS_DOC = {
    'schema': 1, 'kind': 'encodings',
    'provenance': {'chip': {'chip_id': '0x20002927'}, 'date': '2026-11-04 01:00:00', 'openocd': 'Open On-Chip Debugger 0.12.0',
                   'swd_khz': 5000, 'picosim': {'commit': 'abc', 'dirty': False}, 'agent': 'f00d', 'campaign': 'B',
                   'run_id': 'r1', 'runs': 2, 'agreed': True, 'target': 'standin'},
    'facts': [{'id': 'encodings.v6m_defined_16bit', 'value': 1, 'source': 'derived'}],
    'stages': {'B1': {'cases': 2, 'differences': [
        {'status': 'differs', 'class': 'flags', 'encoding': 'ADDS (register)', 'cases': 3, 'known': None,
         'example': {'code': '8818', 'name': 'ADDS (register)', 'id': 7,
                     'regs': ['0x00000000', '0x7FFFFFFF', '0x00000001'] + ['0x00000000'] * 7 +
                             ['0x0000000A', '0x0000000B', '0x0000000C'],
                     'nzcv': '0110', 'psp': '0x20041F00', 'primask': 0, 'detail': 'NZCV chip 1001, picosim 0000'}},
        {'status': 'pending: exception model', 'class': 'faults', 'encoding': 'SVC', 'cases': 1, 'known': None,
         'example': {'code': '00df', 'name': 'SVC', 'id': 9, 'regs': ['0x00000000'] * 13, 'nzcv': '0000',
                     'psp': '0x20041F00', 'primask': 0, 'detail': 'the chip took exception 11'}}]}},
}


class OverviewTest(unittest.TestCase):
    def test_every_campaign_has_a_row_before_anything_ran(self):
        with TempCache():
            rows = O.campaigns('standin')
        self.assertEqual([r['name'] for r in rows], list(ORDER))
        for r in rows:
            self.assertEqual(r['state'], 'not run')
            self.assertTrue(r['title'])
            self.assertTrue(r['about'], r['name'])

    def test_a_campaign_that_does_not_load_still_has_a_row(self):
        with TempCache(), mock.patch('picosim.discover.cases.module', side_effect=ImportError('half written')):
            r = O.row('timing', 'standin')
        self.assertFalse(r['available'])
        self.assertIn('half written', r['error'])
        self.assertEqual(r['about'], O.ABOUT['timing'])

    def test_partial_shows_n_of_m_batches(self):
        with TempCache() as tc:
            d = tc.cache_dir('standin', 'encodings')
            with open(os.path.join(d, 'status.json'), 'w') as f:
                json.dump({'campaign': 'encodings', 'state': 'partial', 'step': 'B2'}, f)
            run = tc.cache_dir('standin', 'encodings-B2', '20261104-000000')
            with open(os.path.join(run, 'state.json'), 'w') as f:
                json.dump({'done': [0, 1, 2], 'n_batches': 8}, f)
            r = O.row('encodings', 'standin')
        self.assertEqual((r['state'], r['step'], r['done'], r['total']), ('partial', 'B2', 3, 8))

    def test_differences_and_the_example_program(self):
        diffs = O.differences(ENCODINGS_DOC)
        self.assertEqual([d['kind'] for d in diffs], ['picosim', 'pending'])
        d = diffs[0]
        self.assertTrue(d['openable'])
        self.assertEqual((d['what'], d['where'], d['cases']), ('ADDS (register)', 'stages.B1', 3))
        with tempfile.TemporaryDirectory() as tmp:
            path = O.case_program(d, 'encodings', out_dir=tmp)
            from picosim.loader import load_program
            prog = load_program(path)
            prog.run_to_main()
            core = prog.cpu._core
            for _ in range(100):
                if core.halted:
                    break
                core.step()
            self.assertTrue(core.halted)
            self.assertEqual(core.regs[0], 0x80000000)          # adds r0, r1, r2: 0x7FFFFFFF + 1
            self.assertEqual(core.regs[10:13], [0xA, 0xB, 0xC])
            self.assertEqual((core.N, core.V), (True, True))
            self.assertIn('adds', ' '.join(prog.asm_map.values()).lower())

    def test_facts_that_differ_from_their_prediction(self):
        doc = dict(ENCODINGS_DOC, stages={}, facts=[
            {'id': 'a', 'value': '0x00000003', 'source': 'measured', 'prediction': {'value': 3, 'from': 'x'}},
            {'id': 'b', 'value': 2, 'source': 'measured', 'prediction': {'value': 3, 'from': 'the datasheet'}},
            {'id': 'c', 'value': True, 'source': 'measured', 'prediction': {'value': '0x410CC601', 'from': 'SDK'}}])
        diffs = O.differences(doc)
        self.assertEqual([d['what'] for d in diffs], ['b'])
        self.assertEqual(diffs[0]['kind'], 'expected')

    def test_flash_status_needs_a_checked_backup(self):
        with TempCache() as tc:
            self.assertFalse(O.flash_status()['ready'])
            b = {k: os.path.join(tc.root, k + '.bin') for k in ('bin', 'cache_bin')}
            state = {'state': 'backed-up', 'backup': b, 'sha256': 'ab' * 32, 'unique_id': 'E66', 'size': 1}
            with open(os.path.join(tc.root, 'flash_state.json'), 'w') as f:
                json.dump(state, f)
            self.assertFalse(O.flash_status()['ready'])          # its files are missing
            for p in b.values():
                open(p, 'wb').close()
            self.assertTrue(O.flash_status()['ready'])
            with open(os.path.join(tc.root, 'flash_state.json'), 'w') as f:
                json.dump(dict(state, state='written'), f)
            fs = O.flash_status()
            self.assertFalse(fs['ready'])
            self.assertTrue(fs['pending_restore'])


class ServerCommandsTest(unittest.TestCase):
    def client(self):
        c = test_server.start(None)
        c.until(lambda m: m['type'] == 'state')
        return c

    def hw(self, c, event, limit=10.0):
        m, _ = c.until(lambda m: m['type'] == 'hw' and m.get('event') == event, limit)
        return m

    def test_status_before_anything_ran(self):
        with TempCache():
            c = self.client()
            c.send(cmd='hw_discover_status', kind='standin')
            m = self.hw(c, 'discover_status')
        self.assertEqual(m['kind'], 'standin')
        self.assertEqual([r['state'] for r in m['campaigns']], ['not run'] * len(ORDER))
        self.assertFalse(m['flash']['ready'])

    def test_run_needs_a_connection(self):
        with TempCache():
            c = self.client()
            c.send(cmd='hw_discover_run', campaign='identity')
            m = self.hw(c, 'discover_refused')
        self.assertIn('connect', m['message'])

    def test_open_case_without_an_example_is_refused(self):
        with TempCache():
            c = self.client()
            c.send(cmd='hw_discover_open_case', campaign='identity', index=0, kind='standin')
            m = self.hw(c, 'discover_refused')
        self.assertIn('could not make the example', m['message'])

    @unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn (pip install unicorn)")
    def test_identity_on_the_standin_through_the_commands(self):
        with TempCache():
            c = self.client()
            c.send(cmd='hw_connect', kind='standin')
            c.until(lambda m: m['type'] == 'hw' and m.get('event') == 'status' and m.get('connected')
                    and not m.get('busy'), 30)
            c.send(cmd='hw_discover_run', campaign='flashed')     # F: never without its confirmation
            self.assertIn('confirm', self.hw(c, 'discover_refused')['message'])
            c.send(cmd='hw_discover_run', campaign='identity', fresh=True)
            self.hw(c, 'discover_start')
            done = self.hw(c, 'discover_done', 120)
            self.assertTrue(done['ok'], done['message'])
            self.assertEqual(done['row']['state'], 'done')
            c.send(cmd='hw_discover_status')
            st = self.hw(c, 'discover_status')
            self.assertEqual(st['kind'], 'standin')
            self.assertEqual({r['name']: r['state'] for r in st['campaigns']}['identity'], 'done')
            c.send(cmd='hw_discover_detail', campaign='identity')
            d = self.hw(c, 'discover_detail')['detail']
            labels = dict((k, v) for k, v in d['provenance'])
            self.assertIn('stand-in', labels['Source'])
            self.assertIn('0x410CC601', labels['Chip'])
            self.assertTrue(d['facts_rows'])
            c.send(cmd='hw_disconnect')

    def test_open_case_loads_the_example(self):
        with TempCache() as tc:
            path = os.path.join(tc.root, 'encodings.json')
            with open(path, 'w') as f:
                json.dump(ENCODINGS_DOC, f)
            d = tc.cache_dir('standin', 'encodings')
            with open(os.path.join(d, 'status.json'), 'w') as f:
                json.dump({'campaign': 'encodings', 'state': 'done', 'facts': path, 'differences': 1}, f)
            c = self.client()
            c.send(cmd='hw_discover_open_case', campaign='encodings', index=0, kind='standin')
            opened = self.hw(c, 'discover_opened')
            self.assertTrue(opened['path'].endswith('.s'))
            self.assertTrue(opened['path'].startswith(tc.root))
            prog, _ = c.until(lambda m: m['type'] == 'program')
            st, _ = c.until(lambda m: m['type'] == 'state' and m.get('loaded'))
            self.assertEqual(st['path'], opened['path'])


if __name__ == '__main__':
    unittest.main()
