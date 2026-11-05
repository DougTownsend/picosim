"""An independent ARMv6-M Thumb encoding table for the Level 5 discovery tool.

The discovery tool runs every 16-bit Thumb encoding (and a structured sample
of 32-bit ones) on a real RP2040 inside an on-chip agent.  This module labels
each case and picks register inputs that keep it safe to run.  It never
judges results.

It was written from the ARMv6-M Architecture Reference Manual (DDI 0419),
chapters A5 (encodings) and A6 (per-instruction pseudocode), and on purpose
NOT from picosim's own decoder (picosim_core.cpp): a mistake there must not
be copied in here, or the hardware comparison could not catch it.

Contents
--------
ENCODINGS       (mask, value, name, cls, fields) for every 16-bit value.  The
                entries do not overlap: each 16-bit value matches exactly
                one entry.  They are built from _RULES16, which is written
                in first-match order for readability, by subtracting every
                earlier rule from each later one (_disjoint).
decode16(hw)    -> Enc.  classify(hw) -> class string.  group(hw) -> page
                group ('data', 'memory', 'control', 'prefix32') used by the
                proposal's per-pass counts.
decode32(h1,h2) -> Enc for BL, MSR, MRS, DMB/DSB/ISB and UDF.W; anything
                else is 'undefined'.
SYSM            the SYSm values MSR/MRS accept on ARMv6-M.
safe_inputs()   seeded register, LR, PSP, NZCV, PRIMASK and CONTROL inputs.

Fields are (lo_bit, width), or a tuple of such pieces joined most significant
first, for the split high-register numbers (DN:Rdn, D:Rd, N:Rn).

Classes (16-bit)
----------------
data          ordinary data processing (also ADR, ADD Rd,SP,#imm, extends, REV)
flag-reading  data ops whose result or C output depends on the input C flag:
              ADCS, SBCS, the register shifts LSLS/LSRS/ASRS/RORS Rdn,Rm (C is
              kept when the shift amount is 0) and MOVS Rd,Rm (LSLS #0, keeps
              C).  Ops that merely leave C/V unchanged (logical ops, MOVS #imm,
              MULS) are 'data': four NZCV inputs already show the pass-through.
              LSRS/ASRS #0 mean a shift by 32 and set C from bit 31: 'data'.
memory        loads and stores, LDM/STM, PUSH, POP without PC
sp-writing    ADD/SUB SP,SP,#imm7; ADD SP,Rm; MOV SP,Rm (ADD SP,SP too)
pc-writing    ADD PC,Rm; MOV PC,Rm; BX; BLX; POP {..,PC}.  POP {PC} is put
              here, not in memory, because what matters is where it lands:
              its target comes from the stack, so safe_inputs() also says
              which word the host must fill (B3 still runs it as a memory
              case by page group).
branch-imm    B imm11
cond-branch   B<cond> imm8, cond 0-13
hint          NOP, YIELD, WFE, WFI, SEV and the unallocated 0xBFx0 hints
system        CPS (0xB660-0xB67F)
svc, bkpt, udf
undefined     everything else in the 16-bit space (CBZ/CBNZ, IT, SETEND,
              0xBA80-0xBABF, the unallocated 0xB6xx/0xB7xx/0xB8xx pages ...)
prefix32      the first halfword of a 32-bit instruction (0xE800-0xFFFF)

32-bit classes: pc-writing (BL), system (MSR, MRS), barrier (DMB, DSB, ISB),
udf (UDF.W), undefined.

Enc.unpredictable is True when the ARM ARM does not fully define the result:
an UNPREDICTABLE encoding, or one that stores an UNKNOWN value.  Enc.reason
says which.  Enc.note carries other remarks (reserved options, a target the
inputs cannot control).  A hardware/simulator difference on an unpredictable
encoding is not by itself a simulator bug.
"""

import random
from collections import Counter
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Classes and layout

CLASSES16 = ('data', 'flag-reading', 'memory', 'sp-writing', 'pc-writing',
             'branch-imm', 'cond-branch', 'hint', 'system', 'svc', 'bkpt',
             'udf', 'undefined', 'prefix32')
CLASSES32 = ('pc-writing', 'system', 'barrier', 'udf', 'undefined')

# Chip memory layout (docs/level5-discovery-plan.md section 3.1).  Windows are
# (start address, size in bytes).  'psp_default' is the value SP has when a
# case starts (the agent's 8-word exception frame lies just below it).
DEFAULT_LAYOUT = {
    'rom': (0x00000000, 0x4000),
    'sled': (0x20010000, 0x8000),
    'slot': 0x20014000,
    'slot_guard': 0x100,        # branch targets avoid [slot, slot+guard)
    'scratch': (0x20018000, 0x1000),
    'psp': (0x20040800, 0x800),
    'psp_default': 0x20040F00,
}

# Room kept free above and below any SP the inputs produce: the 8-word
# exception frame pushed when the case ends (a UDF in the sled) needs 32.
SP_MARGIN = 64

# MSR/MRS SYSm values on ARMv6-M (DDI 0419 B5.2.2 / B5.2.3).
SYSM = {'APSR': 0, 'IAPSR': 1, 'EAPSR': 2, 'XPSR': 3, 'IPSR': 5,
        'EPSR': 6, 'IEPSR': 7, 'MSP': 8, 'PSP': 9, 'PRIMASK': 16,
        'CONTROL': 20}
_SYSM_OK = frozenset(SYSM.values())

COND = ('EQ', 'NE', 'CS', 'CC', 'MI', 'PL', 'VS', 'VC',
        'HI', 'LS', 'GE', 'LT', 'GT', 'LE')


@dataclass(frozen=True)
class Enc:
    """One decoded encoding.  fields holds the extracted field values (and
    for BL the derived 'imm32').  hw2 is None for 16-bit encodings."""
    name: str
    cls: str
    fields: dict
    hw: int
    hw2: object = None
    unpredictable: bool = False
    reason: str = ''
    note: str = ''

    @property
    def size(self):
        return 2 if self.hw2 is None else 4


# ---------------------------------------------------------------------------
# The 16-bit table, in first-match order (A5.2 of DDI 0419)

_LO3 = {'rd': (0, 3), 'rm': (3, 3)}
_RRR = {'rd': (0, 3), 'rn': (3, 3), 'rm': (6, 3)}
_RRI3 = {'rd': (0, 3), 'rn': (3, 3), 'imm3': (6, 3)}
_SHI = {'rd': (0, 3), 'rm': (3, 3), 'imm5': (6, 5)}
_LSR = {'rt': (0, 3), 'rn': (3, 3), 'rm': (6, 3)}
_LSI = {'rt': (0, 3), 'rn': (3, 3), 'imm5': (6, 5)}
_HI_D = ((7, 1), (0, 3))        # DN:Rdn / D:Rd / N:Rn
_RM4 = {'rm': (3, 4)}

_RULES16 = []


def _r(mask, value, name, cls, fields=None):
    _RULES16.append((mask, value, name, cls, dict(fields or {})))


# Shift (immediate), add, subtract, move and compare: 0x0000-0x3FFF
_r(0xFFC0, 0x0000, 'MOVS Rd,Rm (LSLS #0)', 'flag-reading', _LO3)
_r(0xF800, 0x0000, 'LSLS Rd,Rm,#imm5', 'data', _SHI)
_r(0xF800, 0x0800, 'LSRS Rd,Rm,#imm5', 'data', _SHI)    # imm5 0 = #32
_r(0xF800, 0x1000, 'ASRS Rd,Rm,#imm5', 'data', _SHI)    # imm5 0 = #32
_r(0xFE00, 0x1800, 'ADDS Rd,Rn,Rm', 'data', _RRR)
_r(0xFE00, 0x1A00, 'SUBS Rd,Rn,Rm', 'data', _RRR)
_r(0xFE00, 0x1C00, 'ADDS Rd,Rn,#imm3', 'data', _RRI3)
_r(0xFE00, 0x1E00, 'SUBS Rd,Rn,#imm3', 'data', _RRI3)
_r(0xF800, 0x2000, 'MOVS Rd,#imm8', 'data', {'rd': (8, 3), 'imm8': (0, 8)})
_r(0xF800, 0x2800, 'CMP Rn,#imm8', 'data', {'rn': (8, 3), 'imm8': (0, 8)})
_r(0xF800, 0x3000, 'ADDS Rdn,#imm8', 'data', {'rdn': (8, 3), 'imm8': (0, 8)})
_r(0xF800, 0x3800, 'SUBS Rdn,#imm8', 'data', {'rdn': (8, 3), 'imm8': (0, 8)})

# Data processing (register): 0x4000-0x43FF, opcode in bits 9:6
for _op, (_name, _cls, _f) in enumerate([
        ('ANDS Rdn,Rm', 'data', 'dn'), ('EORS Rdn,Rm', 'data', 'dn'),
        ('LSLS Rdn,Rm', 'flag-reading', 'dn'), ('LSRS Rdn,Rm', 'flag-reading', 'dn'),
        ('ASRS Rdn,Rm', 'flag-reading', 'dn'), ('ADCS Rdn,Rm', 'flag-reading', 'dn'),
        ('SBCS Rdn,Rm', 'flag-reading', 'dn'), ('RORS Rdn,Rm', 'flag-reading', 'dn'),
        ('TST Rn,Rm', 'data', 'n'), ('RSBS Rd,Rn,#0', 'data', 'dnn'),
        ('CMP Rn,Rm', 'data', 'n'), ('CMN Rn,Rm', 'data', 'n'),
        ('ORRS Rdn,Rm', 'data', 'dn'), ('MULS Rdm,Rn,Rdm', 'data', 'mul'),
        ('BICS Rdn,Rm', 'data', 'dn'), ('MVNS Rd,Rm', 'data', 'd')]):
    _r(0xFFC0, 0x4000 | _op << 6, _name, _cls, {
        'dn': {'rdn': (0, 3), 'rm': (3, 3)},
        'n': {'rn': (0, 3), 'rm': (3, 3)},
        'dnn': {'rd': (0, 3), 'rn': (3, 3)},
        'mul': {'rdm': (0, 3), 'rn': (3, 3)},
        'd': {'rd': (0, 3), 'rm': (3, 3)}}[_f])

# Special data instructions and branch and exchange: 0x4400-0x47FF.
# ADD (register) T2 sends Rdn=SP or Rm=SP to ADD (SP plus register).
_r(0xFFFF, 0x44FF, 'ADD PC,PC', 'pc-writing', {'rdn': _HI_D, 'rm': (3, 4)})
_r(0xFFFF, 0x44EF, 'ADD PC,SP,PC', 'pc-writing', {'rdm': _HI_D})
_r(0xFF87, 0x4487, 'ADD PC,Rm', 'pc-writing', {'rdn': _HI_D, 'rm': (3, 4)})
_r(0xFFFF, 0x44ED, 'ADD SP,SP', 'sp-writing', {'rdm': _HI_D})
_r(0xFF87, 0x4485, 'ADD SP,Rm', 'sp-writing', _RM4)
_r(0xFF78, 0x4468, 'ADD Rdm,SP,Rdm', 'data', {'rdm': _HI_D})
_r(0xFF00, 0x4400, 'ADD Rdn,Rm', 'data', {'rdn': _HI_D, 'rm': (3, 4)})
_r(0xFF00, 0x4500, 'CMP Rn,Rm (high)', 'data', {'rn': _HI_D, 'rm': (3, 4)})
_r(0xFF87, 0x4687, 'MOV PC,Rm', 'pc-writing', _RM4)
_r(0xFF87, 0x4685, 'MOV SP,Rm', 'sp-writing', _RM4)
_r(0xFF00, 0x4600, 'MOV Rd,Rm', 'data', {'rd': _HI_D, 'rm': (3, 4)})
_r(0xFF80, 0x4700, 'BX Rm', 'pc-writing', {'rm': (3, 4), 'sbz': (0, 3)})
_r(0xFF80, 0x4780, 'BLX Rm', 'pc-writing', {'rm': (3, 4), 'sbz': (0, 3)})

# Loads and stores: 0x4800-0x9FFF
_r(0xF800, 0x4800, 'LDR Rt,[PC,#imm8*4]', 'memory', {'rt': (8, 3), 'imm8': (0, 8)})
for _op, _name in enumerate(['STR', 'STRH', 'STRB', 'LDRSB', 'LDR', 'LDRH', 'LDRB', 'LDRSH']):
    _r(0xFE00, 0x5000 | _op << 9, _name + ' Rt,[Rn,Rm]', 'memory', _LSR)
_r(0xF800, 0x6000, 'STR Rt,[Rn,#imm5*4]', 'memory', _LSI)
_r(0xF800, 0x6800, 'LDR Rt,[Rn,#imm5*4]', 'memory', _LSI)
_r(0xF800, 0x7000, 'STRB Rt,[Rn,#imm5]', 'memory', _LSI)
_r(0xF800, 0x7800, 'LDRB Rt,[Rn,#imm5]', 'memory', _LSI)
_r(0xF800, 0x8000, 'STRH Rt,[Rn,#imm5*2]', 'memory', _LSI)
_r(0xF800, 0x8800, 'LDRH Rt,[Rn,#imm5*2]', 'memory', _LSI)
_r(0xF800, 0x9000, 'STR Rt,[SP,#imm8*4]', 'memory', {'rt': (8, 3), 'imm8': (0, 8)})
_r(0xF800, 0x9800, 'LDR Rt,[SP,#imm8*4]', 'memory', {'rt': (8, 3), 'imm8': (0, 8)})

# PC- and SP-relative address generation: 0xA000-0xAFFF
_r(0xF800, 0xA000, 'ADR Rd,#imm8*4', 'data', {'rd': (8, 3), 'imm8': (0, 8)})
_r(0xF800, 0xA800, 'ADD Rd,SP,#imm8*4', 'data', {'rd': (8, 3), 'imm8': (0, 8)})

# Miscellaneous 16-bit instructions: 0xB000-0xBFFF (A5.2.5)
_r(0xFF80, 0xB000, 'ADD SP,SP,#imm7*4', 'sp-writing', {'imm7': (0, 7)})
_r(0xFF80, 0xB080, 'SUB SP,SP,#imm7*4', 'sp-writing', {'imm7': (0, 7)})
_r(0xF500, 0xB100, 'CBZ/CBNZ (ARMv7-M only)', 'undefined')     # 0xB1,B3,B9,BB pages
_r(0xFFC0, 0xB200, 'SXTH Rd,Rm', 'data', _LO3)
_r(0xFFC0, 0xB240, 'SXTB Rd,Rm', 'data', _LO3)
_r(0xFFC0, 0xB280, 'UXTH Rd,Rm', 'data', _LO3)
_r(0xFFC0, 0xB2C0, 'UXTB Rd,Rm', 'data', _LO3)
_r(0xFE00, 0xB400, 'PUSH {list}', 'memory', {'m': (8, 1), 'list': (0, 8)})
# CPS: 1011 0110 011 im (0)(0)(1)(0) -- ARMv6-M has only the I flag.
_r(0xFFFF, 0xB662, 'CPSIE i', 'system', {'im': (4, 1), 'flags': (0, 4)})
_r(0xFFFF, 0xB672, 'CPSID i', 'system', {'im': (4, 1), 'flags': (0, 4)})
_r(0xFFE0, 0xB660, 'CPS (should-be bits wrong)', 'system', {'im': (4, 1), 'flags': (0, 4)})
_r(0xFFC0, 0xBA00, 'REV Rd,Rm', 'data', _LO3)
_r(0xFFC0, 0xBA40, 'REV16 Rd,Rm', 'data', _LO3)
_r(0xFFC0, 0xBA80, 'undefined (REV space; HLT in ARMv8)', 'undefined', _LO3)
_r(0xFFC0, 0xBAC0, 'REVSH Rd,Rm', 'data', _LO3)
_r(0xFF00, 0xBD00, 'POP {list,PC}', 'pc-writing', {'p': (8, 1), 'list': (0, 8)})
_r(0xFF00, 0xBC00, 'POP {list}', 'memory', {'p': (8, 1), 'list': (0, 8)})
_r(0xFF00, 0xBE00, 'BKPT #imm8', 'bkpt', {'imm8': (0, 8)})
for _op, _name in enumerate(['NOP', 'YIELD', 'WFE', 'WFI', 'SEV']):
    _r(0xFFFF, 0xBF00 | _op << 4, _name, 'hint', {'opa': (4, 4)})
_r(0xFF0F, 0xBF00, 'HINT #opA (unallocated)', 'hint', {'opa': (4, 4)})
_r(0xFF00, 0xBF00, 'IT (ARMv7-M only)', 'undefined', {'firstcond': (4, 4), 'mask': (0, 4)})
_r(0xF000, 0xB000, 'undefined (misc space)', 'undefined')

# Multiple loads and stores: 0xC000-0xCFFF
_r(0xF800, 0xC000, 'STM Rn!,{list}', 'memory', {'rn': (8, 3), 'list': (0, 8)})
_r(0xF800, 0xC800, 'LDM Rn{!},{list}', 'memory', {'rn': (8, 3), 'list': (0, 8)})

# Conditional branch, UDF and SVC: 0xD000-0xDFFF
_r(0xFF00, 0xDE00, 'UDF #imm8', 'udf', {'imm8': (0, 8)})
_r(0xFF00, 0xDF00, 'SVC #imm8', 'svc', {'imm8': (0, 8)})
_r(0xF000, 0xD000, 'B<cond> label', 'cond-branch', {'cond': (8, 4), 'imm8': (0, 8)})

# Unconditional branch and the 32-bit prefixes: 0xE000-0xFFFF
_r(0xF800, 0xE000, 'B label', 'branch-imm', {'imm11': (0, 11)})
_r(0xF800, 0xE800, '32-bit prefix', 'prefix32')
_r(0xF800, 0xF000, '32-bit prefix', 'prefix32')
_r(0xF800, 0xF800, '32-bit prefix', 'prefix32')


# ---------------------------------------------------------------------------
# First-match rules -> non-overlapping cubes

def _cube_minus(a, b):
    """Cube a minus cube b, as a list of disjoint cubes.  A cube is a
    (mask, value) pair: the set of hw with hw & mask == value."""
    am, av = a
    bm, bv = b
    if (av ^ bv) & am & bm:
        return [a]                      # already disjoint
    out = []
    m, v = am, av
    for bit in (1 << i for i in range(15, -1, -1)):
        if bm & bit and not am & bit:
            out.append((m | bit, v | (~bv & bit)))  # the half outside b
            m, v = m | bit, v | (bv & bit)           # keep splitting the half inside
    return out                          # what is left lies inside b: dropped


def _disjoint(rules):
    table, seen = [], []
    for mask, value, name, cls, fields in rules:
        pieces = [(mask, value)]
        for prev in seen:
            pieces = [q for p in pieces for q in _cube_minus(p, prev)]
        table.extend((m, v, name, cls, fields) for m, v in pieces)
        seen.append((mask, value))
    return table


ENCODINGS = _disjoint(_RULES16)


def _cube_values(mask, value):
    free = ~mask & 0xFFFF
    sub = 0
    while True:
        yield value | sub
        sub = (sub - free) & free
        if sub == 0:
            return


# hw -> index into ENCODINGS
_INDEX = [None] * 0x10000
for _i, (_m, _v, *_rest) in enumerate(ENCODINGS):
    for _hw in _cube_values(_m, _v):
        _INDEX[_hw] = _i
_CLASS = [ENCODINGS[i][3] for i in _INDEX]


def _field(word, spec):
    if isinstance(spec[0], tuple):          # split field, msb piece first
        v = 0
        for lo, w in spec:
            v = (v << w) | ((word >> lo) & ((1 << w) - 1))
        return v
    lo, w = spec
    return (word >> lo) & ((1 << w) - 1)


def _popcount(x):
    return bin(x).count('1')


# ---------------------------------------------------------------------------
# UNPREDICTABLE / UNKNOWN conditions (16-bit), from the A6 pseudocode

def _check16(name, f):
    """(reason, note) for one decoded 16-bit encoding."""
    if name == 'ADD PC,PC':
        return 'ADD (register) T2 with n == 15 and m == 15 is UNPREDICTABLE', ''
    if name == 'ADD PC,SP,PC':
        return '', 'target is SP+PC: inputs cannot control it'
    if name == 'ADD SP,SP':
        return '', 'SP becomes 2*SP: inputs cannot keep it valid'
    if name == 'CMP Rn,Rm (high)':
        if f['rn'] < 8 and f['rm'] < 8:
            return 'CMP (register) T2 with both registers low is UNPREDICTABLE', ''
        if f['rn'] == 15 or f['rm'] == 15:
            return 'CMP (register) T2 with PC as an operand is UNPREDICTABLE', ''
    if name in ('BX Rm', 'BLX Rm'):
        if f['sbz']:
            return 'bits 2:0 are (0)(0)(0); other values are UNPREDICTABLE', ''
        if name == 'BLX Rm' and f['rm'] == 15:
            return 'BLX with m == 15 is UNPREDICTABLE', ''
        if f['rm'] == 13:
            return '', 'target is SP: inputs cannot control it'
        if f['rm'] == 15:
            return '', 'BX PC: target fixed (bit 0 clear, so it faults)'
    if name in ('MOV PC,Rm', 'ADD PC,Rm', 'ADD SP,Rm', 'MOV SP,Rm'):
        if f['rm'] == 13 and name != 'MOV SP,Rm':
            return '', 'operand is SP: inputs cannot control the result'
        if f['rm'] == 15:
            return '', 'operand is PC: inputs cannot control the result'
    if name == 'CPS (should-be bits wrong)':
        return ('only CPSIE i (0xB662) and CPSID i (0xB672) are defined; '
                'bits 3:0 are (0)(0)(1)(0), other values are UNPREDICTABLE'), ''
    if name == 'PUSH {list}' and f['list'] == 0 and f['m'] == 0:
        return 'PUSH with an empty register list is UNPREDICTABLE', ''
    if name == 'POP {list}' and f['list'] == 0:
        return 'POP with an empty register list is UNPREDICTABLE', ''
    if name in ('LDM Rn{!},{list}', 'STM Rn!,{list}') and f['list'] == 0:
        return name.split()[0] + ' with an empty register list is UNPREDICTABLE', ''
    if name == 'STM Rn!,{list}':
        rn, lst = f['rn'], f['list']
        if lst >> rn & 1 and lst & ((1 << rn) - 1):
            return 'STM with Rn in the list but not lowest stores an UNKNOWN value', ''
    if name == 'HINT #opA (unallocated)':
        return '', ('unallocated hint: the ARM ARM says it executes as a NOP '
                    '(v7-M wording; to be confirmed for v6-M on hardware)')
    return '', ''


def decode16(hw):
    """Decode a 16-bit value (0..0xFFFF) to exactly one Enc."""
    hw &= 0xFFFF
    _m, _v, name, cls, fspec = ENCODINGS[_INDEX[hw]]
    f = {k: _field(hw, s) for k, s in fspec.items()}
    reason, note = _check16(name, f)
    return Enc(name, cls, f, hw, None, bool(reason), reason, note)


def classify(hw):
    """The class string of a 16-bit value."""
    return _CLASS[hw & 0xFFFF]


def is_prefix32(hw):
    """True if hw is the first halfword of a 32-bit instruction."""
    return (hw >> 11) in (0b11101, 0b11110, 0b11111)


# Page groups used by the proposal's pass counts (section 6.B).  These are
# about pages, not classes: MOV PC,Rm is pc-writing but in the data group.
_DATA_PAGES = set(range(0x00, 0x47)) | set(range(0xA0, 0xB0)) | {0xB0, 0xB2, 0xBA}
_MEM_PAGES = set(range(0x48, 0xA0)) | set(range(0xC0, 0xD0)) | {0xB4, 0xB5, 0xBC, 0xBD}


def group(hw):
    """'data', 'memory', 'control' or 'prefix32', by page."""
    if is_prefix32(hw):
        return 'prefix32'
    page = (hw >> 8) & 0xFF
    if page in _DATA_PAGES:
        return 'data'
    if page in _MEM_PAGES:
        return 'memory'
    return 'control'


def count_by_class():
    return Counter(_CLASS)


def count_by_group():
    return Counter(group(hw) for hw in range(0x10000))


def count_by_page():
    """{page (hw >> 8): Counter of classes}."""
    pages = {p: Counter() for p in range(256)}
    for hw, cls in enumerate(_CLASS):
        pages[hw >> 8][cls] += 1
    return pages


# ---------------------------------------------------------------------------
# 32-bit encodings (A5.3): only the branch and miscellaneous control group
# (hw1 = 11110xxxxxxxxxxx, hw2 bit 15 = 1) is defined on ARMv6-M.

def decode32(hw1, hw2):
    """Decode a 32-bit instruction.  hw1 must satisfy is_prefix32."""
    if not is_prefix32(hw1):
        raise ValueError('0x%04X is not a 32-bit prefix' % hw1)
    hw1 &= 0xFFFF
    hw2 &= 0xFFFF

    def enc(name, cls, f=None, reason='', note=''):
        return Enc(name, cls, f or {}, hw1, hw2, bool(reason), reason, note)

    if hw1 >> 11 != 0b11110 or not hw2 & 0x8000:
        return enc('undefined (32-bit)', 'undefined')
    op1 = (hw1 >> 4) & 0x7F
    op2 = (hw2 >> 12) & 0x7

    if op2 & 0b101 == 0b101:                         # 1x1: BL
        s = hw1 >> 10 & 1
        j1, j2 = hw2 >> 13 & 1, hw2 >> 11 & 1
        i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
        imm = (s << 24 | i1 << 23 | i2 << 22 | (hw1 & 0x3FF) << 12 | (hw2 & 0x7FF) << 1)
        if s:
            imm -= 1 << 25
        f = {'s': s, 'imm10': hw1 & 0x3FF, 'j1': j1, 'j2': j2,
             'imm11': hw2 & 0x7FF, 'imm32': imm}
        return enc('BL label', 'pc-writing', f)

    if op2 == 0b010 and op1 == 0x7F:                 # UDF.W (010 also fits 0x0: check first)
        f = {'imm16': (hw1 & 0xF) << 12 | (hw2 & 0xFFF)}
        return enc('UDF.W #imm16', 'udf', f)

    if op2 & 0b101 == 0b000:                         # 0x0: MSR, MRS, barriers
        if op1 >> 1 == 0b011100:                     # MSR (register)
            rn, sysm = hw1 & 0xF, hw2 & 0xFF
            f = {'rn': rn, 'sysm': sysm}
            reason = ''
            if hw1 & 0x10 or hw2 & 0x2000 or (hw2 >> 8) & 0xF != 0b1000:
                reason = 'should-be bits wrong (hw1 bit 4, hw2 bits 13 and 11:8)'
            elif rn in (13, 15):
                reason = 'MSR with Rn = SP or PC is UNPREDICTABLE'
            elif sysm not in _SYSM_OK:
                reason = 'MSR with an unlisted SYSm is UNPREDICTABLE'
            return enc('MSR spec_reg,Rn', 'system', f, reason)
        if op1 >> 1 == 0b011111:                     # MRS
            rd, sysm = (hw2 >> 8) & 0xF, hw2 & 0xFF
            f = {'rd': rd, 'sysm': sysm}
            reason = ''
            if hw1 & 0x1F != 0xF or hw2 & 0x2000:
                reason = 'should-be bits wrong (hw1 bits 4:0, hw2 bit 13)'
            elif rd in (13, 15):
                reason = 'MRS with Rd = SP or PC is UNPREDICTABLE'
            elif sysm not in _SYSM_OK:
                reason = 'MRS with an unlisted SYSm is UNPREDICTABLE'
            return enc('MRS Rd,spec_reg', 'system', f, reason)
        if op1 == 0b0111011:                         # miscellaneous control
            op, option = (hw2 >> 4) & 0xF, hw2 & 0xF
            names = {0b0100: 'DSB', 0b0101: 'DMB', 0b0110: 'ISB'}
            if op not in names:                      # CLREX etc. are v7 only
                return enc('undefined (misc control)', 'undefined', {'op': op})
            f = {'op': op, 'option': option}
            reason = ''
            if hw1 & 0xF != 0xF or hw2 & 0x2000 or (hw2 >> 8) & 0xF != 0xF:
                reason = 'should-be bits wrong (hw1 bits 3:0, hw2 bits 13 and 11:8)'
            note = '' if option == 0xF else (
                'reserved option: the ARM ARM says it acts as SY, but software '
                'must not rely on it (to be confirmed on hardware)')
            return enc(names[op] + (' SY' if option == 0xF else ' #%d' % option),
                       'barrier', f, reason, note)
        return enc('undefined (32-bit)', 'undefined')

    return enc('undefined (32-bit)', 'undefined')


# ---------------------------------------------------------------------------
# Safe inputs

# Memory forms: name -> (kind, access size)
_MEM = {
    'LDR Rt,[PC,#imm8*4]': ('literal', 4),
    'STR Rt,[Rn,Rm]': ('regoff', 4), 'STRH Rt,[Rn,Rm]': ('regoff', 2),
    'STRB Rt,[Rn,Rm]': ('regoff', 1), 'LDRSB Rt,[Rn,Rm]': ('regoff', 1),
    'LDR Rt,[Rn,Rm]': ('regoff', 4), 'LDRH Rt,[Rn,Rm]': ('regoff', 2),
    'LDRB Rt,[Rn,Rm]': ('regoff', 1), 'LDRSH Rt,[Rn,Rm]': ('regoff', 2),
    'STR Rt,[Rn,#imm5*4]': ('imm', 4), 'LDR Rt,[Rn,#imm5*4]': ('imm', 4),
    'STRB Rt,[Rn,#imm5]': ('imm', 1), 'LDRB Rt,[Rn,#imm5]': ('imm', 1),
    'STRH Rt,[Rn,#imm5*2]': ('imm', 2), 'LDRH Rt,[Rn,#imm5*2]': ('imm', 2),
    'STR Rt,[SP,#imm8*4]': ('sp', 4), 'LDR Rt,[SP,#imm8*4]': ('sp', 4),
    'STM Rn!,{list}': ('multi', 4), 'LDM Rn{!},{list}': ('multi', 4),
    'PUSH {list}': ('push', 4), 'POP {list}': ('pop', 4),
    'POP {list,PC}': ('pop', 4),
}


def _layout(layout):
    lay = dict(DEFAULT_LAYOUT)
    if layout:
        lay.update(layout)
    return lay


def _pick(rng, lo, hi, align):
    """A random multiple of align in [lo, hi] (inclusive)."""
    first = -(-lo // align) * align
    last = hi // align * align
    if last < first:
        raise ValueError('no room for an access in the window')
    return first + align * rng.randrange((last - first) // align + 1)


def _pick_sp(rng, lay, lo, hi):
    """An SP in [lo, hi], word aligned: the default PSP if it fits."""
    d = lay['psp_default']
    if lo <= d <= hi and d % 4 == 0:
        return d
    return _pick(rng, lo, hi, 4)


def _sled_target(rng, lay, thumb):
    lo, size = lay['sled']
    slot, guard = lay['slot'], lay['slot_guard']
    while True:
        t = lo + 2 * rng.randrange(size // 2)
        if not slot <= t < slot + guard:
            return t | 1 if thumb else t


def _set_reg(out, r, v):
    """Give register r the value v.  False for SP and PC (not inputs)."""
    v &= 0xFFFFFFFF
    if r < 13:
        out['regs'][r] = v
    elif r == 14:
        out['lr'] = v
    else:
        return False
    return True


def _as_enc(enc_or_hw):
    if isinstance(enc_or_hw, Enc):
        return enc_or_hw
    if isinstance(enc_or_hw, tuple):
        return decode32(*enc_or_hw)
    return decode16(enc_or_hw)


def safe_inputs(enc_or_hw, seed, pass_='B1', layout=None, unaligned=False,
                thumb_bit=True, addr=None):
    """Seeded inputs for one case: a dict with
        regs     R0-R12 (13 values)
        lr       a Thumb address in the sled (unless the case needs LR)
        psp      SP when the case starts (the frame lies below it)
        nzcv     0-15        primask 0/1        control 2 (SPSEL=1, privileged)
    plus, depending on the class:
        target       pc-writing: the address it should branch to (None if the
                     inputs cannot control it)
        target_addr  POP {..,PC}: where the host must store 'target'
        sp_result    sp-writing: the SP the case should leave (None if the
                     inputs cannot control it)

    enc_or_hw is an Enc, a 16-bit value, or a (hw1, hw2) tuple.  The same
    arguments always give the same result.

    Rules by class:
      memory      the base register points into ROM for pass B1, and into
                  layout['scratch'] for any other pass; offset registers are
                  small (<= 0x100) and every touched byte is in the window.
                  Word/halfword accesses are aligned unless unaligned=True.
                  SP-based forms (LDR/STR [SP,#], PUSH, POP) instead pick a
                  PSP inside layout['psp'] with room for the whole access and
                  SP_MARGIN to spare.  LDR literal is PC-relative: no input
                  controls it (it reads near the slot, inside the sled).
      pc-writing  register targets lie in the sled (outside the slot guard),
                  bit 0 set unless thumb_bit=False.  addr is the case's
                  address (default: the slot); ADD PC,Rm reads PC as addr+4.
      sp-writing  ADD SP,Rm gets a small Rm (<= 0x100); MOV SP,Rm gets a
                  valid PSP; the PSP is chosen so the new SP stays inside
                  layout['psp'] with SP_MARGIN to spare above and below.
      others      R0-R12 distinct and non-zero.
    For pass B1 every register starts small (4..0xFFC, word aligned, so any
    address formed from two of them lands in ROM); other passes start from
    distinct random 32-bit values.
    """
    enc = _as_enc(enc_or_hw)
    lay = _layout(layout)
    rng = random.Random('v6m|%s|%04x|%s|%s' % (pass_, enc.hw, enc.hw2, seed))
    pc = (lay['slot'] if addr is None else addr) + 4

    if pass_ == 'B1':
        regs = [4 * k for k in rng.sample(range(1, 0x400), 13)]
    else:
        vals = set()
        while len(vals) < 13:
            v = rng.getrandbits(32)
            if v:
                vals.add(v)
        regs = sorted(vals)
        rng.shuffle(regs)
    out = {'regs': regs, 'lr': _sled_target(rng, lay, True),
           'psp': lay['psp_default'], 'nzcv': rng.randrange(16),
           'primask': 0, 'control': 2}

    cls, f, name = enc.cls, enc.fields, enc.name
    plo, psize = lay['psp']
    phi = plo + psize                       # exclusive
    if cls == 'hint':
        out['primask'] = 1                  # WFI/WFE wake on a masked pending IRQ
    elif cls == 'system' and enc.hw2 is None:
        out['primask'] = rng.randrange(2)   # CPS: start from either state

    if name in _MEM:
        _memory_inputs(enc, out, rng, lay, pass_, unaligned)
    if cls == 'pc-writing':
        _pc_inputs(enc, out, rng, lay, pc, thumb_bit)
    elif cls == 'sp-writing':
        _sp_inputs(enc, out, rng, lay, plo, phi)
    elif name.startswith('MSR'):
        sysm, rn = f['sysm'], f['rn']
        if sysm in (SYSM['MSP'], SYSM['PSP']):
            _set_reg(out, rn, _pick(rng, plo + SP_MARGIN, phi - SP_MARGIN, 4))
        elif sysm == SYSM['PRIMASK']:
            _set_reg(out, rn, rng.randrange(2))
        elif sysm == SYSM['CONTROL']:
            _set_reg(out, rn, rng.choice((2, 3)))   # keep SPSEL=1: stay on PSP
        elif sysm <= 3:
            _set_reg(out, rn, rng.randrange(16) << 28)
    return out


def _memory_inputs(enc, out, rng, lay, pass_, unaligned):
    kind, size = _MEM[enc.name]
    f = enc.fields
    wlo, wsize = lay['rom'] if pass_ == 'B1' else lay['scratch']
    whi = wlo + wsize                       # exclusive
    plo, psize = lay['psp']
    phi = plo + psize
    mis = rng.randrange(1, size) if unaligned and size > 1 else 0

    if kind == 'regoff':
        rn, rm = f['rn'], f['rm']
        if rn == rm:
            # The address is 2*Rn, so always even: an unaligned word access
            # can only be off by 2, and an unaligned halfword is impossible.
            ea = _pick(rng, wlo, whi - size - 3, max(size, 2))
            if unaligned and size == 4:
                ea += 2
            _set_reg(out, rn, ea // 2)
        else:
            ea = _pick(rng, wlo, whi - size - 3, size) + mis
            off = _pick(rng, 0, min(0x100, ea - wlo), size)
            _set_reg(out, rm, off)
            _set_reg(out, rn, ea - off)
    elif kind == 'imm':
        off = f['imm5'] * size
        ea = _pick(rng, wlo + off, whi - size - 3, size) + mis
        _set_reg(out, f['rn'], ea - off)
    elif kind == 'multi':
        n = _popcount(f['list'])
        base = _pick(rng, wlo, whi - 4 * max(n, 1) - 3, 4) + mis
        _set_reg(out, f['rn'], base)
    elif kind == 'sp':
        out['psp'] = _pick_sp(rng, lay, plo + SP_MARGIN, phi - 4 * f['imm8'] - 4)
    elif kind == 'push':
        n = _popcount(f['list']) + f['m']
        out['psp'] = _pick_sp(rng, lay, plo + 4 * n + SP_MARGIN, phi - SP_MARGIN)
    elif kind == 'pop':
        n = _popcount(f['list']) + f['p']
        out['psp'] = _pick_sp(rng, lay, plo + SP_MARGIN, phi - 4 * n)
    # 'literal': PC-relative, nothing to choose


def _pc_inputs(enc, out, rng, lay, pc, thumb_bit):
    f, name = enc.fields, enc.name
    target = _sled_target(rng, lay, thumb_bit)
    out['target'] = None
    if name in ('ADD PC,PC', 'ADD PC,SP,PC') or f.get('rm') in (13, 15):
        return                              # BX SP, MOV PC,PC, ...: not controllable
    if name in ('BX Rm', 'BLX Rm', 'MOV PC,Rm'):
        _set_reg(out, f['rm'], target)
        out['target'] = target
    elif name == 'ADD PC,Rm':
        _set_reg(out, f['rm'], target - pc)
        out['target'] = target
    elif name == 'POP {list,PC}':
        n = _popcount(f['list']) + 1
        out['target'] = target
        out['target_addr'] = out['psp'] + 4 * (n - 1)
    # BL: the target is fixed by the immediate


def _sp_inputs(enc, out, rng, lay, plo, phi):
    f, name = enc.fields, enc.name
    lo, hi = plo + SP_MARGIN, phi - SP_MARGIN
    out['sp_result'] = None
    if name in ('ADD SP,SP,#imm7*4', 'SUB SP,SP,#imm7*4'):
        delta = 4 * f['imm7'] * (1 if name.startswith('ADD') else -1)
        out['psp'] = _pick_sp(rng, lay, max(lo, lo - delta), min(hi, hi - delta))
        out['sp_result'] = out['psp'] + delta
    elif name == 'ADD SP,Rm' and f['rm'] not in (13, 15):
        v = _pick(rng, 0, 0x100, 4)
        _set_reg(out, f['rm'], v)
        out['psp'] = _pick_sp(rng, lay, lo, hi - v)
        out['sp_result'] = out['psp'] + v
    elif name == 'MOV SP,Rm' and f['rm'] != 15:
        if f['rm'] == 13:
            out['sp_result'] = out['psp']   # MOV SP,SP
        else:
            v = _pick(rng, lo, hi, 4)
            _set_reg(out, f['rm'], v)
            out['sp_result'] = v
    # ADD SP,SP / ADD SP,PC / MOV SP,PC: inputs cannot control the result
