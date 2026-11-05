"""
Level 5 discovery from the command line (docs/level5-discovery.md).

    python3 -m picosim.discover <campaign> [--target openocd|gdb|standin] [--fresh] [--repeat]
                                           [--pins N,N] [--dry-run] [--limit N] [--chip-label L]
    python3 -m picosim.discover check [--standin] [--run DIR] [--report FILE]
    python3 -m picosim.discover status [--standin]
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
                                                      'compare-chips'])
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
    ap.add_argument('--hours', type=float, help="fuzz: keep going for this long (resumable)")
    ap.add_argument('--rounds', type=int, help="fuzz: this many rounds of 1000 blocks")
    ap.add_argument('--a', default='chip1')
    ap.add_argument('--b', default='chip2')
    ap.add_argument('--yes', action='store_true', help="flashed: don't ask before writing the flash")
    return ap


def connect(a):
    from ..hwtest.target import Target
    host, _, port = a.gdb.rpartition(':')
    return Target(a.target, host or '127.0.0.1', int(port or 3333), a.openocd, a.speed)


def main(argv=None):
    a = parser().parse_args(argv)
    a.standin = a.standin or a.target == 'standin'      # either spelling, for check/status/compare-chips
    if a.command == 'check':
        return cmd_check(a)
    if a.command == 'status':
        return cmd_status(a)
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


def cmd_check(a):
    from . import check
    from .replay import HWFACTS
    if a.run:
        res, sources = check.check_run(a.run), [a.run]
    else:
        res, sources = check.check_replays(standin=a.standin)
    text = check.report(res, sources=sources)
    out = a.report or (os.path.join(HWFACTS, 'report.md') if not a.standin and not a.run else None)
    if out:
        with open(out, 'w') as f:
            f.write(text)
        print(f"report: {out}")
    c = res.counts
    print(f"{sum(c.values())} cases: {c['match']} match, {c['differs']} differ, {len(res.new)} kinds new")
    return 1 if res.new else 0


def cmd_status(a):
    from .campaign import read_status
    kind = 'standin' if a.standin else 'openocd'
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
        print(f"{LETTERS[name]:>2}  {name:12} {s.get('state', 'not run')}{extra}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
