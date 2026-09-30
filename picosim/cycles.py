"""
Per-instruction cycle table for the multi-cycle teaching machine.

Each row is produced by clocking a real encoding through the C++ core with
step_cycle(), so the table always matches the simulator.  `picosim
--cycle-table` prints it as Markdown (docs/cycles.md is generated this way).
"""

from ._picosim_core import CPUCore

# (instruction, hw1, hw2, textbook count or None if the sequence is new here)
EXAMPLES = [
    ("ADDS R2, R0, R1",          0x1842, 0,      6),
    ("ADDS R2, R0, #5",          0x1D42, 0,      6),
    ("MOVS R2, #5",              0x2205, 0,      6),
    ("CMP R0, #1",               0x2801, 0,      6),
    ("LSLS R1, R0, #3",          0x00C1, 0,      6),
    ("ANDS / ORRS / EORS / MVNS / MULS ...", 0x4008, 0, None),
    ("MOV R8, R0 (high register)", 0x4680, 0,    None),
    ("ADD SP, #8 / SUB SP, #8",  0xB002, 0,      None),
    ("SXTB / UXTH / REV ...",    0xB240, 0,      None),
    ("LDR R2, [R0, #4]",         0x6842, 0,      7),
    ("LDRB / LDRH (immediate)",  0x7842, 0,      7),
    ("LDR R2, [R0, R1] / LDRSB / LDRSH", 0x5842, 0, None),
    ("LDR R2, [SP, #4]",         0x9A01, 0,      None),
    ("STR R2, [R0, #4]",         0x6042, 0,      7),
    ("STRB / STRH (immediate)",  0x7042, 0,      7),
    ("LDR R0, =literal",         0x4801, 0,      7),
    ("ADR R0, label",            0xA001, 0,      6),
    ("B label",                  0xE000, 0,      5),
    ("B<cond> label",            0xD000, 0,      5),
    ("BX LR",                    0x4770, 0,      6),
    ("BLX R3",                   0x4798, 0,      None),
    ("BL label",                 0xF000, 0xF800, 9),
    ("PUSH {R4, LR}",            0xB510, 0,      None),
    ("POP {R4, PC}",             0xBD10, 0,      None),
    ("STMIA R0!, {R1, R2}",      0xC006, 0,      None),
    ("LDMIA R0!, {R1, R2}",      0xC806, 0,      None),
    ("SVC #n",                   0xDF02, 0,      None),
    ("BKPT",                     0xBE00, 0,      None),
]


def trace(hw1, hw2=0):
    """Clock one instruction on a scratch core; return the list of state names."""
    c = CPUCore()
    c.write16(0x100, hw1)
    c.write16(0x102, hw2)
    for r in range(8):
        c.set_reg(r, 0x400)
    c.set_reg(14, 0x201)
    c.sp = 0x800
    c.pc = 0x100
    states = []
    while True:
        info = c.step_cycle()
        states.append(info['state'])
        if info['last']:
            return states


def _compress(states):
    out, i = [], 0
    while i < len(states):
        j = i
        while j < len(states) and states[j] == states[i]:
            j += 1
        out.append(states[i] + (f" x{j - i}" if j - i > 1 else ""))
        i = j
    return out


def rows():
    for name, hw1, hw2, book in EXAMPLES:
        st = trace(hw1, hw2)
        yield name, len(st), book, st


def print_table():
    print("| Instruction | Cycles | Textbook | States after fetch |")
    print("| --- | ---: | --- | --- |")
    for name, n, book, st in rows():
        fetch = 6 if st[3] == 'FETCH2_ADDR' else 3
        tb = "new" if book is None else ("matches" if book == n else f"MISMATCH ({book})")
        print(f"| `{name}` | {n} | {tb} | {' → '.join(_compress(st[fetch:]))} |")
    print()
    print("Multi-register transfers (PUSH, POP, LDM, STM) take "
          "fetch (3) + DECODE + EVALUATE_ADDRESS + 2 per register "
          "+ 1 WRITEBACK when the base register is updated.")
