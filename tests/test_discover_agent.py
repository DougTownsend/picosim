"""The discovery agent and runner on the stand-in: how cases end, the
fields of a result, lockups, hangs, resume after a kill, a case that damages
the sled, and two runs agreeing."""

import os
import tempfile
import threading
import unittest

from picosim.discover import formats as F
from picosim.discover import runner as R
from picosim.discover.safety import SafetyError

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False

LAY = F.LAYOUTS['hi']
SLOT, S = LAY['SLOT'], LAY['SCRATCH']


def h(*hws):
    return b''.join(x.to_bytes(2, 'little') for x in hws)


def by_id(path):
    recs, _ = R.load(path)
    return {c['id']: (c, r) for c, r in recs}


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn (pip install unicorn)")
class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from picosim.hwtest.target import Target
        cls.t = Target('standin')
        cls.root = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.t.close()

    def run_cases(self, cases, name, **kw):
        run = R.Run.start(self.t, name, cases, root=self.root, **kw)
        run.go()
        run.close()
        return run.path

    def test_one_batch_of_every_ending(self):
        cases = [
            R.make_case(1, h(0x1888), regs=[0, 5, 7], nzcv=0b0010),        # adds r0, r1, r2
            R.make_case(2, h(0xE01E)),                                      # b .+0x40
            R.make_case(3, h(0x6808), regs=[0, 0x50000000]),               # ldr r0, [r1] (unmapped)
            R.make_case(4, h(0xDF05)),                                      # svc #5
            R.make_case(5, h(0x6008), regs=[0xCAFEF00D, S + 8], seed=0x1234),   # str r0, [r1]
            R.make_case(6, h(0xF380, 0x8808), regs=[0x12345679]),          # msr msp, r0 (garbage)
            R.make_case(7, h(0x683E, 0xBF00, 0xBF00, 0x683D), regs=[0] * 7 + [0xE000E018]),
            R.make_case(8, h(0x4700), regs=[(LAY['SLED'] + 0x200)]),       # bx r0, Thumb bit clear
            R.make_case(9, h(0x2001), psp=LAY['PSP_DEFAULT'] - 4),         # movs r0, #1, PSP not 8-aligned
        ]
        got = by_id(self.run_cases(cases, 'endings'))
        end = {i: R.ending(c, r, LAY) for i, (c, r) in got.items()}
        self.assertEqual(end[1], ('fell-through', SLOT + 2))
        self.assertEqual(end[2], ('sled', SLOT + 0x40))
        self.assertEqual(end[3], ('fault', SLOT))
        self.assertEqual(end[4], ('svc', SLOT + 2))
        self.assertEqual(end[5][0], 'fell-through')
        self.assertEqual(end[6][0], 'fell-through')
        self.assertEqual(end[8], ('sled', LAY['SLED'] + 0x200))
        r = got[1][1]
        self.assertEqual((r['f_r0'], r['f_r1'], r['f_r2']), (12, 5, 7))
        self.assertEqual(r['f_xpsr'] >> 28, 0)                   # 5 + 7: no flags
        self.assertEqual(r['f_xpsr'] & (1 << 24), 1 << 24)
        self.assertEqual(r['exc_return'], F.EXC_RETURN_PSP)
        self.assertEqual(r['frame'], LAY['PSP_DEFAULT'] - 32)
        self.assertEqual((r['msp'], r['psp'], r['primask'], r['control'] & 1), (LAY['MSP_TOP'], LAY['PSP_DEFAULT'] - 32, 0, 0))
        self.assertEqual(got[3][1]['f_r0'], 0)                     # the load never happened
        self.assertEqual(got[4][1]['ipsr'], 11)
        self.assertEqual(got[5][1]['changed'], [(8, 0xCAFEF00D)])
        self.assertEqual(got[1][1]['changed'], [])
        self.assertEqual(got[6][1]['msp'], 0x12345678)             # SP[1:0] read as zero
        r7 = got[7][1]
        self.assertEqual((r7['r6'] - r7['r5']) % (1 << 24), 2 + 1 + 1)   # ldr + 2 nops
        self.assertEqual(got[8][1]['f_xpsr'] & (1 << 24), 0)       # INVSTATE: T clear in the frame
        r9 = got[9][1]
        self.assertEqual(r9['frame'], LAY['PSP_DEFAULT'] - 4 - 36)      # realigned to 8 bytes
        self.assertTrue(r9['f_xpsr'] & (1 << 9))

    def test_lockup_is_recorded_and_the_run_goes_on(self):
        cases = [R.make_case(1, h(0x2001)),
                 R.make_case(2, h(0xF380, 0x8809), regs=[0x50000000]),    # msr psp, r0: stacking fails
                 R.make_case(3, h(0x2002))]
        got = by_id(self.run_cases(cases, 'lockup'))
        self.assertEqual(R.ending(*got[2], LAY)[0], 'lockup')
        self.assertEqual(got[3][1]['f_r0'], 2)
        self.assertEqual(R.ending(*got[3], LAY)[0], 'fell-through')

    def test_bkpt_cases_halt_and_are_summed_up_once(self):
        """A BKPT halts the core while a debugger is attached: each such case
        is recorded 'halted' and the batch goes on, with one log line for
        all of them (2026-11-10, batch 74).  The first version of that line
        sat inside the batch loop and hung every batch that stopped early."""
        logged = []
        cases = [R.make_case(1, h(0x2001)),
                 R.make_case(2, h(0xBE00)),                                 # bkpt #0
                 R.make_case(3, h(0xBE07)),                                 # bkpt #7
                 R.make_case(4, h(0x2004))]
        got = by_id(self.run_cases(cases, 'bkpt', log=logged.append))
        self.assertEqual(R.ending(*got[2], LAY)[0], 'halted')
        self.assertEqual(R.ending(*got[3], LAY)[0], 'halted')
        self.assertEqual(got[4][1]['f_r0'], 4)
        self.assertEqual(R.ending(*got[4], LAY)[0], 'fell-through')
        self.assertEqual(sum('2 BKPT cases stopped the core' in l for l in logged), 1, logged)
        self.assertFalse(any('halted (try' in l for l in logged), logged)

    def test_watchdog_ends_an_endless_loop(self):
        cases = [R.make_case(1, h(0xE7FE)), R.make_case(2, h(0x2003))]       # b .
        got = by_id(self.run_cases(cases, 'watchdog', wd_reload=3000))
        self.assertEqual(R.ending(*got[1], LAY), ('watchdog', SLOT))
        self.assertEqual(got[2][1]['f_r0'], 3)

    def test_filling_a_big_window_does_not_set_off_the_watchdog(self):
        # the agent refills 4 KB of scratch (thousands of cycles) before the
        # first case of a batch: the watchdog must start after that
        cases = [R.make_case(i, h(0x2001 + i), seed=0x1111 * (i % 2)) for i in range(3)]
        got = by_id(self.run_cases(cases, 'bigwindow', scratch_words=1024, wd_reload=3000))
        for i in range(3):
            self.assertEqual(R.ending(*got[i], LAY)[0], 'fell-through', i)
            self.assertEqual(got[i][1]['changed'], [])

    def test_a_case_that_writes_the_sled_is_found(self):
        cases = [R.make_case(1, h(0x2001)),
                 R.make_case(2, h(0x6008), regs=[0x12345678, LAY['SLED'] + 0x100]),   # str into the sled
                 R.make_case(3, h(0x2003))]
        got = by_id(self.run_cases(cases, 'sled'))
        self.assertEqual(R.ending(*got[2], LAY)[0], 'corrupts the agent')
        self.assertEqual(got[1][1]['f_r0'], 1)
        self.assertEqual(got[3][1]['f_r0'], 3)

    def test_resume_after_a_kill_gives_the_same_results(self):
        cases = [R.make_case(i, h(0x1888 + (i % 7)), regs=[i, 3 * i, 5 * i + 1, i ^ 0x55]) for i in range(10)]
        whole = self.run_cases(cases, 'whole', max_cases=2)
        run = R.Run.start(self.t, 'killed', cases, root=self.root, max_cases=2)
        run.go(max_batches=2)                                  # "killed" after two batches
        run.close()
        self.assertFalse(run.finished)
        again = R.Run.resume(self.t, run.path, cases)
        again.go()
        again.close()
        self.assertTrue(again.finished)
        self.assertEqual(R.compare_runs(whole, run.path), [])
        self.assertEqual(len(R.load(run.path)[0]), 10)
        other = [dict(c, r0=c['r0'] + 1) for c in cases]
        with self.assertRaises(R.RunError):
            R.Run.resume(self.t, run.path, other)

    def test_repeat_in_another_order_agrees(self):
        cases = [R.make_case(i, h(0x4040 + i % 64), regs=[i * 0x01010101, 0x80000000 >> (i % 31)], nzcv=i % 16)
                 for i in range(12)]
        a = self.run_cases(cases, 'rep-a', max_cases=3)
        b = R.Run.start(self.t, 'rep-b', cases, root=self.root, max_cases=3, repeat_of='rep-a', seed=7)
        self.assertNotEqual(b.state['order'], sorted(b.state['order']))
        b.go()
        b.close()
        self.assertEqual(R.compare_runs(a, b.path), [])

    def test_a_forbidden_write_stops_before_upload(self):
        bad = R.make_case(1, h(0x6008), regs=[0, 0x40064000], writes=[(0x40064000, 4, 0)])   # VREG
        with self.assertRaises(SafetyError):
            R.Run.start(self.t, 'unsafe', [bad], root=self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'unsafe')))

    def test_stop_during_a_batch_keeps_nothing_of_it(self):
        flag = threading.Event()
        cases = [R.make_case(i, h(0xE7FE if i == 1 else 0x2001)) for i in range(3)]    # case 1 loops (no watchdog)
        run = R.Run.start(self.t, 'stopmid', cases, root=self.root, stop_flag=flag)
        threading.Timer(1.0, flag.set).start()
        run.go()
        run.close()
        self.assertEqual(run.state['done'], [])
        self.assertFalse(os.path.exists(os.path.join(run.path, 'batch-0000.bin')))
        flag.clear()
        again = R.Run.resume(self.t, run.path, cases, stop_flag=flag)
        self.assertEqual(again.state['done'], [])
        again.close()

    def test_a_timed_run_sees_the_final_halt(self):
        """Timed runs keep OpenOCD's polling off while the cases run.  It must
        come back on, or the halt at the agent's last BKPT goes unseen and the
        batch ends in a timeout ('stopped between cases, state done')."""
        import time
        logged = []
        cases = [R.make_case(i, h(0x2001)) for i in range(40)]
        run = R.Run.start(self.t, 'timed', cases, root=self.root, timing=True, log=logged.append)
        t0 = time.monotonic()
        run.go()
        run.close()
        self.assertTrue(run.finished)
        self.assertLess(time.monotonic() - t0, 5.0)        # not the timeout
        self.assertFalse(any('not reported' in l or 'still running' in l for l in logged), logged)
        self.assertEqual(self.t.rsp.monitor('poll'), 'background polling: on\n')

    def test_with_polling_off_a_halt_waits_for_polling(self):
        import struct
        from picosim.hwtest.target import CODE
        r = self.t.rsp
        self.t.reset_halt()

        def launch():
            r.write_mem(CODE, struct.pack('<3H', 0x46C0, 0x46C0, 0xBE00))    # nop; nop; bkpt
            r.write_reg(self.t.xpsr_n, 0x01000000)
            r.write_reg(15, CODE)
        launch()
        self.t.poll(False)
        self.assertEqual(self.t.run(0.5), 'timeout')        # the halt is never reported
        self.assertEqual(r.read_core_regs()[15], CODE + 4)  # yet the core reached the BKPT
        self.t.poll(True)
        launch()
        self.assertEqual(self.t.run(5.0, quiet=0.2), 'halted')
        self.assertFalse(self.t.polled_running)
        self.assertEqual(self.t.rsp.monitor('poll'), 'background polling: on\n')

    def test_stop_flag(self):
        flag = threading.Event()
        flag.set()
        run = R.Run.start(self.t, 'stop', [R.make_case(1, h(0x2001))], root=self.root, stop_flag=flag)
        run.go()
        run.close()
        self.assertFalse(run.finished)


class FormatTest(unittest.TestCase):
    def test_sizes_and_layouts(self):
        for name, lay in F.LAYOUTS.items():
            self.assertEqual(lay['VARS'] - lay['AGENT'], lay['AGENT_SIZE'])
            self.assertLessEqual(lay['MSP_TOP'], lay['PSP_LO'])
            self.assertEqual(lay['AGENT'] % 256, 0)
            self.assertIn('.equ R_FRAME, 124', F.layout_inc(name))
        self.assertEqual(R.RESULT_CAP, 1216)

    def test_agent_builds_small(self):
        from picosim.discover.build import agent
        for layout in F.LAYOUTS:
            a = agent(layout)
            self.assertLess(len(a.image), 3 * 1024)
            self.assertEqual(a.symbols['vectors'], F.LAYOUTS[layout]['AGENT'])

    def test_result_round_trip(self):
        vals = {f: i * 0x01010101 for i, f in enumerate(F.RESULT)}
        vals['n_changed'], vals['off01'], vals['off23'] = 6, 0x00080004, 0x0FFC0010
        r = F.unpack_result(F.pack_words(F.RESULT, vals))
        self.assertTrue(r['overflow'])
        self.assertEqual([o for o, _ in r['changed']], [4, 8, 0x10, 0xFFC])

    def test_unwritten_changed_slots_are_not_compared(self):
        """The agent writes only the first n_changed inline slots, and none
        when the scratch window is not checked; the rest hold leftovers
        (power-up SRAM on the chip).  Two runs must not "disagree" there."""
        def res(n, vals, off01=0, r0=1):
            v = {f: 0 for f in F.RESULT}
            v.update(f_r0=r0, n_changed=n, off01=off01,
                     val0=vals[0], val1=vals[1], val2=vals[2], val3=vals[3])
            return F.unpack_result(F.pack_words(F.RESULT, v))
        a = res(F.NOT_CHECKED, (0x8A5FC81E, 1, 2, 3))
        b = res(F.NOT_CHECKED, (0x82DFC81E, 9, 9, 9))
        self.assertEqual(R.behaviour_differs(a, b, R.BEHAVIOUR_FIELDS), [])
        a = res(1, (0x11, 0xDEAD, 0, 0), off01=0x4)
        b = res(1, (0x11, 0xBEEF, 7, 7), off01=0x4)
        self.assertEqual(R.behaviour_differs(a, b, R.BEHAVIOUR_FIELDS), [])
        b = res(1, (0x12, 0xBEEF, 7, 7), off01=0x4)                 # a written slot differs
        self.assertEqual(R.behaviour_differs(a, b, R.BEHAVIOUR_FIELDS), ['changed'])
        b = res(1, (0x11, 0, 0, 0), off01=0x4, r0=2)
        self.assertEqual(R.behaviour_differs(a, b, R.BEHAVIOUR_FIELDS), ['f_r0'])


if __name__ == '__main__':
    unittest.main()
