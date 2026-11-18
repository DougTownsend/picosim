"""
Level 5 discovery from the command line (docs/level5-discovery.md).

    python3 -m picosim.discover <campaign> [--target openocd|gdb|standin] [--fresh] [--repeat]
                                           [--pins N,N] [--dry-run] [--limit N] [--chip-label L]
    python3 -m picosim.discover encodings|timing|peripherals --offline   (facts again from the saved runs, no board)
    python3 -m picosim.discover check [--standin] [--run DIR] [--report FILE] [--json FILE]
                      exit status: 0 pass (known differences only still pass), 1 new
                      differences, 3 nothing compared (no replay files or no comparable case)
    python3 -m picosim.discover status [--standin]   (this computer's runs, and the published
                                                       captures in hwfacts/ with how to replay them)
    python3 -m picosim.discover verify-raw            (the raw captures against raw_index.json, both copies)
    python3 -m picosim.discover preflight             (before a board session: what is in the way, what Resume does)
    python3 -m picosim.discover backup-flash | restore-flash
    python3 -m picosim.discover compare-chips [--a chip1] [--b chip2]

Campaigns: identity (0), memmap (A), registers (C), encodings (B), timing (D),
fuzz (E), flashed (F), peripherals (G).  A campaign that stops part-way
carries on where it stopped when run again (--fresh starts over).
"""

import argparse
import os
import sys

from .cases import ORDER, LETTERS, module


def _pins(text):
    return frozenset(int(p) for p in text.split(',') if p.strip()) if text else frozenset()


def parser():
    ap = argparse.ArgumentParser(prog='python3 -m picosim.discover',
                                 description="Measure the real RP2040 for picosim's Level 5")
    ap.add_argument('command', choices=list(ORDER) + ['check', 'status', 'backup-flash', 'restore-flash',
                                                      'compare-chips', 'verify-raw', 'preflight'])
    ap.add_argument('--target', choices=('openocd', 'gdb', 'standin'), default='openocd')
    ap.add_argument('--gdb', default='127.0.0.1:3333', help="host:port of a running GDB server (--target gdb)")
    ap.add_argument('--speed', type=int, default=5000, help="SWD clock in kHz")
    ap.add_argument('--openocd', help="the openocd executable, if it is not found")
    ap.add_argument('--fresh', action='store_true', help="start the campaign's runs again")
    ap.add_argument('--repeat', action='store_true', help="run each stage twice (batches reordered) and compare")
    ap.add_argument('--pins', type=_pins, default=frozenset(),
                    help="GPIOs this run may drive (default none; GP23/24/25/29 are always refused)")
    ap.add_argument('--dry-run', action='store_true', help="make the cases and check them; touch nothing")
    ap.add_argument('--limit', type=int, help="at most N cases per stage (a quick trial)")
    ap.add_argument('--stages', help="only these stages, e.g. B1,B2 or D3,D4")
    ap.add_argument('--chip-label', default='chip1', help="which board (campaign H runs on chip2)")
    ap.add_argument('--standin', action='store_true', help="check/status: the stand-in's facts (= --target standin)")
    ap.add_argument('--run', help="check: one run folder instead of the replay files")
    ap.add_argument('--report', help="check: write the report here (default hwfacts/report.md)")
    ap.add_argument('--json', help="check: also write the result as JSON (counts, datasets, new and known kinds)")
    ap.add_argument('--hours', type=float, help="fuzz: keep going for this long (resumable)")
    ap.add_argument('--rounds', type=int, help="fuzz: this many rounds of 1000 blocks")
    ap.add_argument('--a', default='chip1')
    ap.add_argument('--b', default='chip2')
    ap.add_argument('--yes', action='store_true', help="flashed: don't ask before writing the flash")
    ap.add_argument('--sweep', choices=('physical', 'smoke'),
                    help="flashed: the GPIO pull-latency sweep; memmap: the address sweep (stage 'sweep'): "
                         "physical (every delay / the full grid; always on a chip) or smoke (a few delays / a "
                         "sparse grid; the stand-in's default)")
    ap.add_argument('--offline', action='store_true',
                    help="encodings, timing, peripherals (SIO part): no board; rebuild the facts from the finished runs on disk "
                         "(after picosim's comparison changed)")
    return ap


def connect(a):
    from ..hwtest.target import Target
    host, _, port = a.gdb.rpartition(':')
    return Target(a.target, host or '127.0.0.1', int(port or 3333), a.openocd, a.speed)


def main(argv=None):
    a = parser().parse_args(argv)
    a.standin = a.standin or a.target == 'standin'      # either spelling, for check/status/compare-chips
    if a.sweep == 'smoke' and a.target != 'standin':
        print("--sweep smoke is for the stand-in only: a chip capture always runs the physical sweep")
        return 2
    if a.command == 'check':
        return cmd_check(a)
    if a.command == 'status':
        return cmd_status(a)
    if a.command == 'verify-raw':
        return cmd_verify_raw(a)
    if a.command == 'preflight':
        from .preflight import main as preflight
        return preflight(a.chip_label)
    if a.command == 'compare-chips':
        from .compare import compare_chips
        print(compare_chips(a.a, a.b, 'standin' if a.standin else 'openocd'))
        return 0
    from . import flashguard
    from .campaign import Context
    if a.command in ('backup-flash', 'restore-flash'):
        t = connect(a)
        try:
            g = flashguard.FlashGuard.from_target(t)
            if a.command == 'backup-flash':
                b = g.backup()
                print(f"backed up: {b}")
            else:
                r = g.restore()
                print(f"restored: backup {r.backup_sha256}\n          read-back {r.readback_sha256}\n"
                      f"boot check: {'ok' if r.boot_ok else 'NOT confirmed'} ({r.boot_detail})")
        finally:
            t.close()
        return 0
    if a.target != 'standin':
        flashguard.require_no_pending_restore()
    m = module(a.command)
    if a.offline:
        return cmd_offline(a)
    if a.dry_run:
        ctx = Context(None, a)
        n = 0
        for stage, cases, kw in m.dry_cases(ctx):
            from . import runner as R
            from . import formats as F
            lay = F.LAYOUTS[kw.get('layout', 'hi')]
            cases = list(cases)
            for c in cases:
                from .safety import check_case
                check_case({**c, 'code_len': len(R._code_bytes(c))}, lay, ctx.pins, kw.get('sites'))
            plan = R.plan(cases, lay, [tuple(s) for s in kw.get('sites') or [(lay['SLED'], lay['SLED'] + lay['SLED_SIZE'])]],
                          kw.get('max_cases'))
            print(f"{stage}: {len(cases)} cases in {len(plan)} batches: safety checks passed")
            n += len(cases)
        print(f"{m.TITLE}: {n} cases, nothing sent to the board")
        return 0
    t = connect(a)
    print(t.description, flush=True)
    try:
        run_campaign(a, t, log=lambda s: print(s, flush=True))
    finally:
        t.close()
    return 0


def run_campaign(a, t, log=print, progress=None, stop_flag=None, ask=None):
    """Run campaign `a.command` (options from parser()) on the connected
    target `t`.  The command line and the Testing window (hwtest/session.py)
    both come here.  A run stopped through `stop_flag` raises runner.RunError
    after the batch it was in; running it again resumes it."""
    from . import flashguard
    from .campaign import Context
    if t.kind != 'standin':
        flashguard.require_no_pending_restore()
    ctx = Context(t, a, log=log, ask=ask, stop_flag=stop_flag, progress=progress)
    return module(a.command).run(ctx)


OFFLINE = ('encodings', 'timing', 'peripherals')   # from their raw records (peripherals: the SIO part only)


def cmd_offline(a):
    """Rebuild a campaign's facts from its finished runs on disk, comparing
    them with today's picosim.  The board is never touched and the raw
    records are not changed; the facts' provenance says it was rebuilt."""
    from .campaign import Context, OfflineTarget, recorded_openocd
    from .runner import RunError
    if a.command not in OFFLINE:
        print(f"--offline: {a.command} reads the chip through the debugger, so it needs the board "
              f"(--offline works for {', '.join(OFFLINE)})")
        return 2
    if a.target == 'standin' or a.fresh or a.dry_run:
        print("--offline rebuilds the chip's facts from disk; it does not go with --target standin, --fresh or --dry-run")
        return 2
    t = OfflineTarget(recorded_openocd())
    print(t.description, flush=True)
    try:
        module(a.command).run(Context(t, a, log=lambda s: print(s, flush=True)))
    except RunError as e:
        print(f"stopped: {e}")
        return 1
    return 0


def cmd_check(a):
    """Replay and compare; the exit status is check.EXIT[verdict] (0 pass,
    1 new differences, 3 nothing compared: see check.py)."""
    from . import check
    from .replay import HWFACTS
    if a.run:
        res, sources = check.check_run(a.run), [a.run]
    else:
        res, sources = check.check_replays(standin=a.standin)
    text = check.report(res, sources=sources)
    out = a.report
    if not out and not a.standin and not a.run:
        out = os.path.join(HWFACTS, 'report.md')
        if res.verdict() == check.EMPTY:
            out = None                  # an empty replay never replaces the published report
    if out:
        with open(out, 'w') as f:
            f.write(text)
        print(f"report: {out}")
    if a.json:
        import json
        with open(a.json, 'w') as f:
            json.dump(res.summary(), f, indent=1)
        print(f"summary: {a.json}")
    c = res.counts
    print(f"evidence: {check.EVIDENCE.get(res.evidence, 'not stated')}")
    if res.sources:
        print("datasets: " + ', '.join(f"{s['name']} ({s['cases']})" for s in res.sources))
    print(f"{res.total} cases: {c['match']} match, {c['differs']} differ ({res.differs_known} known, "
          f"{res.differs_new} new in {len(res.new)} kinds), compared {res.compared}")
    for k in res.new:
        e = res.groups[k]['example']
        print(f"  NEW {k[1]} {k[2]}: {res.groups[k]['n']} cases, e.g. case {e['id']} of {e.get('source')}: "
              f"{e['detail']}")
    verdict = res.verdict()
    print(f"verdict: {verdict}" + (f": {res.next_step}" if res.next_step else ''))
    return res.exit_code()


def cmd_verify_raw(a):
    """Every raw capture raw_index.json lists, in the cache and in its
    backup copy, against its recorded SHA-256; and chip runs not indexed."""
    from .facts import verify_raw, raw_backup_dir
    r = verify_raw('standin' if a.standin else 'openocd')
    print(f"{r['ok']} runs intact in the cache and in {raw_backup_dir()}")
    for p in r['problems']:
        print(f"  PROBLEM {p}")
    for k in r['unindexed']:
        print(f"  not indexed (no SHA-256, no backup): {k}")
    return 1 if r['problems'] or r['unindexed'] else 0


def cmd_status(a):
    """Two things side by side: the campaigns run on THIS computer (its
    cache: a fresh checkout has none), and the evidence published in the
    checkout (hwfacts/: measured on a real chip, with the replay files any
    checkout can compare picosim against with no board)."""
    from .build import cache_dir
    from .campaign import read_status, _sub
    from .facts import published
    kind = 'standin' if a.standin else 'openocd'
    pub = {} if a.standin else published()
    print(f"Local runs on this computer ({cache_dir(_sub(kind, a.chip_label))})"
          + ("" if a.standin else ", and the RP2040 captures published in this checkout") + ":")
    for name in ORDER:
        s = read_status(name, kind, a.chip_label)
        extra = ''
        if s.get('state') == 'partial':
            if s.get('step'):
                extra = f" ({s['step']})"
            elif 'done' in s:
                extra = f" ({s['done']}" + (f" of {s['total']})" if 'total' in s else ' so far)')
        if s.get('differences') is not None:
            extra += f", {s['differences']} differences from picosim"
        elif s.get('state') == 'done':
            extra += ", measured (no picosim comparison)"
        local = s.get('state', 'not run')
        if local == 'not run':
            local = 'not run here'
        print(f"{LETTERS[name]:>2}  {name:12} local: {local}{extra}")
        if not a.standin:
            p = pub.get(name)
            print(f"{'':17}published: {_published_text(p) if p else 'none in this checkout'}")
    if a.standin:
        print("\nThe stand-in's results stay in this computer's cache; nothing from it is published in hwfacts/.")
        return 0
    replays = [r for p in pub.values() for r in p['replays']]
    if pub:
        print(f"\nPublished in this checkout ({pub_root()}): measured on the RP2040 and kept with the "
              "repository, whatever ran on this computer.")
    else:
        print(f"\nNo published evidence in this checkout ({pub_root()}).")
    if replays:
        print(f"{len(replays)} replay files ({sum(r['cases'] for r in replays)} cases) can be compared with "
              "picosim here, with no board:\n"
              "    python3 -m picosim.discover check --report /tmp/picosim-replay.md --json /tmp/picosim-replay.json\n"
              "(without --report it rewrites hwfacts/report.md; the published one is "
              f"{os.path.join(pub_root(), 'report.md')})")
    return 0


def pub_root():
    from .replay import HWFACTS
    return HWFACTS


def _published_text(p):
    out = []
    fs = p['facts']
    if len(fs) > 2:
        dates = sorted((d.get('date') or '?')[:10] for d in fs)
        out.append(f"{len(fs)} facts files in {os.path.dirname(fs[0]['file']) or '.'}/ "
                   f"({sum(d['facts'] for d in fs)} facts, measured {dates[0]} to {dates[-1]})")
    else:
        for d in fs:
            out.append(f"{d['file']} ({d['facts']} facts, measured {(d.get('date') or '?')[:10]})")
    if p['replays']:
        out.append(f"{len(p['replays'])} replay files, {sum(r['cases'] for r in p['replays'])} cases")
    return '; '.join(out)


if __name__ == '__main__':
    sys.exit(main())
