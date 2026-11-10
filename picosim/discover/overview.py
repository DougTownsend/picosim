"""
What the Testing window shows about Level 5 discovery (plan S18), read from
the files the campaigns write.  Nothing here talks to a board: the window
runs campaigns through the same code as the command line
(__main__.run_campaign), and this module only reads their results.

    campaigns(kind)          one row per campaign: its status (not run,
                             partial with n of m batches, done) and its
                             number of differences from picosim
    detail(name, kind)       a campaign's summary, provenance and
                             differences, each with a minimal example
    case_program(diff)       a small .s file that sets up one example's
                             registers and flags and runs its instruction(s)
    flash_status()           campaign F's flash backup, from flashguard

`kind` is 'standin' for the stand-in's results (kept apart from the chip's,
so emulator output is never mistaken for a measurement) and anything else
for the real chip's.  A campaign whose module is missing or fails to load
still gets a row, with what it will measure.
"""

import glob
import json
import os
import re

from .cases import ORDER, LETTERS
from . import campaign as C
from . import facts as FX

# What each campaign measures, for campaigns whose module cannot be loaded
# (not written yet, or broken); a module's own docstring wins.
ABOUT = {
    'identity': "Which chip, ROM and debug parts this is, read through the debugger, plus the boot ROM.",
    'memmap': "The memory map as the chip has it: every region and peripheral, what each width of "
              "access does there, the aliases, and how many cycles an access takes in each region.",
    'registers': "Every peripheral register's reset value and which of its bits can be written, "
                 "compared with the datasheet (the SVD file).",
    'encodings': "Every 16-bit instruction encoding and a sample of the 32-bit ones, run on the chip "
                 "with chosen inputs: how each ends, and the registers, flags and memory it leaves.",
    'timing': "How many clock cycles each instruction takes, alone and in pairs, from each kind of "
              "memory; exception entry and return.",
    'fuzz': "Random short programs, each compared with picosim straight away; a program that differs "
            "is shrunk to the smallest one that still differs.",
    'flashed': "The chip as a flashed program sees it (clocks, the XIP cache, interrupts). This campaign "
               "writes the board's flash, so it backs the flash up first and puts it back at the end.",
    'peripherals': "How the SIO divider, interpolators, FIFOs, spinlocks, GPIO overrides and SysTick behave.",
}
TITLES = {
    'identity': 'Identity: chip, ROM and debug parts', 'memmap': 'Memory map',
    'registers': 'Peripheral registers', 'encodings': 'Every instruction encoding',
    'timing': 'Timing', 'fuzz': 'Random programs', 'flashed': 'Flashed programs',
    'peripherals': 'Peripheral behaviour',
}
MAX_DIFFS = 300                        # differences sent to the window


def _module(name):
    """(module, error text): the campaign's module, or why it can't be loaded."""
    from .cases import module
    try:
        return module(name), ''
    except Exception as e:             # noqa: BLE001 - a campaign being written may not import
        return None, f"{type(e).__name__}: {e}"


def about(name, mod=None):
    """(title, what it measures): the module's TITLE and its docstring's first
    paragraph (or, when that is only a heading, the fallback above)."""
    title = getattr(mod, 'TITLE', None) or TITLES.get(name, name)
    doc = (getattr(mod, '__doc__', None) or '').strip()
    paras = [re.sub(r'\s+', ' ', p).strip() for p in re.split(r'\n\s*\n', doc) if p.strip()]
    text = ''
    if paras:
        # "Campaign 0: which chip ... ." -> the part after the colon
        m = re.match(r'Campaign [0-9A-H]+[,:]\s*(.*)', paras[0])
        text = m.group(1) if m else paras[0]
        text = text[:1].upper() + text[1:]
    if len(text) < 80 and name in ABOUT:
        text = ABOUT[name]
    return title, text


def _target_kind(kind):
    return 'standin' if kind == 'standin' else 'openocd'


def progress(name, kind='openocd', label='chip1', step=None):
    """(done, total) batches of the stage a partial campaign is in, from its
    run folders: the stage named `step` if it has one, otherwise the most
    recently changed unfinished run.  None if nothing is known."""
    sub = 'standin' if kind == 'standin' else label
    root = _cache(sub)
    if step and not os.path.isdir(os.path.join(root, f"{name}-{step}")):
        step = None                    # a step that is not a stage ("round 3", "S1")
    pattern = os.path.join(root, f"{name}-{step}" if step else f"{name}-*", '*', 'state.json')
    best = None
    for p in glob.glob(pattern):
        if os.path.basename(os.path.dirname(p)).endswith('-repeat'):
            continue
        try:
            with open(p) as f:
                st = json.load(f)
            m = os.path.getmtime(p)
        except (OSError, ValueError):
            continue
        if not step and st.get('finished'):
            continue
        if best is None or m > best[0]:
            best = (m, len(st.get('done', [])), int(st.get('n_batches', 0)))
    return (best[1], best[2]) if best else None


def _cache(sub):
    from .build import cache_dir
    return cache_dir(sub)


def row(name, kind='openocd', label='chip1'):
    """One campaign's line in the window's list."""
    mod, err = _module(name)
    title, text = about(name, mod)
    try:
        s = C.read_status(name, _target_kind(kind), label)
    except (OSError, ValueError) as e:
        s = {'state': 'not run', 'error': f"its status file can't be read: {e}"}
    state = s.get('state', 'not run')
    out = {'name': name, 'letter': LETTERS.get(name, '?'), 'title': title, 'about': text,
           'state': state if state in C.STATUS_NAMES else 'not run', 'step': s.get('step') or '',
           'differences': s.get('differences'), 'updated': s.get('updated', ''),
           'facts': s.get('facts') or '', 'available': mod is not None, 'error': err,
           'agent_based': bool(getattr(mod, 'AGENT_BASED', False)),
           'compared_with': s.get('compared_with') or 'picosim'}
    if out['state'] == 'partial':
        pr = progress(name, kind, label, out['step'] or None)
        if pr:
            out['done'], out['total'] = pr
    return out


def campaigns(kind='openocd', label='chip1'):
    return [row(n, kind, label) for n in ORDER]


# ── a campaign's facts ──────────────────────────────────────────────────────

def _facts_doc(name, kind, label, status):
    """The campaign's facts document (validated), or (None, why)."""
    path = status.get('facts') or ''
    files = [p for p in (status.get('files') or {}).values() if p]
    if len(files) > 1:                          # campaign D: timing.json and exceptions.json
        path = files
    if isinstance(path, list):                  # campaign G writes one document per block
        doc, where = _merged_docs([p for p in path if p])
        if doc is not None and status.get('compared_with'):
            doc['compared_with'] = status['compared_with']
        return doc, where
    if not path or not os.path.exists(path):
        return None, ''
    try:
        with open(path) as f:
            return FX.validate(json.load(f)), path
    except (OSError, ValueError, FX.FactsError) as e:
        return None, f"{path} can't be read: {e}"


def _merged_docs(paths):
    """Several facts documents as one: every fact, each document's tables
    under its file name (sio, gpio, …), the first one's provenance."""
    docs = []
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                docs.append((p, FX.validate(json.load(f))))
        except (OSError, ValueError, FX.FactsError) as e:
            return None, f"{p} can't be read: {e}"
    if not docs:
        return None, ''
    merged = {'provenance': docs[0][1].get('provenance') or {}, 'facts': []}
    for p, d in docs:
        merged['facts'] += d.get('facts', [])
        merged[os.path.splitext(os.path.basename(p))[0]] = {k: v for k, v in d.items()
                                                            if k not in ('provenance', 'facts')}
    folder = os.path.dirname(docs[0][0])
    return merged, folder + os.sep + ' + '.join(os.path.basename(p) for p, _ in docs)


def provenance(doc):
    """The facts document's provenance in plain words, as (label, text) pairs."""
    p = (doc or {}).get('provenance') or {}
    if not p:
        return []
    chip = p.get('chip') or {}
    chip_text = ', '.join(f"{k.replace('_', ' ')} {v}" for k, v in chip.items() if v is not None) or 'unknown'
    pc = p.get('picosim') or {}
    commit = (pc.get('commit') or 'unknown')[:10] + (' (with uncommitted changes)' if pc.get('dirty') else '')
    agent = p.get('agent')
    if isinstance(agent, dict):
        agent = agent.get('sha256') or agent.get('hash') or json.dumps(agent)
    runs = p.get('runs')
    agreed = p.get('agreed')
    runs_text = f"{runs} run{'s' if runs != 1 else ''}" if runs is not None else 'unknown'
    if agreed is not None:
        runs_text += ', the repeat agreed' if agreed else ', the repeat DISAGREED'
    target = p.get('target') or ''
    out = [('Chip', chip_text + (f"  ({p['chip_label']})" if p.get('chip_label') not in (None, 'chip1') else '')),
           ('Measured', str(p.get('date') or 'unknown')),
           ('Through', (p.get('openocd') or 'unknown') + (f", SWD {p['swd_khz']} kHz" if p.get('swd_khz') else '')),
           ('picosim', commit),
           ('Agent', (str(agent)[:16] + '…') if agent and len(str(agent)) > 16 else (str(agent) if agent else
                                                                                   'none (debugger reads only)')),
           ('Runs', runs_text)]
    if target == 'standin':
        out.insert(0, ('Source', 'the stand-in (an emulator), not a real chip'))
    return out


def _same(a, b):
    def norm(v):
        if isinstance(v, str) and re.fullmatch(r'0x[0-9A-Fa-f]+', v.strip()):
            return int(v, 16)
        if isinstance(v, str):
            return v.strip().lower()
        return v
    return norm(a) == norm(b)


def _label(d, path):
    """A short name for a table row that carries differences."""
    if d.get('periph') and d.get('name'):
        return f"{d['periph']} {d['name']}" + (f" ({d['addr']})" if d.get('addr') else '')
    for k in ('encoding', 'name', 'id', 'what', 'addr'):
        if d.get(k):
            return str(d[k])
    return path or 'difference'


def _read_vs(item):
    """'field: chip X, model Y; …' for a row with 'read' and 'predicted'."""
    rd, pr = item.get('read'), item.get('predicted')
    if not isinstance(rd, dict) or not isinstance(pr, dict):
        return ''
    return '; '.join(f"{k}: chip {rd.get(k)}, model {pr.get(k)}" for k in rd)


def differences(doc):
    """Every difference a facts document records, as dicts:
        what, kind ('picosim' | 'datasheet' | 'expected' | 'pending'), where, cases,
        detail, known, example (check._example's form, or None), program
    from (1) any 'differences' list in its tables, whose items are check
    groups (with an example) or plain sentences, and (2) facts whose
    measured value is not the one predicted."""
    out = []
    # campaign C compares the chip with the SVD, not with picosim
    plain = 'datasheet' if doc.get('compared_with') == 'datasheet' else 'picosim'

    def walk(o, path):
        if isinstance(o, dict):
            ds = [x for k, v in o.items() if (k == 'differences' or k.endswith('_differences'))
                  and isinstance(v, list) for x in v]          # e.g. G's interp_differences
            if ds:
                for item in ds:
                    if isinstance(item, dict):
                        ex = item.get('example') if isinstance(item.get('example'), dict) else None
                        status = str(item.get('status', 'differs'))
                        out.append({
                            'what': str(item.get('encoding') or item.get('name') or item.get('what')
                                        or (ex or {}).get('name')
                                        or (f"INTERP{item['interp']} case {item.get('id')}" if 'interp' in item else '')
                                        or _label(o, path)),
                            'kind': 'pending' if status.startswith('pending') else plain,
                            'class': item.get('class') or '', 'where': path,
                            'cases': item.get('cases', item.get('n')),
                            'detail': str(item.get('detail') or (ex or {}).get('detail') or _read_vs(item)
                                          or status),
                            'known': item.get('known') or '',
                            'example': ex if ex and ex.get('code') else None,
                            'program': item.get('program') or (ex or {}).get('program') or ''})
                    elif isinstance(item, str):
                        out.append({'what': _label(o, path), 'kind': plain, 'class': '', 'where': path,
                                    'cases': None, 'detail': item, 'known': '', 'example': None, 'program': ''})
            for k, v in o.items():
                if k not in ('differences', 'provenance', 'facts') and not k.endswith('_differences'):
                    walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(o, list):
            for v in o:
                walk(v, path)

    walk({k: v for k, v in doc.items() if k not in ('provenance', 'facts')}, '')
    for f in doc.get('facts', []):
        p = f.get('prediction')
        if not isinstance(p, dict) or 'value' not in p:
            continue
        v = f.get('value')
        bad = (v is False) if isinstance(v, bool) else not _same(v, p['value'])
        if bad and v is not None:
            out.append({'what': f['id'], 'kind': 'expected', 'class': '', 'where': 'facts', 'cases': None,
                        'detail': f"measured {v}; expected {p['value']} (from {p.get('from', '?')})",
                        'known': '', 'example': None, 'program': ''})
    order = {'picosim': 0, 'datasheet': 1, 'expected': 2, 'pending': 3}
    out.sort(key=lambda d: order.get(d['kind'], 4))
    for i, d in enumerate(out):
        d['index'] = i
        d['openable'] = bool(d['example'] or (d['program'] and str(d['program']).endswith('.s')))
    return out


def _facts_rows(doc, limit=200):
    rows = []
    for f in doc.get('facts', [])[:limit]:
        v = f.get('value')
        text = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        if len(text) > 120:
            text = text[:117] + '…'
        rows.append({'id': f['id'], 'value': text, 'unit': f.get('unit', ''), 'source': f.get('source', ''),
                     'expected': '' if not isinstance(f.get('prediction'), dict) else
                     f"{f['prediction'].get('value')} ({f['prediction'].get('from', '')})",
                     'note': f.get('note', '')})
    return rows


def detail(name, kind='openocd', label='chip1'):
    """Everything the window shows for one campaign: its row (where
    'differences' is the count status.json gives), plus provenance, diffs
    (the differences themselves, see differences()), facts_rows."""
    r = row(name, kind, label)
    status = C.read_status(name, _target_kind(kind), label) if r['state'] != 'not run' else {}
    doc, path = _facts_doc(name, kind, label, status)
    out = dict(r, provenance=[], diffs=[], n_diffs=0, facts_rows=[], facts_path='', facts_error='', n_facts=0)
    if doc is None:
        out['facts_error'] = path
    else:
        diffs = differences(doc)
        out.update(provenance=provenance(doc), facts_path=path, n_facts=len(doc.get('facts', [])),
                   diffs=diffs[:MAX_DIFFS], n_diffs=len(diffs), facts_rows=_facts_rows(doc))
    if name == 'flashed':
        out['flash'] = flash_status()
    return out


def find_difference(name, kind, label, index):
    d = detail(name, kind, label)
    for x in d['diffs']:
        if x['index'] == index:
            return x
    raise IndexError(f"campaign {name} has no difference number {index}")


# ── open an example in picosim ──────────────────────────────────────────────

def _halfwords(code):
    from . import v6m
    out, i = [], 0
    while i + 1 < len(code):
        hw1 = int.from_bytes(code[i:i + 2], 'little')
        if v6m.is_prefix32(hw1) and i + 3 < len(code):
            hw2 = int.from_bytes(code[i + 2:i + 4], 'little')
            out.append((f".inst.w 0x{hw1:04X}{hw2:04X}", code[i:i + 4]))
            i += 4
        else:
            out.append((f".inst.n 0x{hw1:04X}", code[i:i + 2]))
            i += 2
    return out


def _insn_name(chunk):
    from .check import name_of
    try:
        return name_of(chunk)
    except Exception:                  # noqa: BLE001 - a comment only
        return ''


def _val(v):
    if isinstance(v, int):
        return v & 0xFFFFFFFF
    s = str(v).strip()
    return int(s, 16) if s.lower().startswith('0x') else int(s or 0)


def case_program(diff, campaign, out_dir=None):
    """Write a .s file that runs one difference's example in picosim: it
    sets R0-R12 and N Z C V as the case had them on the chip, runs the
    case's instruction(s) and stops at a BKPT.  Returns its path.  A
    difference that names its own .s program (campaign E) returns that."""
    prog = diff.get('program') or ''
    if prog and str(prog).endswith('.s') and os.path.exists(prog):
        return os.path.abspath(prog)
    ex = diff.get('example')
    if not ex or not ex.get('code'):
        raise ValueError("this difference has no example program")
    code = bytes.fromhex(ex['code'])
    regs = [_val(v) for v in (ex.get('regs') or [])][:13]
    regs += [0] * (13 - len(regs))
    nzcv = int(str(ex.get('nzcv') or '0'), 2) & 0xF
    what = diff.get('what') or ex.get('name') or 'case'
    out_dir = out_dir or _cache('cases')
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r'[^A-Za-z0-9]+', '_', f"{campaign}_{what}_{ex.get('id', diff.get('index', 0))}").strip('_')
    path = os.path.join(out_dir, stem.lower()[:80] + '.s')
    detail_text = re.sub(r'\s+', ' ', str(diff.get('detail') or ex.get('detail') or ''))
    lines = [
        f"@ Title: {LETTERS.get(campaign, '?')} {what}: a case where the RP2040 and picosim differ",
        f"@ About: Made by the Testing window from campaign {LETTERS.get(campaign, '?')} ({campaign})"
        f"{', ' + diff['where'] if diff.get('where') else ''}, case {ex.get('id', '?')}. "
        f"What differed: {detail_text}",
        "@ Kind: trace",
        "@",
        "@ The registers and flags are set as the case had them on the chip, then the",
        "@ case's instruction(s) run and BKPT stops the program. Addresses in the",
        "@ registers are the chip's, and picosim has the same memory map, but this",
        "@ program is in flash rather than at the chip's slot in SRAM, so a branch",
        "@ lands in this program rather than in the chip's sled.",
        f"@ On the chip: SP was PSP {ex.get('psp', '?')}, PRIMASK {ex.get('primask', '?')}; here SP is picosim's stack.",
        "",
        "        .syntax unified",
        "        .cpu cortex-m0plus",
        "        .thumb",
        "",
        "        .text",
        "main:",
    ]
    for i in range(8, 13):
        lines += [f"        ldr     r0, =0x{regs[i]:08X}", f"        mov     r{i}, r0"]
    lines += [f"        ldr     r0, =0x{nzcv << 28:08X}      @ N Z C V = {nzcv:04b}",
              "        msr     APSR, r0"]
    for i in range(8):
        lines.append(f"        ldr     r{i}, =0x{regs[i]:08X}")
    lines.append("case:                                   @ the case's instruction(s)")
    for text, chunk in _halfwords(code):
        name = _insn_name(chunk)
        lines.append(f"        {text:<24}@ {name}" if name else f"        {text}")
    lines += ["        bkpt    #0                      @ the end of the case", "        .ltorg", ""]
    with open(path, 'w') as f:
        f.write('\n'.join(lines))
    return path


# ── campaign F's flash backup ───────────────────────────────────────────────

def flash_status(state_path=None):
    """Whether campaign F may start: only with a checked backup (A over SWD
    and B over USB, equal) stored in both places, and no unfinished restore."""
    from . import flashguard as G
    try:
        st = G.load_state(state_path)
    except G.FlashGuardError as e:
        return {'state': 'unknown', 'ready': False, 'pending_restore': True, 'message': str(e),
                'backup': {}, 'restorable': False}
    if not st:
        return {'state': None, 'ready': False, 'pending_restore': False, 'backup': {}, 'restorable': False,
                'message': "No flash backup yet, so campaign F can't start. Press Flash backup…, "
                           "then Back up. Don't press any BOOTSEL button beforehand: picosim pops "
                           "up a message when it needs you."}
    b = st.get('backup') or {}
    copies = [p for p in (b.get('bin'), b.get('cache_bin')) if p and os.path.exists(p)]
    pending = st['state'] == 'written'
    ready = st['state'] in ('backed-up', 'restored') and len(copies) == 2
    if pending:
        msg = ("The target Pico's flash was changed by campaign F and the original program has not been "
               f"put back yet (backup {b.get('bin', '?')}). Restore it before anything else.")
    elif ready:
        msg = (f"Backed up {st.get('backed_up_at') or ''}: both backups (A over SWD, B over USB) matched "
               f"(SHA-256 {str(st.get('sha256', ''))[:16]}…), stored twice.")
    else:
        msg = ("The backup's files are missing (" + ', '.join(p for p in (b.get('bin'), b.get('cache_bin'))
                                                       if p and not os.path.exists(p)) +
               "). Make a new backup before campaign F.")
    return {'state': st['state'], 'ready': ready and not pending, 'pending_restore': pending, 'message': msg,
            'backup': {'bin': b.get('bin', ''), 'cache_bin': b.get('cache_bin', ''), 'uf2': b.get('uf2', ''),
                       'sha256': st.get('sha256', ''), 'unique_id': st.get('unique_id', ''),
                       'when': st.get('backed_up_at', '')},
            'restorable': bool(b)}


# The backup and restore procedure, for campaign F's confirmation (the
# steps of flashguard.py's docstring, in plain words).
FLASH_STEPS = {
    'backup': [
        "Identify the flash through OpenOCD: it must be the RP2040's 2 MB flash at 0x10000000.",
        "Backup A: read the whole flash over SWD (the debug probe).",
        "Backup B: read it again over USB with picotool. A message pops up when it needs you: "
        "put the TARGET Pico (never the probe) in BOOTSEL mode, by unplugging its USB, holding "
        "its BOOTSEL button, plugging it back in and letting go.",
        "Compare the two backups' SHA-256. If they differ they are both read once more; if they still "
        "differ, everything stops and nothing is written.",
        "Read the flash's unique ID (still in BOOTSEL). No ID, no backup.",
        "Store the image twice (~/picosim-flash-backups and the cache), each with a .sha256 file and a "
        ".uf2 for drag-and-drop restore; every file is read back and checked.",
        "A last message asks you to plug the TARGET back in normally (no button). macOS may say "
        "\u201cDisk Not Ejected Properly\u201d: that is expected.",
    ],
    'run': [
        "Check over SWD that the flash still equals the backup, then record that it is being written "
        "(from here on every other discover command refuses to start until the restore is done).",
        "Write the probe program to the flash and measure (clocks, the XIP cache, interrupts, ...).",
    ],
    'restore': [
        "Refuse a backup whose unique ID is not this board's.",
        "Write the backup back (flash write_image erase), then verify_image.",
        "Read the whole flash back: its SHA-256 must equal the backup's (both are shown).",
        "Boot check: restart the board and wait for its USB serial port.",
        "Backup files are never deleted.",
    ],
}
