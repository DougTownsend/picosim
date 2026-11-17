"""
Before a board session: what is in the way, and what each campaign will do
when it is run again.  Reads only this computer (the cache, hwfacts/, the
process list); the board is never touched.

    python3 -m picosim.discover preflight

Checks, each 'ok', 'warn' or 'stop':
  - OpenOCD processes already running.  One left from an earlier picosim
    holds the probe, and the next Connect fails (2026-11-11: one had been
    running since the 5th).
  - A flash restore still pending from campaign F (every campaign refuses
    to start until it is done).
  - openocd and picotool on the PATH (picotool: campaign F's second backup).
  - The raw captures against raw_index.json, both copies (verify_raw).
  - For every stage of every agent campaign, what a Resume (or the
    command line without --fresh) will do with the runs on disk, the same
    way campaign.Context.stage decides: reuse a finished run, resume an
    unfinished one at its next batch, or start a new run because the cases
    or the agent changed since (the old run's batches are then set aside).
"""

import json
import os
import shutil
import subprocess
from types import SimpleNamespace

from . import runner as R
from .build import agent as build_agent, cache_dir
from .cases import ORDER, LETTERS, module

OK, WARN, STOP = 'ok', 'warn', 'stop'


def openocd_processes():
    """[(pid, started, command)] of running OpenOCD processes."""
    try:
        out = subprocess.run(['ps', '-axo', 'pid=,lstart=,command='], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        cmd = parts[6]
        exe = os.path.basename(cmd.split()[0]) if cmd.split() else ''
        if exe == 'openocd':
            procs.append((int(parts[0]), ' '.join(parts[1:6]), cmd))
    return procs


def stage_plan(base, cases):
    """What Context.stage will do with the runs in `base` for these cases:
    (verdict, text).  Verdicts: 'reuse', 'resume', 'new', 'not run'."""
    existing = sorted(os.listdir(base)) if os.path.isdir(base) else []
    set_aside = []
    for rid in reversed([e for e in existing if not e.endswith('-repeat')]):
        path = os.path.join(base, rid)
        if not os.path.exists(os.path.join(path, 'state.json')):
            continue
        try:
            state, _ = R.same_cases(path, cases)
        except R.RunError:
            with open(os.path.join(path, 'state.json')) as f:
                st = json.load(f)
            if st.get('done'):
                set_aside.append(f"{rid} ({len(st['done'])} of {st['n_batches']} batches; its cases changed since)")
            continue
        if build_agent(state['layout']).sha1 != state['agent']:
            set_aside.append(f"{rid} ({len(state['done'])} of {state['n_batches']} batches; the agent changed since)")
            continue
        done, n = len(state['done']), state['n_batches']
        if done == n:
            return 'reuse', f"reuses the finished run {rid} ({state['n_cases']:,} cases); no board time"
        return 'resume', f"resumes run {rid} at batch {done + 1} of {n} ({n - done} batches to go)"
    if set_aside:
        return 'new', "starts a NEW run; set aside: " + '; '.join(set_aside[:3])
    return 'not run', "not run yet"


def campaign_plan(name, label='chip1'):
    """[(stage, verdict, text)] for an agent campaign's stages."""
    mod = module(name)
    if not getattr(mod, 'AGENT_BASED', False) or not hasattr(mod, 'dry_cases'):
        return []
    if name == 'fuzz':
        # E's rounds are made as it goes (fuzz-round-NNNNN); a run carries on
        # after the last round, for --rounds or --hours
        from .campaign import read_status
        s = read_status(name, 'openocd', label)
        rounds = sorted(d for d in os.listdir(cache_dir(label)) if d.startswith('fuzz-round-'))
        return [('rounds', 'reuse' if rounds else 'not run',
                 f"{len(rounds)} round{'s' if len(rounds) != 1 else ''} on disk ({s.get('done', 0):,} blocks); "
                 "a new run adds rounds (--rounds N or --hours H)" if rounds else "not run yet")]
    ctx = SimpleNamespace(opts=SimpleNamespace(stages=None, pins=frozenset()), pins=frozenset(),
                          label=label, kind='openocd', limit=None)
    root = cache_dir(label)
    out = []
    for stage, cases, _ in mod.dry_cases(ctx):
        base = os.path.join(root, f"{mod.NAME}-{stage}")
        verdict, text = stage_plan(base, list(cases))
        out.append((stage, verdict, text))
    return out


def run(label='chip1', check_raw=True):
    """[(level, topic, text)]"""
    from . import flashguard
    from .campaign import read_status
    from .facts import verify_raw
    out = []
    procs = openocd_processes()
    if procs:
        for pid, started, _ in procs:
            out.append((STOP, 'OpenOCD', f"already running (pid {pid}, started {started}). It holds the probe: "
                                         f"quit picosim, and if it is still there, `kill {pid}`."))
    else:
        out.append((OK, 'OpenOCD', "no OpenOCD running"))
    try:
        flashguard.require_no_pending_restore()
        out.append((OK, 'flash', "no flash restore pending"))
    except Exception as e:                       # noqa: BLE001 - FlashGuardError: say it as it is
        out.append((STOP, 'flash', str(e)))
    for tool, why in (('openocd', 'every campaign'), ('picotool', "campaign F's second flash backup")):
        out.append((OK, tool, shutil.which(tool)) if shutil.which(tool)
                   else (WARN if tool == 'picotool' else STOP, tool, f"not found on the PATH (needed for {why})"))
    if check_raw:
        r = verify_raw('openocd')
        if r['problems'] or r['unindexed']:
            for p in r['problems']:
                out.append((WARN, 'raw data', p))
            for k in r['unindexed']:
                out.append((WARN, 'raw data', f"{k} is not indexed or backed up"))
        else:
            out.append((OK, 'raw data', f"{r['ok']} runs intact in both copies"))
    for name in ORDER:
        st = read_status(name, 'openocd', label).get('state', 'not run')
        try:
            plan = campaign_plan(name, label)
        except Exception as e:                   # noqa: BLE001 - one campaign's generator must not hide the rest
            out.append((WARN, f"{LETTERS[name]} {name}", f"could not work out its stages: {e}"))
            continue
        for stage, verdict, text in plan:
            level = WARN if verdict == 'new' and st != 'not run' else OK
            out.append((level, f"{LETTERS[name]} {name} {stage}", text))
    return out


def main(label='chip1'):
    rows = run(label)
    mark = {OK: '  ok ', WARN: ' WARN', STOP: ' STOP'}
    for level, topic, text in rows:
        print(f"{mark[level]}  {topic:24} {text}")
    stops = sum(1 for r in rows if r[0] == STOP)
    warns = sum(1 for r in rows if r[0] == WARN)
    print(f"\n{'Ready for the board.' if not stops else 'Fix the STOP lines before connecting.'}"
          + (f" {warns} warning{'s' if warns != 1 else ''}." if warns else ''))
    return 1 if stops else 0
