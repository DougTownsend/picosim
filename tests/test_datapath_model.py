"""
The Python component models (picosim/components.py, datapath_model.py) are
exactly the machine: for every cycle of every test program, the model —
given only the state before the cycle and the FSM's control signals —
predicts the bus value and every register, flag, datapath-register and
memory change the C++ simulator makes.  Run before the core clocks the
cycle, so it cannot copy the answer.
"""

import os
import unittest

from picosim.datapath_model import Datapath
from test_cycle_core import Harness, PROGRAMS, MAX_INSNS
from picosim.loader import LoadError

KIND_NAMES = {'reg', 'flag', 'datapath'}


class DatapathModelTest(unittest.TestCase):
    def test_lockstep(self):
        checked = 0
        for path in PROGRAMS:
            with self.subTest(program=os.path.basename(path)):
                try:
                    h = Harness(path)
                except LoadError:
                    continue
                c = h.core
                held = {}
                for _ in range(MAX_INSNS * 4):
                    if c.halted:
                        break
                    state = c.next_state()
                    model = Datapath.from_core(c, held)
                    info = c.step_cycle()
                    where = f"{info['state']} at 0x{info['insn_addr']:04X} {h.prog.asm_map.get(info['insn_addr'])}"
                    self.assertEqual(state, info['state'], where)
                    got = model.cycle(info['state'], info['signals'])
                    held = got['held']
                    if info['state'] == 'SVC_CALL':
                        # the simplified exception: the OS service runs outside the datapath
                        self.assertEqual(got['bus'], info['bus'], where)
                        continue
                    self.assertEqual(got['bus'], info['bus'], where)
                    want = sorted((ch['name'], ch['old'], ch['new']) for ch in info['changes']
                                  if ch['kind'] in KIND_NAMES)
                    self.assertEqual(sorted(got['changes']), want, where)
                    want_w = sorted((ch['addr'], ch['width'], ch['new']) for ch in info['changes']
                                    if ch['kind'] in ('mem', 'io'))
                    self.assertEqual(sorted(got['writes']), want_w, where)
                    if got['taken'] is not None:
                        self.assertIn(f"BranchTaken={int(got['taken'])}", info['signals'], where)
                    checked += 1
        self.assertGreater(checked, 10000)


    def test_random_states(self):
        """Every ARMv6-M encoding class, at aligned and unaligned addresses,
        with random register and flag values (wide values, negative numbers,
        shift amounts past 32, all condition codes)."""
        import random
        from picosim._picosim_core import CPUCore
        from test_timing import TABLE81
        rnd = random.Random(2026)
        encodings = [(h1, h2) for _, h1, h2, _, _, _ in TABLE81]
        encodings += [(0xD000 | (c << 8), 0) for c in range(14)]          # every B<cond>
        encodings += [(0x5642, 0), (0x5C42, 0), (0x8042, 0), (0x7042, 0),   # LDRSB, LDRB, STRH, STRB
                      (0x4290, 0), (0x40C8, 0), (0x4108, 0), (0x4088, 0), (0x41C8, 0),
                      (0xE7FE, 0), (0xF7FF, 0xFFFE), (0xBC0F, 0), (0xB4F0, 0), (0xC00F, 0), (0xC80F, 0)]
        checked = 0
        for hw1, hw2 in encodings:
            for trial in range(40):
                c = CPUCore()
                c.strict = False                        # random BX targets, unaligned bases
                addr = rnd.choice([0x1000, 0x1002])
                c.write16(addr, hw1)
                c.write16(addr + 2, hw2)
                for r in range(13):
                    v = rnd.choice([rnd.getrandbits(32), rnd.randrange(0, 40), 0x80000000,
                                    0x7FFFFFFF, 0xFFFFFFFF, 0x2000 + 4 * rnd.randrange(64)])
                    c.set_reg(r, v)
                for r in range(8):                      # keep load/store bases inside RAM
                    if hw1 >> 12 in (0x5, 0x6, 0x7, 0x8, 0xC):
                        c.set_reg(r, 0x2000 + 4 * rnd.randrange(64))
                if hw1 >> 12 == 0x5:
                    c.set_reg(1, 4 * rnd.randrange(8))
                c.sp = 0x8000 - 4 * rnd.randrange(16)
                c.set_reg(14, 0x3001)
                for f in 'NZCV':
                    setattr(c, f, rnd.randrange(2))
                for a in range(0x2000, 0x2200, 4):
                    c.write32(a, rnd.getrandbits(32))
                for a in range(c.sp, c.sp + 64, 4):
                    c.write32(a, rnd.getrandbits(32) | 1)
                c.pc = addr
                held = {}
                while True:
                    model = Datapath.from_core(c, held)
                    info = c.step_cycle()
                    got = model.cycle(info['state'], info['signals'])
                    held = got['held']
                    where = f"{info['state']} {hw1:04X} {hw2:04X} trial {trial}"
                    self.assertEqual(got['bus'], info['bus'], where)
                    want = sorted((ch['name'], ch['old'], ch['new']) for ch in info['changes']
                                  if ch['kind'] in KIND_NAMES)
                    self.assertEqual(sorted(got['changes']), want, where)
                    want_w = sorted((ch['addr'], ch['width'], ch['new']) for ch in info['changes']
                                    if ch['kind'] in ('mem', 'io'))
                    self.assertEqual(sorted(got['writes']), want_w, where)
                    checked += 1
                    if info['last']:
                        break
        self.assertGreater(checked, 20000)


if __name__ == '__main__':
    unittest.main()
