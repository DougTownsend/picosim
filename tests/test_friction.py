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
        self.assertEqual(rows[0x10000110]['shown'].split(), ['bne', 'loop'])
        self.assertEqual(rows[0x10000108]['shown'].split(), ['bl', 'putchar'])
        self.assertEqual(rows[0x10000106]['line'], 25)
        self.assertTrue(rows[0x10000106]['source'].startswith("adds r0, #'0'"))
        self.assertEqual(rows[0x10000106]['text'].split()[0], 'adds')     # the raw text is unchanged
        self.assertEqual(rows[0x1000000C]['source'], '')                   # a literal pool row
        self.assertEqual(rows[0x10000010]['source'], 'svc #2')             # the OS's own source

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

    def test_multi_character_immediate_and_missing_tool(self):
        p = self.write("\tmovs r0, #'Hello'\n\tbx lr\n")
        s = server(p)
        e = next(m for m in s.out if m['type'] == 'load_error')['errors'][0]
        self.assertEqual(e['line'], 5)
        self.assertIn("#'H' is the number 72", e['hint'])
        tool = errors.explain("OS build failed:\narm-none-eabi-gcc not found on PATH.\nSee DEPENDENCIES.md", p)
        self.assertEqual(len(tool), 1)
        self.assertIn('picosim --run examples/01_hello.s', tool[0]['hint'])

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
        self.assertEqual(c.pc, 0x10000100)
        self.assertEqual(s.boot_steps, 3)
        presses = 0
        while not c.halted and presses < 50:
            s.cmd_step_insn({})
            drive(s)
            presses += 1
            self.assertTrue(c.halted or c.pc >= 0x10000100, hex(c.pc))   # never inside the OS
        self.assertEqual(printed(s), 'Hi!\n')
        self.assertEqual(presses, 11)        # one per line of main

    def test_level1_quick_presses_are_one_line_each(self):
        # presses that arrive before a bl putchar has finished (a held S key)
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True, step_over_os=True)
        for _ in range(8):
            s.cmd_step_insn({})
        self.assertEqual(s.core.pc, 0x10000116)
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
        self.assertGreaterEqual(s.core.pc, 0x10000100)

    def test_step_over_function_and_breakpoint_inside(self):
        s = server(os.path.join(EX, '07_function.s'))
        c = s.core
        while c.pc != 0x10000108:                # bl print_digit
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(c.pc, 0x1000010C)
        self.assertEqual(printed(s), '4')
        # a breakpoint inside the call stops the Step over there
        s.breakpoints.add(0x10000126)
        while c.pc != 0x10000112:
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(c.pc, 0x10000126)
        self.assertFalse(s.running)

    def test_step_over_plain_instruction_is_a_step(self):
        s = server(os.path.join(EX, '02_add.s'), skip_boot=True)
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(s.core.pc, 0x10000102)

    def test_options_reload_a_fresh_program(self):
        s = server(os.path.join(EX, '01_hello.s'))
        self.assertEqual(s.core.pc, 0x10000000)       # _start
        s.cmd_set_options({'skip_boot': True, 'step_over_os': True})
        self.assertEqual(s.core.pc, 0x10000100)

    def test_step_over_on_pipeline(self):
        s = server(os.path.join(EX, '07_function.s'))
        s.cmd_set_machine({'machine': 'm0plus'})
        while s.core.pc != 0x10000104:           # bl twice
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        self.assertEqual(s.core.pc, 0x10000108)
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
        self.assertEqual(st['changed_by']['R0'], 0x10000102)
        self.assertEqual(st['action'], 'step')

    def test_actions_without_one_instruction(self):
        s = server(os.path.join(EX, '07_function.s'))
        while s.core.pc != 0x10000108:
            s.cmd_step_insn({})
        s.cmd_step_over({})
        drive(s)
        st = self.state(s)
        self.assertEqual(st['action'], 'call')
        self.assertEqual(st['changed_by']['LR'], 0x10000108)     # the bl itself
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
                            '@ Expect: r12 = -1\n@ Expect: SP = 0x20042000\n\tmovs r0, #1\n@ Expect: R1 = 5\n')
        self.assertEqual(c['task'], 'do it')
        self.assertEqual([k for _, k in c['checks']],
                         [('output', None, 'a\n'), ('reg', 0, 72), ('reg', 12, 0xFFFFFFFF), ('reg', 13, 0x20042000)])
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
            self.assertGreaterEqual(s.core.pc, 0x10000100, hex(s.core.pc))
        self.assertEqual(backs, 11)             # one per press of Step
        s._send_state_now()
        self.assertFalse(s.out[-1]['can_back'])



def memory_lesson(change=lambda src: src):
    with open(os.path.join(EX, '04_memory.s')) as f:
        return change(f.read())


def fifth(src):
    """The fifth-number edit: 50 in the list and a count of 5."""
    return src.replace('.word   10, 20, 30, 40', '.word   10, 20, 30, 40, 50').replace(
        'movs    r3, #4', 'movs    r3, #5')


class MemoryChallengeTest(unittest.TestCase):
    """EDU-02: @ Expect: mem32[label] = value checks the stored word."""

    def write(self, body, name='m.s'):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, name)
        with open(p, 'w') as f:
            f.write(body)
        return p

    def finish(self, s):
        s.cmd_run({'cps': 0})
        drive(s)
        s._send_state_now()
        return {c['text']: c for c in s.out[-1]['checks']}

    def test_stock_lesson_shows_the_check_and_fails(self):
        s = server(os.path.join(EX, '04_memory.s'))
        prog = next(m for m in s.out if m['type'] == 'program')
        self.assertIn('mem32[total] = 150', prog['challenge']['checks'])
        checks = self.finish(s)
        self.assertFalse(checks['mem32[total] = 150']['ok'])
        self.assertEqual(checks['mem32[total] = 150']['got'], 'total (0x20000010) holds 100 (0x64)')

    def test_correct_fifth_value_passes(self):
        s = server(self.write(memory_lesson(fifth)))
        checks = self.finish(s)
        self.assertTrue(all(c['ok'] for c in checks.values()), checks)
        self.assertEqual(checks['mem32[total] = 150']['got'], 'total (0x20000014) holds 150 (0x96)')

    def test_storing_the_wrong_register_fails_even_with_r2_right(self):
        src = memory_lesson(fifth).replace('str     r2, [r1]', 'str     r0, [r1]')
        checks = self.finish(server(self.write(src)))
        self.assertTrue(checks['R2 = 150']['ok'])
        self.assertFalse(checks['mem32[total] = 150']['ok'])
        self.assertIn('holds 50', checks['mem32[total] = 150']['got'])

    def test_missing_store_fails(self):
        src = memory_lesson(fifth).replace('str     r2, [r1]', '')
        checks = self.finish(server(self.write(src)))
        self.assertTrue(checks['R2 = 150']['ok'])
        self.assertFalse(checks['mem32[total] = 150']['ok'])
        self.assertIn('holds 0', checks['mem32[total] = 150']['got'])

    def test_unresolved_label_reads_clearly(self):
        src = memory_lesson(fifth).replace('mem32[total]', 'mem32[totl]')
        s = server(self.write(src))
        self.assertIn('no label called totl', ''.join(m.get('text', '') for m in s.out))
        checks = self.finish(s)
        got = checks['mem32[totl] = 150']['got']
        self.assertFalse(checks['mem32[totl] = 150']['ok'])
        self.assertIn('no label called totl', got)

    def test_checking_reads_without_side_effects(self):
        s = server(self.write(memory_lesson(fifth)))
        self.finish(s)
        before = (list(s.core.regs), s.core.cycles, s.core.get_memory())
        s.check_results = None
        s._send_state_now()
        self.assertEqual((list(s.core.regs), s.core.cycles, s.core.get_memory()), before)

    def test_parse_errors(self):
        with self.assertRaises(challenge.ChallengeError):
            challenge.parse('@ Expect: mem32 total = 1\n')
        c = challenge.parse('@ Expect: mem32[x + 4] = 0x10\n@ Hint: look at x\n')
        self.assertEqual(c['checks'][0][1], ('mem32', ('x', 4), 16))
        self.assertEqual(c['hint'], 'look at x')
        r = challenge.run(c, [0] * 16, '', {'x': 0x20000001}, lambda a: 0)
        self.assertIn('not a multiple of 4', r[0]['got'])
        r = challenge.run(c, [0] * 16, '', {'x': 0x10000100}, lambda a: 0)
        self.assertIn('not in SRAM', r[0]['got'])


class StaleBuildTest(unittest.TestCase):
    """EDU-02: a save that does not build leaves the last good build loaded,
    marked stale, with Run and Step refused until it builds."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def write(self, name, body):
        p = os.path.join(self.d, name)
        with open(p, 'w') as f:
            f.write(body)
        t = time.time() + len(os.listdir(self.d)) + getattr(self, 'tick', 0)
        self.tick = getattr(self, 'tick', 0) + 2
        os.utime(p, (t, t))                      # a new modification time each save
        return p

    def state(self, s):
        s._send_state_now()
        return s.out[-1]

    def test_failed_save_is_stale_until_it_builds(self):
        with open(os.path.join(EX, '01_hello.s')) as f:
            good = f.read()
        p = self.write('hello.s', good)
        s = server(p)
        s.cmd_run({'cps': 0})
        drive(s)
        st = self.state(s)
        self.assertIsNone(st['build']['stale'])
        rev = st['build']['rev']
        self.write('hello.s', good.replace("#'H'", "#'Hello'"))
        s.out.clear()
        s.cmd_reload({})
        err = next(m for m in s.out if m['type'] == 'load_error')
        self.assertEqual(err['stale']['line'], 22)
        self.assertEqual(err['stale']['saved'], err['saved'])
        self.assertIn("one character", err['errors'][0]['hint'])
        st = self.state(s)
        stale = st['build']['stale']
        self.assertTrue(stale['same'])
        self.assertFalse(stale['accepted'])
        self.assertEqual(st['build']['rev'], rev)          # the old build is still the one loaded
        self.assertNotEqual(stale['saved'], stale['loaded_saved'])
        # Run and Step are refused, with a reason
        s.out.clear()
        steps = s.core.steps
        s.cmd_step_back({})
        s.cmd_run({'cps': 0})
        s.cmd_step_insn({})
        self.assertEqual(s.core.steps, steps)
        self.assertTrue(any(m['type'] == 'error' and 'has a mistake' in m['message'] for m in s.out))
        # the student may choose the last good build explicitly
        s.cmd_use_last_build({})
        self.assertTrue(self.state(s)['build']['stale']['accepted'])
        s.cmd_step_back({})
        self.assertLess(s.core.steps, steps)
        # a save that builds clears it and loads a new revision
        self.write('hello.s', good)
        s.cmd_reload({})
        st = self.state(s)
        self.assertIsNone(st['build']['stale'])
        self.assertEqual(st['build']['rev'], rev + 1)

    def test_another_file_that_fails_does_not_run_the_old_one(self):
        add = os.path.join(EX, '02_add.s')
        s = server(add)
        for _ in range(4):
            s.cmd_step_insn({})
        bad = self.write('bad.s', HEAD + "\tmovs r0, #300\n\tbx lr\n")
        s.cmd_load({'path': bad})
        s.out.clear()
        s.cmd_run({'cps': 0})
        drive(s)
        self.assertEqual(printed(s), '')                    # not the old lesson's 7
        st = self.state(s)
        self.assertFalse(st['build']['stale']['same'])
        self.assertEqual(st['build']['stale']['loaded'], '02_add.s')
        s.cmd_use_last_build({})                            # back to 02_add.s
        self.assertEqual(s.path, add)
        self.assertIsNone(self.state(s)['build']['stale'])


class StreamedWalkthroughTest(unittest.TestCase):
    """PERF-02: the walkthrough comes from the loaded build, a page at a time."""

    def collect(self, s, kind, req):
        pages = []
        for _ in range(1000):
            pages += [m for m in s.out if m.get('type') == kind and m.get('req') == req and m not in pages]
            if pages and pages[-1]['done']:
                return pages
            s.poll_walk(0.008)
        self.fail('the walkthrough never finished')

    def test_pages_add_up_to_the_whole_walkthrough(self):
        from unittest import mock
        import picosim.sim
        s = server(os.path.join(EX, '05_blink_led.s'))
        for cmd, kind in ((s.cmd_walkthrough, 'walkthrough'), (s.cmd_pipe_walkthrough, 'pipe_walkthrough')):
            s.out.clear()
            with mock.patch.object(picosim.sim, 'assemble', side_effect=AssertionError('assembled')):
                cmd({'compact': True, 'limit': 500})
                whole = next(m for m in s.out if m.get('type') == kind)
                s.out.clear()
                cmd({'compact': True, 'limit': 500, 'stream': True, 'req': 7})
                first = [m for m in s.out if m.get('type') == kind]
                self.assertEqual(len(first), 1)               # the first page at once
                self.assertEqual(first[0]['start'], 0)
                self.assertFalse(first[0]['done'])
                pages = self.collect(s, kind, 7)
            steps = [st for p in pages for st in p['steps']]
            self.assertEqual([p['start'] for p in pages], [sum(len(q['steps']) for q in pages[:i]) for i in range(len(pages))])
            self.assertEqual(steps, whole['steps'])
            self.assertEqual(pages[-1]['stopped'], whole['stopped'])
            self.assertEqual(len(steps), 500)

    def test_reload_drops_a_walkthrough_being_sent(self):
        s = server(os.path.join(EX, '05_blink_led.s'))
        s.cmd_walkthrough({'compact': True, 'limit': 500, 'stream': True, 'req': 1})
        self.assertIsNotNone(s.walk_job)
        s.cmd_reload({})
        self.assertIsNone(s.walk_job)
        s.cmd_walkthrough({'compact': True, 'limit': 500, 'stream': True, 'req': 2})
        s.cmd_set_machine({'machine': 'm0plus'})
        s.out.clear()
        s.poll_walk(0.008)
        self.assertIsNone(s.walk_job)
        self.assertFalse([m for m in s.out if m.get('type') == 'walkthrough'])

    def test_stale_build_walkthrough_is_labelled(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, 'c.s')
        shutil.copy(os.path.join(EX, '03_count_loop.s'), p)
        s = server(p)
        s.cmd_walkthrough({'compact': True})
        good = next(m for m in s.out if m.get('type') == 'walkthrough')
        with open(p, 'a') as f:
            f.write('\tfrobnicate r0\n')
        os.utime(p, (time.time() + 5, time.time() + 5))
        s.cmd_reload({})
        s.out.clear()
        s.cmd_walkthrough({'compact': True})
        old = next(m for m in s.out if m.get('type') == 'walkthrough')
        self.assertEqual(old['steps'], good['steps'])        # still the build that is loaded
        self.assertTrue(old['stale'])
        self.assertEqual(old['rev'], good['rev'])


class LevelBoundaryTest(unittest.TestCase):
    """EDU-05 and EDU-03: changing what is shown never moves the CPU on, and
    Level 1's count is the program's own instructions."""

    def test_pipeline_waits_for_the_instruction_boundary(self):
        s = server(os.path.join(EX, '02_add.s'), skip_boot=True)
        while not (s.core.in_insn and s.core.cycle_index >= 2):
            s.cmd_step_cycle({})
        before = (list(s.core.regs), s.core.steps, s.core.cycle_index, s.core.cycles)
        s.out.clear()
        s.cmd_set_machine({'machine': 'm0plus'})
        self.assertEqual(s.machine, 'multicycle')
        self.assertEqual((list(s.core.regs), s.core.steps, s.core.cycle_index, s.core.cycles), before)
        wait = next(m for m in s.out if m['type'] == 'machine_wait')
        self.assertEqual(wait['done'], before[2])
        # the student chooses to finish it: said in the console, counts start at 0
        s.cmd_set_machine({'machine': 'm0plus', 'finish': True})
        self.assertEqual(s.machine, 'm0plus')
        self.assertFalse(s.core.in_insn)
        text = ''.join(m.get('text', '') for m in s.out)
        self.assertIn('finished', text)
        self.assertIn('starts at 0', text)
        s._send_state_now()
        st = s.out[-1]
        self.assertEqual(st['pipe']['cycle'], 0)
        self.assertEqual(st['pipe_from'], s.core.steps)

    def test_level1_count_and_putchar_explanation(self):
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True, step_over_os=True)
        s._send_state_now()
        st = s.out[-1]
        self.assertEqual(st['steps'] - st['os_steps'], 0)
        for _ in range(2):
            s.cmd_step_insn({})
        s._send_state_now()
        st = s.out[-1]
        self.assertEqual(st['steps'] - st['os_steps'], 2)
        s.cmd_step_insn({})                                # bl putchar: one step
        drive(s)
        s._send_state_now()
        st = s.out[-1]
        self.assertEqual(st['steps'] - st['os_steps'], 3)
        self.assertGreater(st['steps'], 5)
        self.assertIn('went up by 1', st['explain']['now']['simple'])

    def test_explain_after_a_halt_does_not_say_step(self):
        s = server(os.path.join(EX, '01_hello.s'), skip_boot=True, step_over_os=True)
        s.cmd_run({'cps': 0})
        drive(s)
        s._send_state_now()
        simple = s.out[-1]['explain']['now']['simple']
        self.assertNotIn('Step from here', simple)
        self.assertIn('Reload', simple)

    def test_load_at_boot_mentions_skip_to_main(self):
        s = server(os.path.join(EX, '04_memory.s'))
        s._send_state_now()
        self.assertIn('Skip to main', s.out[-1]['explain']['now']['simple'])


if __name__ == '__main__':
    unittest.main()
