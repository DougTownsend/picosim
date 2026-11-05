"""
Campaign E: random programs, compared with picosim as they are captured.

Each block is 8-64 instructions drawn from the encodings campaign B found
to be defined (or, before B has run, the ARM ARM's v6m.py classes), built
so that it is always safe to run:

  * results go to R0-R2 (and R8-R12 through MOV/ADD), never to R4-R7;
  * loads and stores use R4 as the base, with immediate offsets only, so
    every access is inside the scratch window, and a block stores at most 4
    words (so the agent reports every change);
  * branches go forward inside the block, or form a loop counted down in R3
    (the only time R3 is written) with a fixed bound;
  * R5-R7 are the timing wrapper's (two SysTick reads around the block).

Every block runs once on the chip and once in picosim, and is compared on
registers, flags, changed memory and cycles.  A block that differs is shrunk
on the chip by delta debugging (removing ops while the same difference
stays) to a minimal program, which is written to hwtests/e###_<what>.s as a
trace test (only from a real chip: the stand-in's go to the cache).

Seeds are fixed: round k always makes the same 1000 blocks, so an overnight
run (--hours) resumes where it stopped.  Output: fuzz.json.
"""

import os
import random
import time

from .. import formats as F
from .. import replay
from .. import runner as R
from ..campaign import h, timing_case

NAME, TITLE, AGENT_BASED = 'fuzz', 'Random programs: differential fuzzing with shrinking', True
LAY = F.LAYOUTS['hi']
BASE = LAY['SCRATCH'] + 0x100              # R4: the blocks' memory base
SCRATCH_WORDS = 0x200 // 4 + 0x80 // 4      # covers base .. base + 0x80, and the words below it
BLOCKS_PER_ROUND = 1000
MAX_STORES = 4
# R4 holds a chip address (picosim's differs), so it is only ever a load/store base
DST, SRC = (0, 1, 2), (0, 1, 2, 3)
HI_DST, HI_SRC = (8, 9, 10, 11, 12), (0, 1, 2, 3, 8, 9, 10, 11, 12)


# ── blocks as ops (so shrinking can remove ops and re-encode branches) ──────
# {'k': 'raw', 'hw': int, 'store': bool}
# {'k': 'b', 'to': index}  {'k': 'bc', 'cond': c, 'to': index}   (forward)
# {'k': 'loop', 'n': count, 'body': [raw ops]}   (movs r3,#n; body; subs r3,#1; bne)

def _raw(rng, allow_store=True):
    r = rng.random
    d, s, t = rng.choice(DST), rng.choice(SRC), rng.choice(SRC)
    kinds = ['movi', 'addi3', 'addi8', 'addr', 'cmpi', 'shift', 'alu', 'ext', 'rev', 'hi', 'load', 'nop']
    if allow_store:
        kinds.append('store')
    k = rng.choice(kinds)
    if k == 'movi':
        return 0x2000 | d << 8 | rng.randrange(256), False
    if k == 'addi3':
        return (0x1C00 if r() < 0.5 else 0x1E00) | rng.randrange(8) << 6 | s << 3 | d, False
    if k == 'addi8':
        return (0x3000 if r() < 0.5 else 0x3800) | d << 8 | rng.randrange(256), False
    if k == 'addr':
        return (0x1800 if r() < 0.5 else 0x1A00) | t << 6 | s << 3 | d, False
    if k == 'cmpi':
        return 0x2800 | rng.choice(SRC) << 8 | rng.randrange(256), False
    if k == 'shift':
        return rng.choice((0x0000, 0x0800, 0x1000)) | rng.randrange(32) << 6 | s << 3 | d, False
    if k == 'alu':                                          # AND..MVN, Rdn in DST
        return 0x4000 | rng.randrange(16) << 6 | s << 3 | d, False
    if k == 'ext':
        return 0xB200 | rng.randrange(4) << 6 | s << 3 | d, False
    if k == 'rev':
        return 0xBA00 | rng.choice((0, 1, 3)) << 6 | s << 3 | d, False
    if k == 'hi':
        op = rng.choice((0x4400, 0x4500, 0x4600))           # ADD, CMP, MOV (high registers)
        rd = rng.choice(HI_DST + DST)
        rm = rng.choice(HI_SRC)
        if op == 0x4500 and rd < 8 and rm < 8:
            rm = rng.choice(HI_DST)
        return op | (rd >> 3) << 7 | rm << 3 | (rd & 7), False
    if k == 'load':
        w = rng.choice((0x6800, 0x7800, 0x8800))           # LDR, LDRB, LDRH [R4, #imm]
        return w | rng.randrange(32) << 6 | 4 << 3 | d, False
    if k == 'store':
        w = rng.choice((0x6000, 0x7000, 0x8000))
        return w | rng.randrange(32) << 6 | 4 << 3 | rng.choice(SRC), True
    return 0xBF00, False


def make_block(rng):
    n = rng.randrange(8, 65)
    ops, stores = [], 0
    while len(ops) < n:
        x = rng.random()
        if x < 0.07 and len(ops) < n - 2:
            ops.append({'k': 'bc', 'cond': rng.randrange(14), 'to': None})
        elif x < 0.10 and len(ops) < n - 2:
            ops.append({'k': 'b', 'to': None})
        elif x < 0.13:
            body = []
            for _ in range(rng.randrange(1, 6)):
                hw, st = _raw(rng, False)
                body.append({'k': 'raw', 'hw': hw, 'store': st})
            ops.append({'k': 'loop', 'n': rng.randrange(1, 9), 'body': body})
        else:
            hw, st = _raw(rng, stores < MAX_STORES)
            stores += st
            ops.append({'k': 'raw', 'hw': hw, 'store': st})
    for i, o in enumerate(ops):
        if o['k'] in ('b', 'bc'):
            o['to'] = rng.randrange(i + 1, min(len(ops), i + 12) + 1)
    return ops


def _size(o):
    return {'raw': 2, 'b': 2, 'bc': 2}.get(o['k']) or 2 + 2 * len(o['body']) + 4


def encode(ops):
    """The block's bytes.  A branch whose target op is gone goes to the next op."""
    addr, at = 0, []
    for o in ops:
        at.append(addr)
        addr += _size(o)
    at.append(addr)
    out = []
    for i, o in enumerate(ops):
        if o['k'] == 'raw':
            out.append(o['hw'])
        elif o['k'] in ('b', 'bc'):
            to = min(o['to'], len(ops))
            off = (at[to] - (at[i] + 4)) // 2
            out.append(0xE000 | (off & 0x7FF) if o['k'] == 'b' else 0xD000 | o['cond'] << 8 | (off & 0xFF))
        else:
            out.append(0x2300 | o['n'])                   # movs r3, #n
            out += [b['hw'] for b in o['body']]
            out.append(0x3B01)                            # subs r3, #1
            back = -(len(o['body']) + 2)                  # bne: back to the body's start
            out.append(0xD100 | (back & 0xFF))
    return h(*out)


def block_case(id, ops, rng_state):
    rng = random.Random(rng_state)
    regs = [rng.getrandbits(32) for _ in range(5)] + [0, 0, 0] + [rng.getrandbits(32) for _ in range(5)]
    regs[4] = BASE
    return timing_case(id, encode(ops), regs=regs, nzcv=rng.randrange(16), seed=rng.getrandbits(32),
                       ops=ops, rng_state=rng_state)


def round_cases(k, n=BLOCKS_PER_ROUND):
    rng = random.Random(f"E|{k}")
    cases = [timing_case(k * 100000, b'')]                # calibration
    for i in range(n):
        ops = make_block(rng)
        cases.append(block_case(k * 100000 + 1 + i, ops, f"E|{k}|{i}"))
    return cases


# ── comparing one block ─────────────────────────────────────────────────────

def compare(c, r, overhead):
    """None if picosim agrees, else (class, detail)."""
    end = R.ending(c, r, LAY)[0]
    body = c['code_bytes'][2:-2]
    if end != 'fell-through':
        return ('faults', f"the chip ended with {end}")
    sim = replay.run(c, LAY, SCRATCH_WORDS, max_steps=4096, code=body)
    if sim['end'][0] != 'fell-through':
        return ('faults', f"picosim ended with {sim['end'][0]}: {sim['error']}")
    m = sim['map']
    hw = [r['f_r0'], r['f_r1'], r['f_r2'], r['f_r3'], r['r4']] + [r[f'r{i}'] for i in (8, 9, 10, 11)] + [r['f_r12']]
    sw = [sim['regs'][i] for i in (0, 1, 2, 3, 4, 8, 9, 10, 11, 12)]
    names = ['r0', 'r1', 'r2', 'r3', 'r4', 'r8', 'r9', 'r10', 'r11', 'r12']
    bad = [f"{n}: chip 0x{a:08X}, picosim 0x{b:08X}" for n, a, b in zip(names, hw, sw) if not m.same(b, a)]
    if bad:
        return ('registers', '; '.join(bad))
    if r['f_xpsr'] >> 28 != sim['flags']:
        return ('flags', f"NZCV chip {r['f_xpsr'] >> 28:04b}, picosim {sim['flags']:04b}")
    hc = r['changed'] or []
    if sorted(hc) != sorted((o, v) for o, v in sim['changed']):
        return ('memory', f"chip {hc}, picosim {sim['changed']}")
    if overhead is not None:
        cyc = (r['r6'] - r['r5']) % (1 << 24) - overhead
        sc, _ = replay.body_cycles(c, LAY, SCRATCH_WORDS)
        if sc != cyc:
            return ('timing', f"chip {cyc} cycles, picosim {sc}")
    return None


# ── shrinking ───────────────────────────────────────────────────────────────

def names(ops):
    """The block's instructions by name (what a minimal pattern is made of)."""
    from ..v6m import decode16
    out = []
    for o in ops:
        if o['k'] == 'raw':
            out.append(decode16(o['hw']).name)
        elif o['k'] == 'loop':
            out += ['loop'] + [decode16(b['hw']).name for b in o['body']]
        else:
            out.append('B' if o['k'] == 'b' else 'B<cond>')
    return out


def explained(ops, cls, patterns):
    """Does the block contain a minimal pattern already found for this class?"""
    from collections import Counter
    have = Counter(names(ops))
    return any(c == cls and all(have[n] >= k for n, k in Counter(p).items()) for c, p in patterns)


def _without(ops, idx):
    keep = [i for i in range(len(ops)) if i not in idx]
    remap = {old: new for new, old in enumerate(keep)}
    out = []
    for old in keep:
        o = dict(ops[old])
        if o['k'] in ('b', 'bc'):
            to = o['to']
            while to < len(ops) and to not in remap:
                to += 1
            o['to'] = remap.get(to, len(keep))
            if o['to'] <= remap[old]:
                continue
        out.append(o)
    return out


def shrink(ctx, case, cls, overhead, name, max_rounds=40):
    """Delta debugging on the chip: the smallest op list that still shows
    a difference of class `cls`."""
    ops = case['ops']
    n_run = 0
    chunk = max(1, len(ops) // 2)
    for _ in range(max_rounds):
        cands = []
        for i in range(0, len(ops), chunk):
            cand = _without(ops, set(range(i, min(len(ops), i + chunk))))
            if cand:
                cands.append(cand)
        # loop bodies one op at a time
        for j, o in enumerate(ops):
            if o['k'] == 'loop' and len(o['body']) > 1:
                for b in range(len(o['body'])):
                    cand = [dict(x) for x in ops]
                    cand[j] = dict(o, body=o['body'][:b] + o['body'][b + 1:])
                    cands.append(cand)
        if not cands:
            break
        cases = [timing_case(0, b'')] + [block_case(1 + i, cand, case['rng_state']) for i, cand in enumerate(cands)]
        n_run += 1
        recs, _ = ctx.stage(NAME, f"{name}-shrink{n_run}", cases, scratch_words=SCRATCH_WORDS, timing=True)
        ov = next(((r['r6'] - r['r5']) % (1 << 24) for c, r in recs if c['code_len'] == 4), overhead)
        found = None
        for (c, r), cand in zip(recs[1:], cands):
            d = compare(c, r, ov)
            if d and d[0] == cls:
                found = cand
                break
        if found is not None:
            ops = found
            chunk = max(1, min(chunk, len(ops) // 2))
        elif chunk > 1:
            chunk //= 2
        else:
            break
    return ops


def write_test(ctx, ops, case, cls, detail, number):
    """A trace test for hwtests/ (real chip) or the cache (stand-in)."""
    from .. import facts as FX
    from ..v6m import decode16
    lines = []
    rng = random.Random(case['rng_state'])
    regs = [rng.getrandbits(32) for _ in range(5)] + [0, 0, 0] + [rng.getrandbits(32) for _ in range(5)]
    nzcv, seed = rng.randrange(16), rng.getrandbits(32)
    code = encode(ops)
    words = [F.pattern_word(seed, i) for i in range(SCRATCH_WORDS)]
    first = decode16(int.from_bytes(code[:2], 'little')).name if code else '(empty)'
    what = cls.replace(' ', '_')
    lines += [f"@ Title: Campaign E: picosim and the RP2040 differ ({cls}, {first})",
              f"@ About: Found by random programs (round block {case['id']}) and shrunk on the chip. "
              f"{detail}. The block runs once with the registers, flags and memory the chip had.",
              f"@ Kind: {'bench' if cls == 'timing' else 'trace'}", "",
              "        .syntax unified", "        .cpu cortex-m0plus", "        .thumb", "", "        .text",
              "main:", "        push    {r4-r7, lr}"]
    for i in (8, 9, 10, 11, 12):
        lines += [f"        ldr     r0, =0x{regs[i]:08X}", f"        mov     r{i}, r0"]
    lines += [f"        ldr     r0, =0x{nzcv << 28:08X}", "        msr     APSR_nzcvq, r0",
              "        ldr     r4, =scratch + 0x100"]
    for i in (0, 1, 2, 3):
        lines.append(f"        ldr     r{i}, =0x{regs[i]:08X}")
    lines.append("        @ the block, as the chip ran it")
    for k in range(0, len(code), 2):
        hw = int.from_bytes(code[k:k + 2], 'little')
        lines.append(f"        .hword  0x{hw:04X}            @ {decode16(hw).name}")
    lines += ["        pop     {r4-r7, pc}", "        .ltorg", "", "        .data", "        .align  2", "scratch:"]
    for i in range(0, SCRATCH_WORDS, 4):
        lines.append("        .word   " + ', '.join(f"0x{w:08X}" for w in words[i:i + 4]))
    text = '\n'.join(lines) + '\n'
    if ctx.kind == 'standin':
        d = os.path.join(FX.root('standin'), 'fuzz-tests')
    else:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'hwtests')
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"e{number:03d}_{what}.s")
    with open(path, 'w') as f:
        f.write(text)
    return path


# ── the campaign ────────────────────────────────────────────────────────────

def dry_cases(ctx):
    return [('round-0', round_cases(0), {'scratch_words': SCRATCH_WORDS, 'timing': True})]


def run(ctx):
    import json
    from .. import facts as FX
    hours = getattr(ctx.opts, 'hours', None)
    rounds = getattr(ctx.opts, 'rounds', None) or (10 ** 9 if hours else 1)
    end = time.monotonic() + 3600 * hours if hours else None
    log_path = os.path.join(ctx.root, NAME, 'found.json')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    found = json.load(open(log_path)) if os.path.exists(log_path) else {'blocks': 0, 'differences': []}
    patterns = [(d['class'], d['pattern']) for d in found['differences'] if 'pattern' in d]
    k = found.get('next_round', 0)
    state = None
    while rounds > 0 and (end is None or time.monotonic() < end):
        if ctx.stop_flag and ctx.stop_flag.is_set():
            break
        ctx.status(NAME, 'partial', step=f"round {k}", done=found['blocks'])
        cases = round_cases(k, ctx.limit or BLOCKS_PER_ROUND)
        recs, state = ctx.stage(NAME, f"round-{k:05d}", cases, scratch_words=SCRATCH_WORDS, timing=True)
        ov = next(((r['r6'] - r['r5']) % (1 << 24) for c, r in recs if c['code_len'] == 4), None)
        by_id = {c['id']: c for c in cases}
        for c, r in recs:
            if c['code_len'] == 4:
                continue
            found['blocks'] += 1
            d = compare(c, r, ov)
            if not d:
                continue
            src = by_id[c['id']]
            if explained(src['ops'], d[0], patterns):
                found['explained'] = found.get('explained', 0) + 1     # the same cause again
                continue
            ctx.log(f"block {c['id']}: {d[0]}: {d[1]}; shrinking")
            small = shrink(ctx, src, d[0], ov, f"r{k}-b{c['id']}")
            pattern = names(small)
            patterns.append((d[0], pattern))
            test = write_test(ctx, small, src, d[0], d[1], len(found['differences']) + 1)
            found['differences'].append({'class': d[0], 'pattern': pattern, 'detail': d[1], 'block': c['id'],
                                         'ops': len(src['ops']), 'shrunk_to': len(small), 'test': test})
        k += 1
        rounds -= 1
        found['next_round'] = k
        with open(log_path, 'w') as f:
            json.dump(found, f, indent=1)
    fs = [FX.fact('fuzz.blocks', found['blocks']), FX.fact('fuzz.differences', len(found['differences']))]
    prov = ctx.provenance(state, campaign='E')
    path = ctx.write_facts('fuzz.json', 'fuzz', prov, fs, differences=found['differences'], rounds=k)
    ctx.status(NAME, 'done' if not hours else 'partial', facts=path, differences=len(found['differences']),
               done=found['blocks'])
    return path
