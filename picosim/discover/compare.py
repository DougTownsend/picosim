"""
Campaign H: the same campaigns on a second chip, compared with the first.

    python3 -m picosim.discover <campaign> --chip-label chip2     # with the boards swapped
    python3 -m picosim.discover compare-chips --a chip1 --b chip2

compare_chips() lines the two chips' facts up: identity, the memory map's
probes, every register's S1 value and writable bits, every replayed
encoding record, and the timing tables.  Anything that differs is a
chip-revision fact (or a sign that a measurement is not repeatable), and is
written to chip_differences.json.
"""

import glob
import json
import os

from . import facts as FX
from . import formats as F

SKIP_RESULT = {'systick', 'msp', 'psp', 'frame'}


def _doc(label, name, kind):
    root = FX.root(kind)
    path = os.path.join(root, name) if label == 'chip1' else os.path.join(root, 'chips', label, name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _replays(label, kind):
    d = os.path.join(FX.root(kind), 'replay')
    out = {}
    for p in glob.glob(os.path.join(d, 'encodings-B*.json')):
        name = os.path.basename(p)[:-5]
        parts = name.split('-')
        lab = parts[2] if len(parts) > 2 else 'chip1'
        if lab == label:
            out[parts[1]] = p
    return out


def compare_chips(a='chip1', b='chip2', kind='openocd'):
    diffs = []
    for name, key in (('identity.json', 'facts'),):
        da, db = _doc(a, name, kind), _doc(b, name, kind)
        if da and db:
            fa = {f['id']: f['value'] for f in da['facts']}
            fb = {f['id']: f['value'] for f in db['facts']}
            for k in sorted(set(fa) | set(fb)):
                if k.startswith('identity.swd.'):
                    continue
                if fa.get(k) != fb.get(k):
                    diffs.append({'what': 'identity', 'id': k, a: fa.get(k), b: fb.get(k)})
    ma, mb = _doc(a, 'memory_map.json', kind), _doc(b, 'memory_map.json', kind)
    if ma and mb:
        for addr, pa in ma.get('probes', {}).items():
            pb = mb.get('probes', {}).get(addr)
            if pb and (pa.get('debugger'), pa.get('core')) != (pb.get('debugger'), pb.get('core')):
                diffs.append({'what': 'memory map', 'id': addr, a: pa, b: pb})
        ta = {(r['region'], r['op'], r['width']): r['per_access'] for r in ma.get('timing', [])}
        tb = {(r['region'], r['op'], r['width']): r['per_access'] for r in mb.get('timing', [])}
        for k in sorted(set(ta) & set(tb), key=str):
            if ta[k] != tb[k]:
                diffs.append({'what': 'access timing', 'id': '/'.join(map(str, k)), a: ta[k], b: tb[k]})
    ra, rb = _doc(a, 'registers.json', kind), _doc(b, 'registers.json', kind)
    if ra and rb:
        by = {r['addr']: r for r in rb.get('registers', [])}
        for r in ra.get('registers', []):
            o = by.get(r['addr'])
            if not o:
                continue
            for field in ('S1', 'S3'):
                if r.get(field) != o.get(field):
                    diffs.append({'what': f'register {field}', 'id': f"{r['periph']}.{r['name']}", a: r.get(field),
                                  b: o.get(field)})
            wa, wb = (r.get('masks') or {}).get('writable'), (o.get('masks') or {}).get('writable')
            if wa != wb:
                diffs.append({'what': 'writable bits', 'id': f"{r['periph']}.{r['name']}", a: wa, b: wb})
    pa, pb = _replays(a, kind), _replays(b, kind)
    for stage in sorted(set(pa) & set(pb)):
        A, _ = FX.read_replay(pa[stage])
        B, _ = FX.read_replay(pb[stage])
        byb = {c['id']: r for c, r in B}
        n = 0
        for c, r in A:
            o = byb.get(c['id'])
            if o is None:
                continue
            n += 1
            bad = [f for f in F.RESULT if f not in SKIP_RESULT and r[f] != o[f]]
            if bad:
                diffs.append({'what': f'encoding {stage}', 'id': f"case {c['id']:#x} ({c['code_bytes'].hex()})",
                              'fields': bad})
    da, db = _doc(a, 'timing.json', kind), _doc(b, 'timing.json', kind)
    if da and db:
        fa = {f['id']: f['value'] for f in da['facts']}
        fb = {f['id']: f['value'] for f in db['facts']}
        for k in sorted(set(fa) & set(fb)):
            if fa[k] != fb[k]:
                diffs.append({'what': 'timing', 'id': k, a: fa[k], b: fb[k]})
    out = {'schema': 1, 'kind': 'chip_differences', 'a': a, 'b': b, 'differences': diffs}
    path = os.path.join(FX.root(kind), 'chip_differences.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=1, default=str)
    lines = [f"{a} against {b}: {len(diffs)} differences (written to {path})"]
    for d in diffs[:40]:
        lines.append(f"  {d['what']}: {d['id']}" + (f"  {d.get(a)!s:.60} vs {d.get(b)!s:.60}" if a in d else
                                                      f"  fields {', '.join(d.get('fields', []))}"))
    return '\n'.join(lines)
