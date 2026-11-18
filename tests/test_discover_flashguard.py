"""Flash guard (picosim/discover/flashguard.py): backup, restore and the
pending-restore guard, run against a fake OpenOCD and a fake picotool.
Nothing here touches real hardware or the home folder."""

import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from picosim.discover import flashguard as fg
from picosim.discover.flashguard import FlashGuard, FlashGuardError

SIZE = 64 * 1024          # a small "chip" keeps the tests fast
UID = 'E6614C311B4D8F2A'


def _path(cmd):
    m = re.search(r'\{([^}]*)\}', cmd)
    return m.group(1)


def _rd(path, mode='rb'):
    with open(path, mode) as f:
        return f.read()


def _corrupt(data):
    b = bytearray(data)
    b[100] ^= 0x01
    return bytes(b)


class FakeOpenOCD:
    """OpenOCD's flash commands on a bytearray 'flash'."""

    def __init__(self, size=SIZE, reported_size=None):
        self.flash = bytearray(os.urandom(size))
        self.reported_size = size if reported_size is None else reported_size
        self.commands = []
        self.fail_probe = False
        self.fail_verify = False
        self.corrupt_reads = 0          # the next N read_banks return a flipped bit
        self.corrupt_writes = False     # write_image flips a bit
        self.xip_on = True              # False: reads through XIP give zeros (after a reset halt)
        self.psm_frce_off = 0           # PSM.FRCE_OFF (reset_core1 pulses bit 16)

    def monitor(self, cmd, timeout):
        self.commands.append(cmd)
        if cmd in ('halt', 'reset run'):
            return ''
        if cmd.startswith('mww 0x4001'):
            a, v = (int(x, 16) for x in cmd.split()[1:])
            assert a in (0x40012004, 0x40013004) and v == 1 << 16, cmd   # the only PSM write allowed
            self.psm_frce_off = self.psm_frce_off | v if a == 0x40012004 else self.psm_frce_off & ~v
            return ''
        if cmd == 'mdw 0x40010004':
            return f"0x40010004: {self.psm_frce_off:08x} \n"
        if cmd == 'flash probe 0':
            if self.fail_probe:
                return "Error: Failed to probe flash\nauto_probe failed\n"
            return "flash 'rp2040_flash' found at 0x10000000\n"
        if cmd == 'flash info 0':
            # as OpenOCD 0.12 with the rp2040 driver: it prints, then fails the protection check
            raise RuntimeError("monitor command 'flash info 0' failed (E74)")
        if cmd == 'flash banks':
            return (f"#0 : rp2040.flash (rp2040_flash) at 0x10000000, size 0x{self.reported_size:08x}, "
                    "buswidth 0, chipwidth 0\n")
        if cmd.startswith('flash read_bank 0 '):
            p = _path(cmd)
            data = bytes(self.flash) if self.xip_on else bytes(len(self.flash))
            if self.corrupt_reads:
                self.corrupt_reads -= 1
                data = _corrupt(data)
            with open(p, 'wb') as f:
                f.write(data)
            return f"wrote {len(data)} bytes to file {p} from flash bank 0 at offset 0x00000000 in 0.1s\n"
        if cmd.startswith('flash write_image erase '):
            p = _path(cmd)
            assert cmd.endswith(' 0x10000000 bin'), cmd
            with open(p, 'rb') as f:
                data = f.read()
            if self.corrupt_writes:
                data = _corrupt(data)
            self.flash[:len(data)] = data
            return f"auto erase enabled\nwrote {len(data)} bytes from file {p} in 0.2s\n"
        if cmd.startswith('verify_image '):
            p = _path(cmd)
            with open(p, 'rb') as f:
                data = f.read()
            seen = bytes(self.flash[:len(data)]) if self.xip_on else bytes(len(data))
            if self.fail_verify or seen != data:
                return ("checksum mismatch - attempting binary compare\n"
                        "diff 0 address 0x10000064. Was 0x01 instead of 0x00\n"
                        "No more differences found.\n")
            return f"verified {len(data)} bytes from file {p} in 0.1s\n"
        return f'invalid command name "{cmd.split()[0]}"'

    def writes(self):
        return [c for c in self.commands if 'write_image' in c]


class FakePicotool:
    def __init__(self, ocd, uid=UID):
        self.ocd = ocd
        self.uid = uid
        self.corrupt_saves = []        # one bool per save, in order
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        if args[0] == 'save':
            assert args[1:4] == ['--range', '0x10000000', f'0x{0x10000000 + len(self.ocd.flash):08x}'] \
                and args[5:] == ['-t', 'bin'], args
            data = bytes(self.ocd.flash)
            if self.corrupt_saves and self.corrupt_saves.pop(0):
                data = _corrupt(data)
            with open(args[4], 'wb') as f:
                f.write(data)
            return 0, f"Saving file: [==============================]  100%\nWrote {len(data)} bytes to {args[4]}\n"
        if args[:2] == ['info', '-d']:
            if self.uid is None:
                return 0, "Device Information\n type:                   RP2040\n"
            return 0, ("Device Information\n type:                   RP2040\n"
                       " revision:               B2\n flash size:             2048K\n"
                       f" flash id:               0x{self.uid}\n")
        return 1, "ERROR: unknown command"


class FlashGuardTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='flashguard-test-')
        self.ocd = FakeOpenOCD()
        self.pt = FakePicotool(self.ocd)
        self.asked = []
        self.logs = []
        self.state_path = os.path.join(self.tmp, 'state', 'flash_state.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def guard(self, **kw):
        args = dict(run_picotool=self.pt, ask=self.asked.append, log=self.logs.append,
                    boot_check=lambda: (True, 'USB serial port appeared'),
                    backup_dir=os.path.join(self.tmp, 'backups'),
                    cache_dir=os.path.join(self.tmp, 'cache'),
                    work_dir=os.path.join(self.tmp, 'work'),
                    state_path=self.state_path, expected_size=SIZE)
        args.update(kw)
        return FlashGuard(self.ocd.monitor, **args)

    def state(self):
        with open(self.state_path) as f:
            return json.load(f)

    # -- happy path --------------------------------------------------------

    def test_backup_write_restore(self):
        original = bytes(self.ocd.flash)
        g = self.guard()
        b = g.backup()
        self.assertEqual(b.unique_id, UID)
        self.assertEqual(b.sha256, fg.sha256_bytes(original))
        self.assertIn(UID, os.path.basename(b.bin_path))
        for p in (b.bin_path, b.cache_bin_path):
            self.assertEqual(_rd(p), original)
        for p in (b.sha256_path, b.cache_sha256_path):
            self.assertTrue(_rd(p, 'r').startswith(b.sha256))
        for p in (b.uf2_path, b.cache_uf2_path):
            self.assertEqual(fg.decode_uf2(_rd(p)), original)
        self.assertEqual(self.state()['state'], 'backed-up')
        self.assertEqual(len(self.asked), 2)            # BOOTSEL, then plug back in
        fg.require_no_pending_restore(self.state_path)  # backed up, nothing written: fine

        g.mark_written()
        self.assertEqual(self.state()['state'], 'written')
        with self.assertRaises(FlashGuardError):
            fg.require_no_pending_restore(self.state_path)
        self.ocd.flash[:] = os.urandom(SIZE)            # the campaign writes the flash

        r = g.restore(board_id=UID)
        self.assertEqual(bytes(self.ocd.flash), original)
        self.assertEqual(r.readback_sha256, r.backup_sha256)
        self.assertTrue(r.boot_ok)
        st = self.state()
        self.assertEqual(st['state'], 'restored')
        self.assertTrue(st['boot_check'])
        self.assertEqual(st['readback_sha256'], b.sha256)
        text = '\n'.join(self.logs)
        self.assertIn(f"backup    SHA-256: {b.sha256}", text)
        self.assertIn(f"read-back SHA-256: {b.sha256}", text)
        fg.require_no_pending_restore(self.state_path)
        self.assertTrue(any(c.startswith('verify_image ') for c in self.ocd.commands))
        self.assertEqual(self.ocd.commands[-1], 'reset run')
        # core 1 pulsed into reset (SET, read back, CLR, read back) just before it
        self.assertEqual(self.ocd.commands[-5:-1], ['mww 0x40012004 0x00010000', 'mdw 0x40010004',
                                                    'mww 0x40013004 0x00010000', 'mdw 0x40010004'])
        self.assertEqual([c for c in self.ocd.commands if c.startswith('mww')],
                         ['mww 0x40012004 0x00010000', 'mww 0x40013004 0x00010000'])
        self.assertEqual(self.ocd.commands[-2], 'mdw 0x40010004')
        self.assertEqual(self.ocd.psm_frce_off, 0)
        self.assertNotIn('core 1 could not', st['boot_check_detail'] or '')
        self.assertFalse(g.status()['pending_restore'])

    def test_restore_reads_id_with_picotool_when_not_given(self):
        g = self.guard()
        g.backup()
        g.mark_written()
        n = len(self.asked)
        g.restore()
        self.assertEqual(len(self.asked), n + 1)        # asked for BOOTSEL
        self.assertEqual(self.state()['state'], 'restored')

    def test_boot_check_failure_still_restored(self):
        g = self.guard(boot_check=lambda: (False, 'no port'))
        g.backup()
        g.mark_written()
        r = g.restore(board_id=UID)
        self.assertFalse(r.boot_ok)
        st = self.state()
        self.assertEqual(st['state'], 'restored')
        self.assertFalse(st['boot_check'])
        fg.require_no_pending_restore(self.state_path)

    # -- A/B comparison ----------------------------------------------------

    def test_ab_mismatch_once_then_match(self):
        self.pt.corrupt_saves = [True, False]
        g = self.guard()
        b = g.backup()
        self.assertEqual(b.sha256, fg.sha256_bytes(bytes(self.ocd.flash)))
        self.assertEqual(len([c for c in self.pt.calls if c[0] == 'save']), 2)
        self.assertEqual(len([c for c in self.ocd.commands if 'read_bank' in c]), 2)

    def test_ab_persistent_mismatch_stops(self):
        original = bytes(self.ocd.flash)
        self.pt.corrupt_saves = [True, True]
        g = self.guard()
        with self.assertRaises(FlashGuardError) as cm:
            g.backup()
        self.assertIn('nothing has been written', str(cm.exception))
        self.assertEqual(bytes(self.ocd.flash), original)
        self.assertEqual(self.ocd.writes(), [])
        self.assertFalse(os.path.exists(self.state_path))
        with self.assertRaises(FlashGuardError):
            g.mark_written()

    def test_swd_read_corrupt_once(self):
        self.ocd.corrupt_reads = 1
        b = self.guard().backup()
        self.assertEqual(b.sha256, fg.sha256_bytes(bytes(self.ocd.flash)))

    # -- unique ID ---------------------------------------------------------

    def test_unique_id_mismatch_refused(self):
        g = self.guard()
        g.backup()
        g.mark_written()
        campaign = os.urandom(SIZE)
        self.ocd.flash[:] = campaign
        with self.assertRaises(FlashGuardError) as cm:
            g.restore(board_id='0x0123456789ABCDEF')
        self.assertIn('another board', str(cm.exception))
        self.assertEqual(self.ocd.writes(), [])
        self.assertEqual(bytes(self.ocd.flash), campaign)
        self.assertEqual(self.state()['state'], 'written')

    def test_no_unique_id_stops_backup(self):
        self.pt.uid = None
        with self.assertRaises(FlashGuardError) as cm:
            self.guard().backup()
        self.assertIn('unique ID', str(cm.exception))
        self.assertFalse(os.path.exists(self.state_path))

    def test_parse_flash_id(self):
        self.assertEqual(fg.parse_flash_id(" flash id:   0xe6614c311b4d8f2a\n"), UID)
        self.assertEqual(fg.parse_flash_id("Flash ID: E6614C311B4D8F2A"), UID)
        self.assertIsNone(fg.parse_flash_id("type: RP2040\n"))
        with self.assertRaises(FlashGuardError):
            fg.parse_flash_id("flash id: 0x1111111111111111\nflash id: 0x2222222222222222\n")

    # -- unique ID over SWD (flash_id.py) -----------------------------------

    def test_swd_id_when_picotool_reports_none(self):
        """2026-11-17: the first 4 KB erased, picotool printed only type/revision."""
        self.pt.uid = None
        reads = []
        g = self.guard(read_unique_id=lambda: reads.append(1) or UID)
        b = g.backup()
        self.assertEqual(b.unique_id, UID)
        self.assertEqual(self.state()['unique_id'], UID)
        self.assertEqual(len(reads), 1)
        self.assertIn('using the ID read over SWD', '\n'.join(self.logs))
        # read before backup A: identify's probe, then the ID, then read_bank
        first_read = next(i for i, c in enumerate(self.ocd.commands) if c.startswith('flash read_bank'))
        self.assertGreater(first_read, self.ocd.commands.index('flash probe 0'))

    def test_swd_id_cross_checked_with_picotool(self):
        g = self.guard(read_unique_id=lambda: UID)
        self.assertEqual(g.backup().unique_id, UID)
        self.assertIn(f'picotool reports the same flash ID ({UID})', '\n'.join(self.logs))

    def test_swd_id_byte_reversed_picotool_matches(self):
        self.pt.uid = bytes.fromhex(UID)[::-1].hex().upper()
        self.assertEqual(self.guard(read_unique_id=lambda: UID).backup().unique_id, UID)

    def test_swd_and_picotool_disagree_refused(self):
        g = self.guard(read_unique_id=lambda: '0123456789ABCDEF')
        with self.assertRaisesRegex(FlashGuardError, 'different'):
            g.backup()
        self.assertFalse(os.path.exists(self.state_path))
        self.assertEqual(self.ocd.writes(), [])

    def test_swd_read_failure_stops_before_backup(self):
        def broken():
            raise RuntimeError("the ID routine did not finish (timeout)")
        with self.assertRaisesRegex(FlashGuardError, 'debug wires failed.*timeout'):
            self.guard(read_unique_id=broken).backup()
        self.assertFalse(any(c.startswith('flash read_bank') for c in self.ocd.commands))
        self.assertEqual(self.asked, [])                   # never got to BOOTSEL
        self.assertFalse(os.path.exists(self.state_path))

    def test_restore_uses_swd_id_without_bootsel(self):
        self.pt.uid = None
        g = self.guard(read_unique_id=lambda: UID)
        g.backup()
        g.mark_written()
        self.ocd.flash[:] = os.urandom(SIZE)
        n = len(self.asked)
        g.restore()
        self.assertEqual(len(self.asked), n)               # no BOOTSEL replug
        self.assertEqual(self.state()['state'], 'restored')

    def test_restore_refuses_other_board_by_swd_id(self):
        uid = {'v': UID}
        g = self.guard(read_unique_id=lambda: uid['v'])
        g.backup()
        g.mark_written()
        uid['v'] = '0123456789ABCDEF'
        with self.assertRaisesRegex(FlashGuardError, 'another board'):
            g.restore()
        self.assertEqual(self.ocd.writes(), [])

    # -- reads through XIP (2026-11-17: zeros after the reconnect's reset halt)

    def _xip_off_after_reset(self):
        self.ocd.xip_on = False
        prepares = []

        def prepare():
            prepares.append(1)
            self.ocd.xip_on = True
        reconnect = lambda: setattr(self.ocd, 'xip_on', False)     # noqa: E731
        return prepares, prepare, reconnect

    def test_zero_read_is_a_failed_read(self):
        """Today's case without the fix: refused as a failed read, not 'changed'."""
        g = self.guard(read_unique_id=lambda: UID)
        g.backup()
        self.ocd.xip_on = False                       # the reconnect's reset halt
        with self.assertRaisesRegex(FlashGuardError, 'only zeros'):
            g.mark_written()
        self.assertEqual(self.state()['state'], 'backed-up')
        self.assertEqual(self.ocd.writes(), [])

    def test_zero_backup_refused(self):
        self.ocd.xip_on = False
        with self.assertRaisesRegex(FlashGuardError, 'only zeros'):
            self.guard(read_unique_id=lambda: UID).backup()
        self.assertFalse(os.path.exists(self.state_path))

    def test_prepare_read_before_every_read_and_verify(self):
        prepares, prepare, reconnect = self._xip_off_after_reset()
        original = bytes(self.ocd.flash)
        g = self.guard(read_unique_id=lambda: UID, prepare_read=prepare, reconnect=reconnect)
        b = g.backup()
        self.assertEqual(b.sha256, fg.sha256_bytes(original))
        g.mark_written()                               # after the reconnect: XIP set up again
        self.ocd.flash[:] = os.urandom(SIZE)
        reconnect()
        r = g.restore()
        self.assertEqual(bytes(self.ocd.flash), original)
        self.assertEqual(r.readback_sha256, b.sha256)
        reads = len([c for c in self.ocd.commands if c.startswith('flash read_bank')])
        verifies = len([c for c in self.ocd.commands if c.startswith('verify_image')])
        self.assertEqual(len(prepares), reads + verifies)

    def test_prepare_read_failure(self):
        def broken():
            raise RuntimeError("turning XIP on did not finish (timeout)")
        with self.assertRaisesRegex(FlashGuardError, 'reading over SWD.*timeout'):
            self.guard(read_unique_id=lambda: UID, prepare_read=broken).backup()
        self.assertFalse(any(c.startswith('flash read_bank') for c in self.ocd.commands))

    def test_same_flash_id(self):
        self.assertTrue(fg.same_flash_id(UID, '0x' + UID.lower()))
        self.assertTrue(fg.same_flash_id(UID, bytes.fromhex(UID)[::-1].hex()))
        self.assertFalse(fg.same_flash_id(UID, '0123456789ABCDEF'))
        self.assertFalse(fg.same_flash_id(UID, None))

    def test_from_target_reads_id_over_swd(self):
        class T:
            reconnect = None
            rsp = None
        g = FlashGuard.from_target(T())
        self.assertTrue(callable(g.read_unique_id))
        self.assertTrue(callable(g.prepare_read))
        mine = lambda: UID                                  # noqa: E731
        self.assertIs(FlashGuard.from_target(T(), read_unique_id=mine).read_unique_id, mine)

    # -- restore failures --------------------------------------------------

    def test_verify_failure(self):
        g = self.guard()
        g.backup()
        g.mark_written()
        self.ocd.fail_verify = True
        with self.assertRaises(FlashGuardError) as cm:
            g.restore(board_id=UID)
        self.assertIn('verify_image', str(cm.exception))
        self.assertEqual(self.state()['state'], 'written')

    def test_bad_write_caught_by_verify(self):
        g = self.guard()
        g.backup()
        g.mark_written()
        self.ocd.corrupt_writes = True
        with self.assertRaises(FlashGuardError):
            g.restore(board_id=UID)
        self.assertEqual(self.state()['state'], 'written')

    def test_readback_mismatch(self):
        g = self.guard()
        g.backup()
        g.mark_written()
        self.ocd.corrupt_reads = 1
        with self.assertRaises(FlashGuardError) as cm:
            g.restore(board_id=UID)
        self.assertIn('does not match the backup', str(cm.exception))
        self.assertEqual(self.state()['state'], 'written')
        with self.assertRaises(FlashGuardError):
            fg.require_no_pending_restore(self.state_path)
        self.assertNotIn('reset run', self.ocd.commands)
        # a second attempt succeeds
        g.restore(board_id=UID)
        self.assertEqual(self.state()['state'], 'restored')

    def test_restore_uses_good_copy(self):
        original = bytes(self.ocd.flash)
        g = self.guard()
        b = g.backup()
        g.mark_written()
        with open(b.bin_path, 'r+b') as f:            # damage the main copy
            f.seek(10)
            f.write(b'\x00\x00\x00')
        self.ocd.flash[:] = os.urandom(SIZE)
        r = g.restore(board_id=UID)
        self.assertEqual(r.image_used, b.cache_bin_path)
        self.assertEqual(bytes(self.ocd.flash), original)

    def test_restore_from_uf2_when_both_bins_bad(self):
        original = bytes(self.ocd.flash)
        g = self.guard()
        b = g.backup()
        g.mark_written()
        for p in (b.bin_path, b.cache_bin_path):
            with open(p, 'r+b') as f:
                f.write(b'XX')
        self.ocd.flash[:] = os.urandom(SIZE)
        g.restore(board_id=UID)
        self.assertEqual(bytes(self.ocd.flash), original)

    # -- UF2 ---------------------------------------------------------------

    def test_uf2_roundtrip_and_corruption(self):
        data = os.urandom(4096)
        blob = fg.make_uf2(data)
        self.assertEqual(len(blob), 16 * 512)
        self.assertEqual(fg.decode_uf2(blob), data)
        import struct
        m0, m1, flags, addr, size, n, total, fam = struct.unpack_from('<8I', blob, 512)
        self.assertEqual((addr, size, n, total, fam), (0x10000100, 256, 1, 16, 0xE48BFF56))
        bad = bytearray(blob)
        bad[512 * 3 + 40] ^= 0xFF                      # payload byte: decodes, but differs
        self.assertNotEqual(fg.decode_uf2(bytes(bad)), data)
        p = os.path.join(self.tmp, 'x.uf2')
        with open(p, 'wb') as f:
            f.write(bytes(bad))
        with self.assertRaises(FlashGuardError):
            fg.check_uf2(p, data)
        bad = bytearray(blob)
        bad[512 * 2] ^= 0xFF                           # magic number
        with self.assertRaises(FlashGuardError):
            fg.decode_uf2(bytes(bad))
        bad = bytearray(blob)
        bad[512 * 2 + 12] ^= 0x01                      # address
        with self.assertRaises(FlashGuardError):
            fg.decode_uf2(bytes(bad))
        with self.assertRaises(FlashGuardError):
            fg.decode_uf2(blob[:-1])

    # -- guard -------------------------------------------------------------

    def test_require_no_pending_restore(self):
        fg.require_no_pending_restore(self.state_path)           # no state file
        g = self.guard()
        g.backup()
        fg.require_no_pending_restore(self.state_path)           # backed up only
        g.mark_written()
        with self.assertRaises(FlashGuardError) as cm:
            fg.require_no_pending_restore(self.state_path)
        self.assertIn('not', str(cm.exception))
        with self.assertRaises(FlashGuardError):                 # no new backup over a pending one
            g.backup()
        g.restore(board_id=UID)
        fg.require_no_pending_restore(self.state_path)

    def test_unreadable_state_blocks(self):
        os.makedirs(os.path.dirname(self.state_path))
        with open(self.state_path, 'w') as f:
            f.write('{not json')
        with self.assertRaises(FlashGuardError):
            fg.require_no_pending_restore(self.state_path)

    def test_mark_written_detects_changed_flash(self):
        g = self.guard()
        g.backup()
        self.ocd.flash[0] ^= 0xFF                      # someone reflashed after the backup
        with self.assertRaises(FlashGuardError):
            g.mark_written()
        self.assertEqual(self.state()['state'], 'backed-up')

    # -- identify / tools --------------------------------------------------

    def test_wrong_size_refused(self):
        self.ocd.reported_size = 0x400000
        g = self.guard(expected_size=fg.FLASH_SIZE)
        with self.assertRaises(FlashGuardError) as cm:
            g.identify()
        self.assertIn('not the expected', str(cm.exception))
        self.ocd.reported_size = fg.FLASH_SIZE
        self.assertEqual(g.identify(), fg.FLASH_SIZE)

    def test_probe_failure_stops_before_backup(self):
        self.ocd.fail_probe = True
        with self.assertRaises(FlashGuardError):
            self.guard().backup()
        self.assertFalse(any('read_bank' in c for c in self.ocd.commands))
        self.assertEqual(self.pt.calls, [])
        self.assertEqual(self.asked, [])
        self.assertFalse(os.path.exists(self.state_path))

    def test_picotool_missing(self):
        with mock.patch.object(fg.shutil, 'which', return_value=None):
            with self.assertRaises(FlashGuardError) as cm:
                fg.default_run_picotool(['info', '-d'])
            self.assertIn('picotool was not found', str(cm.exception))
            g = self.guard(run_picotool=None)
            with self.assertRaises(FlashGuardError) as cm:
                g.backup()
            self.assertIn('picotool was not found', str(cm.exception))
        self.assertEqual(self.ocd.writes(), [])
        self.assertFalse(os.path.exists(self.state_path))

    def test_monitor_exception_is_flashguard_error(self):
        def boom(cmd, timeout):
            raise OSError('connection reset')
        g = FlashGuard(boom, run_picotool=self.pt, ask=self.asked.append, log=self.logs.append,
                       state_path=self.state_path, expected_size=SIZE,
                       backup_dir=self.tmp, cache_dir=self.tmp, work_dir=self.tmp)
        with self.assertRaises(FlashGuardError):
            g.identify()

    def test_tcl_path(self):
        self.assertEqual(fg.tcl_path('/a b/c.bin'), '{/a b/c.bin}')
        with self.assertRaises(FlashGuardError):
            fg.tcl_path('/a{b}/c.bin')


if __name__ == '__main__':
    unittest.main()
