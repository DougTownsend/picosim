"""
Run Testing from the command line: the suite (or given .s files) on a real
RP2040, with a report at the end.

    python3 -m picosim.hwtest                       # the whole suite, OpenOCD + debug probe
    python3 -m picosim.hwtest --target standin      # no hardware
    python3 -m picosim.hwtest --target gdb --gdb 127.0.0.1:3333 hwtests/t01_add_sub_flags.s
    python3 -m picosim.hwtest --report results.md   # also save a report (and results.json)

A file is a benchmark if its header says "@ Kind: bench" (or --bench).
The exit status is 0 when every test is identical, 1 otherwise.
"""

import argparse
import os
import sys

from . import suite
from .session import Session


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
    ap.add_argument('--report', help="save a Markdown report here (and the data as .json next to it)")
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
    try:
        while True:
            s.thread.join(0.2)
            for m in s.drain():
                e = m.get('event')
                if e == 'start':
                    print(f"\n{m['title']} ({m['kind']})", flush=True)
                elif e == 'summary':
                    r = m['summary']
                    ok &= r['verdict'] == 'identical'
                    print(f"  {r['verdict'].upper()}  {r.get('note', '')}")
                    if r['kind'] == 'trace':
                        c = r['counts']
                        print(f"  {r['steps']} steps: {c['same']} identical, {c['call']} OS calls, "
                              f"{c['io']} I/O differences, {c['diff']} differences; memory: "
                              f"{r['memory']['different_words']} words differ")
                        for row in r.get('diff_rows', [])[:5]:
                            print(f"    step {row['n']} 0x{row['addr']:04X} {row['text']}: {', '.join(row['diffs'])}")
                    else:
                        for g in r['regions']:
                            d = g.get('diff')
                            print(f"    {g['name'][:52]:52} picosim {g.get('sim')!s:>6}  RP2040 {g.get('hw')!s:>6}"
                                  + (f"  {d:+d}" if d is not None else ''))
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
        for p in s.export(a.report):
            print(f"saved {p}")
    s.disconnect()
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
