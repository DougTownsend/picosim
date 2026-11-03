"""
Testing for the GUI backend: one connection to a chip, test runs in a
background thread, and the messages they produce queued for server.py to
send (the server's socket belongs to its own thread).

Messages to the GUI (all {'type': 'hw', 'event': ...}):
  status    connected (description) or not, busy, the last error
  start     a test begins: path, kind, title
  steps     a batch of lockstep rows for the test running
  region    one benchmark region's result
  summary   a test's result (lockstep.py / bench.py summaries)
  error     a test could not run: message
  done      the run is over
"""

import json
import os
import queue
import threading
import time

from . import suite
from .bench import Bench
from .lockstep import Lockstep, TestError, fmt_flags
from .rsp import RSPError
from .target import Target, TargetError, find_openocd, log_dir

BATCH = 0.15          # seconds of lockstep rows sent together


def _lost(e):
    """Did this error lose the connection (or leave it out of step)?"""
    m = str(e).lower()
    return any(k in m for k in ('closed the connection', 'lost the debug server', 'broken pipe',
                                'did not answer in time', 'connection reset', 'out of step'))


class Session:
    def __init__(self):
        self.target = None
        self.q = queue.Queue()
        self.thread = None
        self.stop_flag = threading.Event()
        self.results = {}            # path -> the last summary
        self.order = []              # paths in the order they last ran
        self.error = ''
        self.last_log = ''            # the log of the last connection, kept after it closes
        self._rows = []
        self._rows_path = None
        self._rows_at = 0.0

    # ── what the GUI asks ───────────────────────────────────────────────────

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def info(self):
        exe, _ = find_openocd()
        try:
            from .standin import available
            standin = available()
        except ImportError:
            standin = False
        return {'type': 'hw', 'event': 'info', 'suite': suite.tests(), 'openocd': exe or '',
                'standin': standin, **self._status()}

    def _status(self, busy=None):
        return {'connected': self.target.description if self.target else '',
                'kind': self.target.kind if self.target else '',
                'busy': self.busy() if busy is None else busy, 'error': self.error,
                'calibration': getattr(self.target, 'calibration', None) if self.target else None,
                'log': self.target.log_path if self.target else self.last_log or log_dir()}

    def status(self, busy=None):
        """Queue the connection's status.  The worker thread passes busy=False
        as its last act (it is still alive while it says so)."""
        self.q.put({'type': 'hw', 'event': 'status', **self._status(busy)})

    def connect(self, kind, host='127.0.0.1', port=3333, speed=5000, openocd=None):
        if self.busy():
            return "Testing is busy; stop the run first"

        def work():
            self.error = ''
            self._close()
            try:
                self.target = Target(kind, host, int(port), openocd or None, int(speed))
                self.target.reset_halt()
            except (TargetError, RSPError, RuntimeError, OSError) as e:
                self._close()
                self.error = str(e)
            self.status(busy=False)
        self._start(work)

    def disconnect(self):
        if self.busy():
            self.stop_flag.set()
            self.thread.join(5)
        self._close()
        self.error = ''
        self.status()

    def _close(self):
        if self.target:
            self.last_log = self.target.log_path
            try:
                self.target.close()
            except Exception:
                pass
        self.target = None

    def stop(self):
        self.stop_flag.set()

    def run(self, tests, input_text='', max_steps=20000, stop_on_diff=False):
        """tests: [{'path', 'kind', 'title'?}] run one after the other."""
        if self.thread is not None:
            # the last run may have sent 'done' and still be finishing
            self.thread.join(2.0)
        if self.busy():
            return "a run is already going"
        if not self.target:
            return "connect to a chip first"

        def work():
            for t in tests:
                if self.stop_flag.is_set():
                    break
                self._one(t, input_text, max_steps, stop_on_diff)
            self._flush_rows()
            self.q.put({'type': 'hw', 'event': 'done', 'stopped': self.stop_flag.is_set()})
            self.status(busy=False)
        self._start(work)

    def _start(self, fn):
        self.stop_flag.clear()
        self.thread = threading.Thread(target=fn, daemon=True)
        self.thread.start()
        self.q.put({'type': 'hw', 'event': 'status', **self._status(True)})

    def _one(self, t, input_text, max_steps, stop_on_diff):
        path, kind = t['path'], t.get('kind', 'trace')
        info = suite.header(path)
        inp = t.get('input', input_text) or info.get('input', '')
        self.q.put({'type': 'hw', 'event': 'start', 'path': path, 'kind': kind,
                    'title': t.get('title') or info['title']})

        def emit(what, data):
            if what == 'step':
                self._rows_path = path
                self._rows.append(data)
                if time.monotonic() - self._rows_at > BATCH:
                    self._flush_rows()
            elif what == 'region':
                self.q.put({'type': 'hw', 'event': 'region', 'path': path, 'row': data})
            elif what == 'summary':
                self._flush_rows()
                data['title'] = t.get('title') or info['title']
                self.results[path] = data
                if path in self.order:
                    self.order.remove(path)
                self.order.append(path)
                self.q.put({'type': 'hw', 'event': 'summary', 'path': path, 'summary': data})
        for attempt in (1, 2):
            try:
                if kind == 'bench':
                    Bench(self.target, path, inp, emit, self.stop_flag, info['title']).run()
                else:
                    Lockstep(self.target, path, inp, max_steps, stop_on_diff, emit,
                             self.stop_flag, info['title']).run()
                return
            except (TestError, TargetError, RSPError) as e:
                self._flush_rows()
                lost = _lost(e)
                self.target.log(f"test {os.path.basename(path)} failed: {e}")
                if lost and attempt == 1 and not self.stop_flag.is_set():
                    # one more go on a fresh connection (and a fresh OpenOCD if it stopped)
                    try:
                        self.target.reconnect()
                        self.q.put({'type': 'hw', 'event': 'start', 'path': path, 'kind': kind,
                                    'title': t.get('title') or info['title'], 'retry': True})
                        continue
                    except (TargetError, RSPError) as e2:
                        e = e2
                msg = str(e)
                if lost:
                    msg = ("The connection to the chip was lost during this test, and reconnecting "
                           f"did not help ({e}). The run has stopped.\n\n" + self.target.explain())
                    self.stop_flag.set()
                    self.error = "Connection lost: see the test's message. " + \
                                 f"Full log: {self.target.log_path}"
                    self._close()
                elif isinstance(e, RSPError) or 'failed' in msg:
                    msg += f"\n\nFull log: {self.target.log_path}"
                self.q.put({'type': 'hw', 'event': 'error', 'path': path, 'message': msg})
                return
            except Exception as e:      # report it in the window; keep the session alive
                self._flush_rows()
                self.q.put({'type': 'hw', 'event': 'error', 'path': path,
                            'message': f"internal error: {e!r}"})
                return

    def _flush_rows(self):
        if self._rows:
            self.q.put({'type': 'hw', 'event': 'steps', 'path': self._rows_path, 'rows': self._rows})
            self._rows = []
        self._rows_at = time.monotonic()

    def drain(self, limit=200):
        out = []
        try:
            while len(out) < limit:
                out.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return out

    # ── the report ──────────────────────────────────────────────────────────

    def export(self, path):
        """Write the latest results: a Markdown report and the full data as JSON
        next to it.  Returns the paths written."""
        if not self.results:
            raise ValueError("no results yet: run a test first")
        base = os.path.splitext(path)[0]
        md, js = base + '.md', base + '.json'
        data = {'target': self.target.description if self.target else '',
                'calibration': getattr(self.target, 'calibration', None) if self.target else None,
                'when': time.strftime('%Y-%m-%d %H:%M:%S'),
                'results': [self.results[p] for p in self.order]}
        with open(js, 'w') as f:
            json.dump(data, f, indent=1, default=str)
        with open(md, 'w') as f:
            f.write(report_markdown(data))
        return [md, js]


def _hex(v):
    return f"0x{v & 0xFFFFFFFF:08X}"


def report_markdown(data):
    out = [f"# picosim vs. RP2040: test report", "",
           f"- Target: {data['target'] or 'not connected'}", f"- Date: {data['when']}"]
    cal = data.get('calibration')
    if cal:
        out.append(f"- Benchmark calibration: {cal['overhead']} cycles of measuring overhead "
                   f"(Table 81 predicts {cal['predicted']}); steady: {cal['steady']}")
    out.append("")
    out.append("| Test | Kind | Result | Detail |")
    out.append("| --- | --- | --- | --- |")
    for r in data['results']:
        if r['kind'] == 'trace':
            c = r['counts']
            detail = (f"{r['steps']} steps: {c['same']} identical, {c['call']} OS calls, "
                      f"{c['io']} I/O differences, {c['diff']} differences; memory "
                      f"{r['memory']['different_words']} different words")
        else:
            regs = r['regions']
            detail = f"{sum(1 for g in regs if g.get('diff') == 0)} of {len(regs)} regions exact"
        out.append(f"| {r['title']} | {r['kind']} | {r['verdict']} | {detail} |")
    for r in data['results']:
        out += ["", f"## {r['title']}", "", f"`{r['source']}`", "", r.get('note', '')]
        if r['kind'] == 'trace':
            if r['output']['sim'] or r['output']['hw']:
                out += ["", f"Output: picosim {r['output']['sim']!r}, RP2040 {r['output']['hw']!r}"]
            diffs = r.get('diff_rows') or []
            if r['memory']['different_words']:
                out += ["", "Memory words that differ (picosim address: picosim / RP2040):", ""]
                for d in r['memory']['first'][:20]:
                    out.append(f"- 0x{d['addr']:04X}: {_hex(d['sim'])} / {_hex(d['hw'])}")
            if r['gpio'].get('diffs'):
                out += ["", "GPIO registers that differ: " + ', '.join(r['gpio']['diffs'])]
            if r['gpio'].get('info'):
                out += ["", "Registers the program did not write whose values differ (not counted): " +
                        ', '.join(r['gpio']['info'][:12]) + (' …' if len(r['gpio']['info']) > 12 else '')]
        else:
            out += ["", "| Region | picosim cycles | RP2040 cycles | Difference | Per instruction | Notes |",
                    "| --- | ---: | ---: | ---: | ---: | --- |"]
            for g in r['regions']:
                per = f"{g['per']:+.2f}" if 'per' in g else ''
                diff = f"{g['diff']:+d}" if g.get('diff') is not None else ''
                out.append(f"| {g['name']} | {g.get('sim', '')} | {g.get('hw', '')} | {diff} | {per} | "
                           f"{' '.join(g.get('notes', []))} |")
    for r in data['results']:
        rows = r.get('diff_rows') or []
        if rows:
            out += ["", f"### Differences in {r['title']}", ""]
            for row in rows[:30]:
                out.append(f"- step {row['n']}, 0x{row['addr']:04X} `{row['text']}` "
                           f"(line {row['line']}): {', '.join(row['diffs'])}")
                for k in ('sim', 'hw'):
                    regs = row[k]['regs']
                    out.append(f"  - {'picosim' if k == 'sim' else 'RP2040 '}: " +
                               ' '.join(f"R{i}={_hex(regs[i])}" for i in range(16)) +
                               f" {fmt_flags(row[k]['flags'])}")
    return '\n'.join(out) + '\n'
