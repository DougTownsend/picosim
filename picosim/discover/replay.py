"""
Replay one recorded case in picosim's core and say how picosim ends it.

picosim has the chip's memory map (memmap.py), so a case runs at its chip
addresses: the ROM (hwfacts/rom.bin if present, else zeros), the sled
around the slot (slot-0x4000 .. slot+0x4000), the scratch window and the PSP
area, all in SRAM.  The one exception is a window picosim does not have (the
non-striped SRAM alias at 0x21000000, a D1 code site): it is moved to SPARE,
a free part of SRAM, keeping its offset in its word so an address keeps its
alignment (word loads, PC-relative literals).  A register value inside a
moved window is mapped on the way in; on the way out a chip value and a
picosim value are the same if they are equal or if the chip's maps to
picosim's (same()).

Memory the agent never sets before a case (the PSP area above the case's
frame, a scratch window B leaves unfilled, results, the batch) holds, on
the chip, whatever earlier cases left there: no replay can know it.  Each
such SRAM word holds an UNSET marker naming its own address, so a register
that loads one is seen (unset_addr()) and is not compared (2026-11-11:
B1's LDR [SP], POP and POP-after-STR cases, 2,558 of them).  The 32-byte
frame the agent writes below the case's PSP is laid out as the chip's.

A store into ROM is dropped and counted, as the chip does (the ROM ignores
it), so the rest of what the instruction does is compared.  For students
picosim stops there instead (core.rom_store_stop, on by default).
"""

import os

from .. import timing
from .._picosim_core import CPUCore
from ..memmap import Image, ROM_BASE, ROM_SIZE, SRAM_BASE, SRAM_SIZE, span
from . import formats as F

SLED_REACH = 0x4000                 # the sled picosim lays out on each side of the slot
SPARE = 0x20030000                  # where a window picosim does not have is moved
UNSET_TAG = 0xBAD00000              # an unset SRAM word: UNSET_TAG | its word index in SRAM
UNSET_MASK = 0xFFF00000
HWFACTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'hwfacts')


class SimMap:
    """Chip address <-> picosim address: the identity, except for windows
    outside picosim's memory, which move to SPARE."""

    def __init__(self, lay, slot, direct=False):
        self.direct = direct
        sled = ((slot & ~3) - SLED_REACH, (slot & ~3) + SLED_REACH)
        self.windows = [sled, (lay['SCRATCH'], lay['SCRATCH'] + lay['SCRATCH_MAX']),
                        (lay['PSP_LO'], lay['PSP_HI'])]
        self.ranges = []                  # (chip lo, chip hi, picosim lo)
        spare = SPARE
        for lo, hi in ([] if direct else self.windows):
            if span(lo, hi - lo) is None:
                self.ranges.append((lo, hi, spare))
                spare += (hi - lo + 0xFFF) & ~0xFFF
        self.sled = (self.to_sim(sled[0]), self.to_sim(sled[0]) + (sled[1] - sled[0]))

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
        if os.path.exists(p):
            with open(p, 'rb') as f:
                _ROM = f.read()[:ROM_SIZE]
        else:
            _ROM = bytes(ROM_SIZE)
    return _ROM


_UNSET = None


def unset_image():
    """All of SRAM as UNSET markers (built once)."""
    global _UNSET
    if _UNSET is None:
        _UNSET = b''.join((UNSET_TAG | i).to_bytes(4, 'little') for i in range(SRAM_SIZE // 4))
    return _UNSET


def unset_addr(v):
    """The SRAM address an UNSET marker names, or None if v is not one."""
    if v & UNSET_MASK != UNSET_TAG:
        return None
    i = v & ~UNSET_MASK
    return SRAM_BASE + 4 * i if 4 * i < SRAM_SIZE else None


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
    slot = m.to_sim(case['slot'])
    mem = Image()
    mem.write(ROM_BASE, rom())
    mem.write(SRAM_BASE, unset_image())
    lo, hi = m.sled
    mem.write(lo, F.SLED_HALF.to_bytes(2, 'little') * ((hi - lo) // 2))
    mem.write(slot, code)
    pat = F.pattern(case['seed'], scratch_words) if scratch_words else b''
    scratch = m.to_sim(lay['SCRATCH'])
    if pat:
        mem.write(scratch, pat)
    # the frame the agent's exception return popped, still below the PSP
    # (written last, as the agent does)
    frame = m.to_sim(case['psp']) - 32
    if span(frame, 32) is not None:
        words = [m.to_sim(case[k]) for k in ('r0', 'r1', 'r2', 'r3', 'r12', 'lr', 'slot')]
        words.append(case['xpsr'] | 0x01000000)
        mem.write(frame, b''.join(w.to_bytes(4, 'little') for w in words))
    core = CPUCore()
    core.strict = True
    core.rom_store_stop = False           # the chip's ROM ignores a store: compare the rest
    mem.load_into(core)

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
        elif pc != slot and m.sled[0] <= pc < m.sled[1] and _udf_at(core, pc):
            kind = 'sled'
    regs = list(core.regs)
    changed = []
    if scratch_words:
        now = core.get_mem_slice(scratch, 4 * scratch_words)
        for i in range(scratch_words):
            if now[4 * i:4 * i + 4] != pat[4 * i:4 * i + 4]:
                changed.append((4 * i, int.from_bytes(now[4 * i:4 * i + 4], 'little')))
    return {'end': (kind, m.to_hw(pc)), 'regs': regs,
            'flags': (core.N << 3) | (core.Z << 2) | (core.C << 1) | core.V,
            'changed': changed, 'error': error, 'map': m, 'core': core,
            'rom_stores': core.rom_stores_dropped}


def _udf_at(core, pc):
    return span(pc, 2) is not None and core.get_mem_slice(pc, 2) == F.SLED_HALF.to_bytes(2, 'little')


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
