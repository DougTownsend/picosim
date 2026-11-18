"""
Run Testing from the command line: the suite (or given .s files) on a real
RP2040, with a report at the end.

    python3 -m picosim.hwtest                       # the whole suite, OpenOCD + debug probe
    python3 -m picosim.hwtest --target standin      # no hardware
    python3 -m picosim.hwtest --target gdb --gdb 127.0.0.1:3333 hwtests/t01_add_sub_flags.s
    python3 -m picosim.hwtest --report results.md   # also save a report (and results.json)

A file is a benchmark if its header says "@ Kind: bench" (or --bench).

Every result says what it ran on (the stand-in emulator or a physical
RP2040) from the provenance stamped on it when it ran (provenance.py).

A step-by-step result is one of
  IDENTICAL     complete agreement: the program ran to its end on both
  BOUNDED       agreement through the steps compared, but the step limit
                (--max-steps) stopped the run first ("Agreement through 2
                steps · step limit reached")
  INCONCLUSIVE  nothing differed, but too little was compared (stopped by you)
  DIFFERENT     a disagreement

Exit status:
  0  every test is identical (with --accept-bounded, BOUNDED counts too)
  1  a test differs, could not run, or the run was interrupted
  2  could not connect
  3  nothing differed, but at least one result is BOUNDED or INCONCLUSIVE
     (so a capped run never passes as a full one by accident)

Reports (--report) name each source file by its path in the picosim checkout
or its file name, with its SHA-256; --local-paths adds the full local path.
"""

import argparse
import os
import sys

from . import provenance
from . import suite
from .session import Session

LABEL = {'identical': 'IDENTICAL', 'different': 'DIFFERENT', 'bounded': 'BOUNDED',
         'inconclusive': 'INCONCLUSIVE'}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python3 -m picosim.hwtest',
                                 description="Compare picosim with a real RP2040 (see docs/hardware-testing.md)")
    ap.add_argument('files', nargs='*', help=".s files (default: every test in hwtests/)")
    ap.add_argument('--target', choices=('openocd', 'gdb', 'standin'), default='openocd')
    ap.add_argument('--gdb', default='127.0.0.1:3333', help="host:port of a GDB server (--target gdb)")
    ap.add_argument('--speed', type=int, default=5000, help="SWD clock in kHz (--target openocd)")
    ap.add_argument('--openocd', help="the openocd executable, if picosim does not find it")
    ap.add_argument('--bench', action='store_true', help="time the given files instead of tracing them")
    ap.add_argument('--input', default='', help="what getchar returns (\\n for a new line)")
    ap.add_argument('--max-steps', type=int, default=20000)
    ap.add_argument('--stop-on-diff', action='store_true')
    ap.add_argument('--accept-bounded', action='store_true',
                    help="exit 0 when a trace agrees as far as --max-steps let it run (BOUNDED)")
    ap.add_argument('--report', help="save a Markdown report here (and the data as .json next to it)")
    ap.add_argument('--local-paths', action='store_true',
                    help="put each source file's full local path in the report (off: shareable)")
    a = ap.parse_args(argv)

    if a.files:
        tests = []
        for f in a.files:
            h = suite.header(os.path.abspath(f))
            tests.append({'path': h['path'], 'kind': 'bench' if a.bench else h['kind'], 'title': h['title']})
    else:
        tests = [{'path': t['path'], 'kind': t['kind'], 'title': t['title']} for t in suite.tests()]

    s = Session()
    host, _, port = a.gdb.rpartition(':')
    print(f"connecting ({a.target})…", flush=True)
    s.connect(a.target, host or '127.0.0.1', int(port or 3333), a.speed, a.openocd)
    s.thread.join()
    if not s.target:
        print("could not connect:\n" + s.error, file=sys.stderr)
        return 2
    print(s.target.description, flush=True)
    s.drain()
    s.run(tests, a.input.encode().decode('unicode_escape'), a.max_steps, a.stop_on_diff)
    ok = True
    partial = False
    try:
        while True:
            s.thread.join(0.2)
            for m in s.drain():
                e = m.get('event')
                if e == 'start':
                    print(f"\n{m['title']} ({m['kind']})", flush=True)
                elif e == 'summary':
                    print_summary(m['summary'])
                    v = m['summary']['verdict']
                    if v == 'bounded' and a.accept_bounded:
                        continue
                    if v in ('bounded', 'inconclusive'):
                        partial = True
                    else:
                        ok &= v == 'identical'
                elif e == 'error':
                    ok = False
                    print(f"  ERROR: {m['message']}")
            if not s.busy() and s.q.empty():
                break
    except KeyboardInterrupt:
        s.stop()
        s.thread.join(10)
        ok = False
    if a.report:
        for p in s.export(a.report, local_paths=a.local_paths):
            print(f"saved {p}")
    s.disconnect()
    return 1 if not ok else (3 if partial else 0)


def print_summary(r):
    """A test's result, labelled from its own provenance."""
    prov = r.get('provenance') or provenance.unknown()
    side = provenance.side(prov)
    v = r['verdict']
    if r['kind'] == 'trace':
        print(f"  {LABEL.get(v, v.upper())}  {r.get('headline', '')}")
        print(f"  {r.get('note', '')}")
        print(f"  ran on: {prov['evidence_label']} ({prov['target'].get('description', 'unknown')})")
        c = r['counts']
        print(f"  {r['steps']} steps compared: {c['same']} identical, {c['call']} OS calls, "
              f"{c['io']} I/O differences, {c['diff']} differences; memory: "
              f"{r['memory']['different_words']} words differ")
        if r.get('stop'):
            print(f"  stop: {r['stop']['text']}; normalized: {r['normalization']['text']}")
        print("  addresses: " + provenance.address_text(side))
        for row in r.get('diff_rows', [])[:5]:
            print(f"    step {row['n']} 0x{row['addr']:08X} (0x{row.get('chip_addr', row['addr']):08X} on the "
                  f"{side}) {row['text']}: {', '.join(row['diffs'])}")
    else:
        print(f"  {LABEL.get(v, v.upper())}  {r.get('note', '')}")
        print(f"  ran on: {prov['evidence_label']} ({prov['target'].get('description', 'unknown')})")
        tl = r.get('timing') or provenance.timing_labels(prov, r.get('stretches'), r.get('calibration'))
        print(f"  cycles from: {tl['picosim']}")
        print(f"               {tl['target']}")
        print(f"  scope: {tl['scope']}; unit: {tl['unit']}")
        for g in r['regions']:
            d = g.get('diff')
            print(f"    {g['name'][:52]:52} picosim {g.get('sim')!s:>6}  {side} {g.get('hw')!s:>6}"
                  + (f"  {d:+d}" if d is not None else ''))


if __name__ == '__main__':
    sys.exit(main())
