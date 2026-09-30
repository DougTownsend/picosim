"""Protocol test for the GUI backend (picosim/server.py)."""

import json
import os
import socket
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

    def test_load_error(self):
        c = start(os.path.join(ROOT, 'test_asm_files', 'gpio.s'))
        m, _ = c.until(lambda m: m['type'] == 'load_error')
        self.assertIn('Error', m['message'])


if __name__ == '__main__':
    unittest.main()
