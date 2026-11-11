"""
The facts store: what campaigns measured, with where it came from.

Each campaign writes one JSON document:

    {"schema": 1, "kind": "identity", "provenance": {...},
     "facts": [{"id": "...", "value": ..., "unit": ..., "source": "measured",
                "prediction": {"value": ..., "from": "..."}, ...}, ...],
     ...campaign-specific tables...}

Every fact carries its provenance: the chip (CHIP_ID, ROM version), the
date, the OpenOCD version and SWD speed, the picosim commit (and whether the
tree had uncommitted changes), the agent's hash, the campaign and run, how
many runs there were and whether they agreed.

Real-chip results go in the repository's hwfacts/.  Results from the
stand-in (Unicorn) go to the cache's hwfacts-standin/ instead, so emulator
output can never be mistaken for a measurement.

The replay subset (raw records that `check` replays with no board) is
written by write_replay(): a gzip of entries
    case record (96 bytes) | code (padded to 4) | result (128 bytes)
with the run's settings in a small JSON next to it.
"""

import gzip
import hashlib
import json
import os
import random
import re
import time

from . import formats as F
from .build import cache_dir
from .replay import HWFACTS

SCHEMA = 1
PROVENANCE_KEYS = ('chip', 'date', 'openocd', 'swd_khz', 'picosim', 'agent', 'campaign', 'run_id',
                   'runs', 'agreed', 'target')


class FactsError(Exception):
    pass


def root(target_kind):
    """Where facts from this kind of target go."""
    if target_kind == 'standin':
        return cache_dir('hwfacts-standin')
    os.makedirs(HWFACTS, exist_ok=True)
    return HWFACTS


def raw_backup_dir():
    """The second copy of the chip's raw captures (decision D4), outside the
    cache.  PICOSIM_RAW_BACKUP moves it (the tests use a temporary folder)."""
    return os.environ.get('PICOSIM_RAW_BACKUP') or os.path.expanduser('~/picosim-hwfacts-raw')


def provenance(state, runs=1, agreed=None, openocd=None, swd_khz=None, extra=None):
    """Provenance from a run's state.json (runner.Run.state)."""
    p = {'chip': state.get('chip', {}), 'date': state.get('finished') or state.get('created'),
         'openocd': openocd or state.get('openocd'), 'swd_khz': swd_khz or state.get('swd_khz'),
         'picosim': state.get('picosim', {}), 'agent': state.get('agent'),
         'campaign': state.get('campaign'), 'run_id': state.get('run_id'), 'runs': runs,
         'agreed': agreed, 'target': state.get('target')}
    if extra:
        p.update(extra)
    return p


def fact(id, value, unit=None, source='measured', prediction=None, **extra):
    f = {'id': id, 'value': value, 'source': source}
    if unit:
        f['unit'] = unit
    if prediction is not None:
        f['prediction'] = prediction
    f.update(extra)
    return f


def validate(doc):
    """Check a facts document against the schema (raises FactsError)."""
    if not isinstance(doc, dict):
        raise FactsError("a facts document is a JSON object")
    for k, t in (('schema', int), ('kind', str), ('provenance', dict), ('facts', list)):
        if not isinstance(doc.get(k), t):
            raise FactsError(f"'{k}' is missing or not a {t.__name__}")
    if doc['schema'] != SCHEMA:
        raise FactsError(f"schema {doc['schema']}, expected {SCHEMA}")
    missing = [k for k in PROVENANCE_KEYS if k not in doc['provenance']]
    if missing:
        raise FactsError(f"provenance lacks {', '.join(missing)}")
    if not doc['provenance'].get('chip'):
        raise FactsError("provenance does not say which chip")
    seen = set()
    for f in doc['facts']:
        if not isinstance(f, dict) or not isinstance(f.get('id'), str) or 'value' not in f:
            raise FactsError(f"a fact needs an id and a value: {str(f)[:80]}")
        if f['id'] in seen:
            raise FactsError(f"fact {f['id']} appears twice")
        seen.add(f['id'])
        if f.get('source') not in ('measured', 'datasheet', 'derived', 'not modelled', 'varies'):
            raise FactsError(f"fact {f['id']}: unknown source {f.get('source')!r}")
        p = f.get('prediction')
        if p is not None and not (isinstance(p, dict) and 'from' in p):
            raise FactsError(f"fact {f['id']}: a prediction says where it is from")
    return doc


def write(name, kind, prov, facts, target_kind, **tables):
    doc = {'schema': SCHEMA, 'kind': kind, 'provenance': prov, 'facts': facts, **tables}
    validate(doc)
    path = os.path.join(root(target_kind), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as f:
        json.dump(doc, f, indent=1, sort_keys=False, default=_jsonable)
        f.write('\n')
    os.replace(path + '.tmp', path)
    return path


def _jsonable(o):
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(type(o))


def read(name, target_kind='openocd'):
    path = os.path.join(root(target_kind), name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return validate(json.load(f))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def record_raw(run_path, target_kind):
    """Note a run's raw files (with SHA-256) in raw_index.json, and copy
    them to ~/picosim-hwfacts-raw/ (decision D4: kept out of git, twice)."""
    import shutil
    idx_path = os.path.join(root(target_kind), 'raw_index.json')
    idx = {}
    if os.path.exists(idx_path):
        with open(idx_path) as f:
            idx = json.load(f)
    files = {}
    for n in sorted(os.listdir(run_path)):
        if n.startswith('batch-') or n == 'state.json':
            files[n] = sha256_file(os.path.join(run_path, n))
    # <label>/<campaign>-<stage>/<run id>: where the run sits under the cache
    key = '/'.join(os.path.normpath(os.path.abspath(run_path)).split(os.sep)[-3:])
    in_cache = os.path.realpath(run_path).startswith(os.path.realpath(cache_dir()) + os.sep)
    entry = {'files': files, 'recorded': time.strftime('%Y-%m-%d %H:%M:%S')}
    try:
        with open(os.path.join(run_path, 'state.json')) as f:
            st = json.load(f)
        entry.update(finished=len(st['done']) == st['n_batches'], batches_done=len(st['done']),
                     batches=st['n_batches'], cases=st['n_cases'])
    except (OSError, ValueError, KeyError):
        pass
    old = idx.get(key)
    if old and old.get('files') == files and old.get('finished') == entry.get('finished'):
        entry['recorded'] = old['recorded']          # nothing new: keep the date it was first recorded
    idx[key] = entry
    if target_kind != 'standin' and in_cache:      # a run elsewhere (a test's temp folder) is not backed up
        dst = os.path.join(raw_backup_dir(), key)
        os.makedirs(dst, exist_ok=True)
        for n, sha in files.items():
            d = os.path.join(dst, n)
            if not (os.path.exists(d) and sha256_file(d) == sha):
                shutil.copy2(os.path.join(run_path, n), d)
            if sha256_file(d) != sha:
                raise FactsError(f"the backup copy {d} does not match the capture it was copied from")
    with open(idx_path + '.tmp', 'w') as f:
        json.dump(idx, f, indent=1)
    os.replace(idx_path + '.tmp', idx_path)
    return idx[key]


# ── the replay subset ───────────────────────────────────────────────────────

def write_replay(name, records, state, target_kind, fraction=1.0, seed=0, extra=None):
    """Save (case, result) pairs (runner.load() output) for `check`: all of
    them, or a seeded sample of `fraction`."""
    rng = random.Random(f"replay|{name}|{seed}")
    out = bytearray()
    n = 0
    full = {}
    for c, r in records:
        if fraction < 1 and rng.random() >= fraction:
            continue
        if r.get('changed_full') is not None:
            full[str(c['id'])] = r['changed_full']
        code = c['code_bytes']
        out += F.pack_words(F.CASE, c) + code + bytes(-len(code) % 4) + F.pack_words(F.RESULT, r)
        n += 1
    d = os.path.join(root(target_kind), 'replay')
    os.makedirs(d, exist_ok=True)
    data = os.path.join(d, name + '.bin.gz')
    with open(data + '.tmp', 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as f:
        f.write(bytes(out))           # mtime 0: the same records always give the same file
    os.replace(data + '.tmp', data)
    meta = {'name': name, 'cases': n, 'fraction': fraction, 'layout': state['layout'],
            'scratch_words': state['scratch_words'], 'flags': state['flags'], 'campaign': state['campaign'],
            'run_id': state['run_id'], 'sha256': sha256_file(data), 'timing': state.get('timing', False),
            'full_changes': full}
    if extra:
        meta.update(extra)
    with open(os.path.join(d, name + '.json'), 'w') as f:
        json.dump(meta, f, indent=1)
    return data, meta


def read_replay(path_json):
    """The (case, result) pairs of a replay file, and its settings."""
    with open(path_json) as f:
        meta = json.load(f)
    data = os.path.join(os.path.dirname(path_json), meta['name'] + '.bin.gz')
    if sha256_file(data) != meta['sha256']:
        raise FactsError(f"{data} does not match its recorded SHA-256")
    with gzip.open(data) as f:
        blob = f.read()
    out, o = [], 0
    while o < len(blob):
        c = F.unpack_words(F.CASE, blob[o:o + F.CASE_SIZE])
        o += F.CASE_SIZE
        n = c['code_len']
        c['code_bytes'] = blob[o:o + n]
        o += n + (-n % 4)
        r = F.unpack_result(blob[o:o + F.RESULT_SIZE])
        o += F.RESULT_SIZE
        if str(c['id']) in meta.get('full_changes', {}):
            r['changed_full'] = [tuple(x) for x in meta['full_changes'][str(c['id'])]]
        out.append((c, r))
    return out, meta


# ── checking the raw captures ──────────────────────────────────────────────

def verify_raw(target_kind='openocd'):
    """Check every run raw_index.json lists against its two copies (the cache
    and raw_backup_dir()), and list chip runs in the cache that the index
    does not have.  Returns {'ok': n, 'problems': [text], 'unindexed': [key]}."""
    idx_path = os.path.join(root(target_kind), 'raw_index.json')
    idx = {}
    if os.path.exists(idx_path):
        with open(idx_path) as f:
            idx = json.load(f)
    ok, problems = 0, []
    for key, e in sorted(idx.items()):
        bad = []
        for where, base in (('cache', cache_dir()), ('backup', raw_backup_dir())):
            if where == 'backup' and target_kind == 'standin':
                continue
            for n, sha in e['files'].items():
                p = os.path.join(base, key, n)
                if not os.path.exists(p):
                    bad.append(f"{where}: {n} missing")
                elif sha256_file(p) != sha:
                    bad.append(f"{where}: {n} differs from its recorded SHA-256")
        if bad:
            problems.append(f"{key}: " + '; '.join(bad[:4]) + (f" (+{len(bad) - 4} more)" if len(bad) > 4 else ''))
        else:
            ok += 1
    unindexed = []
    for label in sorted(os.listdir(cache_dir())):
        if not re.fullmatch(r'chip\d+', label):
            continue
        for camp in sorted(os.listdir(cache_dir(label))):
            d = os.path.join(cache_dir(label), camp)
            if not os.path.isdir(d):
                continue
            for rid in sorted(os.listdir(d)):
                st = os.path.join(d, rid, 'state.json')
                if not os.path.exists(st) or f"{label}/{camp}/{rid}" in idx:
                    continue
                with open(st) as f:
                    if json.load(f).get('done'):
                        unindexed.append(f"{label}/{camp}/{rid}")
    return {'ok': ok, 'problems': problems, 'unindexed': unindexed}
