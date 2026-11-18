"""Flash unique ID over SWD (picosim/discover/flash_id.py).

The routine runs in Unicorn against a model of the parts it touches: a boot
ROM whose function table points at stubs (call order recorded), IO_QSPI's
chip-select override, and the SSI with a flash behind it that answers
0x4B (Read Unique ID).  Nothing here touches real hardware."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from picosim.discover import flash_id as FI

try:
    import unicorn as U
    from unicorn import arm_const as A
except ImportError:                                  # pragma: no cover
    U = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ID = bytes.fromhex('E6614C311B4D8F2A')
SS_CTRL = 0x4001800C
SSI = 0x18000000
STUBS = {'IF': 0x200, 'EX': 0x210, 'FC': 0x220, 'CX': 0x230}


def fake_rom():
    rom = bytearray(0x4000)
    rom[0x10:0x13] = FI.ROM_MAGIC
    struct.pack_into('<HHH', rom, 0x14, 0x80, 0x100, 0x300)     # func table, data table, lookup
    t = 0x80
    for name in ('WV', 'IF', 'EX', 'FC', 'CX'):
        addr = STUBS.get(name, 0x240) | 1
        struct.pack_into('<HH', rom, t, ord(name[0]) | ord(name[1]) << 8, addr)
        t += 4
    for a in list(STUBS.values()) + [0x240]:
        struct.pack_into('<H', rom, a, 0x4770)                   # bx lr
    return bytes(rom)


class Chip:
    """Unicorn core + ROM stubs + IO_QSPI SS override + SSI + flash."""

    def __init__(self, answers=True, uid=ID, rom=None):
        self.uc = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_THUMB | U.UC_MODE_MCLASS)
        self.uc.mem_map(0, 0x4000)
        self.uc.mem_write(0, rom or fake_rom())
        self.uc.mem_map(0x20000000, 0x42000)
        self.answers, self.uid = answers, uid
        self.ss_ctrl = 0                       # OUTOVER = 0 (normal)
        self.calls = []                        # ROM stubs, in order, with CS state
        self.rx = []
        self.sent = []                         # bytes clocked while CS was low
        self.cs_low_log = []
        self.transfers = 0
        self.uid_for = None                    # transfer number -> ID (for a flaky flash)
        self.uc.mmio_map(SS_CTRL & ~0xFFF, 0x1000, self._io_r, None, self._io_w, None)
        self.uc.mmio_map(SSI, 0x1000, self._ssi_r, None, self._ssi_w, None)
        self.uc.hook_add(U.UC_HOOK_CODE, self._code)
        self.uc.hook_add(U.UC_HOOK_INTR, self._intr)
        self.stopped_at_bkpt = False

    def cs_low(self):
        return (self.ss_ctrl >> 8) & 3 == 2

    def _io_r(self, uc, off, size, data):
        return self.ss_ctrl if off == SS_CTRL & 0xFFF else 0

    def _io_w(self, uc, off, size, value, data):
        if off == SS_CTRL & 0xFFF:
            was = self.cs_low()
            self.ss_ctrl = value
            self.cs_low_log.append(self.cs_low())
            if self.cs_low() and not was:              # a new command starts
                self.transfers += 1
                self.sent = []
                if self.uid_for:
                    self.uid = self.uid_for(self.transfers)

    def _ssi_r(self, uc, off, size, data):
        if off == 0x28:                        # SR: TFNF always, RFNE when data waits
            return 0x2 | (0x8 if self.rx else 0)
        if off == 0x60:
            return self.rx.pop(0) if self.rx else 0
        return 0

    def _ssi_w(self, uc, off, size, value, data):
        if off != 0x60 or not self.cs_low():
            return
        i = len(self.sent)
        self.sent.append(value & 0xFF)
        if not self.answers:
            return
        if self.sent[0] == 0x4B and 5 <= i < 13:
            self.rx.append(self.uid[i - 5])
        else:
            self.rx.append(0x00 if i else 0xFF)

    def _code(self, uc, addr, size, data):
        for name, a in STUBS.items():
            if addr == a:
                self.calls.append((name, self.cs_low()))

    def _intr(self, uc, intno, data):
        self.stopped_at_bkpt = True
        uc.emu_stop()

    # the rsp/target surface flash_id uses
    def read_mem(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))

    def write_mem(self, addr, data):
        self.uc.mem_write(addr, bytes(data))


class FakeTarget:
    xpsr_n = 16

    def __init__(self, chip):
        self.chip = chip
        self.rsp = self
        self.resets = 0
        self.regs = {}

    def reset_halt(self):
        self.resets += 1

    def read_mem(self, addr, n):
        return self.chip.read_mem(addr, n)

    def write_mem(self, addr, data):
        self.chip.write_mem(addr, data)

    def write_reg(self, n, v):
        self.regs[n] = v

    def run(self, timeout):
        uc = self.chip.uc
        names = {0: A.UC_ARM_REG_R0, 4: A.UC_ARM_REG_R4, 5: A.UC_ARM_REG_R5, 6: A.UC_ARM_REG_R6,
                 7: A.UC_ARM_REG_R7, 13: A.UC_ARM_REG_SP}
        for n, v in self.regs.items():
            if n in names:
                uc.reg_write(names[n], v)
        self.chip.stopped_at_bkpt = False
        uc.emu_start(self.regs[15] | 1, 0xFFFFFFFF, count=5_000_000)
        return 'halted' if self.chip.stopped_at_bkpt else 'timeout'

    def read_core_regs(self):
        uc = self.chip.uc
        regs = [uc.reg_read(getattr(A, f'UC_ARM_REG_R{i}')) for i in range(13)]
        return regs + [uc.reg_read(A.UC_ARM_REG_SP), uc.reg_read(A.UC_ARM_REG_LR),
                       uc.reg_read(A.UC_ARM_REG_PC)]


class SourceTest(unittest.TestCase):

    @unittest.skipUnless(shutil.which('arm-none-eabi-as') and shutil.which('arm-none-eabi-objcopy'),
                         "no ARM toolchain")
    def test_code_is_the_source_assembled(self):
        d = tempfile.mkdtemp(prefix='flash-id-')
        try:
            s, o, b = (os.path.join(d, n) for n in ('fid.s', 'fid.o', 'fid.bin'))
            with open(s, 'w') as f:
                f.write(FI.SOURCE)
            subprocess.run(['arm-none-eabi-as', '-o', o, s], check=True)
            subprocess.run(['arm-none-eabi-objcopy', '-O', 'binary', o, b], check=True)
            with open(b, 'rb') as f:
                self.assertEqual(f.read(), FI.CODE)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(shutil.which('arm-none-eabi-as') and shutil.which('arm-none-eabi-objcopy'),
                         "no ARM toolchain")
    def test_xip_code_is_the_source_assembled(self):
        d = tempfile.mkdtemp(prefix='flash-id-')
        try:
            s, o, b = (os.path.join(d, n) for n in ('x.s', 'x.o', 'x.bin'))
            with open(s, 'w') as f:
                f.write("        .syntax unified\n        .cpu cortex-m0plus\n        .thumb\n" + FI.XIP_SOURCE)
            subprocess.run(['arm-none-eabi-as', '-o', o, s], check=True)
            subprocess.run(['arm-none-eabi-objcopy', '-O', 'binary', o, b], check=True)
            with open(b, 'rb') as f:
                self.assertEqual(f.read(), FI.XIP_CODE)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_bkpt_offset(self):
        self.assertEqual(FI.CODE[FI.BKPT_AT:FI.BKPT_AT + 2], b'\x4b\xbe')


class RomTableTest(unittest.TestCase):

    def test_measured_rom(self):
        """The RP2040 B2 ROM read from the chip (hwfacts/rom.bin)."""
        with open(os.path.join(ROOT, 'hwfacts', 'rom.bin'), 'rb') as f:
            rom = f.read()
        funcs = FI.rom_functions(lambda a, n: rom[a:a + n])
        self.assertEqual(funcs, {'IF': 0x2491, 'EX': 0x23E5, 'FC': 0x2351, 'CX': 0x2321})

    def test_not_an_rp2040(self):
        with self.assertRaises(FI.FlashIdError):
            FI.rom_functions(lambda a, n: b'\0' * n)

    def test_missing_function(self):
        rom = bytearray(fake_rom())
        struct.pack_into('<H', rom, 0x80 + 4 * 4, 0)          # table ends before CX
        with self.assertRaisesRegex(FI.FlashIdError, 'CX'):
            FI.rom_functions(lambda a, n: bytes(rom[a:a + n]))


class ReplyTest(unittest.TestCase):

    def test_id_bytes_5_to_12(self):
        self.assertEqual(FI.id_from_reply(b'\xff' + b'\0' * 4 + ID), 'E6614C311B4D8F2A')

    def test_no_answer_refused(self):
        for fill in (b'\0', b'\xff'):
            with self.assertRaises(FI.FlashIdError):
                FI.id_from_reply(b'\xff' * 5 + fill * 8)

    def test_short_reply_refused(self):
        with self.assertRaises(FI.FlashIdError):
            FI.id_from_reply(b'\0' * 12)


@unittest.skipUnless(U, "unicorn not installed")
class RoutineTest(unittest.TestCase):

    def test_reads_the_id(self):
        chip = Chip()
        t = FakeTarget(chip)
        self.assertEqual(FI.read_unique_id(t), ID.hex().upper())
        self.assertEqual(t.resets, 1)
        # each of the two reads: IF, EX with CS released, the transfer, then FC, CX
        self.assertEqual(chip.calls, [('IF', False), ('EX', False), ('FC', False), ('CX', False)] * 2)
        self.assertEqual(chip.sent[:13], [0x4B] + [0] * 12)
        self.assertEqual(chip.cs_low_log, [True, False] * 2)   # low for the command, high after

    def test_ends_with_chip_select_high_and_no_overrun(self):
        chip = Chip()
        FI.read_unique_id(FakeTarget(chip))
        self.assertFalse(chip.cs_low())
        self.assertEqual(chip.rx, [])                           # all 13 bytes taken

    def test_no_answer_times_out_cleanly(self):
        chip = Chip(answers=False)
        with self.assertRaisesRegex(FI.FlashIdError, 'did not answer'):
            FI.read_unique_id(FakeTarget(chip))
        self.assertFalse(chip.cs_low())                         # still released
        self.assertEqual([c[0] for c in chip.calls], ['IF', 'EX', 'FC', 'CX'])   # XIP back on

    def test_blank_answer_refused(self):
        with self.assertRaisesRegex(FI.FlashIdError, 'all 0xFF'):
            FI.read_unique_id(FakeTarget(Chip(uid=b'\xff' * 8)))

    def test_reads_must_agree(self):
        chip = Chip()
        chip.uid_for = lambda n: ID if n == 1 else bytes.fromhex('1111111111111111')
        with self.assertRaisesRegex(FI.FlashIdError, 'disagree'):
            FI.read_unique_id(FakeTarget(chip))

    def test_enable_xip(self):
        chip = Chip()
        t = FakeTarget(chip)
        FI.enable_xip(t)
        self.assertEqual(t.resets, 1)
        self.assertEqual([c[0] for c in chip.calls], ['IF', 'EX', 'FC', 'CX'])
        self.assertEqual(chip.sent, [])                         # no flash command sent
        self.assertFalse(chip.cs_low())

    def test_writes_only_sram_from_the_debugger(self):
        chip = Chip()
        t = FakeTarget(chip)
        writes = []
        real = chip.write_mem
        chip.write_mem = lambda a, d: (writes.append(a), real(a, d))
        FI.read_unique_id(t)
        self.assertTrue(writes and all(0x20000000 <= a < 0x20042000 for a in writes))


if __name__ == '__main__':
    unittest.main()
