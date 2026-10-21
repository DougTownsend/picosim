"""Protocol test for the GUI backend (picosim/server.py)."""

import json
import os
import socket
import tempfile
import threading
import time
import unittest

from picosim.server import Server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Client:
    def __init__(self, port):
        self.s = socket.create_connection(('127.0.0.1', port))
        self.s.settimeout(5)
        self.buf = b''

    def send(self, **msg):
        self.s.sendall((json.dumps(msg) + '\n').encode())

    def recv(self):
        while b'\n' not in self.buf:
            self.buf += self.s.recv(65536)
        line, self.buf = self.buf.split(b'\n', 1)
        return json.loads(line)

    def until(self, pred, limit=5.0):
        end = time.time() + limit
        seen = []
        while time.time() < end:
            m = self.recv()
            seen.append(m)
            if pred(m):
                return m, seen
        raise AssertionError(f"timed out; last={seen[-1:]}")


def start(path):
    srv = Server(0)
    srv.path = path
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return Client(srv.port)


class ServerTest(unittest.TestCase):
    def test_step_and_breakpoint(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'test_main.s'))
        prog, _ = c.until(lambda m: m['type'] == 'program')
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(st['next_state'], 'FETCH_ADDR')

        c.send(cmd='step_cycle')
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(st['history'][-1]['state'], 'FETCH_ADDR')
        self.assertTrue(st['in_insn'])

        c.send(cmd='step_insn')
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertFalse(st['in_insn'])
        self.assertEqual(len(st['history']), st['history'][-1]['total'])

        main = prog['main']
        c.send(cmd='set_bp', addr=main)
        c.until(lambda m: m['type'] == 'state')
        c.send(cmd='run', cps=0)
        st, _ = c.until(lambda m: m['type'] == 'state' and not m['running'])
        self.assertEqual(st['pc'], main)

        c.send(cmd='clear_bp', addr=main)
        c.send(cmd='run', cps=0)
        st, _ = c.until(lambda m: m['type'] == 'state' and m['halted'])

    def test_console_echo(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'echo_test.s'))
        c.until(lambda m: m['type'] == 'state')
        c.send(cmd='run', cps=0)
        c.until(lambda m: m['type'] == 'state' and m['waiting_input'])
        c.send(cmd='console_input', text='hi\r')
        out = ''
        end = time.time() + 5
        while 'hi' not in out and time.time() < end:
            m = c.recv()
            if m['type'] == 'console' and m['source'] == 'sim':
                out += m['text']
        self.assertIn('hi', out)

    def test_gpio_state(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'gpio_test.s'))
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(len(st['gpio']), 30)
        c.send(cmd='gpio_set', pin=3, value=1)
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(st['gpio'][3]['level'], 1)
        self.assertEqual(st['gpio_changed'], [3])

    def test_program_settings(self):
        """Settings from the GUI change the program that runs, are stored
        outside the program file, and bad values are refused."""
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get('PICOSIM_SETTINGS_DIR')
            os.environ['PICOSIM_SETTINGS_DIR'] = tmp
            try:
                path = os.path.join(ROOT, 'examples', '05_blink_led.s')
                with open(path, 'rb') as f:
                    original = f.read()
                c = start(path)
                prog, _ = c.until(lambda m: m['type'] == 'program')
                led = {s['name']: s for s in prog['settings']}['LED_PIN']
                self.assertEqual((led['value'], led['default']), ('15', '15'))

                c.send(cmd='set_settings', values={'LED_PIN': '20'})
                prog, _ = c.until(lambda m: m['type'] == 'program')
                self.assertEqual(prog['settings'][0]['value'], '20')
                c.until(lambda m: m['type'] == 'state')     # the reload's own state
                for _ in range(12):     # the OS's 3 instructions, then main up to the first flip
                    c.send(cmd='step_insn')
                    st, _ = c.until(lambda m: m['type'] == 'state')
                self.assertEqual(st['gpio'][20]['level'], 1)
                self.assertNotEqual(st['gpio'][15]['level'], 1)

                c.send(cmd='set_settings', values={'LED_PIN': 'abc'})
                err, _ = c.until(lambda m: m['type'] == 'settings_error')
                self.assertIn('LED_PIN', err['message'])

                c.send(cmd='set_settings', defaults=True)
                prog, _ = c.until(lambda m: m['type'] == 'program')
                self.assertEqual(prog['settings'][0]['value'], '15')
                with open(path, 'rb') as f:
                    self.assertEqual(f.read(), original)   # file never touched
            finally:
                if old is None:
                    os.environ.pop('PICOSIM_SETTINGS_DIR', None)
                else:
                    os.environ['PICOSIM_SETTINGS_DIR'] = old

    def test_board_pin_frames(self):
        """Live-pin frames from a real board are taken out of the console
        text (even when split across reads) and become the drawn pins."""
        srv = Server(0)
        shown = []
        srv.console = lambda text, source='pico': shown.append(text)
        srv.send_state = lambda: None
        srv.mode, srv.serial = 'pico', object()
        srv.serial_text('Hi\x02P0010')                 # a frame cut in half
        srv.serial_text('0000,00008000\x03 there\n')
        self.assertEqual(''.join(shown), 'Hi there\n')
        self.assertTrue(srv.board_live())
        pins = srv.gpio_pins()
        self.assertEqual((pins[20]['level'], pins[15]['dir']), (1, 'out'))
        srv.serial_text('\x02P00000000,00008000\x03')  # GP20 goes low
        self.assertEqual(srv.board_changed, [20])
        srv.serial_text('a \x02 b\n')                  # a stray \x02 that is no frame
        srv.serial_text('x' * 50)
        self.assertTrue(''.join(shown).endswith('a \x02 b\n' + 'x' * 50))
        srv.serial = None

    def test_walkthrough(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'gpio.s'))
        c.until(lambda m: m['type'] == 'state')
        c.send(cmd='walkthrough')
        m, _ = c.until(lambda m: m['type'] == 'walkthrough')
        self.assertGreater(len(m['steps']), 10)
        first = m['steps'][0]
        self.assertTrue(first['summary'])
        self.assertEqual(first['cycles'][0]['info']['state'], 'FETCH_ADDR')
        self.assertIn('getchar', m['stopped'])
        # the GUI's own machine was not advanced
        c.send(cmd='state')
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(st['cycles'], 0)

    def test_pipe_walkthrough(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'uint16_loop_test.s'))
        c.until(lambda m: m['type'] == 'state')
        c.send(cmd='pipe_walkthrough', limit=40)
        m, _ = c.until(lambda m: m['type'] == 'pipe_walkthrough')
        steps = m['steps']
        self.assertEqual(len(steps), 40)
        self.assertIsNone(steps[0]['cycles'][0]['view']['E'])         # the first fetch
        cycle = 0
        for i, s in enumerate(steps):
            self.assertTrue(s['summary'])
            for k, cy in enumerate(s['cycles']):
                v = cy['view']
                self.assertEqual(v['cycle'], cycle)                    # no gaps, no repeats
                cycle += 1
                if not (i == 0 and k == 0):
                    self.assertEqual(v['E']['addr'], s['addr'])        # this step executes
        self.assertTrue(any(s['flushes'] for s in steps))
        c.send(cmd='state')
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(st['cycles'], 0)                              # the GUI's machine is untouched

    def test_perf_and_sources(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'gpio.s'))
        c.until(lambda m: m['type'] == 'state')
        c.send(cmd='step_insn')
        c.send(cmd='step_insn')
        c.until(lambda m: m['type'] == 'state' and m['steps'] == 2)
        c.send(cmd='perf', machine='pipe3', n=8)
        m, _ = c.until(lambda m: m['type'] == 'perf')
        self.assertEqual(m['stock']['insns'], 2)
        self.assertEqual(m['stock']['machines']['m0plus']['cycles'], 4)   # two LDRs: 2 + 2
        self.assertEqual(m['stock']['machines']['multicycle']['cycles'], 14)
        self.assertTrue(m['schedule']['rows'][-1]['pending'])          # the next instruction
        c.send(cmd='component_source')
        m, _ = c.until(lambda m: m['type'] == 'sources')
        self.assertIn('class ALU', m['sources']['alu'])
        self.assertIn('def tri_state', m['sources']['gatePC'])

    def test_pipeline_machine(self):
        """2-stage pipeline: the instruction behind the one executing is
        always in Fetch, a taken branch flushes it, and the cycles stepped
        are the RP2040's Table 81 counts."""
        from picosim import timing
        c = start(os.path.join(ROOT, 'test_asm_files', 'uint16_loop_test.s'))
        c.until(lambda m: m['type'] == 'state')
        c.send(cmd='set_machine', machine='m0plus')
        st, _ = c.until(lambda m: m['type'] == 'state' and m['machine'] == 'm0plus')
        self.assertIsNone(st['pipe']['E'])
        self.assertEqual(st['pipe']['F']['addr'], st['pc'])      # the first fetch
        seen = {'held': False, 'redirect': False}
        prev_e = None
        for _ in range(60):
            c.send(cmd='step_cycle')
            st, _ = c.until(lambda m: m['type'] == 'state')
            p = st['pipe']
            e, f = p['E'], p['F']
            self.assertIsNotNone(e)
            self.assertIsNotNone(f)
            self.assertGreater(f['id'], e['id'])                  # one step behind
            if f['status'] == 'held':
                seen['held'] = True
                self.assertGreater(e['cycles'], 1)
            if e['redirect']:
                seen['redirect'] = True
                self.assertTrue(f['flushed'])                    # the wrong-path fetch
            if prev_e is not None and prev_e['redirect']:
                self.assertTrue(e['refilling'])                   # target enters Fetch
                self.assertFalse(f['flushed'])
            prev_e = e
        self.assertTrue(seen['held'])
        self.assertTrue(seen['redirect'])
        self.assertEqual(p['cycle'], 60)
        # the cycles stepped match Table 81 for the instructions finished
        c.send(cmd='perf', machine='m0plus', n=64)
        m, _ = c.until(lambda m: m['type'] == 'perf')
        done = [r for r in m['schedule']['rows'] if not r['flushed'] and not r.get('pending')]
        finished = sum(r['cycles'] for r in done[:p['insns']])
        self.assertEqual(finished + e['k'], 60)                  # + the one in Execute
        # Step Insn moves to the next instruction's first execute cycle
        c.send(cmd='step_insn')
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(st['pipe']['E']['k'], 1)
        # back to the multi-cycle machine
        c.send(cmd='set_machine', machine='multicycle')
        st, _ = c.until(lambda m: m['type'] == 'state' and m['machine'] == 'multicycle')
        self.assertIsNone(st['pipe'])
        c.send(cmd='step_cycle')
        st, _ = c.until(lambda m: m['type'] == 'state')
        self.assertEqual(st['history'][-1]['state'], 'FETCH_ADDR')

    def test_pipeline_run(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'test_main.s'))
        prog, _ = c.until(lambda m: m['type'] == 'program')
        c.until(lambda m: m['type'] == 'state')
        c.send(cmd='set_machine', machine='m0plus')
        c.send(cmd='set_bp', addr=prog['main'])
        c.send(cmd='run', cps=0)
        st, _ = c.until(lambda m: m['type'] == 'state' and m['machine'] == 'm0plus' and not m['running']
                        and m['steps'] > 0)
        self.assertEqual(st['pc'], prog['main'])
        self.assertEqual(st['pipe']['F']['addr'], prog['main'])  # about to execute
        c.send(cmd='clear_bp', addr=prog['main'])
        c.send(cmd='run', cps=0)
        st, _ = c.until(lambda m: m['type'] == 'state' and m['halted'] and not m['running'])
        self.assertFalse(st['pipe']['more'])

    def test_load_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, 'bad.s')
            with open(bad, 'w') as f:
                f.write('.syntax unified\n.thumb\nmain:\n    frobnicate r0\n')
            c = start(bad)
            m, _ = c.until(lambda m: m['type'] == 'load_error')
        self.assertIn('Error', m['message'])


if __name__ == '__main__':
    unittest.main()
