"""picosim against the recorded hardware facts (hwfacts/), with no board.

Replays every record in hwfacts/replay/ in picosim and fails on a
difference that hwfacts/known_differences.json does not list.  Skipped while
hwfacts/ has no real-chip data.  The checker itself is tested on the
stand-in: a capture from it, replayed, must show only the differences
picosim and Unicorn are known to have."""

import glob
import os
import tempfile
import unittest

from picosim.discover import check
from picosim.discover.replay import HWFACTS

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False


def h(*hws):
    return b''.join(x.to_bytes(2, 'little') for x in hws)


class HardwareFactsTest(unittest.TestCase):
    @unittest.skipUnless(glob.glob(os.path.join(HWFACTS, 'replay', '*.json')),
                         "no real-chip replay data in hwfacts/replay yet")
    def test_picosim_matches_the_chip(self):
        res, files = check.check_replays()
        if res.new:
            self.fail("picosim differs from the RP2040 in ways not yet known:\n" +
                      check.report(res, sources=files))


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn")
class CheckerTest(unittest.TestCase):
    def test_replay_of_a_stand_in_capture(self):
        from picosim.discover import facts as FX
        from picosim.discover import formats as F
        from picosim.discover import runner as R
        from picosim.hwtest.target import Target
        lay = F.LAYOUTS['hi']
        S = lay['SCRATCH']
        cases = [R.make_case(1, h(0x1888), regs=[0, 5, 7]),                       # adds: matches
                 R.make_case(2, h(0x4148), regs=[1, 0xFFFFFFFF], nzcv=0b0010),    # adcs: matches
                 R.make_case(3, h(0x6008), regs=[0xCAFEF00D, S + 8], seed=9),     # str: matches
                 R.make_case(4, h(0xE01E)),                                       # b: matches
                 R.make_case(5, h(0xDF05)),                                       # svc: matches
                 R.make_case(6, h(0xF380, 0x8808), regs=[0x20000F00]),            # msr msp: picosim has one SP
                 R.make_case(7, h(0x4700), regs=[lay['SLED'] + 0x200]),           # bx even
                 R.make_case(8, h(0x683E, 0xBF00, 0x4348, 0x683D), kind='timing',
                             regs=[3, 4, 0, 0, 0, 0, 0, 0xE000E018]),
                 R.make_case(9, h(0x683E, 0x683D), kind='timing', regs=[0] * 7 + [0xE000E018])]
        t = Target('standin')
        root = tempfile.mkdtemp()
        try:
            run = R.Run.start(t, 'chk', cases, root=root)
            run.go()
            run.close()
        finally:
            t.close()
        recs, state = R.load(run.path)
        res = check.check_records(recs, lay, state['scratch_words'], table=[])
        self.assertEqual(res.counts['match'], 7, check.report(res))
        kinds = {(k[0], k[1], k[2]) for k in res.groups}
        self.assertIn(('differs', 'decoder', 'MSR spec_reg,Rn'), kinds)        # MSP and PSP banked
        self.assertIn(('differs', 'faults', 'BX Rm'), kinds)                   # picosim faults at the BX
        # a known-differences entry silences it
        res2 = check.check_records(recs, lay, state['scratch_words'],
                                   table=[{'class': 'decoder', 'name': '^MSR'}, {'class': 'faults', 'name': '^BX'}])
        self.assertEqual(res2.new, [])
        # through a replay file and back
        data, meta = FX.write_replay('chk', recs, state, 'standin')
        back, meta2 = FX.read_replay(data[:-len('.bin.gz')] + '.json')
        self.assertEqual([c['id'] for c, _ in back], [c['id'] for c, _ in recs])
        self.assertEqual(back[0][1], recs[0][1])
        os.remove(data)
        os.remove(data[:-len('.bin.gz')] + '.json')


if __name__ == '__main__':
    unittest.main()
