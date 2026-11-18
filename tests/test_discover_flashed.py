"""Campaign F: its measurement code runs on the stand-in, and the flash is
always restored, even when a measurement fails part-way (with a fake guard:
no real flash is touched).

The GPIO pull-latency sweep (PERF-03): the stand-in runs a small, time-
bounded set of delays (STANDIN_SWEEP; test budget STANDIN_TEST_BUDGET_S for
the whole stand-in run of F, which took about 3.5 s on an M1 Pro on
2026-11-18); a chip always runs the physical sweep, call for call the one
recorded on 2026-11-17 (476 calls per synchroniser setting).  Success, a
timeout and the pad's restoration are tested with a fake core (no board, no
Unicorn)."""

import argparse
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from picosim.discover.cases import flashed
from picosim.discover.cases import peripherals as P

STANDIN_TEST_BUDGET_S = 60          # the whole stand-in run of campaign F (it takes a few seconds)

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
        t0 = time.monotonic()
        flashed.run(self.ctx('standin'))
        took = time.monotonic() - t0
        self.assertLess(took, STANDIN_TEST_BUDGET_S, f"the stand-in run of F took {took:.1f} s")
        env = self.facts()['environment']
        self.assertIn('not needed', env['restore'])
        # the SRAM loop the stand-in times: 64 x (subs, bne) + overheads, from Table 81
        self.assertEqual(env['interference']['min'], env['interference']['max'])
        self.assertEqual(env['interference']['runs'], 256)
        # every stage says how it ended and how long it took
        stages = {s['stage']: s for s in env['stages']}
        for name in ('clocks', 'xip_cache', 'flash_loop', 'interference', 'real_time', 'peripherals'):
            self.assertIn(name, stages)
            self.assertIn('outcome', stages[name])
            self.assertIn('seconds', stages[name])
        parts = {s['stage']: s for s in stages['peripherals']['parts']}
        self.assertEqual(set(parts), {'timer', 'uart', 'gpio'})
        # the GPIO sweep: the stand-in's reduced set, every call recorded and finished
        g = env['peripherals']['gpio']
        cov = g['coverage']
        self.assertEqual(cov['profile'], 'stand-in smoke')
        self.assertTrue(cov['status'].startswith('reduced'), cov['status'])
        self.assertEqual(cov['not_run'], [])
        self.assertEqual(cov['calls'], len(g['calls']))
        self.assertEqual(cov['ends'], {'done': cov['calls']})
        self.assertEqual({c[2] for c in g['calls'] if c[1] == 'coarse'}, set(P.STANDIN_SWEEP['coarse']))
        self.assertIn('gpio', parts['gpio']['stage'])
        self.assertEqual(parts['gpio']['outcome'], cov['status'])
        # picosim's pull model, as F's routine sees it on the stand-in (the chip: rise 49-52, fall 67-69)
        v = g['synchronised']['value']
        self.assertTrue(48 <= v['rise_seen_from_d'] <= 56, v)
        self.assertTrue(64 <= v['fall_seen_from_d'] <= 72, v)

    def _real(self, fail_in=None):
        guard = FakeGuard()
        boom = RuntimeError("a measurement failed")
        # The physical sweep's ~1000 calls run against a fake core here (each one
        # takes 5.5 s in Unicorn); the schedule is the chip's, see SweepScheduleTest.
        fake = FakeCore(self.t.rsp)
        real_call = P.Mini.call

        def call(m, code, regs=None, primask=1, timeout=2.0):
            if isinstance(code, tuple):
                return fake.call(code, regs, primask, timeout)
            return real_call(m, code, regs, primask, timeout)
        with mock.patch('picosim.discover.flashguard.FlashGuard.from_target', return_value=guard), \
                mock.patch.object(P, 'pull_latency_code', lambda d, settle=P.FLASHED_SYNC_SETTLE: ('pull', d, settle)), \
                mock.patch.object(P.Mini, 'call', call), \
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
        env = self.facts()['environment']
        self.assertTrue(env['restore']['identical'])
        cov = env['peripherals']['gpio']['coverage']
        self.assertEqual(cov['profile'], 'physical')
        self.assertEqual(cov['settle_cycles'], 250000)
        self.assertIsNone(cov['budget_s'])
        self.assertEqual(env['peripherals']['gpio']['synchronised']['ends'], {'done': 476})
        self.assertEqual([s['stage'] for s in env['stages']][-2:], ['peripherals', 'old_checks'])

    def test_the_flash_is_restored_when_a_measurement_fails(self):
        g = self._real(fail_in='measure_clocks')
        self.assertEqual(g.calls, ['mark_written', 'restore'])
        self.assertEqual(g.state['state'], 'restored')



# ── the pull-latency sweep with a fake core ────────────────────────────────

class FakeRsp:
    """Debugger reads and writes of a fake chip: a dict, every write logged."""

    def __init__(self, mem=None, fail_reads=()):
        self.mem = dict(mem or {})
        self.writes = []
        self.fail_reads = set(fail_reads)

    def read32(self, a):
        if a in self.fail_reads:
            raise OSError("not readable")
        return self.mem.get(a, 0)

    def write32(self, a, v):
        self.writes.append((a, v))
        self.mem[a] = v


class FakeCore:
    """Mini.call for pull_latency_code (patched to return ('pull', d,
    settle)): the pin reads high from d >= rise (rise - 2 with the
    synchroniser bypassed) and low from d >= fall; or every call ends as
    `end` ('timeout').  Each call moves `clock` on by `seconds`."""

    def __init__(self, rsp, rise=52, fall=69, end='done', seconds=0.0, clock=None, boom_after=None):
        self.rsp, self.rise, self.fall, self.end = rsp, rise, fall, end
        self.seconds, self.clock, self.boom_after = seconds, clock, boom_after
        self.log = []              # (bypassed, d, settle, timeout)

    def call(self, code, regs=None, primask=1, timeout=2.0):
        _, d, settle = code
        bypassed = bool(self._bypass() >> P.SYNC_PIN & 1)
        self.log.append((bypassed, d, settle, timeout))
        if self.boom_after is not None and len(self.log) > self.boom_after:
            raise OSError("the probe went away")
        if self.clock:
            self.clock.now += self.seconds
        regs = [0] * 17
        if self.end != 'done':
            return {'end': self.end, 'regs': regs}
        shift = 2 if bypassed else 0
        regs[4] = (1 << P.SYNC_PIN) if d >= self.rise - shift else 0
        regs[5] = 0 if d >= self.fall - shift else (1 << P.SYNC_PIN)
        return {'end': 'done', 'regs': regs}


    def _bypass(self):
        if hasattr(self.rsp, 'mem'):
            return self.rsp.mem.get(P.SYSCFG_BYPASS, 0)
        try:
            return self.rsp.read32(P.SYSCFG_BYPASS)
        except Exception:                        # noqa: BLE001 - the stand-in has no SYSCFG
            return 0


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


ORIG_PAD, ORIG_BYPASS = 0x56, 0x00000001


class SweepTestBase(unittest.TestCase):
    def setUp(self):
        self.rsp = FakeRsp({P.pad_addr(P.SYNC_PIN): ORIG_PAD, P.SYSCFG_BYPASS: ORIG_BYPASS})
        self.t = mock.Mock(rsp=self.rsp)
        self.logs = []
        p = mock.patch.object(P, 'pull_latency_code', lambda d, settle=P.FLASHED_SYNC_SETTLE: ('pull', d, settle))
        p.start()
        self.addCleanup(p.stop)

    def ctx(self, kind, sweep=None):
        return mock.Mock(kind=kind, pins=frozenset(), opts=argparse.Namespace(sweep=sweep), log=self.logs.append)

    def sweep(self, core, kind='openocd', profile=None, clock=None, sweep=None):
        m = mock.Mock()
        m.call = core.call
        return P._flashed_gpio(self.ctx(kind, sweep), self.t, m, 1.0, profile=profile,
                               **({'clock': clock} if clock else {}))

    def assert_restored(self):
        self.assertEqual(self.rsp.mem[P.pad_addr(P.SYNC_PIN)], ORIG_PAD)
        self.assertEqual(self.rsp.mem[P.SYSCFG_BYPASS], ORIG_BYPASS)
        last = {}
        for a, v in self.rsp.writes:
            last[a] = v
        self.assertEqual(last[P.pad_addr(P.SYNC_PIN)], ORIG_PAD)
        self.assertEqual(last[P.SYSCFG_BYPASS], ORIG_BYPASS)


class SweepScheduleTest(SweepTestBase):
    """A chip capture schedules the same calls as before PERF-03."""

    def test_the_physical_constants_are_unchanged(self):
        self.assertEqual(list(P.FLASHED_SYNC_COARSE), list(range(0, 8001, 32)))
        self.assertEqual((P.FLASHED_SYNC_FINE, P.FLASHED_SYNC_REPEATS, P.FLASHED_SYNC_SETTLE), (40, 3, 250000))
        self.assertEqual(P.PHYSICAL_SWEEP, {'name': 'physical', 'coarse': P.FLASHED_SYNC_COARSE, 'fine': 40,
                                            'repeats': 3, 'settle': 250000, 'call_timeout': 2.0,
                                            'budget_s': None})

    def test_a_chip_always_gets_the_physical_sweep(self):
        self.assertIs(P.sweep_profile(self.ctx('openocd')), P.PHYSICAL_SWEEP)
        self.assertIs(P.sweep_profile(self.ctx('gdb', 'physical')), P.PHYSICAL_SWEEP)
        with self.assertRaises(ValueError):
            P.sweep_profile(self.ctx('openocd', 'smoke'))
        self.assertIs(P.sweep_profile(self.ctx('standin')), P.STANDIN_SWEEP)
        self.assertIs(P.sweep_profile(self.ctx('standin', 'physical')), P.PHYSICAL_SWEEP)

    def test_the_command_line_refuses_smoke_on_a_chip(self):
        from picosim.discover.__main__ import main
        with mock.patch('builtins.print'):
            self.assertEqual(main(['flashed', '--sweep', 'smoke']), 2)

    def test_the_physical_schedule_call_for_call(self):
        core = FakeCore(self.rsp)                   # edges as the chip had them
        res = self.sweep(core)
        # written out from the rule, not from the module's constants: every D
        # 0..8000 step 32, then 3 x every D from (coarse edge - 40) to (edge + 2)
        want = []
        for bypassed, (er, ef) in ((False, (64, 96)), (True, (64, 96))):
            fine = sorted(set(range(er - 40, er + 3)) | set(range(ef - 40, ef + 3)))
            want += [(bypassed, d) for d in range(0, 8001, 32)] + [(bypassed, d) for d in fine * 3]
        self.assertEqual([(b, d) for b, d, _, _ in core.log], want)
        self.assertEqual({(s, t) for _, _, s, t in core.log}, {(250000, 2.0)})
        # 476 calls per setting, as recorded on the chip (hwfacts/peripherals/gpio_flashed.json)
        self.assertEqual(res['synchronised']['ends'], {'done': 476})
        self.assertEqual(res['bypass']['ends'], {'done': 476})
        self.assertEqual(res['coverage']['status'], 'complete: 952 calls, 952 finished')
        self.assertEqual(len(res['calls']), 952)
        self.assertEqual(res['calls'][0], ['synchronised', 'coarse', 0, 'done', 0])

    def test_the_schedule_matches_the_recorded_capture(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'hwfacts', 'peripherals', 'gpio_flashed.json')
        if not os.path.exists(path):
            self.skipTest("no recorded gpio_flashed.json")
        with open(path) as f:
            chip = json.load(f)['results']
        res = self.sweep(FakeCore(self.rsp, rise=52, fall=69))
        for setting in ('synchronised', 'bypass'):
            self.assertEqual(res[setting]['ends'], chip[setting]['ends'], setting)
            self.assertEqual(sorted(map(int, res[setting]['coarse_rise'])),
                             sorted(map(int, chip[setting]['coarse_rise'])), setting)
            self.assertEqual(sorted(map(int, res[setting]['rise'])), sorted(map(int, chip[setting]['rise'])), setting)


class SweepOutcomeTest(SweepTestBase):
    """Success, a timeout and restoring the pad, with a fake core."""

    def test_success(self):
        res = self.sweep(FakeCore(self.rsp), kind='standin')
        self.assertEqual(res['synchronised']['value']['rise_seen_from_d'], 52)
        self.assertEqual(res['synchronised']['value']['fall_seen_from_d'], 69)
        self.assertEqual(res['bypass']['value']['rise_seen_from_d'], 50)
        self.assertEqual(res['synchroniser_cycles_rise'], {'value': 2, 'predicted': 2})
        self.assertEqual(res['_differences'], [])
        cov = res['coverage']
        self.assertEqual(cov['profile'], 'stand-in smoke')
        self.assertEqual(cov['ends'], {'done': cov['calls']})
        self.assertLess(cov['calls'], 200)
        self.assertNotIn('settings_not_run', cov)
        self.assert_restored()
        json.dumps(res)                                  # goes into a facts file as it is

    def test_a_timeout_gives_a_bounded_readable_result(self):
        clock = FakeClock()
        core = FakeCore(self.rsp, end='timeout', seconds=10.0, clock=clock)    # calls that hang for 10 s
        t0 = time.monotonic()
        res = self.sweep(core, kind='standin', clock=clock)
        self.assertLess(time.monotonic() - t0, 5)
        cov = res['coverage']
        budget = P.STANDIN_SWEEP['budget_s']
        # calls stop once the budget is spent; the rest are listed, not run
        self.assertLessEqual(cov['calls'], budget / 10.0 + 1)
        self.assertGreater(len(cov['not_run']), 0)
        self.assertEqual(cov['calls'] + len(cov['not_run']), 2 * len(P.STANDIN_SWEEP['coarse']))
        self.assertTrue(cov['status'].startswith('budget exhausted'), cov['status'])
        self.assertEqual(cov['ends'], {'timeout': cov['calls']})
        self.assertTrue(all(c[3] == 'timeout' for c in res['calls']))
        v = res['synchronised']['value']
        self.assertIsNone(v['rise_seen_from_d'])
        self.assertIsNone(v['fall_seen_from_d'])
        self.assertNotIn('synchroniser_cycles_rise', res)
        self.assert_restored()
        json.dumps(res)
        self.assertTrue(any('budget exhausted' in s for s in self.logs))

    def test_a_timeout_on_a_chip_runs_every_call_and_says_so(self):
        clock = FakeClock()
        res = self.sweep(FakeCore(self.rsp, end='timeout', seconds=2.0, clock=clock), clock=clock)
        cov = res['coverage']
        self.assertEqual(cov['not_run'], [])
        self.assertEqual(cov['calls'], 2 * 251)                  # no edge, so no fine sweep
        self.assertEqual(cov['status'], 'no call finished (502 timeout): no result')
        self.assert_restored()

    def test_the_pad_is_restored_when_a_call_raises(self):
        with self.assertRaises(OSError):
            self.sweep(FakeCore(self.rsp, boom_after=300))
        self.assert_restored()

    def test_an_unreadable_syscfg_is_reported(self):
        self.rsp.fail_reads.add(P.SYSCFG_BYPASS)
        self.rsp.mem[P.SYSCFG_BYPASS] = 0
        res = self.sweep(FakeCore(self.rsp), kind='standin')
        self.assertIn('bypass', res['coverage']['settings_not_run'])
        self.assertIn('not run: bypass', res['coverage']['status'])
        self.assertEqual(self.rsp.mem[P.pad_addr(P.SYNC_PIN)], ORIG_PAD)

    def test_stage_outcomes(self):
        t0 = time.monotonic()
        self.assertEqual(P.stage_outcome('x', {'a': 1}, t0)['outcome'], 'ok')
        e = P.stage_outcome('x', {'error': 'TIMER is not readable'}, t0)
        self.assertEqual((e['outcome'], e['detail']), ('error', 'TIMER is not readable'))
        self.assertEqual(P.stage_outcome('g', {'coverage': {'status': 'complete: 1 calls'}}, t0)['outcome'],
                         'complete: 1 calls')


if __name__ == '__main__':
    unittest.main()
