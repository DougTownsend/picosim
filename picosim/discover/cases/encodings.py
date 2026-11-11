"""
Campaign B: what every instruction encoding does on the chip.

    B1  classify: all 59,392 16-bit encodings once, from a safe state
        (registers small, so addresses land in ROM; SP in the PSP area)
    B2  data operations: every data encoding x 4 register sets (2 of edge
        values, 2 seeded random) x 4 NZCV inputs (all 16 for the encodings
        that read a flag)
    B3  memory: every load/store encoding x 2 base placements x 2 scratch
        patterns, with addresses in the 4 KB scratch window (SP-based forms
        get an SP inside the window too, so pushes and pops are seen)
    B4  control flow: B<cond> x 16 NZCV, every B, BX/BLX to a Thumb, an
        even and an execute-never target, PC-writing MOV/ADD, POP {PC},
        CPS from both PRIMASK states
    B5  32-bit: first halfwords 0xF000-0xF7FF x 64 second halfwords (bits
        15:12 all 16 values x 4), 0xE800-0xEFFF and 0xF800-0xFFFF x 4,
        MRS/MSR for every SYSm, DMB/DSB/ISB for every option; BL only when
        its target is in the sled (anything else would run unknown code)

Inputs come from v6m.safe_inputs (an encoding table independent of
picosim's decoder).  BKPT halts the core under a debugger; the runner
records that and carries on.  An endless loop (B . and taken B<cond> .) is
stopped by the agent's SysTick watchdog.  Output: encodings.json and the
replay subset (all of B1, 5 % of B2-B5).
"""

import random

from .. import formats as F
from .. import runner as R
from .. import v6m
from ..campaign import h

NAME, TITLE, AGENT_BASED = 'encodings', 'Every encoding: behaviour of all 16-bit and sampled 32-bit', True
LAY = F.LAYOUTS['hi']
SCRATCH = LAY['SCRATCH']
STACK_LAYOUT = {'psp': (SCRATCH + 0x100, 0xE00), 'psp_default': SCRATCH + 0x800}
EDGE = [0, 1, 2, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x80000001, 0x7FFFFFFE, 31, 32, 33,
        0xFFFFFFE0, 0x0000FFFF, 0x00010000, 0x00008000, 0xFFFF8000, 0x000000FF, 0x00000080]
XN_TARGET = 0xE0100001            # execute-never system space: a fetch there faults
STAGES = ('B1', 'B2', 'B3', 'B4', 'B5')
# These branch to SP: they would run whatever the stack holds (never run)
SP_TARGET = {0x46EF: 'MOV PC,SP', 0x4768: 'BX SP', 0x47E8: 'BLX SP'}
# These leave SP where no input can keep it valid (v6m.safe_inputs gives
# them sp_result None): 2*SP, or SP+PC, lands outside memory (0x4008xxxx),
# and MOV SP,PC puts the stack on the code being run.  The next exception
# entry then cannot stack its frame and the core locks up: ADD SP,SP did,
# B1 batch 27 on the chip (2026-11-10, "clearing lockup after double
# fault").  Never run.
SP_UNCONTROLLED = {0x44ED: 'ADD SP,SP', 0x44FD: 'ADD SP,PC', 0x46FD: 'MOV SP,PC'}
NEVER_RUN = {**SP_TARGET, **SP_UNCONTROLLED}
STACK_NAMES = ('PUSH', 'POP', '[SP', 'SP,#')


def _case(id, code, inp, stage, **kw):
    return R.make_case(id, code, regs=inp['regs'], lr=inp['lr'], psp=inp['psp'], nzcv=inp['nzcv'],
                       primask=inp['primask'], control=inp.get('control', 2) & 1, stage=stage, **kw)


def _stacky(enc):
    return any(s in enc.name for s in STACK_NAMES)


def _pop_pc(hw, seed, id, stage):
    """POP {..,PC} preceded by STR r7,[SP,#4k], so the word it loads into PC
    is a known sled address (never what the stack happened to hold)."""
    enc = v6m.decode16(hw)
    inp = v6m.safe_inputs(enc, seed, 'B3', layout=STACK_LAYOUT)
    k = bin(hw & 0xFF).count('1')
    inp['regs'][7] = inp.get('target') or ((LAY['SLED'] + 0x400) | 1)
    return _case(id, h(0x9700 | k, hw), inp, stage, enc=hw, seed=0x2468ACE0)


def b1():
    out = []
    for hw in range(0x10000):
        if v6m.is_prefix32(hw):
            continue
        if hw in NEVER_RUN:
            continue
        enc = v6m.decode16(hw)
        if enc.name.startswith('POP') and enc.cls == 'pc-writing':
            out.append(_pop_pc(hw, 0, hw, 'B1'))           # the PC word is set first
            continue
        inp = v6m.safe_inputs(enc, 0, 'B1')
        if enc.cls == 'hint' and hw not in (0xBF20, 0xBF30):
            inp['primask'] = 0            # the watchdog can end it, whatever it does
        out.append(_case(hw, h(hw), inp, 'B1'))
    return out


def b2():
    out, n = [], 0
    for hw in range(0x10000):
        if v6m.is_prefix32(hw) or v6m.group(hw) != 'data' or hw in NEVER_RUN:
            continue
        enc = v6m.decode16(hw)
        if enc.cls == 'pc-writing':
            continue                      # B4
        flags = range(16) if enc.cls == 'flag-reading' else (0b0000, 0b0110, 0b1001, 0b1111)
        for rs in range(4):
            inp = v6m.safe_inputs(enc, rs, 'B2')
            if rs < 2 and enc.cls == 'data':
                rot = rs * 7 + hw % 5
                inp['regs'] = [EDGE[(rot + i) % len(EDGE)] for i in range(13)]
            for nzcv in flags:
                inp = dict(inp, nzcv=nzcv)
                out.append(_case(0x20000000 + n, h(hw), inp, 'B2', enc=hw))
                n += 1
    return out


def b3():
    out, n = [], 0
    for pattern in (0x13579BDF, 0xECA86420):          # outermost: the agent sets a pattern once per change
        for hw in range(0x10000):
            if v6m.is_prefix32(hw) or v6m.group(hw) != 'memory':
                continue
            enc = v6m.decode16(hw)
            if enc.cls == 'pc-writing':
                continue                      # POP {PC}: B4
            for place in range(2):
                lay = STACK_LAYOUT if _stacky(enc) else None
                inp = v6m.safe_inputs(enc, place, 'B3', layout=lay)
                out.append(_case(0x30000000 + n, h(hw), inp, 'B3', enc=hw, seed=pattern))
                n += 1
    return out


def b4():
    out, n = [], 0

    def add(code, inp, **kw):
        nonlocal n
        out.append(_case(0x40000000 + n, code, inp, 'B4', **kw))
        n += 1
    for hw in range(0xD000, 0xDE00):                       # B<cond>
        inp = v6m.safe_inputs(hw, 0, 'B4')
        for nzcv in range(16):
            add(h(hw), dict(inp, nzcv=nzcv), enc=hw)
    for hw in range(0xE000, 0xE800):                       # B
        add(h(hw), v6m.safe_inputs(hw, 0, 'B4'), enc=hw)
    for hw in range(0x4700, 0x4800):                       # BX / BLX
        if hw in SP_TARGET:
            continue
        enc = v6m.decode16(hw)
        rm = (hw >> 3) & 0xF
        for kind in ('thumb', 'even', 'xn'):
            inp = v6m.safe_inputs(enc, 0, 'B4', thumb_bit=kind != 'even')
            if kind == 'xn' and rm < 13:
                inp['regs'][rm] = XN_TARGET
            elif kind == 'xn' and rm == 14:
                inp['lr'] = XN_TARGET
            elif kind == 'xn':
                continue
            add(h(hw), inp, enc=hw, target_kind=kind)
    for hw in range(0x10000):                              # PC-writing MOV/ADD and POP {..,PC}
        if v6m.is_prefix32(hw):
            continue
        enc = v6m.decode16(hw)
        if enc.cls != 'pc-writing' or 0x4700 <= hw < 0x4800 or hw in SP_TARGET:
            continue
        for seed in range(2):
            if enc.name.startswith('POP'):
                out.append(_pop_pc(hw, seed, 0x40000000 + n, 'B4'))
                n += 1
            else:
                add(h(hw), v6m.safe_inputs(enc, seed, 'B4'), enc=hw)
    for hw in range(0xB660, 0xB680):                       # CPS
        for pm in (0, 1):
            add(h(hw), dict(v6m.safe_inputs(hw, 0, 'B4'), primask=pm), enc=hw)
    return out


def _bl_target(hw1, hw2, slot):
    s = (hw1 >> 10) & 1
    j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
    off = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1)
    if s:
        off -= 1 << 25
    return (slot + 4 + off) & 0xFFFFFFFF


def b5():
    out, n = [], 0
    rng = random.Random('B5')
    lo, hi = LAY['SLED'] + 0x40, LAY['SLED'] + LAY['SLED_SIZE'] - 0x40

    def add(hw1, hw2, inp=None, **kw):
        nonlocal n
        enc = v6m.decode32(hw1, hw2)
        if enc.name.startswith('BL') and not lo <= _bl_target(hw1, hw2, LAY['SLOT']) < hi:
            return
        inp = inp or v6m.safe_inputs((hw1, hw2), n % 4, 'B5')
        out.append(_case(0x50000000 + n, h(hw1, hw2), inp, 'B5', enc=hw1 << 16 | hw2, **kw))
        n += 1
    for hw1 in range(0xF000, 0xF800):
        for top in range(16):
            for k in range(4):
                add(hw1, (top << 12) | rng.randrange(0x1000))
    for hw1 in list(range(0xE800, 0xF000)) + list(range(0xF800, 0x10000)):
        for k in range(4):
            add(hw1, rng.randrange(0x10000))
    for sysm in range(256):
        for rd in (0, 7):
            add(0xF3EF, 0x8000 | (rd << 8) | sysm)                     # MRS rd, sysm
        add(0xF380, 0x8800 | sysm)                                     # MSR sysm, r0
    for op in (4, 5, 6):                                               # DSB DMB ISB, every option
        for opt in range(16):
            add(0xF3BF, 0x8F00 | (op << 4) | opt)
    return out


GENERATORS = {'B1': b1, 'B2': b2, 'B3': b3, 'B4': b4, 'B5': b5}
SCRATCH_WORDS = {'B1': 0, 'B2': 0, 'B3': 1024, 'B4': 1024, 'B5': 0}


def stage_kw(stage, kind):
    kw = {'wd_reload': 200_000 if kind != 'standin' else 3000}
    if SCRATCH_WORDS[stage]:
        kw['scratch_words'] = SCRATCH_WORDS[stage]
        kw['flags'] = F.HF_SYSTICK
    else:
        kw['flags'] = F.HF_SYSTICK | F.HF_NO_SCRATCH
    return kw


def _stages(ctx):
    """The stages asked for with --stages.  Names of other campaigns' stages
    are left out (one option serves every campaign); none of ours means all."""
    want = getattr(ctx.opts, 'stages', None) if ctx else None
    mine = [s.strip() for s in (want or '').split(',') if s.strip().startswith('B')]
    for s in mine:
        if s not in STAGES:
            raise ValueError(f"no stage {s}: the stages are {', '.join(STAGES)}")
    return mine or list(STAGES)


def dry_cases(ctx):
    return [(s, GENERATORS[s](), stage_kw(s, 'openocd')) for s in _stages(ctx)]


def _check_progress(ctx, stage, n, keep=None):
    """Say what B does after a stage's batches: run every result through
    picosim (B2's 389,584 take minutes, and the window used to show
    nothing: 2026-11-10).  Stop works here too."""
    import time
    ctx.status(NAME, 'partial', step=f"{stage}: comparing {n:,} results with picosim", **(keep or {}))
    ctx.log(f"{stage}: all its batches ran on the chip; comparing {n:,} results with picosim "
            "(on this computer; the board is idle) ...")
    t0 = time.monotonic()

    def progress(done, total):
        if ctx.stop_flag is not None and ctx.stop_flag.is_set():
            raise R.RunError(f"stopped by request while comparing {stage}'s results")
        rate = done / max(time.monotonic() - t0, 1e-6)
        ctx.log(f"{stage}: compared {done:,} of {total:,} ({int((total - done) / rate) + 1} s left)")
    return progress


def run(ctx):
    from .. import facts as FX
    from .. import check
    summary, state0, diffs, path = {}, None, 0, None
    for s in _stages(ctx):
        # keep pointing at the facts of the stages already in, should this one stop
        ctx.status(NAME, 'partial', step=s, **({'facts': path, 'differences': diffs} if path else {}))
        cases = GENERATORS[s]()
        if ctx.label != 'chip1' and s != 'B1':
            rng = random.Random(f"H|{s}")         # campaign H: a 10 % sample on the second chip
            cases = [c for c in cases if rng.random() < 0.10]
        recs, state = ctx.stage(NAME, s, cases, **stage_kw(s, ctx.kind))
        if not recs:
            continue
        state0 = state0 or state
        ends = {}
        for c, r in recs:
            k = R.ending(c, r, LAY)[0]
            ends[k] = ends.get(k, 0) + 1
        res = check.check_records(recs, LAY, SCRATCH_WORDS[s], progress=_check_progress(ctx, s, len(recs),
                                                     {'facts': path, 'differences': diffs} if path else None))
        diffs += res.counts['differs']
        summary[s] = {'cases': len(recs), 'endings': ends, 'check': dict(res.counts),
                      'compared_in_part': dict(res.notes),     # check.NOTE_UNSET / NOTE_ROM: not differences
                      'repeat': state.get('repeat'),
                      'differences': [{'status': k[0], 'class': k[1], 'encoding': k[2], 'cases': g['n'],
                                       'example': g['example'], 'known': g['known']}
                                      for k, g in res.groups.items() if k[0] != check.NOT_COMPARED]}
        FX.write_replay(f"encodings-{s}" + ('' if ctx.label == 'chip1' else f"-{ctx.label}"), recs, state, ctx.kind,
                        fraction=1.0 if s == 'B1' else 0.05, seed=1)
        path = _write_facts(ctx, summary, state0, diffs)
    return path or _write_facts(ctx, summary, state0, diffs)


def _write_facts(ctx, summary, state0, diffs):
    """encodings.json from the stages finished so far.  Called after each
    stage, so a run that stops in a later stage (a pulled cable, Stop, a
    power cut) still leaves the finished stages' facts: on 2026-11-10 B1 and
    B2 finished on the chip and B3 stopped, and no encodings.json was
    written at all.  The status stays 'partial' until every stage is in."""
    from .. import facts as FX
    defined = sum(1 for hw in range(0x10000) if not v6m.is_prefix32(hw)
                  and v6m.classify(hw) not in ('undefined', 'udf'))
    fs = [FX.fact('encodings.v6m_defined_16bit', defined, source='derived', note='from v6m.py (the ARM ARM)'),
          FX.fact('encodings.never_run', {f"0x{hw:04X}": name for hw, name in sorted(NEVER_RUN.items())},
                  source='derived', note='a branch to SP, or an SP no input can keep valid: they would lock the core up')]
    if 'B1' in summary:
        fs.append(FX.fact('encodings.B1.endings', summary['B1']['endings']))
    prov = ctx.provenance(state0, campaign='B')
    whole = set(summary) >= set(STAGES) and not ctx.limit
    prov['stages_done'] = sorted(summary)
    path = ctx.write_facts('encodings.json', 'encodings', prov, fs, stages=summary)
    ctx.status(NAME, 'done' if whole else 'partial', step=None if whole else _part(ctx, summary),
               facts=path, differences=diffs)
    return path


def _part(ctx, ran):
    return f"only {','.join(ran) or 'none'}" + (f", {ctx.limit} cases each" if ctx.limit else '')

