"""Campaign F: its measurement code runs on the stand-in, and the flash is
always restored, even when a measurement fails part-way (with a fake guard:
no real flash is touched)."""

import argparse
import json
import os
import tempfile
import unittest
from unittest import mock

from picosim.discover.cases import flashed

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False


class FakeGuard:
    def __init__(self):
        self.calls = []
        self.state = {'state': 'backed-up'}

    def backup(self):
        self.calls.append('backup')

    def mark_written(self):
        self.calls.append('mark_written')
        self.state['state'] = 'written'

    def restore(self):
        self.calls.append('restore')
        self.state['state'] = 'restored'
        return mock.Mock(backup_sha256='ab' * 32, readback_sha256='ab' * 32, boot_ok=True, boot_detail='port')


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn")
class FlashedTest(unittest.TestCase):
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

    def ctx(self, kind):
        from picosim.discover.campaign import Context
        opts = argparse.Namespace(target=kind, pins=frozenset(), limit=None, fresh=False, repeat=False,
                                  chip_label='chip1', yes=True)
        c = Context(self.t, opts, log=lambda s: None)
        c.kind = kind
        return c

    def facts(self):
        with open(os.path.join(self.root, 'facts', 'environment.json')) as f:
            return json.load(f)

    def test_stand_in_run(self):
        flashed.run(self.ctx('standin'))
        env = self.facts()['environment']
        self.assertIn('not needed', env['restore'])
        # the SRAM loop the stand-in times: 64 x (subs, bne) + overheads, from Table 81
        self.assertEqual(env['interference']['min'], env['interference']['max'])
        self.assertEqual(env['interference']['runs'], 256)

    def _real(self, fail_in=None):
        guard = FakeGuard()
        boom = RuntimeError("a measurement failed")
        with mock.patch('picosim.discover.flashguard.FlashGuard.from_target', return_value=guard), \
                mock.patch('picosim.discover.flashguard.load_state', side_effect=lambda *a: dict(guard.state)), \
                mock.patch.object(flashed, 'build_firmware', return_value='/nonexistent/probe.elf'), \
                mock.patch.object(flashed, '_symbols', return_value={'__bss_end__': 0x20004000}), \
                mock.patch.object(flashed, 'flash_program'), \
                mock.patch.object(flashed, 'wait_for_bkpt', return_value=True), \
                mock.patch.object(flashed, 'old_checks', return_value={}), \
                mock.patch.object(flashed, fail_in or 'real_time',
                                  side_effect=boom if fail_in else flashed.real_time):
            self.t.reset_halt()
            try:
                flashed.run(self.ctx('openocd'))
            except RuntimeError as e:
                self.assertIs(e, boom)
        return guard

    def test_the_flash_is_restored_after_a_full_run(self):
        g = self._real()
        self.assertEqual(g.calls, ['mark_written', 'restore'])
        self.assertTrue(self.facts()['environment']['restore']['identical'])

    def test_the_flash_is_restored_when_a_measurement_fails(self):
        g = self._real(fail_in='measure_clocks')
        self.assertEqual(g.calls, ['mark_written', 'restore'])
        self.assertEqual(g.state['state'], 'restored')


if __name__ == '__main__':
    unittest.main()
