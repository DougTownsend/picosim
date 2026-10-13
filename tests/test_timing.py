"""
Timing-model tests.

1. Every Table 81 row picosim implements gives the RP2040 datasheet's cycle
   count (zero wait states), including the "2 or 1" load/store rule and the
   "1 or 2" conditional-branch rule.
2. The multi-cycle machine's totals from the profile equal the core's own
   cycle counter, and the atomic step() and the clocked step_cycle() paths
   record identical profiles.
3. Pipeline schedules are consistent: executions never overlap, every
   instruction spends depth-1 cycles in the earlier stages, and the total
   equals the sum of the per-instruction counts.
"""

import os
import unittest

from picosim._picosim_core import CPUCore
from picosim import timing
from test_cycle_core import Harness, PROGRAMS, MAX_INSNS
from picosim.loader import LoadError


def run_one(hw1, hw2=0, regs=None, flags=None):
    """Execute one instruction at 0x100 on a scratch core; return its record."""
    c = CPUCore()
    c.write16(0x100, hw1)
    c.write16(0x102, hw2)
    for r in range(8):
        c.set_reg(r, 0x400)
    c.set_reg(14, 0x201)
    c.sp = 0x800
    for r, v in (regs or {}).items():
        c.set_reg(r, v)
    for f, v in (flags or {}).items():
        setattr(c, f, v)
    c.pc = 0x100
    c.step()
    return c.recent(1)[0]


# (name, hw1, hw2, regs, flags, Table 81 cycles)
TABLE81 = [
    ('MOVS Rd, #<imm>', 0x2001, 0, None, None, 1),
    ('MOVS Rd, Rm (LSLS #0)', 0x0008, 0, None, None, 1),
    ('MOV Rd, Rm', 0x4680, 0, None, None, 1),
    ('MOV PC, Rm', 0x4687, 0, {0: 0x201}, None, 2),
    ('ADDS Rd, Rn, #<imm>', 0x1C42, 0, None, None, 1),
    ('ADDS Rd, Rn, Rm', 0x1842, 0, None, None, 1),
    ('ADD Rd, Rd, Rm', 0x4440, 0, None, None, 1),
    ('ADD PC, PC, Rm', 0x4487, 0, {0: 0}, None, 2),
    ('ADDS Rd, Rd, #<imm>', 0x3001, 0, None, None, 1),
    ('ADCS', 0x4148, 0, None, None, 1),
    ('ADD SP, SP, #<imm>', 0xB002, 0, None, None, 1),
    ('ADD Rd, SP, #<imm>', 0xA801, 0, None, None, 1),
    ('ADR', 0xA001, 0, None, None, 1),
    ('SUBS Rd, Rn, Rm', 0x1A42, 0, None, None, 1),
    ('SBCS', 0x4188, 0, None, None, 1),
    ('SUB SP, SP, #<imm>', 0xB082, 0, None, None, 1),
    ('RSBS', 0x4241, 0, None, None, 1),
    ('MULS', 0x4348, 0, None, None, 1),
    ('CMP Rn, Rm', 0x4288, 0, None, None, 1),
    ('CMN', 0x42C8, 0, None, None, 1),
    ('CMP Rn, #<imm>', 0x2801, 0, None, None, 1),
    ('ANDS', 0x4008, 0, None, None, 1),
    ('EORS', 0x4048, 0, None, None, 1),
    ('ORRS', 0x4308, 0, None, None, 1),
    ('BICS', 0x4388, 0, None, None, 1),
    ('MVNS', 0x43C8, 0, None, None, 1),
    ('TST', 0x4208, 0, None, None, 1),
    ('LSLS imm', 0x00C1, 0, None, None, 1),
    ('LSLS reg', 0x4088, 0, None, None, 1),
    ('LSRS imm', 0x08C1, 0, None, None, 1),
    ('LSRS reg', 0x40C8, 0, None, None, 1),
    ('ASRS imm', 0x10C1, 0, None, None, 1),
    ('ASRS reg', 0x4108, 0, None, None, 1),
    ('RORS', 0x41C8, 0, None, None, 1),
    ('LDR Rd, [Rn, #<imm>]', 0x6842, 0, None, None, 2),
    ('LDRH Rd, [Rn, #<imm>]', 0x8842, 0, None, None, 2),
    ('LDRB Rd, [Rn, #<imm>]', 0x7842, 0, None, None, 2),
    ('LDR Rd, [Rn, Rm]', 0x5842, 0, {1: 0}, None, 2),
    ('LDRSH Rd, [Rn, Rm]', 0x5E42, 0, {1: 0}, None, 2),
    ('LDR Rd, <label>', 0x4801, 0, None, None, 2),
    ('LDR Rd, [SP, #<imm>]', 0x9A01, 0, None, None, 2),
    ('LDM Rn!, {r1, r2}', 0xC806, 0, None, None, 3),
    ('LDM Rn, {r0, r1} (base in list)', 0xC803, 0, None, None, 3),
    ('STR Rd, [Rn, #<imm>]', 0x6042, 0, None, None, 2),
    ('STRB Rd, [Rn, Rm]', 0x5442, 0, {1: 0}, None, 2),
    ('STR Rd, [SP, #<imm>]', 0x9201, 0, None, None, 2),
    ('STM Rn!, {r1, r2}', 0xC006, 0, None, None, 3),
    ('PUSH {r4}', 0xB410, 0, None, None, 2),
    ('PUSH {r4, LR}', 0xB510, 0, None, None, 3),
    ('POP {r4}', 0xBC10, 0, None, None, 2),
    ('POP {r4, PC}', 0xBD10, 0, None, None, 5),
    ('B<cc> not taken', 0xD000, 0, None, {'Z': 0}, 1),
    ('B<cc> taken', 0xD000, 0, None, {'Z': 1}, 2),
    ('B <label>', 0xE000, 0, None, None, 2),
    ('BL <label>', 0xF000, 0xF800, None, None, 3),
    ('BX Rm', 0x4770, 0, None, None, 2),
    ('BLX Rm', 0x4798, 0, {3: 0x201}, None, 2),
    ('SXTH', 0xB201, 0, None, None, 1),
    ('SXTB', 0xB241, 0, None, None, 1),
    ('UXTH', 0xB281, 0, None, None, 1),
    ('UXTB', 0xB2C1, 0, None, None, 1),
    ('REV', 0xBA01, 0, None, None, 1),
    ('REV16', 0xBA41, 0, None, None, 1),
    ('REVSH', 0xBAC1, 0, None, None, 1),
]


class Table81Test(unittest.TestCase):
    def test_rows(self):
        for name, hw1, hw2, regs, flags, want in TABLE81:
            with self.subTest(row=name):
                rec = run_one(hw1, hw2, regs, flags)
                self.assertEqual(timing.cycles('pico2', hw1, hw2, rec), want)

    def test_single_cycle_io_port(self):
        # "2 if to AHB interface or SCS, 1 if to single-cycle I/O port"
        rec = run_one(0x6842, regs={0: 0xD0000000})          # LDR r2, [r0, #4] -> SIO
        self.assertEqual(timing.cycles('pico2', 0x6842, 0, rec), 1)
        rec = run_one(0x6042, regs={0: 0xD0000010})          # STR r2, [r0, #4] -> SIO
        self.assertEqual(timing.cycles('pico2', 0x6042, 0, rec), 1)
        rec = run_one(0x6842, regs={0: 0x40014000})          # APB: Table 81 still says 2
        self.assertEqual(timing.cycles('pico2', 0x6842, 0, rec), 2)
        self.assertEqual(rec['reads'][timing.RG_APB], 1)

    def test_iterative_multiplier(self):
        rec = run_one(0x4348)
        self.assertEqual(timing.cycles('pico2', 0x4348, 0, rec, mul_cycles=32), 32)
        self.assertEqual(timing.cycles('multicycle', 0x4348, 0, rec, mul_cycles=32), 6 + 31)

    def test_untimed(self):
        self.assertIsNone(timing.cycles('pico2', 0xDF02, 0, run_one(0xDF02)))   # SVC
        self.assertIsNone(timing.pico_row(0xBE00)[2])                          # BKPT

    def test_three_stage_branch_penalty(self):
        for hw1, hw2, flags, extra in [(0xD000, 0, {'Z': 1}, 1), (0xD000, 0, {'Z': 0}, 0),
                                       (0xE000, 0, None, 1), (0xF000, 0xF800, None, 1),
                                       (0x1842, 0, None, 0), (0x6842, 0, None, 0)]:
            rec = run_one(hw1, hw2, flags=flags)
            self.assertEqual(timing.cycles('pipe3', hw1, hw2, rec),
                             timing.cycles('pico2', hw1, hw2, rec) + extra, hex(hw1))


class ProfileTest(unittest.TestCase):
    def test_profiles(self):
        for path in PROGRAMS:
            with self.subTest(program=os.path.basename(path)):
                try:
                    fast, slow = Harness(path), Harness(path)
                except LoadError:
                    continue
                for _ in range(MAX_INSNS):
                    if fast.core.halted:
                        break
                    fast.core.step()
                    fast.core.check_halt()
                    while True:
                        info = slow.core.step_cycle()
                        if info['last'] or slow.core.halted:
                            break
                self.assertEqual(fast.core.profile(), slow.core.profile())
                self.assertEqual(fast.core.recent(512), slow.core.recent(512))
                prof = slow.core.profile()
                s = timing.summarize(prof)
                self.assertEqual(s['machines']['multicycle']['cycles'], slow.core.cycles)
                self.assertEqual(s['insns'], slow.core.steps)
                self.assertEqual(sum(s['phases'].values()), slow.core.cycles)

    def test_schedule(self):
        h = Harness(PROGRAMS[0])
        for _ in range(300):
            if h.core.halted:
                break
            h.core.step()
            h.core.check_halt()
        recs = h.core.recent(200)
        for m in ('pico2', 'pipe3'):
            sch = timing.schedule(m, recs)
            busy = set()
            total = 0
            for row in sch['rows']:
                if row['flushed']:
                    self.assertNotIn('E', row['stages'].values())
                    continue
                e = [c for c, s in row['stages'].items() if s == 'E']
                self.assertEqual(len(e), row['cycles'])
                self.assertFalse(busy & set(e))          # one instruction executes per cycle
                busy |= set(e)
                self.assertEqual(sum(1 for s in row['stages'].values() if s != 'E'), sch['depth'] - 1)
                total += row['cycles']
            self.assertEqual(total, sch['total'])
            self.assertEqual(busy, set(range(total)))   # no idle execute cycles
            # starting at t0 shifts every cycle by t0
            later = timing.schedule(m, recs, t0=7)
            self.assertEqual(later['total'], total + 7)
            for a, b in zip(sch['rows'], later['rows']):
                self.assertEqual({c + 7: s for c, s in a['stages'].items()}, b['stages'])
                self.assertEqual(a['start'] + 7, b['start'])


if __name__ == '__main__':
    unittest.main()
