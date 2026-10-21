"""The beginner-friendly behaviours of the GUI backend: source lines and
labels in the listing, readable load errors, auto-reload, Step over and the
level-1 option that hides the built-in OS, and Step back."""

import os
import shutil
import tempfile
import time
import unittest

from picosim import challenge, errors
from picosim.server import Server, symbolic, tidy_source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'examples')
HEAD = "\t.syntax unified\n\t.cpu cortex-m0plus\n\t.thumb\nmain:\n"


def server(path=None, **options):
    """A Server with no socket: its messages are collected in .out."""
    s = Server.__new__(Server)
    s.__init__(0)
    s.sock.close()                 # not needed: nothing connects
    s.out = []
    s.send = s.out.append
    if options:
        s.cmd_set_options(options)
    if path:
        s.cmd_load({'path': path})
    return s


def drive(s, limit=500):
    """Let a Run or Step over that is in progress finish, as the server loop would."""
    for _ in range(limit):
        if not s.running or s.waiting_input:
            return
        s.run_slice()


def printed(s):
    return ''.join(m['text'] for m in s.out if m.get('type') == 'console' and m['source'] == 'sim')


class ListingTest(unittest.TestCase):
    def test_source_lines_and_labels(self):
        s = server(os.path.join(EX, '03_count_loop.s'))
        rows = {r['addr']: r for r in next(m for m in s.out if m['type'] == 'program')['listing']}
        self.assertEqual(rows[0x3010]['shown'].split(), ['bne', 'loop'])
        self.assertEqual(rows[0x3008]['shown'].split(), ['bl', 'putchar'])
        self.assertEqual(rows[0x3006]['line'], 24)
        self.assertTrue(rows[0x3006]['source'].startswith("adds r0, #'0'"))
        self.assertEqual(rows[0x3006]['text'].split()[0], 'adds')     # the raw text is unchanged
        self.assertEqual(rows[0x000C]['source'], '')                   # a literal pool row
        self.assertEqual(rows[0x0010]['source'], 'svc #2')             # the OS's own source

    def test_symbolic_and_tidy(self):
        self.assertEqual(symbolic('bl       #0x10', {0x10: 'putchar'}).split(), ['bl', 'putchar'])
        self.assertEqual(symbolic('bl       #0x20', {}), 'bl       #0x20')
        self.assertEqual(symbolic('movs     r0, #0x10', {0x10: 'putchar'}), 'movs     r0, #0x10')
        self.assertEqual(tidy_source("movs    r0, #'H'      @ R0 = H"), "movs r0, #'H'   @ R0 = H")


class ErrorTest(unittest.TestCase):
    def write(self, body):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 'bad.s')
        with open(p, 'w') as f:
            f.write(HEAD + body)
        return p

    def test_assembler_errors_have_lines_and_hints(self):
        s = server(self.write("\tpush {r7, lr}\n\tmovs r0, #300\n\tadd r1, r2, r3\n\tpop {r7, pc}\n"))
        e = next(m for m in s.out if m['type'] == 'load_error')
        self.assertEqual([x['line'] for x in e['errors']], [6, 7])
        self.assertEqual(e['errors'][0]['source'], 'movs r0, #300')
        self.assertIn('0 to 255', e['errors'][0]['hint'])
        self.assertIn('missing S', e['errors'][1]['hint'])
        self.assertNotIn('/', e['message'].split(':')[0])          # no temporary paths

    def test_undefined_label(self):
        s = server(self.write("\tpush {r7, lr}\n\tbne nowhre\n\tpop {r7, pc}\n"))
        e = next(m for m in s.out if m['type'] == 'load_error')
        self.assertEqual(e['errors'][0]['line'], 6)
        self.assertIn('no label called nowhre', e['errors'][0]['message'])
        self.assertNotIn('entry symbol', e['message'])

    def test_explain_unknown_text(self):
        self.assertEqual(errors.explain("something odd", "/nonexistent.s"), [])

    def test_failed_load_keeps_path_and_auto_reloads(self):
        p = self.write("\tmovs r0, #300\n\tbx lr\n")
        s = server(p)
        self.assertTrue(any(m['type'] == 'load_error' for m in s.out))
        self.assertEqual(s.path, p)
        time.sleep(1.1)                       # a new modification time
        with open(p, 'w') as f:
            f.write(HEAD + "\tmovs r0, #3\n\tbx lr\n")
        s.out.clear()
        s.watch_next = 0
        s.poll_file()
        self.assertTrue(any(m['type'] == 'program' for m in s.out))
        self.assertIn('reloading', ''.join(m.get('text', '') for m in s.out))


class StepTest(unittest.TestCase):
    def test_level1_starts_at_main_and_steps_over_putchar(self):
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True, step_over_os=True)
        c = s.core
        self.assertEqual(c.pc, 0x3000)
        self.assertEqual(s.boot_steps, 3)
        presses = 0
        while not c.halted and presses < 50:
            s.cmd_step_insn({})
            drive(s)
            presses += 1
            self.assertTrue(c.halted or c.pc >= 0x3000, hex(c.pc))   # never inside the OS
        self.assertEqual(printed(s), 'Hi!\n')
        self.assertEqual(presses, 11)        # one per line of main

    def test_level1_quick_presses_are_one_line_each(self):
        # presses that arrive before a bl putchar has finished (a held S key)
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True, step_over_os=True)
        for _ in range(8):
            s.cmd_step_insn({})
        self.assertEqual(s.core.pc, 0x3016)
        self.assertEqual(printed(s), 'Hi!')

    def test_level1_getchar_waits_then_continues(self):
        s = server(os.path.join(EX, '06_echo.s'), skip_boot=True, step_over_os=True)
        s.cmd_step_insn({})                  # push
        s.cmd_step_insn({})                  # bl getchar
        drive(s)
        self.assertTrue(s.waiting_input)
        s.cmd_console_input({'text': 'a'})
        drive(s)
        self.assertFalse(s.running)
        self.assertEqual(s.core.regs[0], ord('a'))
        self.assertGreaterEqual(s.core.pc, 0x3000)

    def test_step_over_function_and_breakpoint_inside(self):
        s = server(os.path.join(EX, '07_function.s'))
        c = s.core
        while c.pc != 0x3008:                # bl print_digit
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(c.pc, 0x300C)
        self.assertEqual(printed(s), '4')
        # a breakpoint inside the call stops the Step over there
        s.breakpoints.add(0x3026)
        while c.pc != 0x3012:
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(c.pc, 0x3026)
        self.assertFalse(s.running)

    def test_step_over_plain_instruction_is_a_step(self):
        s = server(os.path.join(EX, '02_add.s'), skip_boot=True)
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(s.core.pc, 0x3002)

    def test_options_reload_a_fresh_program(self):
        s = server(os.path.join(EX, '01_hello.s'))
        self.assertEqual(s.core.pc, 0)
        s.cmd_set_options({'skip_boot': True, 'step_over_os': True})
        self.assertEqual(s.core.pc, 0x3000)

    def test_step_over_on_pipeline(self):
        s = server(os.path.join(EX, '07_function.s'))
        s.cmd_set_machine({'machine': 'm0plus'})
        while s.core.pc != 0x3004:           # bl twice
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(s.core.pc, 0x3008)
        self.assertEqual(s.core.regs[0], 4)


class ChangedByTest(unittest.TestCase):
    def state(self, s):
        s._send_state_now()
        return s.out[-1]

    def test_each_register_names_its_instruction(self):
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True)
        s.cmd_step_insn({})
        s.cmd_step_insn({})                     # 3002 movs r0, #'H'
        st = self.state(s)
        self.assertEqual(st['changed_by']['R0'], 0x3002)
        self.assertEqual(st['action'], 'step')

    def test_actions_without_one_instruction(self):
        s = server(os.path.join(EX, '07_function.s'))
        while s.core.pc != 0x3008:
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        st = self.state(s)
        self.assertEqual(st['action'], 'call')
        self.assertEqual(st['changed_by']['LR'], 0x3008)     # the bl itself
        self.assertNotIn('R0', st['changed_by'])             # inside the call
        s.cmd_set_reg({'reg': 5, 'value': 9})
        self.assertEqual(self.state(s)['action'], 'edit')
        s.cmd_step_back({})
        st = self.state(s)
        self.assertEqual((st['action'], st['changed_by']), ('back', {}))


class ChallengeTest(unittest.TestCase):
    def write(self, body):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 'c.s')
        with open(p, 'w') as f:
            f.write(body)
        return p

    def finish(self, s):
        s.cmd_run({'cps': 0})
        drive(s)
        s._send_state_now()
        return s.out[-1]['checks']

    def test_parse_and_values(self):
        c = challenge.parse('@ Challenge: do it\n@ Expect: output "a\\n"\n@ Expect: R0 = \'H\'\n'
                            '@ Expect: r12 = -1\n@ Expect: SP = 0x10000\n\tmovs r0, #1\n@ Expect: R1 = 5\n')
        self.assertEqual(c['task'], 'do it')
        self.assertEqual([k for _, k in c['checks']],
                         [('output', None, 'a\n'), ('reg', 0, 72), ('reg', 12, 0xFFFFFFFF), ('reg', 13, 0x10000)])
        self.assertIsNone(challenge.parse('@ Title: x\n'))
        with self.assertRaises(challenge.ChallengeError):
            challenge.parse('@ Expect: R2 is 7\n')
        with self.assertRaises(challenge.ChallengeError):
            challenge.parse('@ Expect: output 7\n')

    def test_example_fails_until_changed(self):
        s = server(os.path.join(EX, '02_add.s'))
        prog = next(m for m in s.out if m['type'] == 'program')
        self.assertEqual(prog['challenge']['checks'], ['output "9\\n"', 'R3 = 1'])
        checks = self.finish(s)
        self.assertEqual([c['ok'] for c in checks], [False, True])
        self.assertEqual(checks[0]['got'], 'it printed "7\\n"')

    def test_pass_and_step_back_clears(self):
        with open(os.path.join(EX, '02_add.s')) as f:
            src = f.read()
        src = src.replace('movs    r0, #3', 'movs    r0, #4').replace('movs    r1, #4', 'movs    r1, #5')
        s = server(self.write(src), skip_boot=True, step_over_os=True)
        self.assertTrue(all(c['ok'] for c in self.finish(s)))
        s.cmd_step_back({})
        s._send_state_now()
        self.assertIsNone(s.out[-1]['checks'])

    def test_bad_expect_is_reported_not_fatal(self):
        s = server(self.write('@ Expect: R2 is 7\n' + HEAD + '\tbx lr\n'))
        self.assertTrue(any(m['type'] == 'program' for m in s.out))
        self.assertIn('cannot be checked', ''.join(m.get('text', '') for m in s.out))


def snap(s):
    c = s.core
    return (list(c.regs), [getattr(c, f) for f in 'NZCV'], c.get_memory(), c.steps, c.cycle_index)


class StepBackTest(unittest.TestCase):
    def write(self, body):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 'prog.s')
        with open(p, 'w') as f:
            f.write(HEAD + body)
        return p

    def test_back_k_equals_state_at_n_minus_k(self):
        s = server(os.path.join(EX, '04_memory.s'))
        seen = [snap(s)]
        for _ in range(25):
            s.cmd_step_insn({})
            seen.append(snap(s))
        for k in range(1, 26):
            s.cmd_step_back({})
            self.assertEqual(snap(s), seen[-1 - k], f"after {k} steps back")
        s.out.clear()
        s.cmd_step_back({})                     # at the very start
        self.assertEqual(s.out[0]['type'], 'error')

    def test_back_one_cycle_inside_an_instruction(self):
        s = server(os.path.join(EX, '02_add.s'), skip_boot=True)
        seen = [snap(s)]
        for _ in range(3):
            s.cmd_step_cycle({})
            seen.append(snap(s))
        self.assertTrue(s.core.in_insn or s.core.steps > 0)
        for k in range(1, 4):
            s.cmd_step_back({})
            self.assertEqual(snap(s), seen[-1 - k])

    def test_printed_text_and_keys_come_back(self):
        s = server(os.path.join(EX, '06_echo.s'))
        s.cmd_console_input({'text': 'ab'})
        while printed(s) != 'ab':
            s.cmd_step_insn({})
        n = s.core.steps
        while s.keys_used != [ord('a')]:        # back past the second getchar
            s.cmd_step_back({})
        self.assertEqual(list(s.inp), [ord('b')])
        reset = [m for m in s.out if m['type'] == 'console_reset'][-1]
        self.assertEqual(reset['text'], 'a')
        while s.core.steps < n:                 # forward again: the same output
            s.cmd_step_insn({})
        self.assertEqual(s.out_text, 'ab')

    def test_pin_clicks_and_register_edits_replay(self):
        s = server(self.write("\tldr r2, =0xd0000004\nloop:\n\tldr r1, [r2]\n\tadds r3, r3, #1\n\tb loop\n"),
                   skip_boot=True)
        for _ in range(2):
            s.cmd_step_insn({})
        s.cmd_gpio_set({'pin': 3, 'value': 1})
        s.cmd_set_reg({'reg': 3, 'value': 100})
        for _ in range(4):
            s.cmd_step_insn({})
        self.assertEqual(s.core.regs[1], 8)
        want = snap(s)
        s.cmd_step_insn({})
        s.cmd_step_back({})
        self.assertEqual(snap(s), want)
        self.assertEqual(s.core.regs[3], 102)
        for _ in range(4):
            s.cmd_step_back({})
        self.assertEqual(len(s.journal), 2)     # back at the moment of the edits: kept
        s.cmd_step_back({})
        self.assertEqual(s.journal, [])         # before them: undone
        self.assertEqual(s.core.regs[3], 0)

    def test_level1_back_skips_the_os(self):
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True, step_over_os=True)
        c = s.core
        while not c.halted:
            s.cmd_step_insn({})
            drive(s)
        backs = 0
        while s.core.steps > s.boot_steps:
            s.cmd_step_back({})
            backs += 1
            self.assertGreaterEqual(s.core.pc, 0x3000, hex(s.core.pc))
        self.assertEqual(backs, 11)             # one per press of Step
        s._send_state_now()
        self.assertFalse(s.out[-1]['can_back'])


if __name__ == '__main__':
    unittest.main()
