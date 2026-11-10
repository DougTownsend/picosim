"""
Check picosim against recorded hardware facts, with no board.

    python3 -m picosim.discover check                 # hwfacts/replay/*.json
    python3 -m picosim.discover check --standin       # the stand-in's facts
    python3 -m picosim.discover check --run <run dir> # one capture, straight from the cache

Every recorded case is replayed in picosim (replay.py) and compared:

    how it ended    a fault at the instruction, falling through, a branch
                    into the sled (where), SVC, an endless loop
    registers       R0-R12, SP, LR and the next PC (when both ended alike)
    flags           N Z C V
    memory          the scratch window's changed words
    timing          the body's cycles (timing cases), with picosim's
                    RP2040-model timing (timing.py 'm0plus')

Differences are grouped by class (faults, decoder, flags, memory, timing)
and by encoding, each with its first example, in report.md.  Things picosim
cannot model yet are 'pending', never failures: exception details (stacked
xPSR, EXC_RETURN, lockups, interrupts) until it has an exception model, and
addresses outside its memory map (ROM, flash, SRAM and the I/O it simulates).

hwfacts/known_differences.json lists differences that are understood, each
with a reason and a to-do; anything else is new, and tests/test_hwfacts.py
fails on it.
"""

import glob
import json
import os
import re
from collections import Counter, OrderedDict

from . import formats as F
from . import replay, v6m
from .runner import ending, MARK_NAMES
from .replay import HWFACTS

CLASSES = ('faults', 'decoder', 'flags', 'memory', 'timing')
PENDING = ('pending: exception model', 'pending: Stage 2')


def name_of(code):
    """The first instruction's name (from the independent table)."""
    if len(code) < 2:
        return '(empty)'
    hw1 = int.from_bytes(code[:2], 'little')
    if v6m.is_prefix32(hw1) and len(code) >= 4:
        return v6m.decode32(hw1, int.from_bytes(code[2:4], 'little')).name
    return v6m.decode16(hw1).name


def hw_state(c, r):
    """The chip's registers as the case left them (from the stacked frame)."""
    regs = [r['f_r0'], r['f_r1'], r['f_r2'], r['f_r3']] + [r[f'r{i}'] for i in range(4, 12)] + [r['f_r12']]
    sp = (r['frame'] + 32 + (4 if r['f_xpsr'] & 0x200 else 0)) & 0xFFFFFFFF
    return regs + [sp, r['f_lr'], r['f_pc']], r['f_xpsr'] >> 28


def compare(c, r, lay, scratch_words, overhead=None):
    """(status, class, detail): status 'match', 'differs' or a PENDING."""
    hw_end = ending(c, r, lay)
    kind = hw_end[0]
    if kind in MARK_NAMES.values():
        if kind in ('lockup', 'hang'):
            return PENDING[0], 'faults', f"the chip: {kind}"
        return 'skipped', None, kind
    if kind in ('exception', 'elsewhere'):
        return PENDING[0], 'faults', f"the chip took exception {hw_end[1]}" if kind == 'exception' else \
            f"a HardFault at 0x{hw_end[1]:08X}"
    if c['kind'] == F.KINDS['timing']:
        return _compare_timing(c, r, lay, overhead, scratch_words)
    sim = replay.run(c, lay, scratch_words, max_steps=64 if kind == 'watchdog' else None)
    m = sim['map']
    if kind == 'watchdog':
        if sim['end'][0] == 'running':
            return 'match', None, ''
        return 'differs', 'faults', f"the chip loops forever; picosim: {_end_text(sim)}"
    if sim['end'][0] == 'fault' and sim['error'] and sim['error'].startswith('bus fault') and kind != 'fault':
        return PENDING[1], 'faults', "picosim has nothing at an address the case used"
    hw_pc, sim_pc = hw_end[1], sim['end'][1]
    if kind == 'sled' and not r['f_xpsr'] & (1 << 24):
        hw_text = f"branches to 0x{hw_pc:08X} with the Thumb bit clear, then faults there (INVSTATE)"
    else:
        hw_text = _hw_end_text(hw_end)
    if kind != sim['end'][0] or sim_pc != hw_pc:
        return 'differs', 'faults', f"the chip {hw_text}; picosim {_end_text(sim)}"
    regs, flags = hw_state(c, r)
    names = [f'r{i}' for i in range(13)] + ['sp', 'lr']
    bad = [f"{n}: chip 0x{h:08X}, picosim 0x{s:08X}" for n, h, s in zip(names, regs, sim['regs'])
           if not m.same(s, h)]
    if bad:
        return 'differs', 'decoder', '; '.join(bad)
    if flags != sim['flags']:
        return 'differs', 'flags', f"NZCV chip {flags:04b}, picosim {sim['flags']:04b}"
    if r['changed'] is not None:
        hw_ch = r.get('changed_full') if r['overflow'] and r.get('changed_full') is not None else r['changed']
        sim_ch = sim['changed']
        # a frame the chip stacked into the window (stack cases): not the case's own doing
        fo = r['frame'] - lay['SCRATCH']
        if 0 <= fo < 4 * max(scratch_words, 1):
            span = range(fo, fo + 32 + (4 if r['f_xpsr'] & 0x200 else 0))
            hw_ch = [(o, v) for o, v in hw_ch if o not in span]
            sim_ch = [(o, v) for o, v in sim_ch if o not in span]
            if r['overflow'] and r.get('changed_full') is None:
                return PENDING[0], 'memory', "the stacked frame hides the case's own changes (no full read)"
        if r['overflow'] and r.get('changed_full') is None:
            ok = len(sim_ch) > F.MAX_INLINE and [o for o, _ in sim_ch[:4]] == [o for o, _ in hw_ch]
        else:
            ok = len(sim_ch) == len(hw_ch) and all(o1 == o2 and m.same(v2, v1) for (o1, v1), (o2, v2) in zip(hw_ch, sim_ch))
        if not ok:
            return 'differs', 'memory', f"scratch changes: chip {_ch(hw_ch, r['overflow'])}, picosim {_ch(sim_ch, False)}"
    return 'match', None, ''


def _ch(ch, more):
    s = ', '.join(f"+0x{o:03X}=0x{v:08X}" for o, v in ch[:4]) or 'none'
    return s + (' and more' if more or len(ch) > 4 else '')


def _hw_end_text(e):
    k, pc = e
    return {'fault': f"faults at the instruction (0x{pc:08X})", 'fell-through': "runs it and falls through",
            'sled': f"branches to 0x{pc:08X}", 'svc': "takes SVCall"}.get(k, k)


def _end_text(sim):
    k, pc = sim['end']
    if k == 'fault':
        return f"stops with an error at 0x{pc:08X}: {sim['error']}"
    return {'fell-through': "runs it and falls through", 'sled': f"branches to 0x{pc:08X}",
            'svc': "takes SVCall", 'running': "loops forever", 'halted': "halts"}.get(k, k)


def _compare_timing(c, r, lay, overhead, scratch_words=0):
    raw = (r['r6'] - r['r5']) % (1 << 24)
    if c['code_len'] <= 4:
        return 'match', None, ''                 # a calibration case
    if overhead is None:
        return 'skipped', None, 'no calibration in this batch'
    hw = raw - overhead
    try:
        sim, _ = replay.body_cycles(c, lay, scratch_words)
    except Exception as e:                       # noqa: BLE001
        return PENDING[1], 'timing', f"picosim could not run the body: {e}"
    if sim is None:
        return PENDING[0], 'timing', "picosim gives no time for this encoding"
    if sim != hw:
        return 'differs', 'timing', f"chip {hw} cycles, picosim {sim}"
    return 'match', None, ''


def overheads(records):
    """Per batch: the timing calibration (an empty body's raw count)."""
    out = {}
    for c, r in records:
        if c['kind'] == F.KINDS['timing'] and c['code_len'] == 4 and r['ipsr'] == 3:
            out.setdefault(c.get('batch', 0), (r['r6'] - r['r5']) % (1 << 24))
    if out:
        out.setdefault('run', next(iter(out.values())))       # for a batch without its own
    return out


# ── known differences ───────────────────────────────────────────────────────

def known():
    p = os.path.join(HWFACTS, 'known_differences.json')
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)['known']


def is_known(cls, name, detail, table):
    for k in table:
        if k.get('class') not in (None, cls):
            continue
        if not re.search(k.get('name', '.*'), name):
            continue
        if 'detail' in k and not re.search(k['detail'], detail):
            continue
        return k
    return None


# ── whole sets ──────────────────────────────────────────────────────────────

class Result:
    def __init__(self):
        self.counts = Counter()
        self.groups = OrderedDict()     # (status, class, name) -> {'n': , 'example': }
        self.new = []                   # differences not in the known list

    def add(self, status, cls, name, detail, c, r, known_entry):
        self.counts[status] += 1
        if status == 'match' or status == 'skipped':
            return
        key = (status, cls, name)
        g = self.groups.get(key)
        if g is None:
            g = self.groups[key] = {'n': 0, 'example': _example(c, r, detail),
                                    'known': known_entry and known_entry.get('reason')}
            if status == 'differs' and not known_entry:
                self.new.append(key)
        g['n'] += 1


def _example(c, r, detail):
    return {'code': c['code_bytes'].hex(), 'name': name_of(c['code_bytes']), 'id': c['id'],
            'regs': [f"0x{c[f'r{i}']:08X}" for i in range(13)], 'nzcv': f"{c['xpsr'] >> 28:04b}",
            'psp': f"0x{c['psp']:08X}", 'primask': c['primask'], 'detail': detail}


def check_records(records, lay, scratch_words, result=None, table=None):
    result = result or Result()
    table = known() if table is None else table
    ov = overheads(records)
    for c, r in records:
        status, cls, detail = compare(c, r, lay, scratch_words, ov.get(c.get('batch', 0), ov.get('run')))
        name = name_of(c['code_bytes'][2:] if c['kind'] == F.KINDS['timing'] else c['code_bytes'])
        k = is_known(cls, name, detail, table) if status == 'differs' else None
        result.add(status, cls, name, detail, c, r, k)
    return result


def check_replays(folder=None, standin=False):
    from .facts import read_replay, root
    folder = folder or os.path.join(root('standin' if standin else 'openocd'), 'replay')
    result = Result()
    files = sorted(glob.glob(os.path.join(folder, '*.json')))
    for p in files:
        records, meta = read_replay(p)
        for c, _ in records:
            c['watchdog'] = bool(meta['flags'] & F.HF_WATCHDOG)
        check_records(records, F.LAYOUTS[meta['layout']], 0 if meta['flags'] & F.HF_NO_SCRATCH
                      else meta['scratch_words'], result)
    return result, files


def check_run(path):
    from .runner import load
    records, state = load(path)
    return check_records(records, F.LAYOUTS[state['layout']],
                         0 if state['flags'] & F.HF_NO_SCRATCH else state['scratch_words'])


def report(result, title="picosim against the RP2040", sources=()):
    c = result.counts
    lines = [f"# {title}", "",
             f"{sum(c.values())} cases: {c['match']} match, {c['differs']} differ "
             f"({len(result.new)} kinds not yet known), "
             + ', '.join(f"{c[p]} {p}" for p in PENDING) + f", {c['skipped']} skipped.", ""]
    if sources:
        lines += ["Sources: " + ', '.join(os.path.basename(s) for s in sources), ""]
    for status in ('differs',) + PENDING:
        rows = [(k, g) for k, g in result.groups.items() if k[0] == status]
        if not rows:
            continue
        lines += [f"## {'Differences' if status == 'differs' else status.capitalize()}", ""]
        for cls in CLASSES:
            sub = [(k, g) for k, g in rows if k[1] == cls]
            if not sub:
                continue
            lines += [f"### {cls}", "", "| Encoding | Cases | Example | What | Known |", "|---|---|---|---|---|"]
            for (st, cl, name), g in sorted(sub, key=lambda x: -x[1]['n']):
                e = g['example']
                lines.append(f"| {name} | {g['n']} | `{e['code']}` r0–r3 {', '.join(e['regs'][:4])} NZCV {e['nzcv']} "
                             f"| {e['detail']} | {g['known'] or '**new**'} |")
            lines.append("")
    return '\n'.join(lines) + '\n'
