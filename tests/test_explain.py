"""The "Explain this step" panel's words (picosim/explain.py) and the run
loop's power saving: the forecast of the next step must be exactly what the
step then does, every example must be explainable at every step, and a Run at
a set speed must sleep between cycles and send only real progress."""

import os
import time
import unittest

from picosim.server import IDLE_POLL
from tests.test_friction import server, drive, EX

EXAMPLES = ['01_hello.s', '02_add.s', '03_count_loop.s', '04_memory.s', '05_blink_led.s',
            '07_function.s', '08_button_led.s']


def state(s):
    """The state message the GUI would get now."""
    s.out.clear()
    s._send_state_now()
    return [m for m in s.out if m.get('type') == 'state'][-1]


class ForecastTest(unittest.TestCase):
    def check(self, name, level, steps=120):
        hide = level == 1
        s = server(os.path.join(EX, name), skip_boot=hide, step_over_os=hide)
        c = s.core
        for i in range(steps):
            if c.halted:
                break
            run = s.explainer.forecast(c, s.prog.cpu.gpio, s.inp, hide)
            if run.waits:
                break
            s.cmd_step_insn({})
            drive(s)
            where = f"{name} level {level} step {i + 1} ({run.text})"
            self.assertEqual(run.post['regs'], list(c.regs), where)
            self.assertEqual(run.post['flags'], {k: getattr(c, k) for k in 'NZCV'}, where)
            self.assertEqual(run.halted, c.halted, where)

    def test_forecast_is_what_the_step_does(self):
        for name in EXAMPLES:
            for level in (1, 2):
                with self.subTest(name=name, level=level):
                    self.check(name, level)

    def test_forecast_leaves_the_machine_alone(self):
        s = server(os.path.join(EX, '05_blink_led.s'))
        c = s.core
        for _ in range(12):
            regs, mem, pins = list(c.regs), c.get_memory(), s.gpio_pins()
            s.explainer.forecast(c, s.prog.cpu.gpio, s.inp, False)
            self.assertEqual((regs, mem, pins), (list(c.regs), c.get_memory(), s.gpio_pins()))
            s.cmd_step_insn({})


class ExplanationTest(unittest.TestCase):
    def walk(self, name, level, cmd='step_insn', steps=60):
        hide = level == 1
        s = server(os.path.join(EX, name), skip_boot=hide, step_over_os=hide)
        e = state(s)['explain']
        self.assertEqual(e['kind'], 'load')
        for _ in range(steps):
            if s.core.halted or s.waiting_input:
                break
            getattr(s, 'cmd_' + cmd)({})
            drive(s)
            e = state(s)['explain']
            self.assertNotEqual(e['kind'], 'error', e)
            self.assertTrue(e['now']['simple'], e)
            if not s.core.halted:
                self.assertTrue(e['next']['simple'], e)
        return s

    def test_every_example_at_every_level(self):
        for name in EXAMPLES + ['06_echo.s']:
            for level in (1, 2):
                with self.subTest(name=name, level=level):
                    self.walk(name, level)

    def test_clock_cycles(self):
        s = self.walk('02_add.s', 3, 'step_cycle', 40)
        e = state(s)['explain']
        self.assertEqual(e['kind'], 'cycle')
        self.assertIn('Clock cycle', e['now']['simple'])

    def test_hello_level_1(self):
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True, step_over_os=True)
        nxt = state(s)['explain']['next']
        self.assertIn('push {r7, lr}', nxt['head'])
        s.cmd_step_insn({})
        s.cmd_step_insn({})                     # movs r0, #'H'
        e = state(s)['explain']
        self.assertIn("72 (0x48), the character 'H'", e['now']['simple'])
        self.assertIn('putchar', e['next']['simple'])
        self.assertIn('“H”', e['next']['simple'])
        s.cmd_step_insn({})                     # bl putchar: the whole call
        drive(s)
        e = state(s)['explain']
        self.assertEqual(e['kind'], 'call')
        self.assertIn('printed “H”', e['now']['simple'])

    def test_branch_forecast_says_where(self):
        s = server(os.path.join(EX, '03_count_loop.s'), skip_boot=True, step_over_os=True)
        for _ in range(7):                      # up to the first bne loop
            s.cmd_step_insn({})
            drive(s)
        e = state(s)['explain']
        self.assertIn('bne loop', e['next']['head'])
        self.assertIn('it will jump to loop', e['next']['simple'])
        self.assertIn('back up to line', e['next']['goes'])
        self.assertIn('round the loop again', e['next']['why'])

    def test_getchar_waits(self):
        s = server(os.path.join(EX, '06_echo.s'), skip_boot=True, step_over_os=True)
        s.cmd_step_insn({})                     # push
        nxt = state(s)['explain']['next']
        self.assertIn('waits for you to type a key', nxt['simple'])
        s.cmd_step_insn({})                     # bl getchar: stops in getchar
        drive(s)
        e = state(s)['explain']
        self.assertIn('waiting', e['now']['simple'])
        self.assertIn('getchar is waiting for a key', e['next']['simple'])

    def test_run_is_not_explained_frame_by_frame(self):
        s = server(os.path.join(EX, '05_blink_led.s'))
        s.cmd_run({'cps': 0})
        s.run_slice()
        self.assertEqual(state(s)['explain'], {'kind': 'running'})
        s.cmd_pause({})
        e = state(s)['explain']
        self.assertEqual(e['kind'], 'run')
        self.assertIn('because you pressed Pause', e['now']['simple'])

    def test_back_and_edit(self):
        s = server(os.path.join(EX, '02_add.s'), skip_boot=True, step_over_os=True)
        for _ in range(3):
            s.cmd_step_insn({})
        s.cmd_step_back({})
        self.assertEqual(state(s)['explain']['kind'], 'back')
        s.cmd_set_reg({'reg': 1, 'value': 9})
        e = state(s)['explain']
        self.assertEqual(e['kind'], 'edit')
        self.assertIn('R1 to 9', e['now']['simple'])


class RunLoopPowerTest(unittest.TestCase):
    def test_sleeps_until_the_next_cycle(self):
        s = server(os.path.join(EX, '05_blink_led.s'))
        self.assertEqual(s._wait(), IDLE_POLL)
        s.cmd_run({'cps': 8})
        s.run_slice()
        self.assertGreater(s._wait(), 0.0)      # a cycle every 1/8 s: no busy loop
        s.cmd_run({'cps': 0})
        self.assertEqual(s._wait(), 0)          # Max: as fast as possible

    def test_sends_only_progress(self):
        s = server(os.path.join(EX, '05_blink_led.s'))
        s.cmd_run({'cps': 2})
        end = time.monotonic() + 0.6
        sent = 0
        while time.monotonic() < end:
            s.out.clear()
            s.run_slice()
            if s.dirty:
                s._send_state_now()
                sent += 1
            time.sleep(0.005)
        # 2 cycles a second for 0.6 s: about 2 states, never one per frame
        self.assertLessEqual(sent, 3)
        self.assertGreaterEqual(sent, 1)


if __name__ == '__main__':
    unittest.main()
