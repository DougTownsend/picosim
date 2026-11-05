"""
Replay one recorded case in picosim's core and say how picosim ends it.

picosim (until Stage 2) has one flat 64 KiB space, so the chip's addresses
are mapped onto it:

    chip                                   picosim
    ROM          0x00000000-0x00003FFF     0x0000-0x3FFF   (hwfacts/rom.bin if present)
    the sled     slot-0x4000 .. slot+0x4000 0x4000-0xBFFF  (slot at 0x8000 + slot % 4)
    scratch      SCRATCH (+4 KiB)          0xC000-0xCFFF
    PSP area     PSP_LO..PSP_HI            0xF000-0xF7FF

The window around the slot starts on a word, so an address keeps its
alignment in picosim (word loads, PC-relative literals).  A register value
inside one of these ranges is mapped on the way in; on
the way out a chip value and a picosim value are the same if they are equal
or if the chip's maps to picosim's (same()).  Once picosim has the chip's
real memory map (Stage 2) the map becomes the identity: SimMap(direct=True).
"""

import os

from .. import timing
from .._picosim_core import CPUCore
from . import formats as F

SIM_SLOT = 0x8000
SIM_SLED = (0x4000, 0xC000)
SIM_SCRATCH = 0xC000
SIM_PSP = 0xF000
HWFACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'hwfacts')


class SimMap:
    def __init__(self, lay, slot, direct=False):
        self.direct = direct
        self.ranges = [] if direct else [
            (0, 0x4000, 0),
            ((slot & ~3) - 0x4000, (slot & ~3) + 0x4000, SIM_SLED[0]),
            (lay['SCRATCH'], lay['SCRATCH'] + lay['SCRATCH_MAX'], SIM_SCRATCH),
            (lay['PSP_LO'], lay['PSP_HI'], SIM_PSP),
        ]

    def to_sim(self, v):
        for lo, hi, s in self.ranges:
            if lo <= v < hi:
                return v - lo + s
        return v

    def to_hw(self, v):
        for lo, hi, s in self.ranges:
            if s <= v < s + (hi - lo):
                return v - s + lo
        return v

    def same(self, sim_v, hw_v):
        return sim_v == hw_v or self.to_sim(hw_v) == sim_v


_ROM = None


def rom():
    global _ROM
    if _ROM is None:
        p = os.path.join(HWFACTS, 'rom.bin')
        _ROM = open(p, 'rb').read()[:0x4000] if os.path.exists(p) else bytes(0x4000)
    return _ROM


class _Bus(Exception):
    pass


class _SVC(Exception):
    pass


def run(case, lay, scratch_words, max_steps=None, code=None, lenient_bus=False):
    """Run a case (a load()ed case record with 'code_bytes') in picosim.
    Returns {'end': (kind, hw-equivalent address), 'regs': [16], 'flags':
    nzcv, 'changed': [(offset, value)], 'error': text, 'profile': [...]}."""
    code = case['code_bytes'] if code is None else code
    m = SimMap(lay, case['slot'])
    slot = m.to_sim(case['slot'])                     # SIM_SLOT, plus the chip slot's offset in its word
    mem = bytearray(0x10000)
    mem[0:0x4000] = rom()
    mem[SIM_SLED[0]:SIM_SLED[1]] = F.SLED_HALF.to_bytes(2, 'little') * ((SIM_SLED[1] - SIM_SLED[0]) // 2)
    mem[slot:slot + len(code)] = code
    pat = F.pattern(case['seed'], scratch_words) if scratch_words else b''
    mem[SIM_SCRATCH:SIM_SCRATCH + len(pat)] = pat
    core = CPUCore()
    core.strict = True
    core.load_memory(bytes(mem), 0)

    def bus(*a):
        if lenient_bus:                  # timing only: a peripheral reads as 0
            return 0
        raise _Bus()

    def svc(num):
        raise _SVC()
    core.peripheral_read = bus
    core.peripheral_write = bus
    core.svc_handler = svc
    for i in range(13):
        core.set_reg(i, m.to_sim(case[f'r{i}']))
    psp = m.to_sim(case['psp'])
    core.set_reg(13, psp)
    core.PSP = psp
    core.set_reg(14, m.to_sim(case['lr']))
    core.CONTROL = 2 | (case['control'] & 1)
    core.PRIMASK = case['primask'] & 1
    f = case['xpsr'] >> 28
    core.N, core.Z, core.C, core.V = bool(f & 8), bool(f & 4), bool(f & 2), bool(f & 1)
    core.pc = slot
    core.reset_profile()
    end_sim, error = None, None
    for _ in range(max_steps or (16 + len(code))):
        try:
            core.step()
        except _SVC:
            end_sim, error = ('svc', core.pc + 2), 'SVC'
            break
        except _Bus:
            end_sim, error = ('fault', core.pc), 'bus fault (an address outside picosim)'
            break
        except Exception as e:                       # noqa: BLE001 - picosim's fault, whatever it is
            end_sim, error = ('fault', core.pc), str(e).split('\n')[0][:200]
            break
        if core.halted:
            end_sim = ('halted', core.pc)
            break
    else:
        end_sim = ('running', core.pc)
    kind, pc = end_sim
    if kind == 'fault':
        if pc == slot + len(code) and _udf_at(core, pc):
            kind = 'fell-through'
        elif pc != slot and SIM_SLED[0] <= pc < SIM_SLED[1] and _udf_at(core, pc):
            kind = 'sled'
    regs = list(core.regs)
    changed = []
    if scratch_words:
        now = core.get_mem_slice(SIM_SCRATCH, 4 * scratch_words)
        for i in range(scratch_words):
            if now[4 * i:4 * i + 4] != pat[4 * i:4 * i + 4]:
                changed.append((4 * i, int.from_bytes(now[4 * i:4 * i + 4], 'little')))
    return {'end': (kind, m.to_hw(pc)), 'regs': regs,
            'flags': (core.N << 3) | (core.Z << 2) | (core.C << 1) | core.V,
            'changed': changed, 'error': error, 'map': m, 'core': core}


def _udf_at(core, pc):
    return core.get_mem_slice(pc, 2) == F.SLED_HALF.to_bytes(2, 'little')


def body_cycles(case, lay, scratch_words=0):
    """picosim's RP2040-model cycles for a timing case's body (its code
    without the first and last instruction, the two SysTick reads), run
    with the same scratch window as on the chip (its loads steer branches)."""
    body = case['code_bytes'][2:-2]
    r = run(case, lay, scratch_words, max_steps=4096, code=body, lenient_bus=True)
    total = 0
    for e in r['core'].profile():
        total += timing.cycles('m0plus', e['hw1'], e['hw2'], e)
    return total, r
