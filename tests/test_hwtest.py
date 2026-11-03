"""Testing (picosim/hwtest): the SRAM image, the RSP client, lockstep and
benchmarks against the stand-in RP2040, the detection of differences, and
the server's hw_* commands."""

import glob
import json
import os
import socket
import tempfile
import time
import unittest

from picosim.hwtest import image as img
from picosim.hwtest import suite
from picosim.hwtest.lockstep import Lockstep, SimSide
from picosim.hwtest.bench import Bench, calibrate, PREDICTED_OVERHEAD

try:
    from picosim.hwtest.standin import available, Chip
    HAVE_STANDIN = available()
except ImportError:
    HAVE_STANDIN = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'examples')


class ImageTest(unittest.TestCase):
    def test_addresses_mirror_picosim(self):
        src = os.path.join(EX, '04_memory.s')
        sim = SimSide(src)
        hw = img.build(src, sim.prog)
        self.assertEqual(len(hw.image), 0x10000)
        self.assertEqual(hw.halt_addr, 0x6)          # _start's BKPT, where main returns
        self.assertEqual(set(hw.calls), {'putchar', 'getchar', 'bench_start', 'bench_stop'})
        for name, addr in hw.symbols.items():
            self.assertEqual(sim.prog.label_map.get(name, addr), addr)
        # the main pointer the hardware OS reads
        self.assertEqual(int.from_bytes(hw.image[0x2FFC:0x3000], 'little'),
                         img.to_hw(sim.prog.main_addr) | 1)

    def test_mapping(self):
        self.assertTrue(img.same(0x3104, 0x20003104))
        self.assertTrue(img.same(0x10000, 0x20010000))   # the stack's top
        self.assertTrue(img.same(5, 5))
        self.assertFalse(img.same(5, 6))
        self.assertFalse(img.same(0x20000, 0x20020000))  # outside the 64 KiB
        self.assertEqual(img.to_sim(0x2000FFF8), 0xFFF8)
        self.assertEqual(img.to_sim(0xD0000000), 0xD0000000)

    def test_suite_headers(self):
        tests = suite.tests()
        self.assertGreaterEqual(len(tests), 15)
        kinds = {t['kind'] for t in tests}
        self.assertEqual(kinds, {'trace', 'bench'})
        for t in tests:
            self.assertTrue(t['title'] and t['about'], t['path'])


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn (pip install unicorn)")
class StandInTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from picosim.hwtest.target import Target
        cls.t = Target('standin')

    @classmethod
    def tearDownClass(cls):
        cls.t.close()

    def tearDown(self):
        # undo any sabotage
        self.t.standin.chip.__class__ = Chip

    def test_whole_suite_is_identical(self):
        for info in suite.tests():
            if info['kind'] == 'bench':
                s = Bench(self.t, info['path']).run()
            else:
                s = Lockstep(self.t, info['path'], info['input']).run()
            self.assertEqual(s['verdict'], 'identical', (info['path'], s.get('note'), s.get('diff_rows')))

    def test_examples_are_identical(self):
        for p in sorted(glob.glob(os.path.join(EX, '*.s'))):
            s = Lockstep(self.t, p, 'hi\n', max_steps=800).run()
            self.assertEqual(s['verdict'], 'identical', p)
            self.assertEqual(s['output']['sim'], s['output']['hw'])

    def test_calibration(self):
        cal = calibrate(self.t, force=True)
        self.assertEqual(cal['overhead'], {0: PREDICTED_OVERHEAD, 2: PREDICTED_OVERHEAD})
        self.assertTrue(cal['steady'])

    def _sabotage(self, after_step):
        """Make the stand-in's chip call after_step(chip, pc) after each instruction."""
        class Bad(Chip):
            def step(self):
                pc = self.pc()
                r = Chip.step(self)
                if r == 'ok':
                    after_step(self, pc)
                return r
        self.t.standin.chip.__class__ = Bad

    def test_a_wrong_register_is_found_and_resynced(self):
        src = os.path.join(EX, '02_add.s')
        sim = SimSide(src)
        target = sim.prog.label_map.get('main') or sim.prog.main_addr
        bad_at = img.to_hw(target + 6)            # adds r2, r0, r1

        def corrupt(chip, pc):
            if pc == bad_at:
                chip.write_reg(2, chip.read_reg(2) ^ 0x100)
        self._sabotage(corrupt)
        L = Lockstep(self.t, src)
        s = L.run()
        self.assertEqual(s['verdict'], 'different')
        bad = [r for r in L.rows if r['status'] == 'diff']
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]['diffs'], ['R2'])
        self.assertEqual(bad[0]['addr'], target + 6)
        self.assertEqual(s['end'], 'halted')       # picosim took the chip's value and went on
        self.assertEqual(s['output']['sim'], s['output']['hw'])

    def test_stop_at_first_difference(self):
        src = os.path.join(EX, '03_count_loop.s')

        def corrupt(chip, pc):
            chip.write_reg(7, chip.read_reg(7) + 1)
        self._sabotage(corrupt)
        s = Lockstep(self.t, src, stop_on_diff=True).run()
        self.assertEqual(s['end'], 'diff')
        self.assertEqual(s['counts']['diff'], 1)

    def test_a_wrong_store_is_found(self):
        src = os.path.join(EX, '04_memory.s')

        def corrupt(chip, pc):
            hw1 = int.from_bytes(chip.uc.mem_read(pc, 2), 'little')
            if (hw1 & 0xF800) == 0x6000:          # STR Rt, [Rn, #imm]
                rn, imm = (hw1 >> 3) & 7, ((hw1 >> 6) & 0x1F) * 4
                a = chip.read_reg(rn) + imm
                if img.to_hw(0x3000) <= a < img.to_hw(0x10000):     # the program's memory only
                    chip.write_mem(a, bytes([chip.read_mem(a, 1)[0] ^ 1]))
        self._sabotage(corrupt)
        L = Lockstep(self.t, src)
        s = L.run()
        mem_rows = [r for r in L.rows if any(d.startswith('[') for d in r.get('diffs', []))]
        self.assertTrue(mem_rows)
        self.assertEqual(s['verdict'], 'different')

    def test_a_different_input_pin_is_io(self):
        src = os.path.join(ROOT, 'hwtests', 't10_gpio.s')

        def corrupt(chip, pc):
            # every pin but the test's reads high on this "board"
            chip.gpio._ext = [True if i not in (21, 22) else None for i in range(30)]
        self._sabotage(corrupt)
        L = Lockstep(self.t, src)
        s = L.run()
        self.assertGreater(s['counts']['io'], 0)
        self.assertEqual(s['counts']['diff'], 0)
        self.assertEqual(s['verdict'], 'identical')

    def test_the_debugger_cannot_reach_the_sio(self):
        # as on the RP2040 (the SIO is on the cores' I/O port): Testing must
        # set and read it with code run on the core
        from picosim.hwtest.rsp import RSPError
        with self.assertRaises(RSPError):
            self.t.rsp.read_mem(0xD0000010, 4)
        with self.assertRaises(RSPError):
            self.t.rsp.write32(0xD0000028, 1)
        self.t.reset_halt()
        self.t.run_code(bytes.fromhex('8161' '00be'), {0: 0xD0000000 + 0x14 - 0x18, 1: 1 << 3})  # OUT_SET bit 3
        self.assertEqual(self.t.read_sio(), (1 << 3, 0))

    def test_registers_are_written_at_their_size(self):
        # OpenOCD drops the connection on "gdb sent 32 bits for a 1-bit
        # register (primask)"; the stand-in does too
        r = self.t.rsp
        r.write_reg(r.regnum('primask'), 1)
        self.assertEqual(r.read_reg(r.regnum('primask')) & 1, 1)
        r.write_reg(r.regnum('primask'), 0)
        s = Lockstep(self.t, os.path.join(ROOT, 'hwtests', 't06_high_registers.s')).run()
        self.assertEqual(s['verdict'], 'identical')

    def test_a_connection_out_of_step_is_remade(self):
        # a stray answer (as when a keep-alive was taken for one) puts every
        # later answer one behind: picosim must notice and start afresh
        from picosim.hwtest.session import Session
        sess = Session()
        sess.target = self.t
        n = {'steps': 0}

        def stray(chip, pc):
            n['steps'] += 1
            if n['steps'] == 15:
                self.t.standin._send('OK')
        self._sabotage(stray)
        tests = [{'path': os.path.join(EX, '03_count_loop.s'), 'kind': 'trace'},
                 {'path': os.path.join(EX, '02_add.s'), 'kind': 'trace'}]
        sess.run(tests)
        sess.thread.join(60)
        msgs = sess.drain(10000)
        verdicts = [m['summary']['verdict'] for m in msgs if m['event'] == 'summary']
        self.assertEqual(verdicts, ['identical', 'identical'], [m for m in msgs if m['event'] == 'error'])
        self.assertTrue(any(m.get('retry') for m in msgs if m['event'] == 'start'))

    def test_a_lost_connection_is_remade(self):
        from picosim.hwtest.session import Session
        sess = Session()
        sess.target = self.t
        src = os.path.join(EX, '03_count_loop.s')
        n = {'steps': 0}

        def drop(chip, pc):
            n['steps'] += 1
            if n['steps'] == 10:
                self.t.standin.conn.close()      # the server hangs up mid-test
        self._sabotage(drop)
        sess.run([{'path': src, 'kind': 'trace'}])
        sess.thread.join(60)
        msgs = sess.drain(10000)
        events = [m['event'] for m in msgs]
        self.assertIn('summary', events, msgs[-3:])
        self.assertTrue(any(m.get('retry') for m in msgs if m['event'] == 'start'))
        summary = next(m['summary'] for m in msgs if m['event'] == 'summary')
        self.assertEqual(summary['verdict'], 'identical')

    def test_a_slow_instruction_shows_in_the_benchmark(self):
        src = os.path.join(ROOT, 'hwtests', 'b01_alu_timing.s')
        orig = Chip._cost

        def slow_muls(chip, hw1, hw2, taken):
            return orig(chip, hw1, hw2, taken) + (1 if (hw1 & 0xFFC0) == 0x4340 else 0)
        try:
            Chip._cost = slow_muls
            s = Bench(self.t, src).run()
        finally:
            Chip._cost = orig
        by = {r['name']: r for r in s['regions']}
        self.assertEqual(by['MULS Rd, Rn, Rd x 64']['diff'], 64)
        self.assertEqual(by['MULS Rd, Rn, Rd x 64']['per'], 1.0)
        self.assertEqual(by['ADDS Rd, Rn, Rm x 64']['diff'], 0)


@unittest.skipUnless(HAVE_STANDIN, "the stand-in needs Unicorn (pip install unicorn)")
class ServerTest(unittest.TestCase):
    def test_hw_commands(self):
        import threading
        from picosim.server import Server
        srv = Server(0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        s = socket.create_connection(('127.0.0.1', srv.port))
        s.settimeout(30)
        buf = b''

        def send(**m):
            s.sendall((json.dumps(m) + '\n').encode())

        def until(pred, limit=60):
            nonlocal buf
            end = time.time() + limit
            while time.time() < end:
                while b'\n' not in buf:
                    buf += s.recv(1 << 20)
                line, buf = buf.split(b'\n', 1)
                m = json.loads(line)
                if pred(m):
                    return m
            raise AssertionError("timed out")

        send(cmd='hw_info')
        info = until(lambda m: m.get('type') == 'hw' and m.get('event') == 'info')
        self.assertTrue(info['standin'])
        self.assertTrue(info['suite'])
        send(cmd='hw_connect', kind='standin')
        st = until(lambda m: m.get('event') == 'status' and not m.get('busy'))
        self.assertIn('Stand-in', st['connected'])
        t01 = os.path.join(ROOT, 'hwtests', 't08_algorithms.s')
        send(cmd='hw_run', tests=[{'path': t01, 'kind': 'trace'}])
        steps = 0
        while True:
            m = until(lambda m: m.get('type') == 'hw' and m.get('event') in ('steps', 'summary', 'error'))
            self.assertNotEqual(m['event'], 'error', m)
            if m['event'] == 'steps':
                steps += len(m['rows'])
            else:
                break
        self.assertEqual(m['summary']['verdict'], 'identical')
        self.assertEqual(steps, m['summary']['steps'])
        until(lambda m: m.get('event') == 'done')
        with tempfile.TemporaryDirectory() as d:
            send(cmd='hw_export', path=os.path.join(d, 'report.md'))
            m = until(lambda m: m.get('event') == 'exported')
            with open(m['paths'][0]) as f:
                self.assertIn('Small programs', f.read())
            with open(m['paths'][1]) as f:
                self.assertEqual(json.load(f)['results'][0]['verdict'], 'identical')
        # run the whole suite again, twice, straight after: the same results
        tests = [{'path': t['path'], 'kind': t['kind']} for t in suite.tests()]
        for _ in range(2):
            send(cmd='hw_run', tests=tests)
            verdicts = []
            while True:
                m = until(lambda m: m.get('type') == 'hw' and m.get('event') in ('summary', 'error', 'done'))
                self.assertNotEqual(m['event'], 'error', m)
                if m['event'] == 'done':
                    break
                verdicts.append(m['summary']['verdict'])
            self.assertEqual(verdicts, ['identical'] * len(tests))
        send(cmd='hw_disconnect')
        until(lambda m: m.get('event') == 'status' and not m.get('connected'))
        s.close()

    def test_two_processes_at_once(self):
        # Testing in the GUI and a command-line run at the same time: they
        # build the same files and must neither break each other's builds
        # nor write into each other's logs
        import subprocess
        import sys
        cmd = [sys.executable, '-m', 'picosim.hwtest', '--target', 'standin']
        procs = [subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                 for _ in range(2)]
        outs = [p.communicate(timeout=300)[0] for p in procs]
        for p, out in zip(procs, outs):
            self.assertEqual(p.returncode, 0, out[-2000:])
            self.assertEqual(out.count('IDENTICAL'), len(suite.tests()), out[-2000:])


if __name__ == '__main__':
    unittest.main()
