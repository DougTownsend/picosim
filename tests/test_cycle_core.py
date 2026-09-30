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


if __name__ == '__main__':
    unittest.main()
