"""
The blocks of the CPU diagram, written as small Python models.

Each class or function here is one box (or wire junction) on the diagram.
They are deliberately plain: a value goes in, a value comes out.  Storage
elements (Register, RegisterFile, Flags) only change in `clock()`, which is
the clock edge at the end of a cycle; everything else is combinational and
just computes its output from its inputs during the cycle.

datapath_model.py wires these together and drives them from the FSM's
control signals alone.  tests/test_datapath_model.py checks, for every cycle
of every test program, that the result is exactly what the C++ simulator
does — so this code is not an illustration, it is a proven description of
the machine.  All values are 32-bit unsigned integers.
"""

MASK = 0xFFFFFFFF


# ── storage ─────────────────────────────────────────────────────────────────

class Register:
    """PC, IR, IR2, MAR, MDR and the ALU input latches A and B.

    The output is the stored value for the whole cycle.  If its load enable
    (LD.x) is 1, the value on its input is captured at the clock edge."""

    def __init__(self, name, value=0, width=32):
        self.name, self.value, self.mask = name, value, (1 << width) - 1

    def clock(self, load, d):
        if load:
            self.value = d & self.mask


class RegisterFile:
    """R0–R12, SP, LR: two combinational read ports and one write port.

    The PC is a separate register; reading "R15" through a read port gives
    the PC read value (the instruction's address + 4), from PC / Align."""

    NAMES = ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10',
             'R11', 'R12', 'SP', 'LR']

    def __init__(self, values):
        self.r = list(values[:15])

    def read(self, n, pc_read):
        return pc_read if n == 15 else self.r[n]

    def clock(self, load, dr, d):
        if load:
            self.r[dr] = d & MASK


class Flags:
    """N Z C V.  Loaded from the ALU's flag outputs when LD.CC is 1."""

    def __init__(self, n, z, c, v):
        self.f = {'N': int(n), 'Z': int(z), 'C': int(c), 'V': int(v)}

    def clock(self, load, new):
        if load:
            self.f.update(new)


# ── combinational blocks ────────────────────────────────────────────────────

def mux(select, inputs):
    """A multiplexer: `inputs` maps each select value to the input it passes."""
    return inputs[select]


def adder(a, b):
    """The address adder (and the +2 / +4 incrementers): a 32-bit sum, no flags."""
    return (a + b) & MASK


def tri_state(enable, data):
    """A tri-state buffer: drives `data` when enabled, otherwise None (Z)."""
    return data if enable else None


def bus(*drivers):
    """The shared bus: the value of the one enabled buffer (None if idle).
    Two enabled buffers would short the bus — the FSM must never do that."""
    on = [d for d in drivers if d is not None]
    assert len(on) <= 1, "two tri-state buffers drive the bus at once"
    return on[0] if on else None


def pc_read(pc, ir):
    """PC / Align: the PC read value A + 4 of the instruction at address A.

    After fetch the PC register already points past the instruction: A + 2
    for a 16-bit instruction, A + 4 for a 32-bit one such as BL."""
    wide = (ir >> 11) in (0x1D, 0x1E, 0x1F)
    return pc if wide else (pc + 2) & MASK


def align4(v):
    """Align(PC, 4): round down to a word boundary."""
    return v & ~3 & MASK


def clr_thumb(v):
    """CLR THUMB: clear bit 0 to get a halfword-aligned fetch address."""
    return v & ~1 & MASK


def set_thumb(v):
    """SET THUMB: set bit 0 to mark a return address as Thumb code."""
    return (v | 1) & MASK


def sext(v, bits):
    """Sign-extend a `bits`-wide field to 32 bits."""
    v &= (1 << bits) - 1
    return (v - (1 << bits) if v >> (bits - 1) else v) & MASK


def load_ext(mdr, size, signed):
    """LOAD EXT: pick the low byte / halfword of MDR and zero- or sign-extend it."""
    bits = {'byte': 8, 'halfword': 16, 'word': 32}[size]
    v = mdr & ((1 << bits) - 1)
    return sext(v, bits) if signed and bits < 32 else v


def store_align(v, size):
    """STORE ALIGN: keep only the byte lanes a STRB / STRH writes."""
    return v & {'byte': 0xFF, 'halfword': 0xFFFF, 'word': MASK}[size]


def cond_eval(cond, f):
    """COND EVAL: is condition IR[11:8] true for the flags?"""
    n, z, c, v = f['N'], f['Z'], f['C'], f['V']
    return [z, not z, c, not c, n, not n, v, not v, c and not z, not c or z,
            n == v, n != v, not z and n == v, z or n != v, True, True][cond & 15]


VECTOR_SVCALL = 0x2C      # VECTOR ADDR: SVCall is exception 11, vector at 11 x 4


class ImmExtract:
    """IMM / OFFSET: pulls a constant out of IR (and IR2) and widens it.

    The FSM says which field it wants (the ADDR2MUX / SR2MUX select names the
    field); the bit positions come from the instruction format in IR."""

    @staticmethod
    def offset(name, ir, ir2):
        """The ADDR2MUX immediate inputs."""
        if name == 'ZEXT(IR[7:0])x4':
            return (ir & 0xFF) << 2
        if name == 'ZEXT(IR[10:6])x4':
            return ((ir >> 6) & 0x1F) << 2
        if name == 'ZEXT(IR[10:6])x2':
            return ((ir >> 6) & 0x1F) << 1
        if name == 'ZEXT(IR[10:6])':
            return (ir >> 6) & 0x1F
        if name == 'ZEXT(IR2[11:0])':
            return ir2 & 0xFFF
        if name == 'SEXT(offset)x2':
            if (ir >> 12) == 0b1101:                  # B<cond>: imm8
                return sext(ir & 0xFF, 8) << 1 & MASK
            if (ir >> 11) == 0b11100:                 # B: imm11
                return sext(ir & 0x7FF, 11) << 1 & MASK
            # BL: S, J1, J2, imm10 from IR and imm11 from IR2
            s, j1, j2 = (ir >> 10) & 1, (ir2 >> 13) & 1, (ir2 >> 11) & 1
            i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
            return sext((s << 24) | (i1 << 23) | (i2 << 22) | ((ir & 0x3FF) << 12) |
                        ((ir2 & 0x7FF) << 1), 25)
        raise ValueError(name)

    @staticmethod
    def alu_immediate(ir):
        """The SR2MUX immediate input: the constant field of a 16-bit ALU instruction."""
        top5 = ir >> 11
        if top5 <= 2:                                 # LSL/LSR/ASR #imm5 (LSR/ASR #0 means 32)
            imm = (ir >> 6) & 0x1F
            return 32 if top5 and imm == 0 else imm
        if top5 == 3:                                 # ADDS/SUBS Rd, Rn, #imm3
            return (ir >> 6) & 7
        if top5 <= 7:                                 # MOVS/CMP/ADDS/SUBS #imm8
            return ir & 0xFF
        if top5 in (0b10100, 0b10101):                # ADR / ADD Rd, SP, #imm8 x 4
            return (ir & 0xFF) << 2
        if (ir >> 8) == 0b10110000:                   # ADD/SUB SP, #imm7 x 4
            return (ir & 0x7F) << 2
        raise ValueError(hex(ir))

    @staticmethod
    def list_bytes(ir):
        """4 x n for PUSH / POP / LDM / STM (n = registers in the list)."""
        if (ir >> 9) == 0b1011010 or (ir >> 9) == 0b1011110:    # PUSH / POP: + LR / PC bit
            return 4 * bin(ir & 0x1FF).count('1')
        return 4 * bin(ir & 0xFF).count('1')


class ALU:
    """The ALU.  compute() returns (result, flags): flags holds only the flags
    this operation produces (they are loaded only if LD.CC is 1)."""

    @staticmethod
    def add_with_carry(a, b, carry):
        s = a + b + carry
        r = s & MASK
        return r, {'N': r >> 31, 'Z': int(r == 0), 'C': s >> 32 & 1,
                   'V': ((a ^ r) & (b ^ r)) >> 31 & 1}

    @staticmethod
    def nz(r):
        return {'N': r >> 31 & 1, 'Z': int(r == 0)}

    @staticmethod
    def shift(op, a, n, c):
        """LSL / LSR / ASR / ROR by n (the bottom byte of B); returns (result, carry)."""
        n &= 0xFF
        if n == 0:
            return a, c                               # no shift: carry unchanged
        if op == 'LSL':
            return (a << n) & MASK if n < 32 else 0, (a >> (32 - n)) & 1 if n <= 32 else 0
        if op == 'LSR':
            return a >> n if n < 32 else 0, (a >> (n - 1)) & 1 if n <= 32 else 0
        if op == 'ASR':
            sa = a - (1 << 32) if a >> 31 else a
            if n >= 32:
                return (MASK if a >> 31 else 0), a >> 31
            return (sa >> n) & MASK, (a >> (n - 1)) & 1
        r = n & 31                                    # ROR
        v = ((a >> r) | (a << (32 - r))) & MASK if r else a
        return v, v >> 31

    @classmethod
    def compute(cls, op, a, b, c):
        """op is the ALUK value; a, b the inputs; c the current carry flag."""
        if op in ('ADD',):
            return cls.add_with_carry(a, b, 0)
        if op in ('SUB', 'CMP'):
            return cls.add_with_carry(a, ~b & MASK, 1)
        if op == 'CMN':
            return cls.add_with_carry(a, b, 0)
        if op == 'ADC':
            return cls.add_with_carry(a, b, c)
        if op == 'SBC':
            return cls.add_with_carry(a, ~b & MASK, c)
        if op == 'NEG':
            return cls.add_with_carry(0, ~b & MASK, 1)
        if op in ('LSL', 'LSR', 'ASR', 'ROR'):
            r, carry = cls.shift(op, a, b, c)
            return r, {**cls.nz(r), 'C': carry}
        logic = {'AND': a & b, 'TST': a & b, 'ORR': a | b, 'EOR': a ^ b, 'BIC': a & ~b & MASK,
                 'MVN': ~b & MASK, 'MOV': b, 'MUL': (a * b) & MASK}
        if op in logic:
            return logic[op], cls.nz(logic[op])
        if op == 'PASS':
            return a, {}
        ext = {'SXTB': sext(b, 8), 'SXTH': sext(b, 16), 'UXTB': b & 0xFF, 'UXTH': b & 0xFFFF,
               'REV': int.from_bytes(b.to_bytes(4, 'little'), 'big'),
               'REV16': ((b & 0x00FF00FF) << 8 | (b >> 8) & 0x00FF00FF) & MASK,
               'REVSH': sext(((b & 0xFF) << 8) | (b >> 8 & 0xFF), 16)}
        if op in ext:
            return ext[op], {}
        raise ValueError(f"ALU operation {op} is not modelled (not ARMv6-M)")


class Memory:
    """MEMORY / I-O: reads and writes at MAR.  `read(addr, nbytes)` is the
    simulator's memory; writes are returned (they happen at the clock edge)."""

    SIZE = {'byte': 1, 'halfword': 2, 'word': 4}

    def __init__(self, read):
        self.read_fn = read

    def read(self, addr, size):
        return self.read_fn(addr, self.SIZE[size])
