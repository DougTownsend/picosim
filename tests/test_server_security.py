"""The private GUI connection (picosim/server.py): only a client that sends
this run's secret first is served; nothing runs before that; commands are a
fixed list with checked argument types; long lines and a GUI that stops
reading cannot hang the server."""

import json
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picosim import server as S
from picosim.server import Server


def _lines(sock, limit=5.0):
    """Every JSON message the server sends until it goes quiet or closes."""
    sock.settimeout(limit)
    buf, out = b'', []
    try:
        while True:
            data = sock.recv(65536)
            if not data:
                break
            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                out.append(json.loads(line))
            sock.settimeout(0.5)
    except socket.timeout:
        pass
    return out


class _Srv(unittest.TestCase):
    def serve(self, **patches):
        for k, v in patches.items():
            p = mock.patch.object(S, k, v)
            p.start()
            self.addCleanup(p.stop)
        srv = Server(0)
        handled = []
        for name in S.COMMANDS:                     # record any handler that runs
            setattr(srv, 'cmd_' + name, lambda msg, n=name: handled.append(n))
        srv.send_state = lambda: None
        self.thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self.thread.start()
        return srv, handled

    def connect(self, srv):
        s = socket.create_connection(('127.0.0.1', srv.port), timeout=5)
        self.addCleanup(s.close)
        return s

    @staticmethod
    def put(s, **msg):
        s.sendall((json.dumps(msg) + '\n').encode())


class HelloTest(_Srv):
    def test_the_right_secret_gets_in(self):
        srv, handled = self.serve()
        s = self.connect(srv)
        self.put(s, cmd='hello', token=srv.token)
        self.put(s, cmd='pause')
        msgs = _lines(s, 2)
        self.assertTrue(any(m.get('type') == 'facts' for m in msgs))
        self.assertEqual(handled, ['pause'])

    def test_commands_right_behind_the_hello(self):
        srv, handled = self.serve()
        s = self.connect(srv)
        s.sendall((json.dumps({'cmd': 'hello', 'token': srv.token}) + '\n' +
                   json.dumps({'cmd': 'pause'}) + '\n').encode())
        _lines(s, 2)
        self.assertEqual(handled, ['pause'])

    def test_no_secret_wrong_secret_or_no_hello_get_nothing(self):
        srv, handled = self.serve()
        for first in ({'cmd': 'pause'}, {'cmd': 'hello', 'token': 'x' * 64},
                      {'cmd': 'hello'}, {'cmd': 'hello', 'token': 7}, ['hello']):
            s = self.connect(srv)
            s.sendall((json.dumps(first) + '\n' + json.dumps({'cmd': 'pause'}) + '\n').encode())
            self.assertEqual(_lines(s, 2), [], first)       # closed, nothing shown
        s = self.connect(srv)
        s.sendall(b'not json\n')
        self.assertEqual(_lines(s, 2), [])
        self.assertEqual(handled, [])
        # the GUI can still get in after all of that
        s = self.connect(srv)
        self.put(s, cmd='hello', token=srv.token)
        self.put(s, cmd='pause')
        self.assertTrue(_lines(s, 2))
        self.assertEqual(handled, ['pause'])

    def test_silent_and_endless_hellos_time_out(self):
        srv, handled = self.serve(HELLO_TIMEOUT=0.5)
        quiet = self.connect(srv)
        t = time.monotonic()
        self.assertEqual(_lines(quiet, 3), [])
        self.assertLess(time.monotonic() - t, 2.5)
        flood = self.connect(srv)
        try:
            flood.sendall(b'x' * (S.HELLO_MAX + 10))
        except OSError:
            pass
        self.assertEqual(_lines(flood, 3), [])
        s = self.connect(srv)
        self.put(s, cmd='hello', token=srv.token)
        self.assertTrue(_lines(s, 2))
        self.assertEqual(handled, [])

    def test_nobody_else_after_the_gui(self):
        srv, _ = self.serve()
        s = self.connect(srv)
        self.put(s, cmd='hello', token=srv.token)
        _lines(s, 1)
        with self.assertRaises(OSError):
            other = socket.create_connection(('127.0.0.1', srv.port), timeout=2)
            self.addCleanup(other.close)
            other.sendall(b'{"cmd": "hello", "token": "' + srv.token.encode() + b'"}\n')
            if not other.recv(1):
                raise ConnectionResetError

    def test_secrets_differ_and_compare_safely(self):
        a, b = Server(0), Server(0)
        self.addCleanup(a.sock.close)
        self.addCleanup(b.sock.close)
        self.assertNotEqual(a.token, b.token)
        self.assertGreaterEqual(len(a.token), 64)
        self.assertTrue(a.hello_ok(json.dumps({'cmd': 'hello', 'token': a.token}).encode()))
        self.assertFalse(a.hello_ok(json.dumps({'cmd': 'hello', 'token': b.token}).encode()))
        self.assertFalse(a.hello_ok(b'\xff\xfe'))


class CommandTest(_Srv):
    def ready(self, **patches):
        srv, handled = self.serve(**patches)
        s = self.connect(srv)
        self.put(s, cmd='hello', token=srv.token)
        _lines(s, 1)
        return s, handled

    def test_only_listed_commands_run(self):
        s, handled = self.ready()
        for cmd in ('serve_forever', '__init__', 'dispatch', 'send', 7, None, 'x' * 1000):
            self.put(s, cmd=cmd)
        msgs = _lines(s, 1)
        self.assertEqual(handled, [])
        self.assertEqual(sum(m.get('type') == 'error' for m in msgs), 7)
        self.assertTrue(all(len(m['message']) < 100 for m in msgs))

    def test_argument_types(self):
        s, handled = self.ready()
        self.put(s, cmd='load', path=['/etc/passwd'])
        self.put(s, cmd='hw_connect', port='3333; rm')
        self.put(s, cmd='hw_discover_run', confirmed='yes')
        self.put(s, cmd='hw_discover_flash', index=True)
        msgs = _lines(s, 1)
        self.assertEqual(handled, [])
        self.assertEqual([m['message'].split(':')[0] for m in msgs],
                         ['load', 'hw_connect', 'hw_discover_run', 'hw_discover_flash'])
        self.put(s, cmd='load', path='examples/01_hello.s')
        self.put(s, cmd='hw_connect', kind='openocd', port=3333, speed=5000)
        _lines(s, 1)
        self.assertEqual(handled, ['load', 'hw_connect'])

    def test_a_long_line_is_dropped_and_the_next_one_runs(self):
        s, handled = self.ready(MAX_LINE=1000)
        s.sendall(b'{"cmd": "pause", "pad": "' + b'x' * 5000 + b'"}\n')
        self.put(s, cmd='pause')
        msgs = _lines(s, 1)
        self.assertEqual(handled, ['pause'])
        self.assertEqual(sum('longer than' in m.get('message', '') for m in msgs), 1)

    def test_a_gui_that_stops_reading_is_let_go(self):
        srv, _ = self.serve(SEND_TIMEOUT=0.5)
        s = self.connect(srv)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        self.put(s, cmd='hello', token=srv.token)
        srv.cmd_state = lambda msg: [srv.send({'type': 'x', 'pad': 'y' * 100000}) for _ in range(200)]
        self.put(s, cmd='state')                     # floods; we never read
        self.thread.join(15)
        self.assertFalse(self.thread.is_alive())


class RegistryTest(unittest.TestCase):
    def test_every_command_is_listed_once(self):
        import re
        with open(os.path.join(ROOT, 'picosim', 'server.py')) as f:
            src = f.read()
        names = set(re.findall(r'    def cmd_(\w+)\(', src))
        self.assertEqual(names, set(S.COMMANDS))
        groups = (S.SIM_COMMANDS, S.FILE_COMMANDS, S.HARDWARE_COMMANDS)
        self.assertEqual(sum(len(g) for g in groups), len(S.COMMANDS))

    def test_the_gui_sends_only_listed_commands(self):
        import glob
        import re
        sent = set()
        for p in glob.glob(os.path.join(ROOT, 'gui', 'scripts', '*.gd')):
            with open(p) as f:
                sent |= set(re.findall(r'\.send\("([a-z_]+)"', f.read()))
        self.assertTrue(sent)
        self.assertEqual(sent - set(S.COMMANDS), set())

    def test_the_gui_says_hello_first(self):
        with open(os.path.join(ROOT, 'gui', 'scripts', 'backend.gd')) as f:
            src = f.read()
        self.assertIn('"cmd": "hello", "token": _token', src)
        self.assertIn('OS.unset_environment("PICOSIM_TOKEN")', src)

    def test_the_server_keeps_the_secret_from_its_children(self):
        with mock.patch.dict(os.environ, {S.TOKEN_ENV: 'abc' * 20}), \
                mock.patch.object(Server, 'serve_forever', lambda self: None), \
                mock.patch('builtins.print'):
            seen = {}
            orig = Server.__init__

            def init(self, port, token=None):
                orig(self, port, token)
                seen['token'] = self.token
                self.sock.close()
            with mock.patch.object(Server, '__init__', init):
                S.main([])
            self.assertEqual(seen['token'], 'abc' * 20)
            self.assertNotIn(S.TOKEN_ENV, os.environ)


if __name__ == '__main__':
    unittest.main()
