"""The independent ARMv6-M encoding table (picosim/discover/v6m.py): every
16-bit value has exactly one class and one table entry, the proposal's
per-pass counts come out exactly, and safe_inputs keeps every case inside
its windows.  Effective addresses are worked out here from the raw bits, not
with the module's own helpers."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from picosim.discover import v6m

LAY = v6m.DEFAULT_LAYOUT
ROM = LAY['rom']
SCRATCH = LAY['scratch']
PSP = LAY['psp']
SLED = LAY['sled']
SLOT = LAY['slot']


def popcount(x):
    return bin(x).count('1')


def reg(inp, r):
    return inp['regs'][r] if r < 13 else inp['lr']


def mem_range(hw, inp):
    """(window name, lo, hi exclusive, access size) for a memory encoding,
    from the raw bits, or None for forms with no window to check."""
    top5 = hw >> 11
    if top5 == 0b01001:                                   # LDR literal
        ea = ((SLOT + 4) & ~3) + (hw & 0xFF) * 4
        return 'sled', ea, ea + 4, 4
    if hw >> 12 == 0b0101:                                # register offset
        op = (hw >> 9) & 7
        size = {0: 4, 4: 4, 1: 2, 5: 2, 7: 2, 2: 1, 3: 1, 6: 1}[op]
        ea = reg(inp, (hw >> 3) & 7) + reg(inp, (hw >> 6) & 7)
        return 'data', ea, ea + size, size
    if 0b01100 <= top5 <= 0b10001:                        # immediate offset
        size = {0b0110: 4, 0b0111: 1, 0b1000: 2}[hw >> 12]
        ea = reg(inp, (hw >> 3) & 7) + ((hw >> 6) & 31) * size
        return 'data', ea, ea + size, size
    if hw >> 12 == 0b1001:                                # SP-relative
        ea = inp['psp'] + (hw & 0xFF) * 4
        return 'psp', ea, ea + 4, 4
    if hw >> 12 == 0b1100:                                # LDM/STM
        base = reg(inp, (hw >> 8) & 7)
        return 'data', base, base + 4 * popcount(hw & 0xFF), 4
    if hw >> 9 == 0b1011010:                              # PUSH
        n = popcount(hw & 0x1FF)
        return 'psp', inp['psp'] - 4 * n, inp['psp'], 4
    if hw >> 9 == 0b1011110:                              # POP
        n = popcount(hw & 0x1FF)
        return 'psp', inp['psp'], inp['psp'] + 4 * n, 4
    return None


class CoverageTest(unittest.TestCase):
    def test_every_value_one_entry_one_class(self):
        hits = [0] * 0x10000
        for mask, value, name, cls, fields in v6m.ENCODINGS:
            self.assertEqual(value & ~mask, 0, name)
            self.assertIn(cls, v6m.CLASSES16, name)
            for hw in v6m._cube_values(mask, value):
                hits[hw] += 1
        self.assertEqual(set(hits), {1})                  # non-overlapping and complete
        for hw in range(0x10000):
            e = v6m.decode16(hw)
            self.assertEqual(e.cls, v6m.classify(hw))
            self.assertEqual(e.unpredictable, bool(e.reason))

    def test_prefix_counts(self):
        n = sum(v6m.is_prefix32(hw) for hw in range(0x10000))
        self.assertEqual(n, 6144)
        self.assertEqual(0x10000 - n, 59392)
        self.assertEqual(v6m.count_by_class()['prefix32'], 6144)

    def test_group_counts_from_proposal(self):
        g = v6m.count_by_group()
        self.assertEqual(g['data'], 23040)
        self.assertEqual(g['memory'], 27648)
        self.assertEqual(g['control'], 8704)
        self.assertEqual(g['prefix32'], 6144)
        # the proposal's breakdown of the data group
        data = [hw for hw in range(0x10000) if v6m.group(hw) == 'data']
        self.assertEqual(sum(hw < 0x4000 for hw in data), 16384)
        self.assertEqual(sum(0x4000 <= hw < 0x4400 for hw in data), 1024)
        self.assertEqual(sum(0x4400 <= hw < 0x4700 for hw in data), 768)
        self.assertEqual(sum(0xA000 <= hw < 0xB000 for hw in data), 4096)
        self.assertEqual(sum(0xB000 <= hw < 0xC000 for hw in data), 768)

    def test_class_counts(self):
        c = v6m.count_by_class()
        self.assertEqual(c['cond-branch'], 14 * 256)
        self.assertEqual(c['branch-imm'], 2048)
        self.assertEqual(c['svc'], 256)
        self.assertEqual(c['bkpt'], 256)
        self.assertEqual(c['udf'], 256)
        self.assertEqual(c['system'], 32)
        self.assertEqual(c['hint'], 16)
        self.assertEqual(c['flag-reading'], 64 + 6 * 64)
        self.assertEqual(c['pc-writing'], 256 + 16 + 16 + 256)
        self.assertEqual(c['sp-writing'], 256 + 16 + 16)
        self.assertEqual(sum(c.values()), 0x10000)
        pages = v6m.count_by_page()
        self.assertEqual(pages[0xDE], {'udf': 256})
        self.assertEqual(sum(sum(p.values()) for p in pages.values()), 0x10000)


class SpotTest(unittest.TestCase):
    def cls(self, hw):
        return v6m.decode16(hw).cls

    def test_16bit(self):
        cases = {0xDE00: 'udf', 0xBE00: 'bkpt', 0xDF00: 'svc', 0xBF30: 'hint',
                 0xBF08: 'undefined', 0xB100: 'undefined', 0xB900: 'undefined',
                 0xB672: 'system', 0xB662: 'system', 0x4687: 'pc-writing',
                 0x4770: 'pc-writing', 0xBD00: 'pc-writing', 0xBC01: 'memory',
                 0x46ED: 'sp-writing', 0x46E8: 'data', 0xE7FE: 'branch-imm',
                 0xD0FE: 'cond-branch', 0xBA80: 'undefined', 0xBA00: 'data',
                 0x0000: 'flag-reading', 0x0040: 'data', 0x4140: 'flag-reading',
                 0x4340: 'data', 0xB000: 'sp-writing', 0xB080: 'sp-writing',
                 0x4485: 'sp-writing', 0x4468: 'data', 0xE800: 'prefix32',
                 0xB650: 'undefined', 0xBF50: 'hint'}
        for hw, cls in cases.items():
            self.assertEqual(self.cls(hw), cls, hex(hw))
        self.assertEqual(v6m.decode16(0xB672).name, 'CPSID i')
        self.assertEqual(v6m.decode16(0x4687).fields['rm'], 0)
        self.assertEqual(v6m.decode16(0x4770).fields['rm'], 14)
        self.assertEqual(v6m.decode16(0x46ED).name, 'MOV SP,Rm')      # MOV SP,SP
        self.assertEqual(v6m.decode16(0x46ED).fields['rm'], 13)
        self.assertEqual(v6m.decode16(0x44C0).fields['rdn'], 8)        # ADD R8,R8

    def test_unpredictable(self):
        for hw in (0x44FF, 0x4500, 0x4578, 0x4587, 0x4701, 0x47F8, 0xB400, 0xBC00,
                   0xC000, 0xC800, 0xB660, 0xB67F, 0xC103):
            self.assertTrue(v6m.decode16(hw).unpredictable, hex(hw))
        for hw in (0x4540, 0x4770, 0x47F0, 0xB500, 0xBD00, 0xB662, 0xC101,
                   0xC901, 0x4687, 0x46FF):
            self.assertFalse(v6m.decode16(hw).unpredictable, hex(hw))

    def test_32bit(self):
        d = v6m.decode32
        bl = d(0xF000, 0xF800)
        self.assertEqual((bl.name, bl.cls, bl.fields['imm32']), ('BL label', 'pc-writing', 0))
        self.assertEqual(d(0xF7FF, 0xFFFE).fields['imm32'], -4)
        mrs = d(0xF3EF, 0x8008)
        self.assertEqual((mrs.cls, mrs.fields), ('system', {'rd': 0, 'sysm': v6m.SYSM['MSP']}))
        msr = d(0xF380, 0x8809)
        self.assertEqual((msr.cls, msr.fields), ('system', {'rn': 0, 'sysm': v6m.SYSM['PSP']}))
        self.assertFalse(msr.unpredictable)
        self.assertTrue(d(0xF380, 0x8804).unpredictable)                # SYSm 4
        self.assertTrue(d(0xF38D, 0x8808).unpredictable)                # Rn = SP
        self.assertEqual(d(0xF3BF, 0x8F5F).cls, 'barrier')
        self.assertEqual(d(0xF3BF, 0x8F5F).name, 'DMB SY')
        self.assertEqual(d(0xF3BF, 0x8F4F).name, 'DSB SY')
        self.assertEqual(d(0xF3BF, 0x8F6F).name, 'ISB SY')
        self.assertEqual(d(0xF3BF, 0x8F2F).cls, 'undefined')            # CLREX (v7)
        self.assertEqual(d(0xF7F0, 0xA000).cls, 'udf')
        self.assertEqual(d(0xF000, 0x8000).cls, 'undefined')
        self.assertEqual(d(0xE800, 0x0000).cls, 'undefined')
        self.assertEqual(d(0xF800, 0xF800).cls, 'undefined')
        self.assertEqual(d(0xF000, 0x7800).cls, 'undefined')            # hw2 bit 15 clear
        self.assertRaises(ValueError, d, 0x4770, 0)
        self.assertEqual(v6m.SYSM['CONTROL'], 20)


class SafeInputsTest(unittest.TestCase):
    def check_keys(self, inp):
        self.assertEqual(len(inp['regs']), 13)
        self.assertTrue(0 <= inp['nzcv'] <= 15)
        self.assertIn(inp['primask'], (0, 1))
        self.assertTrue(0 <= inp['control'] <= 3)

    def test_deterministic(self):
        for hw in (0x1800, 0x6800, 0x4770, 0xB081, 0xC0FF, 0xBF30):
            for p in ('B1', 'B3'):
                a = v6m.safe_inputs(hw, 7, p)
                self.assertEqual(a, v6m.safe_inputs(hw, 7, p))
                self.assertEqual(a, v6m.safe_inputs(v6m.decode16(hw), 7, p))
                self.check_keys(a)
            self.assertNotEqual(v6m.safe_inputs(hw, 7), v6m.safe_inputs(hw, 8))

    def test_memory_in_window(self):
        windows = {'B1': ROM, 'B3': SCRATCH}
        for pass_, (wlo, wsize) in windows.items():
            for hw in range(0x10000):
                if v6m.group(hw) != 'memory':
                    continue
                for unaligned in (False, True):
                    if unaligned and pass_ == 'B1':
                        continue
                    inp = v6m.safe_inputs(hw, 3, pass_, unaligned=unaligned)
                    r = mem_range(hw, inp)
                    if r is None:
                        continue
                    which, lo, hi, size = r
                    win = {'data': (wlo, wsize), 'psp': PSP, 'sled': SLED}[which]
                    self.assertTrue(win[0] <= lo <= hi <= win[0] + win[1],
                                    '%s %04X %x..%x' % (pass_, hw, lo, hi))
                    if which == 'psp':
                        self.assertGreaterEqual(lo, PSP[0] + 32)    # frame room
                    aligned = lo % size == 0
                    rn_is_rm = hw >> 12 == 5 and (hw >> 3) & 7 == (hw >> 6) & 7
                    if which == 'data' and unaligned and size > 1 and not (rn_is_rm and size == 2):
                        self.assertFalse(aligned, hex(hw))
                    elif not unaligned:
                        self.assertTrue(aligned, hex(hw))
                    if hw >> 12 == 5 and not rn_is_rm:
                        self.assertLessEqual(reg(inp, (hw >> 6) & 7), 0x100)

    def test_pc_writing_targets_in_sled(self):
        n_checked = 0
        for hw in range(0x10000):
            if v6m.classify(hw) != 'pc-writing':
                continue
            for thumb in (True, False):
                inp = v6m.safe_inputs(hw, 5, 'B4', thumb_bit=thumb)
                t = inp['target']
                if t is None:
                    continue
                n_checked += 1
                self.assertTrue(SLED[0] <= t < SLED[0] + SLED[1], hex(hw))
                self.assertFalse(SLOT <= t < SLOT + LAY['slot_guard'], hex(hw))
                self.assertEqual(t & 1, 1 if thumb else 0, hex(hw))
                rm = (hw >> 3) & 15
                if hw >> 7 in (0x4700 >> 7, 0x4780 >> 7) or hw & 0xFF87 == 0x4687:
                    self.assertEqual(reg(inp, rm), t, hex(hw))     # BX, BLX, MOV PC
                elif hw & 0xFF87 == 0x4487:                        # ADD PC,Rm
                    self.assertEqual((SLOT + 4 + reg(inp, rm)) & 0xFFFFFFFF, t, hex(hw))
                else:                                              # POP {..,PC}
                    n = popcount(hw & 0x1FF)
                    self.assertEqual(inp['target_addr'], inp['psp'] + 4 * (n - 1))
                    self.assertTrue(PSP[0] <= inp['target_addr'] < PSP[0] + PSP[1] - 3)
        self.assertGreater(n_checked, 2 * 500)
        bl = v6m.safe_inputs((0xF000, 0xF800), 1)
        self.assertIsNone(bl['target'])

    def test_sp_writing_in_range(self):
        lo, hi = PSP[0] + v6m.SP_MARGIN, PSP[0] + PSP[1] - v6m.SP_MARGIN
        uncontrolled = set()
        for hw in range(0x10000):
            if v6m.classify(hw) != 'sp-writing':
                continue
            inp = v6m.safe_inputs(hw, 9, 'B2')
            self.assertTrue(lo <= inp['psp'] <= hi)
            # the new SP, worked out from the raw bits
            if hw >> 8 == 0xB0:
                d = 4 * (hw & 0x7F)
                sp = inp['psp'] + (-d if hw & 0x80 else d)
            else:
                rm = (hw >> 3) & 15
                if rm in (13, 15):
                    sp = inp['psp'] if hw == 0x46ED else None
                elif hw & 0xFF00 == 0x4400:                         # ADD SP,Rm
                    self.assertLessEqual(reg(inp, rm), 0x100)
                    sp = inp['psp'] + reg(inp, rm)
                else:                                               # MOV SP,Rm
                    sp = reg(inp, rm)
            self.assertEqual(inp['sp_result'], sp, hex(hw))
            if sp is None:
                uncontrolled.add(hw)
            else:
                self.assertTrue(lo <= sp <= hi and sp % 4 == 0, hex(hw))
        self.assertEqual(uncontrolled, {0x44ED, 0x44FD, 0x46FD})
        msr = v6m.safe_inputs((0xF383, 0x8809), 1)                  # MSR PSP,R3
        self.assertTrue(lo <= msr['regs'][3] <= hi)

    def test_others_distinct(self):
        other = set(v6m.CLASSES16) - {'memory', 'pc-writing', 'sp-writing'}
        for hw in range(0, 0x10000, 7):
            if v6m.classify(hw) not in other:
                continue
            for p in ('B1', 'B2'):
                regs = v6m.safe_inputs(hw, 11, p)['regs']
                self.assertEqual(len(set(regs)), 13, hex(hw))
                self.assertNotIn(0, regs)
                if p == 'B1':
                    self.assertTrue(all(r < 0x1000 for r in regs))
        self.assertEqual(v6m.safe_inputs(0xBF30, 1)['primask'], 1)       # WFI wakes

    def test_custom_layout(self):
        lay = {'scratch': (0x20030000, 0x200)}
        for hw in (0x6FFF, 0x5800, 0xC8FF, 0x8FC0):
            inp = v6m.safe_inputs(hw, 2, 'B3', layout=lay)
            _, lo, hi, _ = mem_range(hw, inp)
            self.assertTrue(0x20030000 <= lo <= hi <= 0x20030200, hex(hw))


if __name__ == '__main__':
    unittest.main()
