"""Blocks taken out of reset, and a debug port that stops answering.

On the RP2040 a block whose clock is off after `reset halt` (UART, SPI, the
ADC) never finishes leaving reset, and a debugger access to one that has
been released but is not done stalls the bus and the debug port until the
board is power-cycled; OpenOCD then answers every read with zeros.  Campaign
C's step S3 did exactly that (2026-11-05).  The stand-in models the hazard
(standin.CLOCKLESS), so these tests run without a board."""

import os
import subprocess
import sys
import unittest

try:
    from picosim.hwtest.standin import available
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False

ADC_CS, TIMER_HW = 0x4004C000, 0x40054008
ADC, TIMER = 1 << 0, 1 << 21
RESET, DONE = 0x4000C000, 0x4000C008
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn (pip install unicorn)")
class ResetHazardTest(unittest.TestCase):
    def setUp(self):
        from picosim.hwtest.target import Target
        self.t = Target('standin')
        self.t.reset_halt()

    def tearDown(self):
        self.t.close()

    def test_a_half_released_block_kills_the_debug_port(self):
        from picosim.discover.cases.memmap import debugger_read
        from picosim.hwtest.target import TargetError
        self.assertEqual(debugger_read(self.t, ADC_CS), 'error')    # in reset: answers, no stall
        self.assertFalse(self.t.link_lost())
        self.t.rsp.write32(0x4000F000, ADC)                         # released the old way
        self.assertFalse(self.t.rsp.read32(DONE) & ADC)              # its clock is off: never done
        with self.assertRaises(TargetError) as cm:
            debugger_read(self.t, ADC_CS)
        self.assertIn('unplug the TARGET Pico', cm.exception.args[0])
        self.assertEqual(self.t.rsp.read32(DONE), 0)                 # zeros now, as from OpenOCD

    def test_release_ready_puts_a_stuck_block_back(self):
        from picosim.discover.campaign import release, release_ready
        from picosim.discover.cases.memmap import debugger_read
        self.t.rsp.write32(0x4000E000, ADC | TIMER)                  # into reset (SET alias)
        self.assertEqual(release_ready(self.t, ADC | TIMER), TIMER)  # TIMER came out, the ADC not
        self.assertTrue(self.t.rsp.read32(RESET) & ADC)              # the ADC is held in reset again
        self.assertFalse(self.t.rsp.read32(RESET) & TIMER)
        debugger_read(self.t, ADC_CS)                                # safe now
        self.assertFalse(self.t.link_lost())
        self.assertFalse(release(self.t, ADC))
        self.assertFalse(self.t.link_lost())

    def test_campaign_c_leaves_the_chip_reachable(self):
        r = subprocess.run([sys.executable, '-m', 'picosim.discover', 'registers', '--target', 'standin',
                            '--fresh'], cwd=ROOT, capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn('ADC, SPI0, SPI1, UART0, UART1 did not come out of reset', r.stdout)
        import json
        from picosim.discover.build import cache_dir
        path = os.path.join(os.path.dirname(cache_dir()), 'discover', 'hwfacts-standin', 'registers.json')
        if not os.path.exists(path):
            path = os.path.expanduser('~/.cache/picosim/discover/hwfacts-standin/registers.json')
        with open(path) as f:
            d = json.load(f)
        facts = {x['id']: x['value'] for x in d['facts']}
        self.assertEqual(facts['registers.S3.did_not_come_out_of_reset'], ['ADC', 'SPI0', 'SPI1', 'UART0', 'UART1'])
        chip_id = next(r for r in d['registers'] if r['name'] == 'CHIP_ID')
        self.assertTrue(chip_id['S1'].endswith('(held in reset)'))   # kept, but not taken as a reset value
        self.assertNotIn('differences', chip_id)


class RefusedWriteTest(unittest.TestCase):
    """On the chip, TIMER's TIMEHW refuses a debugger write through its SET
    alias (0x40056000: OpenOCD answers E0E, 2026-11-10).  Campaign C used to
    stop there, on every Run and Resume; a refused write is now recorded and
    the sweep goes on.  A hang (no answer: reconnect, chip reset) still stops."""

    class Rsp:
        def __init__(self, refuse=(), hang=()):
            self.mem, self.refuse, self.hang = {}, set(refuse), set(hang)

        def read32(self, a):
            return self.mem.get(a, 0)

        def write32(self, a, v):
            from picosim.hwtest.rsp import RSPError
            if a in self.hang:
                raise RSPError('no reply in time')
            if a in self.refuse:
                raise RSPError(f"writing memory at 0x{a:08X} failed (E0E)")
            self.mem[a & ~0x3000] = v & 0xFFFFFFFF      # TIMEHW is write-only: modelled loosely

    class T:
        def __init__(self, rsp):
            self.rsp, self.reconnects = rsp, 0

        def reconnect(self):
            self.reconnects += 1

    def _timehw(self):
        from picosim.discover import svd
        return next(r for r in svd.registers() if r['addr'] == 0x40054000)

    def test_set_alias_refused_is_recorded(self):
        from picosim.discover.cases import registers as C
        t = self.T(self.Rsp(refuse={0x40056000}))
        res = C.mask_sweep(t, self._timehw(), frozenset(), lambda m: None)
        self.assertEqual(res['set'], C.BUS_ERROR)
        self.assertIn('xor', res)
        self.assertIn('clr', res)                       # the sweep went on past the refused write
        self.assertEqual(len(res['normal']), 4)
        self.assertEqual(t.rsp.mem[0x40054000], 0)      # original value restored
        self.assertEqual(C.refused_writes({0x40054000: res}, [self._timehw()]), ['TIMER.TIMEHW through set'])

    def test_refused_normal_write(self):
        from picosim.discover.cases import registers as C
        t = self.T(self.Rsp(refuse={0x40054000}))
        res = C.mask_sweep(t, self._timehw(), frozenset(), lambda m: None)
        self.assertEqual(set(res['normal'].values()), {C.BUS_ERROR})
        self.assertNotIn('writable', res)

    def test_hang_stops_the_run(self):
        from picosim.discover.cases import registers as C
        from picosim.hwtest.target import TargetError
        t = self.T(self.Rsp(hang={0x40056000}))
        with self.assertRaises(TargetError):
            C.mask_sweep(t, self._timehw(), frozenset(), lambda m: None)
        self.assertEqual(t.reconnects, 1)


class RegisterComparisonTest(unittest.TestCase):
    """The first full board run of C (2026-11-10) reported 128 differences
    from the SVD; 109 were the comparison's own mistakes.  The readings here
    are the chip's."""

    def _reg(self, periph, name):
        from picosim.discover import svd
        return next(r for r in svd.registers() if (r['periph'], r['name']) == (periph, name))

    def test_minimum_value_field_is_writable(self):
        from picosim.discover.cases import registers as C
        # IC_SS_SCL_HCNT: writing 0 reads 6 (the hardware's minimum)
        m = C.writable_bits({0: 6, 0xFFFFFFFF: 0xFFFF, 0x55555555: 0x5555, 0xAAAAAAAA: 0xAAAA})
        self.assertEqual(m['writable'], '0x0000FFFF')
        row = C.compare_row(self._reg('I2C0', 'IC_SS_SCL_HCNT'), '0x00000028', '0x00000028', m, {'I2C0'})
        self.assertNotIn('differences', row)

    def test_saturating_field_still_differs(self):
        from picosim.discover.cases import registers as C
        # IC_RX_TL: any value over 15 reads 15; the SVD says 8 bits
        m = C.writable_bits({0: 0, 0xFFFFFFFF: 0xF, 0x55555555: 0xF, 0xAAAAAAAA: 0xF})
        self.assertEqual(m['writable'], '0x0000000F')
        row = C.compare_row(self._reg('I2C0', 'IC_RX_TL'), '0x00000000', '0x00000000', m, {'I2C0'})
        self.assertEqual(row['differences'], ['writable 0x0000000F, SVD predicts 0x000000FF'])

    def test_write_one_to_clear_is_not_compared(self):
        from picosim.discover.cases import registers as C
        # PWM INTR: reads 0xFF, a write of 1s clears it
        m = C.writable_bits({0: 0xFF, 0xFFFFFFFF: 0, 0x55555555: 0, 0xAAAAAAAA: 0})
        self.assertEqual(m['writable'], '0x00000000')
        row = C.compare_row(self._reg('PWM', 'INTR'), '0x00000000', '0x00000000', m, {'PWM'})
        self.assertNotIn('differences', row)
        # PWM CH0_CSR: PH_ADV/PH_RET are strobes; the rest is compared and agrees
        m = C.writable_bits({0: 0, 0xFFFFFFFF: 0x7F, 0x55555555: 0x55, 0xAAAAAAAA: 0x6A})
        self.assertNotIn('differences', C.compare_row(self._reg('PWM', 'CH0_CSR'), None, '0x00000000', m, {'PWM'}))

    def test_write_only_is_not_compared(self):
        from picosim.discover.cases import registers as C
        m = C.writable_bits({0: 0, 0xFFFFFFFF: 0, 0x55555555: 0, 0xAAAAAAAA: 0})
        self.assertNotIn('differences', C.compare_row(self._reg('TIMER', 'TIMEHW'), 'not read', 'not read', m,
                                                      {'TIMER'}))

    def test_only_blocks_reset_here_are_compared(self):
        from picosim.discover.cases import registers as C
        # CLOCKS is never put through RESETS: it holds what the boot ROM set
        row = C.compare_row(self._reg('CLOCKS', 'CLK_SYS_CTRL'), '0x00000001', '0x00000001', None, {'PWM'})
        self.assertFalse(row['reset_compared'])
        self.assertNotIn('differences', row)
        # S1 is the last program's leftovers (a PWM counter still running), not a reset value
        row = C.compare_row(self._reg('PWM', 'CH0_CTR'), '0x000060E3', '0x00000000', None, {'PWM'})
        self.assertNotIn('differences', row)
        # a real reset-value difference in a block reset here is still found
        row = C.compare_row(self._reg('BUSCTRL', 'BUS_PRIORITY_ACK'), '0x00000001', '0x00000001', None,
                            {'BUSCTRL'})
        self.assertEqual(row['differences'], ['S3 reset value 0x00000001, SVD 0x00000000'])

    def test_overview_says_datasheet(self):
        from picosim.discover import overview
        doc = {'compared_with': 'datasheet', 'registers': [{'name': 'X', 'differences': ['writable 0x1']}]}
        self.assertEqual([d['kind'] for d in overview.differences(doc)], ['datasheet'])
        doc = {'registers': [{'name': 'X', 'differences': ['writable 0x1']}]}
        self.assertEqual([d['kind'] for d in overview.differences(doc)], ['picosim'])


class SeveralFactsFilesTest(unittest.TestCase):
    """Campaign G's status lists three facts files; the window's detail
    used to stop with "stat: path should be string … not list" (2026-11-10),
    and its interpolator differences (interp_differences) were never shown."""

    def test_merged(self):
        import json
        import tempfile
        from picosim.discover import facts as FX
        from picosim.discover import overview
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for name, extra in (('sio', {'interp_differences': [
                    {'id': 420, 'interp': 0, 'read': {'accum1_after': '0x3000000B'},
                     'predicted': {'accum1_after': '0x0000000B'}}]}), ('gpio', {})):
                p = os.path.join(d, name + '.json')
                doc = {'schema': FX.SCHEMA, 'kind': 'peripherals-' + name, 'provenance': {},
                       'facts': [FX.fact(f'{name}.x', 1)], **extra}
                with open(p, 'w') as f:
                    json.dump(doc, f)
                paths.append(p)
            from unittest import mock
            with mock.patch.object(overview.FX, 'validate', lambda d: d):   # real files carry full provenance
                doc, where = overview._facts_doc('peripherals', 'openocd', 'chip1',
                                                 {'facts': paths, 'compared_with': 'datasheet'})
            self.assertIn('sio.json + gpio.json', where)
            self.assertEqual(len(doc['facts']), 2)
            ds = overview.differences(doc)
            self.assertEqual([(x['what'], x['kind']) for x in ds], [('INTERP0 case 420', 'datasheet')])
            self.assertEqual(ds[0]['detail'], 'accum1_after: chip 0x3000000B, model 0x0000000B')


class LockupTest(unittest.TestCase):
    """B1 batch 27 on the chip (2026-11-10): ADD SP,SP (0x44ED) doubled SP
    to 0x4008xxxx, the next exception could not stack, the core locked up,
    OpenOCD halted it ("clearing lockup after double fault") and the run
    stopped with "the agent stopped between cases"."""

    def test_sp_uncontrolled_never_generated(self):
        from picosim.discover.cases import encodings as E
        self.assertEqual(set(E.SP_UNCONTROLLED), {0x44ED, 0x44FD, 0x46FD})
        b1 = E.GENERATORS['B1']()
        self.assertFalse({c['id'] for c in b1} & set(E.NEVER_RUN))
        # every SP-writing encoding v6m cannot control is in the list
        from picosim.discover import v6m
        loose = {hw for hw in range(0x4400, 0x4800) if not v6m.is_prefix32(hw)
                 and v6m.classify(hw) == 'sp-writing' and v6m.safe_inputs(hw, 0, 'B1').get('sp_result') is None}
        self.assertEqual(loose, set(E.SP_UNCONTROLLED))

    def test_openocd_lockup_message(self):
        from picosim.hwtest.target import Target
        t = Target.__new__(Target)
        t.standin = None

        class Ocd:
            log = ['Info : clock speed 5000 kHz']
        t.ocd = Ocd()
        t._lockup_seen = 1
        self.assertFalse(t.saw_lockup())
        Ocd.log.append('Error: [rp2040.core0] clearing lockup after double fault')
        self.assertTrue(t.saw_lockup())
        self.assertFalse(t.saw_lockup())                 # reported once


class ExceptionComparisonTest(unittest.TestCase):
    """D4's 31 differences from the Arm documents (2026-11-10) were the
    comparison's: re-scored from the same chip run, none is left."""

    def test_interrupted_verdict(self):
        from picosim.discover.cases import timing as T
        chip = {'-2': 'not started', '-1': 'abandoned (part done)', '4': 'completed', '5': 'later (+1 NOPs)'}
        self.assertEqual(T.interrupted_verdict(chip), T.RESTARTED)
        self.assertEqual(T.interrupted_verdict({'0': 'resumed'}), {'does not fit': {'0': 'resumed'}})

    def test_board_run_rescored(self):
        import json
        p = os.path.join(ROOT, 'hwfacts', 'exceptions.json')
        if not os.path.exists(p):
            self.skipTest('no board exceptions.json')
        with open(p) as f:
            doc = json.load(f)
        bad = [x['id'] for x in doc['facts'] if isinstance(x.get('prediction'), dict)
               and x['value'] != x['prediction'].get('value')]
        self.assertEqual(bad, [])


class FlashWordingTest(unittest.TestCase):
    """BOOTSEL was held on both Picos (2026-11-10): every BOOTSEL prompt now
    says which Pico, and that the probe's must never be pressed."""

    def test_prompts(self):
        import inspect
        from picosim.discover import flashguard as G
        src = inspect.getsource(G.FlashGuard) if hasattr(G, 'FlashGuard') else inspect.getsource(G)
        self.assertIn('Never press BOOTSEL on the PROBE', G.WHICH_PICO)
        self.assertGreaterEqual(src.count('WHICH_PICO'), 3)
        from picosim.discover import overview
        self.assertIn('never the probe', ' '.join(overview.FLASH_STEPS['backup']))


class LinkLostTest(unittest.TestCase):
    """OpenOCD's log line is what says the debug port has gone."""

    def test_openocd_log(self):
        from picosim.hwtest.target import Target
        t = Target.__new__(Target)
        t.standin = None

        class Ocd:
            log = ['Info : clock speed 5000 kHz']
        t.ocd = Ocd()
        t._ocd_seen, t.lost = len(t.ocd.log), False
        self.assertFalse(t.link_lost())
        t.ocd.log.append('Error: Failed to connect multidrop rp2040.dap0')
        self.assertTrue(t.link_lost())
        self.assertTrue(t.link_lost())                               # stays lost until a reconnect


if __name__ == '__main__':
    unittest.main()


class ReaperTest(unittest.TestCase):
    """OpenOCD must not outlive picosim (the GUI stops the server with SIGKILL)."""

    def test_child_goes_when_the_parent_is_killed(self):
        import signal
        import time
        parent = subprocess.Popen(
            [sys.executable, '-c', "import subprocess, sys, time\n"
             "from picosim.hwtest.target import reaper\n"
             "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
             "r = reaper(child.pid)\nprint(child.pid, flush=True)\ntime.sleep(60)\n"],
            cwd=ROOT, stdout=subprocess.PIPE, text=True)
        pid = int(parent.stdout.readline())
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait()
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            try:                                   # reap it if it became our zombie
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
            self.fail("the child outlived its killed parent")
