"""The teaching text agrees with the simulator: the cycle counts the GUI
quotes, the OS rows of the listing, Skip to main and the board's fold."""

import os
import re
import unittest

from picosim import teaching
from tests.test_friction import EX, ROOT, server

GUI = os.path.join(ROOT, 'gui', 'scripts')

# state_info.gd SEQUENCES row → the cycle-table row (picosim/cycles.py) it describes
SEQ_ROWS = {
    'Operate': 'ADDS R2, R0, R1', 'Load': 'LDR R2, [R0, #4]', 'Store': 'STR R2, [R0, #4]',
    'ADR': 'ADR R0, label', 'B, B<cond>': 'B label', 'BX': 'BX LR', 'BLX': 'BLX R3',
    'BL (32-bit)': 'BL label', 'SVC': 'SVC #n', 'BKPT': 'BKPT', 'NOP': 'NOP / WFI / CPSID i ...',
    'DMB': 'DMB / DSB / ISB', 'MRS': 'MRS R0, APSR',
}


def gd(name):
    with open(os.path.join(GUI, name), encoding='utf-8') as f:
        return f.read()


class CycleCountTest(unittest.TestCase):
    def test_state_sequences_match_the_simulator(self):
        counts = teaching.cycle_counts()
        rows = re.findall(r'\[\s*"([^"]+)",\s*"([^"]+)",\s*(\d+)\s*\]', gd('state_info.gd'))
        checked = 0
        for label, _, n in rows:
            key = next((k for k in SEQ_ROWS if label.startswith(k)), None)
            if key:
                self.assertEqual(int(n), counts[SEQ_ROWS[key]], label)
                checked += 1
        self.assertEqual(checked, len(SEQ_ROWS))

    def test_push_pop_formula(self):
        counts = teaching.cycle_counts()
        self.assertIn('"6 + 2n"', gd('state_info.gd'))
        self.assertEqual(counts['PUSH {R4, LR}'], 6 + 2 * 2)
        self.assertEqual(counts['POP {R4, PC}'], 6 + 2 * 2)

    def test_guide_quotes_real_rows(self):
        counts = teaching.cycle_counts()
        names = re.findall(r'_cyc\("([^"]+)"\)', gd('guide.gd'))
        self.assertTrue(names)
        for name in names:
            self.assertIn(name, counts)


class ListingTest(unittest.TestCase):
    def test_os_rows_and_io_use(self):
        s = server(os.path.join(EX, '02_add.s'))
        prog = next(m for m in s.out if m.get('type') == 'program')
        self.assertTrue(all(r['os'] == (r['addr'] < 0x3000) for r in prog['listing']))
        self.assertFalse(prog['uses_io'])
        s = server(os.path.join(EX, '05_blink_led.s'))
        prog = next(m for m in s.out if m.get('type') == 'program')
        self.assertTrue(prog['uses_io'])


class SkipToMainTest(unittest.TestCase):
    def test_runs_the_start_up_to_main(self):
        s = server(os.path.join(EX, '02_add.s'))
        self.assertTrue(s.at_boot())
        s.cmd_run_to_main({})
        self.assertEqual(s.core.pc, s.prog.main_addr)
        self.assertFalse(s.core.in_insn)
        self.assertEqual(s.core.steps, 3)          # the OS's start-up, run for real
        self.assertFalse(s.at_boot())
        s.cmd_step_back({})                        # and Step back undoes it one at a time
        self.assertEqual(s.core.steps, 2)

    def test_from_partway_through_a_cycle(self):
        s = server(os.path.join(EX, '02_add.s'))
        s.cmd_step_cycle({})
        s.cmd_run_to_main({})
        self.assertEqual(s.core.pc, s.prog.main_addr)

    def test_pipeline(self):
        s = server(os.path.join(EX, '02_add.s'))
        s.cmd_set_machine({'machine': 'm0plus'})
        s.cmd_run_to_main({})
        self.assertEqual(s.core.pc, s.prog.main_addr)

    def test_not_at_level_1(self):
        s = server(os.path.join(EX, '02_add.s'), skip_boot=True)
        self.assertFalse(s.at_boot())


if __name__ == '__main__':
    unittest.main()
