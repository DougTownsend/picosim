"""Campaign F's two old checks without a board: 01_hello's port is found by
the flash ID even when its name is the probe firmware's (2026-11-17, the
first board run missed it that way), the pin-report frames are stripped,
and the FUNCSEL result is scored."""

import contextlib
import unittest
from unittest import mock

from picosim.discover import flashguard
from picosim.discover.cases import flashed

BOARD = 'E6642815E38BA828'
PROBE = 'E6642815E33ABA28'          # the debug probe's own serial number
FRAME = '\x02P0000abcd,00200000\x03'


class BoardPortTest(unittest.TestCase):
    def test_same_name_found_by_flash_id(self):
        # macOS reuses the location-based name: the port is NOT new
        ports = {'/dev/cu.usbmodem1102', '/dev/cu.usbmodem1101'}
        serials = {'/dev/cu.usbmodem1102': PROBE, '/dev/cu.usbmodem1101': BOARD}
        self.assertEqual(flashguard.board_serial_port(BOARD, before=ports, serials=serials, ports=ports),
                         ('/dev/cu.usbmodem1101', 'flash id'))

    def test_byte_reversed_id_matches(self):
        rev = bytes.fromhex(BOARD)[::-1].hex().upper()
        self.assertEqual(flashguard.board_serial_port(BOARD, serials={'/dev/x': rev}, ports=set())[0], '/dev/x')

    def test_the_probe_is_never_taken_for_the_board(self):
        ports = {'/dev/cu.usbmodem1102'}
        serials = {'/dev/cu.usbmodem1102': PROBE}
        self.assertEqual(flashguard.board_serial_port(BOARD, before=ports, serials=serials, ports=ports),
                         (None, None))

    def test_new_port_fallback_without_serial_numbers(self):
        self.assertEqual(flashguard.board_serial_port(BOARD, before={'/dev/a'}, serials={},
                                                      ports={'/dev/a', '/dev/b'}),
                         ('/dev/b', 'new port'))

    def test_no_id_uses_new_port_only(self):
        self.assertEqual(flashguard.board_serial_port(None, before=set(), serials={'/dev/a': BOARD},
                                                      ports=set()), (None, None))


class FakeSerial:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b''


def opener(chunks):
    @contextlib.contextmanager
    def open_port(port):
        yield FakeSerial(chunks)
    return open_port


class HelloTest(unittest.TestCase):
    def test_frames_are_stripped(self):
        self.assertEqual(flashed.strip_pin_frames(FRAME + 'H' + FRAME + 'i!\n' + FRAME), 'Hi!\n')

    def test_read_stops_once_the_text_is_in(self):
        chunks = [FRAME.encode(), b'H', FRAME.encode() + b'i!\n', b'never read']
        raw = flashed.read_console('/dev/x', timeout=5, open_port=opener(chunks))
        self.assertEqual(flashed.strip_pin_frames(raw), 'Hi!\n')

    def test_hello_check_with_reused_port_name(self):
        ports = {'/dev/cu.usbmodem1101', '/dev/cu.usbmodem1102'}
        serials = {'/dev/cu.usbmodem1101': BOARD, '/dev/cu.usbmodem1102': PROBE}
        flashes = []
        with mock.patch.object(flashguard, '_serial_ports', return_value=ports), \
                mock.patch.object(flashguard, '_usb_serials', return_value=serials), \
                mock.patch.object(flashed, 'read_console', return_value=FRAME + 'Hi!\n' + FRAME):
            r = flashed.hello_check(None, '/x.elf', BOARD, print, flash=lambda *a: flashes.append(a))
        self.assertEqual(len(flashes), 1)
        self.assertTrue(r['ok'])
        self.assertEqual((r['port'], r['found_by'], r['printed'], r['pin_frames']),
                         ('/dev/cu.usbmodem1101', 'flash id', 'Hi!\n', 2))

    def test_hello_check_no_port(self):
        with mock.patch.object(flashguard, '_serial_ports', return_value=set()), \
                mock.patch.object(flashguard, '_usb_serials', return_value={}):
            r = flashed.hello_check(None, '/x.elf', BOARD, print, flash=lambda *a: None, port_timeout=0.2)
        self.assertFalse(r['ok'])
        self.assertIsNone(r['port'])

    def test_frames_only_is_not_ok(self):
        with mock.patch.object(flashguard, '_serial_ports', return_value={'/dev/a'}), \
                mock.patch.object(flashguard, '_usb_serials', return_value={'/dev/a': BOARD}), \
                mock.patch.object(flashed, 'read_console', return_value=FRAME * 3):
            r = flashed.hello_check(None, '/x.elf', BOARD, print, flash=lambda *a: None)
        self.assertFalse(r['ok'])


class BootCheckTest(unittest.TestCase):
    """The restore's boot check (2026-11-17: a stale port passed it)."""

    def run_check(self, before, polls):
        bc = flashguard.UsbSerialBootCheck(timeout=0.5, poll=0.01, board_id=BOARD)
        seq = iter(polls)
        last = [polls[-1]]

        def serials():
            try:
                last[0] = next(seq)
            except StopIteration:
                pass
            return last[0]
        with mock.patch.object(flashguard, '_serial_ports', side_effect=lambda: set(last[0])), \
                mock.patch.object(flashguard, '_usb_serials', side_effect=serials):
            last[0] = before
            bc.before_reset()
            return bc()

    def test_stale_port_that_stays_is_not_a_pass(self):
        ok, detail = self.run_check({'/dev/b': BOARD}, [{'/dev/b': BOARD}])
        self.assertFalse(ok, detail)

    def test_port_that_goes_and_comes_back_passes(self):
        ok, _ = self.run_check({'/dev/b': BOARD}, [{'/dev/b': BOARD}, {}, {}, {'/dev/b': BOARD}])
        self.assertTrue(ok)

    def test_port_new_after_reset_passes(self):
        ok, _ = self.run_check({}, [{}, {'/dev/b': BOARD}])
        self.assertTrue(ok)

    def test_only_the_probe_is_not_a_pass(self):
        ok, _ = self.run_check({'/dev/p': PROBE}, [{'/dev/p': PROBE}])
        self.assertFalse(ok)


class ReadRetryTest(unittest.TestCase):
    def test_a_failed_open_is_retried(self):
        tries = []

        @contextlib.contextmanager
        def open_port(port):
            tries.append(port)
            if len(tries) < 3:
                raise OSError("the old program's port went away")
            yield FakeSerial([b'Hi!\n'])
        raw = flashed.read_console('/dev/x', timeout=5, open_port=open_port)
        self.assertEqual(raw, 'Hi!\n')
        self.assertEqual(len(tries), 3)


class FakePSM:
    """OpenOCD's mww/mdw on PSM.FRCE_OFF with its SET/CLR aliases."""

    def __init__(self, stuck=None):
        self.frce_off, self.cmds, self.stuck = 0, [], stuck

    def __call__(self, cmd):
        self.cmds.append(cmd)
        op, *args = cmd.split()
        if op == 'mww':
            a, v = int(args[0], 16), int(args[1], 16)
            if a == 0x40012004 and self.stuck != 'off':
                self.frce_off |= v
            elif a == 0x40013004 and self.stuck != 'on':
                self.frce_off &= ~v
            return ''
        if op == 'mdw':
            return f"0x{int(args[0], 16):08x}: {self.frce_off:08x} \n"
        return ''


class Core1ResetTest(unittest.TestCase):
    def test_pulses_bit_16_only_through_the_aliases(self):
        m = FakePSM()
        self.assertEqual(flashguard.reset_core1(m), 0)
        writes = [c for c in m.cmds if c.startswith('mww')]
        self.assertEqual(writes, ['mww 0x40012004 0x00010000', 'mww 0x40013004 0x00010000'])

    def test_never_set_is_an_error_and_is_cleared(self):
        m = FakePSM(stuck='off')
        with self.assertRaises(flashguard.FlashGuardError):
            flashguard.reset_core1(m)
        self.assertEqual(m.cmds[-1], 'mww 0x40013004 0x00010000')

    def test_stuck_on_is_an_error(self):
        with self.assertRaises(flashguard.FlashGuardError):
            flashguard.reset_core1(FakePSM(stuck='on'))

    def test_start_program_resets_core1_before_reset_run(self):
        m = FakePSM()
        t = mock.Mock()
        t.rsp.monitor.side_effect = lambda c, timeout=None: m(c)
        flashed.start_program(t)
        self.assertTrue(m.cmds[0].startswith('mww 0x40012004'))
        self.assertEqual(m.cmds[-1], 'reset run')


class Core1SafetyTest(unittest.TestCase):
    def setUp(self):
        from picosim.discover import safety
        self.s = safety

    def test_allowed_only_when_asked(self):
        with self.assertRaises(self.s.SafetyError):
            self.s.check_write(0x40012004, 4, 1 << 16)
        self.s.check_write(0x40012004, 4, 1 << 16, allow_core1_reset=True)
        self.s.check_write(0x40013004, 4, 1 << 16, allow_core1_reset=True)

    def test_nothing_else_in_psm(self):
        for addr, v in ((0x40010004, 1 << 16), (0x40011004, 1 << 16), (0x40012004, 1 << 15),
                        (0x40012004, 0xFFFFFFFF), (0x40012000, 1 << 16), (0x40012008, 1 << 16)):
            with self.assertRaises(self.s.SafetyError, msg=hex(addr)):
                self.s.check_write(addr, 4, v, allow_core1_reset=True)


class KeepRunTest(unittest.TestCase):
    """Each F run's facts documents are kept (a repeat run used to replace them)."""

    def test_this_runs_files_are_copied_and_recorded(self):
        import os
        import tempfile
        import time
        import types
        with tempfile.TemporaryDirectory() as d:
            hw, cache = os.path.join(d, 'hwfacts'), os.path.join(d, 'cache')
            os.makedirs(os.path.join(hw, 'peripherals'))
            for n in ('environment.json', 'peripherals/gpio_flashed.json', 'peripherals/uart.json'):
                with open(os.path.join(hw, n), 'w') as f:
                    f.write('{}')
            old = os.path.join(hw, 'peripherals', 'timer.json')
            with open(old, 'w') as f:
                f.write('{}')
            os.utime(old, (1, 1))               # from an earlier run: not this run's
            ctx = types.SimpleNamespace(root=cache, kind='openocd', log=lambda s: None)
            with mock.patch('picosim.discover.facts.record_raw') as rec:
                run = flashed.keep_run(ctx, os.path.join(hw, 'environment.json'), time.time() - 5)
            self.assertEqual(sorted(os.listdir(run)), ['facts-environment.json',
                                                       'facts-peripherals-gpio_flashed.json',
                                                       'facts-peripherals-uart.json'])
            rec.assert_called_once_with(run, 'openocd')

    def test_record_raw_takes_facts_files(self):
        import json
        import os
        import tempfile
        from picosim.discover import facts
        with tempfile.TemporaryDirectory() as d:
            run = os.path.join(d, 'chip1', 'flashed', '20261117-155530')
            os.makedirs(run)
            with open(os.path.join(run, 'facts-environment.json'), 'w') as f:
                f.write('{}')
            with mock.patch.object(facts, 'root', return_value=d):
                e = facts.record_raw(run, 'standin')
            self.assertEqual(list(e['files']), ['facts-environment.json'])
            self.assertIn('chip1/flashed/20261117-155530', json.load(open(os.path.join(d, 'raw_index.json'))))


class FuncselTest(unittest.TestCase):
    def test_reset_state_is_ok(self):
        r = flashed.funcsel_result(0x00000000)
        self.assertTrue(r['ok'])
        self.assertFalse(r['pad_driven'])

    def test_driven_pad_is_not_ok(self):
        self.assertFalse(flashed.funcsel_result(1 << 13 | 1 << 9)['ok'])
        self.assertFalse(flashed.funcsel_result(1 << 9)['ok'])

    def test_unread(self):
        self.assertIsNone(flashed.funcsel_result(None)['ok'])


if __name__ == '__main__':
    unittest.main()
