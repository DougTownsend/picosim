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

Two things are compared only in part, and counted as notes in report.md:
a register that loaded memory the agent never set (the chip's value is
whatever an earlier case left; replay.unset_addr), and a store into ROM,
which the replay drops as the chip does (for students picosim stops there
on purpose, Stage 2).

hwfacts/known_differences.json lists differences that are understood, each
with a reason and a to-do; anything else is new, and tests/test_hwfacts.py
fails on it.  Known and new differences are counted case by case (the
order the cases come in does not matter), each kind with a case id and the
dataset it came from, so it can be found again.

The verdict, and `check`'s exit status:

    0  pass              at least one case compared, and no new difference
                         (known differences only still pass: they are listed,
                         with their reasons, in the report)
    1  new differences   at least one case differs in a way not yet known
    3  nothing compared  no replay files, an empty run, or no case that
                         could be compared (all pending or skipped)

The report names its evidence (recorded chip captures, the stand-in, or one
run from the cache) and every input dataset with its run, SHA-256 and
counts; `--json FILE` writes the same as plain data (Result.summary()).
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
NOT_COMPARED = 'not compared: memory the agent never set'
NOTE_UNSET = 'loaded memory the agent never set (those registers not compared)'
NOTE_ROM = 'stored into ROM (dropped, as on the chip; for students picosim stops there on purpose)'


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


def compare(c, r, lay, scratch_words, overhead=None, notes=None):
    """(status, class, detail): status 'match', 'differs', a PENDING,
    NOT_COMPARED or 'skipped'.  `notes` (a list) gets NOTE_UNSET/NOTE_ROM."""
    notes = [] if notes is None else notes
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
    if sim['rom_stores']:
        notes.append(NOTE_ROM)
    if sim['end'][0] == 'fault' and _names_unset(sim['error']):
        # picosim branched to (or through) a word the agent never set
        return NOT_COMPARED, 'faults', f"picosim: {sim['error']}"
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
    unset = [n for n, s in zip(names, sim['regs']) if replay.unset_addr(s) is not None]
    if unset:
        notes.append(NOTE_UNSET)
    bad = [f"{n}: chip 0x{h:08X}, picosim 0x{s:08X}" for n, h, s in zip(names, regs, sim['regs'])
           if n not in unset and not m.same(s, h)]
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
            ok = len(sim_ch) == len(hw_ch) and all(
                o1 == o2 and (m.same(v2, v1) or replay.unset_addr(v2) is not None)   # a stored unset word
                for (o1, v1), (o2, v2) in zip(hw_ch, sim_ch))
        if not ok:
            return 'differs', 'memory', f"scratch changes: chip {_ch(hw_ch, r['overflow'])}, picosim {_ch(sim_ch, False)}"
    return 'match', None, ''


# the result fields that hold R0-R12 and LR after a case (hw_state's order)
_REG_FIELDS = ['f_r0', 'f_r1', 'f_r2', 'f_r3'] + [f'r{i}' for i in range(4, 12)] + ['f_r12', 'sp', 'f_lr']


def unset_fields(c, r, lay, scratch_words):
    """The result fields of case c whose value comes from memory the agent
    never set (picosim's replay loads an UNSET marker into them): earlier
    cases' leftovers, which depend on the order the cases ran in.  'all' if
    the case went through such memory (branched to a word never set).  Only
    non-timing cases that fell through, faulted or branched are replayed."""
    if c['kind'] == F.KINDS['timing'] or ending(c, r, lay)[0] in MARK_NAMES.values():
        return set()
    try:
        sim = replay.run(c, lay, scratch_words)
    except Exception:                                   # noqa: BLE001 - not explainable: compare all
        return set()
    if sim['end'][0] == 'fault' and _names_unset(sim['error']):
        return 'all'
    if sim['end'][0] == 'sled' and replay.unset_addr(sim['end'][1]) is not None:
        return 'all'
    return {f for f, v in zip(_REG_FIELDS, sim['regs']) if replay.unset_addr(v) is not None and f != 'sp'}


def _names_unset(text):
    """True if an error message quotes an UNSET marker as an address."""
    return any(replay.unset_addr(int(x, 16)) is not None for x in re.findall(r'0x([0-9A-Fa-f]{8})', text or ''))


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


def is_known(cls, name, detail, table, code=''):
    """The known_differences entry that explains a difference, or None.
    An entry matches by class, a regex on the name, and optionally regexes
    on the detail and on `code` (the case's first instruction's bytes, in
    hex as stored: '00bc' is 0xBC00)."""
    for k in table:
        if k.get('class') not in (None, cls):
            continue
        if not re.search(k.get('name', '.*'), name):
            continue
        if 'detail' in k and not re.search(k['detail'], detail):
            continue
        if 'code' in k and not re.search(k['code'], code):
            continue
        return k
    return None


# ── whole sets ──────────────────────────────────────────────────────────────

# What a set of results was compared against.  A report and its summary say
# which, so a stand-in run can never read as a comparison with the chip.
EVIDENCE = {
    'recorded': "recorded RP2040 captures (hwfacts/replay: measured on the chip, replayed with no board)",
    'standin': "the stand-in (Unicorn emulator, not the RP2040): it checks the code, not the chip",
    'run': "one capture run, read straight from the cache",
}
TITLES = {'recorded': "picosim against recorded RP2040 captures",
          'standin': "picosim against the stand-in (not the RP2040)",
          'run': "picosim against one capture run"}

# The verdict and the exit status of `check` (python3 -m picosim.discover check)
PASS, NEW, EMPTY = 'pass', 'new differences', 'nothing compared'
EXIT = {PASS: 0, NEW: 1, EMPTY: 3}


class Result:
    """Every case's outcome, counted.  Known and new differences are counted
    case by case, whatever order the cases come in: a group of differences
    is (status, class, encoding, verdict), verdict 'known' or 'new' for a
    difference (None otherwise), so a new case is never hidden in a group a
    known one opened first."""

    def __init__(self, evidence=None):
        self.counts = Counter()
        self.groups = OrderedDict()     # (status, class, name, verdict) -> {'n': , 'example': , 'known': }
        self.new = []                   # the keys of groups of differences not in the known list
        self.differs_known = 0          # cases
        self.differs_new = 0            # cases
        self.notes = Counter()          # NOTE_UNSET, NOTE_ROM: compared only in part
        self.in_part = 0                # cases with at least one note
        self.calibration = 0            # timing calibration cases (an empty body: nothing to compare)
        self.first = OrderedDict()      # status -> the first case with it: {'source', 'id', 'name'}
        self.by_source = OrderedDict()  # dataset -> Counter of statuses, 'known', 'new', 'in part', 'calibration'
        self.sources = []               # the input datasets: {'name', 'campaign', 'run_id', 'sha256', 'cases'}
        self.evidence = evidence        # an EVIDENCE key
        self.next_step = None           # what to do when there was nothing to compare

    def add(self, status, cls, name, detail, c, r, known_entry, source=None, notes=(), calibration=False):
        self.counts[status] += 1
        per = self.by_source.setdefault(source or '', Counter())
        per[status] += 1
        if notes:
            self.in_part += 1
            per['in part'] += 1
        if calibration:
            self.calibration += 1
            per['calibration'] += 1
        verdict = None
        if status == 'differs':
            verdict = 'known' if known_entry else 'new'
            per[verdict] += 1
            if verdict == 'new':
                self.differs_new += 1
            else:
                self.differs_known += 1
        fk = f"{status} ({verdict})" if verdict else status
        if fk not in self.first:
            self.first[fk] = {'source': source, 'id': c['id'], 'name': name}
        if status == 'match' or status == 'skipped':
            return
        key = (status, cls, name, verdict)
        g = self.groups.get(key)
        if g is None:
            g = self.groups[key] = {'n': 0, 'example': _example(c, r, detail, source),
                                    'known': known_entry.get('reason') if known_entry else None}
            if verdict == 'new':
                self.new.append(key)
        g['n'] += 1

    @property
    def total(self):
        return sum(self.counts.values())

    @property
    def compared(self):
        """Cases picosim was compared on (a match or a difference; not the
        timing calibration, which has nothing to compare)."""
        return self.counts['match'] + self.counts['differs'] - self.calibration

    def verdict(self):
        if self.differs_new:
            return NEW
        if not self.compared:
            return EMPTY
        return PASS

    def exit_code(self):
        return EXIT[self.verdict()]

    def summary(self):
        """The result as plain data (JSON), for regression checks."""
        def counts(cn):
            d = {k: cn[k] for k in ('match', 'differs', 'known', 'new', 'in part', 'calibration') + PENDING
                 + (NOT_COMPARED, 'skipped')}
            d['compared'] = cn['match'] + cn['differs'] - cn['calibration']
            d['total'] = sum(cn[k] for k in ('match', 'differs', 'skipped', NOT_COMPARED) + PENDING)
            return d
        allc = Counter(self.counts)
        allc.update({'known': self.differs_known, 'new': self.differs_new, 'in part': self.in_part,
                     'calibration': self.calibration})
        return {'verdict': self.verdict(), 'exit_code': self.exit_code(),
                'evidence': self.evidence, 'evidence_text': EVIDENCE.get(self.evidence),
                'sources': self.sources, 'counts': counts(allc),
                'by_source': {k or '(records)': counts(v) for k, v in self.by_source.items()},
                'compared_in_part': dict(self.notes),
                'first_case_by_status': dict(self.first),
                'new': [{'status': k[0], 'class': k[1], 'encoding': k[2], 'cases': self.groups[k]['n'],
                         'example': self.groups[k]['example']} for k in self.new],
                'known': [{'class': k[1], 'encoding': k[2], 'cases': g['n'], 'case_id': g['example']['id'],
                           'source': g['example'].get('source'), 'reason': g['known']}
                          for k, g in self.groups.items() if k[3] == 'known'],
                'next_step': self.next_step}


def _example(c, r, detail, source=None):
    return {'code': c['code_bytes'].hex(), 'name': name_of(c['code_bytes']), 'id': c['id'], 'source': source,
            'regs': [f"0x{c[f'r{i}']:08X}" for i in range(13)], 'nzcv': f"{c['xpsr'] >> 28:04b}",
            'psp': f"0x{c['psp']:08X}", 'primask': c['primask'], 'detail': detail}


def check_records(records, lay, scratch_words, result=None, table=None, progress=None, every=25000, source=None):
    """Every record run through picosim and compared.  `progress(done,
    total)` is called every `every` records (it may raise to stop).
    `source` names the dataset the records came from (a replay file)."""
    result = result if result is not None else Result()
    table = known() if table is None else table
    ov = overheads(records)
    total = len(records)
    for i, (c, r) in enumerate(records):
        if progress and i and i % every == 0:
            progress(i, total)
        notes = []
        status, cls, detail = compare(c, r, lay, scratch_words, ov.get(c.get('batch', 0), ov.get('run')), notes)
        result.notes.update(notes)
        timing = c['kind'] == F.KINDS['timing']
        first = c['code_bytes'][2:] if timing else c['code_bytes']
        name = name_of(first)
        code = first[:4 if len(first) >= 4 and v6m.is_prefix32(int.from_bytes(first[:2], 'little')) else 2].hex()
        k = is_known(cls, name, detail, table, code) if status == 'differs' else None
        result.add(status, cls, name, detail, c, r, k, source=source, notes=notes,
                   calibration=timing and status == 'match' and c['code_len'] <= 4)
    return result


def replay_folder(standin=False):
    from .facts import root
    return os.path.join(root('standin' if standin else 'openocd'), 'replay')


def replay_manifests(folder=None, standin=False):
    """The replay files' settings (their .json manifests), in name order."""
    folder = folder or replay_folder(standin)
    out = []
    for p in sorted(glob.glob(os.path.join(folder, '*.json'))):
        try:
            with open(p) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        meta['path'] = p
        out.append(meta)
    return out


def check_replays(folder=None, standin=False):
    from .facts import read_replay
    folder = folder or replay_folder(standin)
    result = Result('standin' if standin else 'recorded')
    files = sorted(glob.glob(os.path.join(folder, '*.json')))
    for p in files:
        records, meta = read_replay(p)
        for c, _ in records:
            c['watchdog'] = bool(meta['flags'] & F.HF_WATCHDOG)
        result.sources.append({'name': meta['name'], 'file': os.path.basename(p), 'campaign': meta.get('campaign'),
                               'run_id': meta.get('run_id'), 'sha256': meta.get('sha256'), 'cases': len(records),
                               'fraction': meta.get('fraction')})
        check_records(records, F.LAYOUTS[meta['layout']], 0 if meta['flags'] & F.HF_NO_SCRATCH
                      else meta['scratch_words'], result, source=meta['name'])
    if not files:
        if standin:
            result.next_step = (f"The stand-in has no replay files yet (in {folder}). Make some with "
                                "`python3 -m picosim.discover encodings --target standin` (or `timing --target "
                                "standin`), then run `python3 -m picosim.discover check --standin` again.")
        else:
            result.next_step = (f"No replay files in {folder}. They come with the repository "
                                "(hwfacts/replay/*.json and .bin.gz): check that the checkout is complete.")
    elif not result.compared:
        result.next_step = (f"None of the {result.total} cases could be compared (all pending, skipped or "
                            "not compared): there is no evidence to check picosim against.")
    return result, files


def check_run(path):
    from .runner import load
    records, state = load(path)
    target = state.get('target')
    res = Result('standin' if target == 'standin' else 'run')
    res.sources.append({'name': os.path.basename(os.path.normpath(path)), 'campaign': state.get('campaign'),
                        'run_id': state.get('run_id'), 'target': target, 'cases': len(records)})
    check_records(records, F.LAYOUTS[state['layout']],
                  0 if state['flags'] & F.HF_NO_SCRATCH else state['scratch_words'], res,
                  source=state.get('run_id') or os.path.basename(os.path.normpath(path)))
    if not res.compared:
        res.next_step = (f"The run in {path} has no case picosim could be compared on "
                         f"({res.total} cases read)." + (" Is it unfinished?" if not res.total else ''))
    return res


def report(result, title=None, sources=()):
    c = result.counts
    title = title or TITLES.get(result.evidence, "picosim against the RP2040")
    verdict = result.verdict()
    lines = [f"# {title}", ""]
    if result.evidence:
        lines += [f"Evidence: {EVIDENCE[result.evidence]}.", ""]
    if verdict == NEW:
        v = (f"**FAIL**: {result.differs_new} cases differ in {len(result.new)} kinds not yet known "
             f"(hwfacts/known_differences.json).")
    elif verdict == EMPTY:
        v = "**NOTHING COMPARED**: " + (result.next_step or "no case could be compared.")
    else:
        v = ("**PASS**: no new differences" + (f" ({result.differs_known} known differences)."
                                               if result.differs_known else "."))
    lines += [f"Verdict: {v}", ""]
    lines += [f"{result.total} cases: {c['match']} match"
              + (f" ({result.calibration} of them timing calibration)" if result.calibration else '')
              + f", {c['differs']} differ ({result.differs_known} known, {result.differs_new} new"
              f" in {len(result.new)} kinds not yet known), "
              + ', '.join(f"{c[p]} {p}" for p in PENDING) + f", {c[NOT_COMPARED]} {NOT_COMPARED}, "
              f"{c['skipped']} skipped. Compared: {result.compared} of {result.total}.", ""]
    if result.notes:
        lines += [f"Compared in part ({result.in_part} cases): "
                  + '; '.join(f"{n} cases {what}" for what, n in sorted(result.notes.items())) + ".", ""]
    if result.sources:
        lines += ["## Sources", "",
                  "| Dataset | Run | SHA-256 | Cases | Compared | Match | Differ (known / new) | In part "
                  "| Pending | Not compared | Skipped | Calibration |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s in result.sources:
            n = result.by_source.get(s['name'], Counter())
            lines.append(f"| {s.get('file') or s['name']} | {s.get('run_id') or ''} | "
                         f"{(s.get('sha256') or '')[:12]} | {s['cases']} | "
                         f"{n['match'] + n['differs'] - n['calibration']} | {n['match']} | "
                         f"{n['differs']} ({n['known']} / {n['new']}) | {n['in part']} | "
                         f"{sum(n[p] for p in PENDING)} | {n[NOT_COMPARED]} | {n['skipped']} | {n['calibration']} |")
        lines.append("")
    elif sources:
        lines += ["Sources: " + ', '.join(os.path.basename(s) for s in sources), ""]
    for status in ('differs',) + PENDING + (NOT_COMPARED,):
        rows = [(k, g) for k, g in result.groups.items() if k[0] == status]
        if not rows:
            continue
        lines += [f"## {'Differences' if status == 'differs' else status.capitalize()}", ""]
        for cls in CLASSES:
            sub = [(k, g) for k, g in rows if k[1] == cls]
            if not sub:
                continue
            lines += [f"### {cls}", "", "| Encoding | Cases | Example | What | Known |", "|---|---|---|---|---|"]
            for (st, cl, name, verdict), g in sorted(sub, key=lambda x: (x[0][3] != 'new', -x[1]['n'])):
                e = g['example']
                where = f"case {e['id']}" + (f" of {e['source']}" if e.get('source') else '')
                lines.append(f"| {name} | {g['n']} | `{e['code']}` r0–r3 {', '.join(e['regs'][:4])} NZCV {e['nzcv']} "
                             f"({where}) | {e['detail']} | "
                             f"{g['known'] or ('**new**' if verdict == 'new' else 'not compared yet')} |")
            lines.append("")
    return '\n'.join(lines) + '\n'
