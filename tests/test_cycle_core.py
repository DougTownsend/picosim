"""
Cycle-level core tests.

Run with:  python3 -m unittest discover tests

1. Lockstep equivalence: every test program is run twice, once with the
   atomic step() and once clock-by-clock with step_cycle(); the architectural
   state must match after every instruction.
2. Cycle counts: the textbook's Chapter 7 summary table must hold.
"""

import glob
import os
import unittest

from picosim.loader import load_program, LoadError
from picosim._picosim_core import CPUCore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAMS = sorted(glob.glob(os.path.join(ROOT, 'test_asm_files', '*.s')))
MAX_INSNS = 20000
INPUT = b"hello\rq\x04"


class Harness:
    """A loaded program with scripted console I/O (no real stdin/stdout)."""

    def __init__(self, path):
        self.prog = load_program(path)
        self.core = self.prog.cpu._core
        self.out = []
        self.inp = list(INPUT)
        self.exited = False
        self.core.svc_handler = self._svc

    def _svc(self, num):
        c = self.core
        if num == 1:
            self.exited = True
            c.halted = True
        elif num == 2:
            self.out.append(c.get_reg(0) & 0xFF)
        elif num == 3:
            c.set_reg(0, self.inp.pop(0) if self.inp else 0xFFFFFFFF)
        elif num == 0:
            self.out.extend(c.get_mem_slice(c.get_reg(1) & 0xFFFF, c.get_reg(2)))

    def state(self):
        c = self.core
        return (tuple(c.regs), c.N, c.Z, c.C, c.V, c.halted, c.steps, c.cycles)


class LockstepTest(unittest.TestCase):
    def test_programs_match(self):
        self.assertTrue(PROGRAMS)
        for path in PROGRAMS:
            with self.subTest(program=os.path.basename(path)):
                self._run_lockstep(path)

    def _run_lockstep(self, path):
        try:
            fast, slow = Harness(path), Harness(path)
        except LoadError as e:
            self.skipTest(f"does not assemble: {str(e).splitlines()[-1]}")
        for n in range(MAX_INSNS):
            if fast.core.halted:
                break
            pc = fast.core.pc
            hw1 = fast.core.read16(pc)
            hw2 = fast.core.read16(pc + 2)
            expected = CPUCore.insn_cycle_count(hw1, hw2)

            fast.core.step(); fast.core.check_halt()
            count = 0
            while True:
                info = slow.core.step_cycle()
                count += 1
                self.assertEqual(info['index'], count - 1)
                if info['last'] or slow.core.halted:
                    break
            if not slow.exited:
                self.assertEqual(count, expected,
                                 f"cycle count at 0x{pc:04X}: {slow.prog.asm_map.get(pc)}")
                self.assertEqual(info['total'], expected)
            self.assertEqual(fast.state(), slow.state(),
                             f"state diverged after insn #{n} at 0x{pc:04X}: "
                             f"{fast.prog.asm_map.get(pc)}")
        self.assertEqual(fast.core.get_memory(), slow.core.get_memory())
        self.assertEqual(fast.out, slow.out)


# Hand-assembled encodings for the Chapter 7 summary table.
TEXTBOOK = {
    'ADDS R2,R0,R1': (0x1842, 0, 6),
    'CMP R0,#1':     (0x2801, 0, 6),
    'LDR R2,[R0,#4]': (0x6842, 0, 7),
    'LDRB R2,[R0,#1]': (0x7842, 0, 7),
    'LDRH R2,[R0,#2]': (0x8842, 0, 7),
    'STR R2,[R0,#4]': (0x6042, 0, 7),
    'STRB R2,[R0,#1]': (0x7042, 0, 7),
    'STRH R2,[R0,#2]': (0x8042, 0, 7),
    'LDR R0,[PC,#4]': (0x4801, 0, 7),
    'ADR R0,label':  (0xA001, 0, 6),
    'B label':       (0xE000, 0, 5),
    'BEQ label':     (0xD000, 0, 5),
    'BX LR':         (0x4770, 0, 6),
    'BL label':      (0xF000, 0xF800, 9),
    'SVC #2':        (0xDF02, 0, 5),
}


class CycleCountTest(unittest.TestCase):
    def test_textbook_table(self):
        for name, (hw1, hw2, cycles) in TEXTBOOK.items():
            with self.subTest(insn=name):
                self.assertEqual(CPUCore.insn_cycle_count(hw1, hw2), cycles)

    def test_cycle_trace_shape(self):
        # ADDS R2, R0, R1 at 0x100: walk the six states explicitly.
        c = CPUCore()
        c.write16(0x100, 0x1842)
        c.pc = 0x100
        c.set_reg(0, 3); c.set_reg(1, 5)
        states = []
        while True:
            info = c.step_cycle()
            states.append(info['state'])
            if info['last']:
                break
        self.assertEqual(states, ['FETCH_ADDR', 'FETCH_MEMORY', 'FETCH_IR', 'DECODE',
                                  'FETCH_OPERANDS', 'EXECUTE_COMMIT'])
        self.assertEqual(c.get_reg(2), 8)
        self.assertEqual(info['changes'][0], {'kind': 'reg', 'name': 'R2', 'old': 0, 'new': 8})
        self.assertIn('LD.REG', info['signals'])


def _run(hw, regs=None, carry=0, addr=0x100):
    """Clock one 16-bit instruction at `addr` on a scratch core."""
    c = CPUCore()
    c.write16(addr, hw)
    c.pc = addr
    for r, v in (regs or {}).items():
        c.set_reg(r, v)
    c.C = carry
    while not c.step_cycle()['last']:
        pass
    return c


class ArchitectureTest(unittest.TestCase):
    """Edge cases checked against the ARMv6-M pseudocode (AddWithCarry, Shift_C)."""

    def flags(self, c):
        return (c.N, c.Z, c.C, c.V)

    def test_add_overflow_to_zero(self):
        c = _run(0x1842, {0: 0x80000000, 1: 0x80000000})       # ADDS R2, R0, R1
        self.assertEqual((c.get_reg(2),) + self.flags(c), (0, 0, 1, 1, 1))

    def test_sub_overflow_from_zero(self):
        c = _run(0x1A42, {0: 0, 1: 0x80000000})                 # SUBS R2, R0, R1
        self.assertEqual((c.get_reg(2),) + self.flags(c), (0x80000000, 1, 0, 0, 1))

    def test_sbc_borrow(self):
        c = _run(0x4188, {0: 5, 1: 5}, carry=0)                 # SBCS R0, R1
        self.assertEqual((c.get_reg(0),) + self.flags(c), (0xFFFFFFFF, 1, 0, 0, 0))

    def test_adc_carry_in(self):
        c = _run(0x4148, {0: 0xFFFFFFFF, 1: 0}, carry=1)        # ADCS R0, R1
        self.assertEqual((c.get_reg(0),) + self.flags(c), (0, 0, 1, 1, 0))

    def test_shift_by_zero_register(self):
        c = _run(0x40C8, {0: 0x1234, 1: 0}, carry=1)            # LSRS R0, R1
        self.assertEqual((c.get_reg(0), c.C), (0x1234, 1))
        c = _run(0x4108, {0: 0x80000000, 1: 0}, carry=0)        # ASRS R0, R1
        self.assertEqual((c.get_reg(0), c.C), (0x80000000, 0))

    def test_pc_read_is_address_plus_4(self):
        c = _run(0x4678, addr=0x102)                            # MOV R0, PC
        self.assertEqual(c.get_reg(0), 0x106)
        c = _run(0xA000, addr=0x102)                            # ADR R0, here (aligned)
        self.assertEqual(c.get_reg(0), 0x104)

    def test_link_bus_carries_pc(self):
        c = CPUCore()
        c.write16(0x100, 0xF000)
        c.write16(0x102, 0xF800)                                # BL .+4
        c.pc = 0x100
        while True:
            info = c.step_cycle()
            if info['state'] == 'LINK':
                self.assertEqual(info['bus'], 0x104)           # SET THUMB BIT adds bit 0 after the bus
                self.assertEqual(c.lr, 0x105)
            if info['last']:
                break


# Which load-enable signal(s) allow each kind of stored value to change.
LOADS = {'MAR': ('LD.MAR', 'MAR+4'), 'MDR': ('LD.MDR',), 'IR': ('LD.IR',), 'IR2': ('LD.IR2',),
         'ALU_A': ('LD.ALUA',), 'ALU_B': ('LD.ALUB',), 'PC': ('LD.PC',),
         'SP': ('LD.SP', 'LD.REG'), 'LR': ('LD.LR', 'LD.REG')}
GATES = ('GatePC', 'GateADDR', 'GateALU', 'GateMDR', 'GateVEC')


class FaithfulnessTest(unittest.TestCase):
    """Chapter 7's rules, checked on every cycle of every test program:
    one bus driver at a time, a bus value only when a gate drives it, and no
    stored value changes without its load enable."""

    def test_rules(self):
        for path in PROGRAMS:
            with self.subTest(program=os.path.basename(path)):
                try:
                    h = Harness(path)
                except LoadError:
                    continue
                for _ in range(MAX_INSNS * 4):
                    if h.core.halted:
                        break
                    info = h.core.step_cycle()
                    sig = info['signals']
                    where = f"{info['state']} at 0x{info['insn_addr']:04X} {h.prog.asm_map.get(info['insn_addr'])}"
                    gates = [g for g in GATES if g in sig]
                    self.assertLessEqual(len(gates), 1, where)
                    self.assertEqual(info['bus'] is not None, bool(gates), where)
                    # every load has a source
                    for ld in ('LD.MAR', 'LD.IR', 'LD.IR2', 'LD.REG', 'LD.SP', 'LD.LR'):
                        if ld in sig:
                            self.assertTrue(gates, f"{ld} with nothing on the bus: {where}")
                    if 'LD.PC' in sig:
                        self.assertTrue(any(x.startswith('PCMUX=') for x in sig), where)
                    if 'LD.MDR' in sig:
                        self.assertTrue(gates or 'R/W=READ' in sig, where)
                    for ch in info['changes']:
                        if ch['kind'] == 'flag':
                            self.assertIn('LD.CC', sig, where)
                        elif ch['kind'] in ('mem', 'io'):
                            self.assertIn('R/W=WRITE', sig, where)
                        elif ch['name'] in LOADS:
                            self.assertTrue(any(l in sig for l in LOADS[ch['name']]), f"{ch['name']}: {where}")
                        elif info['state'] != 'SVC_CALL':   # the simplified SVC sets R0 directly
                            self.assertIn('LD.REG', sig, f"{ch['name']}: {where}")


if __name__ == '__main__':
    unittest.main()
