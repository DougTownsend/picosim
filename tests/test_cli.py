"""The terminal workflow (picosim/sim.py): the first lesson, data labels at
the prompt, batch output and tracing, Ctrl+C, reset/reload, piped input,
and the private build folder (loader.build_dir)."""

import contextlib
import io
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'examples')
sys.path.insert(0, ROOT)

from picosim import sim, loader
from picosim.loader import load_program, LoadError


class _BuildDir(unittest.TestCase):
    """Every test builds into its own private folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='picosim-cli-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = dict(os.environ, PICOSIM_BUILD_DIR=os.path.join(self.tmp, 'build'))
        p = mock.patch.dict(os.environ, {'PICOSIM_BUILD_DIR': self.env['PICOSIM_BUILD_DIR']})
        p.start()
        self.addCleanup(p.stop)

    def cli(self, *args, inp=None, timeout=60):
        return subprocess.run([sys.executable, '-m', 'picosim.sim', *args], input=inp,
                              capture_output=True, text=True, timeout=timeout, env=self.env,
                              cwd=self.tmp)

    def copy(self, name, new=None):
        dst = os.path.join(self.tmp, new or name)
        shutil.copy(os.path.join(EX, name), dst)
        return dst

    def session(self, path):
        return sim.Session(load_program(path))

    @staticmethod
    def said(fn, *a):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn(*a)
        return out.getvalue()


class FirstLessonTest(_BuildDir):
    def test_hello_from_start_to_the_character(self):
        r = self.cli(os.path.join(EX, '01_hello.s'), inp='b main\nr\ns 2\np r0\nq\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("start-up code", r.stdout)               # why _start comes first
        self.assertIn("b main", r.stdout)
        self.assertIn("Next line: 01_hello.s line", r.stdout)
        self.assertIn("r0 = 0x00000048 = 72 = 'H'", r.stdout)


class DataLabelTest(_BuildDir):
    def test_memory_lesson_by_label(self):
        s = self.session(os.path.join(EX, '04_memory.s'))
        before = self.said(s.command, 'x', ['x', 'numbers', '4'])
        for v in (10, 20, 30, 40):
            self.assertIn(f"({v})", before)
        self.assertIn('<numbers>', before)
        self.said(s.execute)
        self.assertIn('(100)', self.said(s.command, 'x', ['x', 'total', '1']))

    def _edit(self, store_reg):
        src = open(os.path.join(EX, '04_memory.s')).read()
        src = src.replace('.word   10, 20, 30, 40', '.word   10, 20, 30, 40, 50')
        self.assertIn('.word   10, 20, 30, 40, 50', src)
        src = src.replace('movs    r3, #4', 'movs    r3, #5')
        if store_reg != 'r2':
            src = src.replace('str     r2, [r1]', f'str     {store_reg}, [r1]')
        p = os.path.join(self.tmp, f'five_{store_reg}.s')
        open(p, 'w').write(src)
        s = self.session(p)
        self.said(s.execute)
        return s

    def test_five_words_and_the_wrong_store(self):
        s = self._edit('r2')
        self.assertIn('(150)', self.said(s.command, 'x', ['x', 'total', '1']))
        s = self._edit('r0')
        self.assertEqual(s.cpu.regs[2], 150)                     # computed ...
        self.assertNotIn('(150)', self.said(s.command, 'x', ['x', 'total', '1']))  # ... not stored

    def test_breakpoints_need_code(self):
        s = self.session(os.path.join(EX, '04_memory.s'))
        with self.assertRaisesRegex(sim.CommandError, 'data at 0x20000010.*x total'):
            s.code_addr('total')
        with self.assertRaisesRegex(sim.CommandError, "no label named 'lop'.*loop"):
            s.code_addr('lop')
        with self.assertRaisesRegex(sim.CommandError, 'no instruction'):
            s.code_addr('20000000')
        self.assertEqual(s.code_addr('loop'), s.prog.label_map['loop'])
        self.assertEqual(s.code_addr('main'), s.prog.main_addr)
        with self.assertRaisesRegex(sim.CommandError, 'not a hex address or a label'):
            s.data_addr('nothing_here')


class CheckCommandTest(_BuildDir):
    def test_hello_challenge(self):
        p = self.copy('01_hello.s', 'hello.s')
        s = self.session(p)
        self.assertIn('not finished yet', self.said(s.check))
        self.said(s.execute)
        out = self.said(s.check)
        self.assertIn('FAIL', out)                       # the stock program prints Hi!, not Hey!
        self.assertIn('it printed "Hi!\\n"', out)
        src = open(p).read().replace("movs    r0, #'i'", "movs    r0, #'e'\n        bl      putchar\n"
                                     "        movs    r0, #'y'")
        open(p, 'w').write(src)
        self.said(s.reload)
        self.said(s.execute)
        self.assertIn('Challenge passed', self.said(s.check))

    def test_memory_challenge_checks_the_stored_word(self):
        for reg, passes in (('r2', True), ('r0', False)):
            s = DataLabelTest._edit(self, reg)
            out = self.said(s.check)
            self.assertEqual('Challenge passed' in out, passes, out)


class BatchOutputTest(_BuildDir):
    def test_stdout_is_only_the_program(self):
        r = self.cli('--run', os.path.join(EX, '01_hello.s'))
        self.assertEqual((r.returncode, r.stdout), (0, 'Hi!\n'))
        self.assertIn('Assembling', r.stderr)
        r = self.cli('-q', '--run', os.path.join(EX, '01_hello.s'))
        self.assertEqual((r.stdout, r.stderr), ('Hi!\n', ''))

    def test_trace_with_run(self):
        r = self.cli('-q', '--trace', '--run', os.path.join(EX, '01_hello.s'))
        self.assertEqual(r.stdout, 'Hi!\n')
        self.assertIn('<_start>', r.stderr)
        self.assertIn('<main>', r.stderr)
        self.assertGreater(r.stderr.count('\n'), 20)

    def test_trace_with_steps(self):
        r = self.cli('-q', '--trace', '-n', '4', os.path.join(EX, '01_hello.s'))
        self.assertEqual(len(r.stderr.strip().splitlines()), 4)
        self.assertIn('PC 0x', r.stdout)

    def test_fault_exit_and_stderr(self):
        p = os.path.join(self.tmp, 'bad.s')
        open(p, 'w').write('.syntax unified\n.thumb\nmain:\n ldr r0, =0x20000001\n ldr r1, [r0]\n bx lr\n')
        r = self.cli('-q', '--run', p)
        self.assertEqual((r.returncode, r.stdout), (1, ''))
        self.assertIn('ERROR', r.stderr)

    def test_time_says_what_it_measures(self):
        r = self.cli('-q', '--time', os.path.join(EX, '03_count_loop.s'))
        self.assertEqual(r.stdout, '12345\n')
        self.assertIn('host time', r.stderr)
        self.assertIn("not the Pico's", r.stderr)

    def test_save_the_elf(self):
        out = os.path.join(self.tmp, 'saved.elf')
        r = self.cli('-q', '--run', '--elf', out, os.path.join(EX, '01_hello.s'))
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, 'rb') as f:
            self.assertEqual(f.read(4), b'\x7fELF')


class PipedInputTest(_BuildDir):
    def test_echo_stops_at_q_and_at_end_of_input(self):
        for inp in ('hiq', 'hi'):
            r = self.cli('-q', '--run', os.path.join(EX, '06_echo.s'), inp=inp, timeout=30)
            self.assertEqual((r.returncode, r.stdout), (0, 'hi\n'), inp)


class CtrlCTest(_BuildDir):
    def test_interactive_run_comes_back_to_the_prompt(self):
        s = self.session(os.path.join(EX, '05_blink_led.s'))
        threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGINT)).start()
        out = self.said(s.execute)
        self.assertIn('Stopped (Ctrl+C)', out)
        self.assertFalse(s.cpu.halted)
        pc, steps = s.cpu.pc, s.cpu.steps
        self.said(s.execute, 1)                         # and it can still step
        self.assertEqual(s.cpu.steps, steps + 1)
        self.assertIn(pc, s.prog.asm_map)

    def test_batch_run_stops_with_130(self):
        p = os.path.join(self.tmp, 'forever.s')
        open(p, 'w').write('.syntax unified\n.thumb\nmain:\nspin:\n b spin\n')
        proc = subprocess.Popen([sys.executable, '-m', 'picosim.sim', '-q', '--run', p],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self.env, cwd=self.tmp)
        time.sleep(3)
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 130, err)
        self.assertIn('Stopped (Ctrl+C)', err)
        self.assertNotIn('Traceback', err)


class ResetReloadTest(_BuildDir):
    def test_reset_starts_the_same_build_over(self):
        s = self.session(os.path.join(EX, '03_count_loop.s'))
        s.command('b', ['b', 'loop'])
        self.said(s.execute)
        self.said(s.execute)
        self.assertNotEqual(s.cpu.steps, 0)
        out = self.said(s.reset)
        self.assertIn('Breakpoints are kept', out)
        self.assertEqual(s.cpu.steps, 0)
        self.assertIn(s.prog.label_map['loop'], s.breakpoints)

    def test_reload_after_an_edit_and_after_a_bad_edit(self):
        p = self.copy('01_hello.s', 'hello.s')
        s = self.session(p)
        self.said(s.command, 'b', ['b', 'main'])
        src = open(p).read()
        open(p, 'w').write(src.replace("#'H'", "#'J'"))
        out = self.said(s.reload)
        self.assertIn('Rebuilt', out)
        self.assertIn('Breakpoints kept: main', out)
        out = self.said(s.execute) + self.said(s.execute)     # to main, then to the end
        self.assertIn('Ji!', out)
        good = s.prog
        open(p, 'w').write(src.replace('movs    r0, #\'H\'', 'movs    r0, #\'Hello\''))
        out = self.said(s.reload)
        self.assertIn('did not build', out)
        self.assertIn(s.loaded_at, out)
        self.assertIs(s.prog, good)

    def test_reload_reports_lost_breakpoints(self):
        p = self.copy('03_count_loop.s', 'count.s')
        s = self.session(p)
        self.said(s.command, 'b', ['b', 'loop'])
        src = open(p).read()
        open(p, 'w').write(src.replace('loop', 'again'))
        out = self.said(s.reload)
        self.assertIn('Removed', out)
        self.assertIn('loop', out)
        self.assertEqual(s.breakpoints, {})


class PrivateBuildTest(_BuildDir):
    def test_read_only_lesson_folder(self):
        d = os.path.join(self.tmp, 'lesson')
        os.mkdir(d)
        shutil.copy(os.path.join(EX, '01_hello.s'), d)
        with open(os.path.join(d, 'consts.inc'), 'w') as f:
            f.write('.equ EXCLAIM, 33\n')
        main = os.path.join(d, 'main.s')
        with open(main, 'w') as f:
            f.write('.syntax unified\n.thumb\n.include "consts.inc"\nmain:\n push {r7, lr}\n'
                    ' movs r0, #EXCLAIM\n bl putchar\n movs r0, #0\n pop {r7, pc}\n')
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, d, stat.S_IRWXU)
        for name, want in (('01_hello.s', 'Hi!\n'), ('main.s', '!')):
            r = self.cli('-q', '--run', os.path.join(d, name))
            self.assertEqual((r.returncode, r.stdout), (0, want), r.stderr)
        self.assertEqual(sorted(os.listdir(d)), ['01_hello.s', 'consts.inc', 'main.s'])

    def test_builds_go_in_the_private_folder(self):
        prog = load_program(os.path.join(EX, '01_hello.s'))
        base = os.environ['PICOSIM_BUILD_DIR']
        self.assertTrue(prog.elf.startswith(base))
        self.assertTrue(loader.build_os_elf().startswith(base))
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(prog.elf)).st_mode), 0o700)

    def test_timeout_stops_the_build_and_keeps_the_last_good_one(self):
        p = self.copy('01_hello.s')
        prog = load_program(p)
        before = open(prog.elf, 'rb').read()
        with mock.patch.object(sim, 'BUILD_TIMEOUT', 0.001), \
                mock.patch.object(sim, '_run_bounded',
                                  side_effect=subprocess.TimeoutExpired('gcc', 0.001)):
            ok, msg = sim.assemble(p, prog.elf, os.path.join(ROOT, 'picosim', 'link.ld'))
        self.assertFalse(ok)
        self.assertIn('was stopped', msg)
        self.assertEqual(open(prog.elf, 'rb').read(), before)
        self.assertEqual([f for f in os.listdir(os.path.dirname(prog.elf)) if f.endswith('.tmp')], [])

    def test_a_real_timeout_kills_the_child(self):
        slow = os.path.join(self.tmp, 'slow.py')
        with open(slow, 'w') as f:
            f.write('import time\ntime.sleep(30)\n')
        t = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            sim._run_bounded([sys.executable, slow], 0.5)
        self.assertLess(time.monotonic() - t, 10)

    def test_failed_build_keeps_the_previous_elf(self):
        p = self.copy('01_hello.s')
        prog = load_program(p)
        before = open(prog.elf, 'rb').read()
        with open(p, 'a') as f:
            f.write('\n        notaninstruction r0\n')
        with self.assertRaises(LoadError):
            load_program(p)
        self.assertEqual(open(prog.elf, 'rb').read(), before)


if __name__ == '__main__':
    unittest.main()
