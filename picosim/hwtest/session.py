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

Level 5 discovery (picosim/discover; the Testing window's "Hardware facts"
group) runs its campaigns on the same connection, in the same worker:
  discover_start     a campaign begins: campaign, fresh
  discover_log       a line of its log: campaign, text
  discover_progress  a stage's batches: campaign, done, total, step
  discover_ask       the campaign needs the person (BOOTSEL, a replug):
                     message; it waits for hw_discover_answer (or Stop)
  discover_done      it ended: campaign, ok, stopped, message, restore
                     (True: the flash needs restoring), row (its new status)
"""

import json
import os
import queue
import threading
import time

from . import provenance
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
        self.discovering = ''        # the campaign running, if one is
        self._answer = threading.Event()

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
        self._answer.set()            # a campaign waiting for the person gives up

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
        ev, _ = provenance.target_info(self.target)
        # what the window labels the run with until its summary (and provenance) comes
        labels = {'evidence': ev, 'side': provenance.SIDE[ev],
                  'evidence_label': provenance.EVIDENCE[ev], 'address_map': provenance.address_map()}
        self.q.put({'type': 'hw', 'event': 'start', 'path': path, 'kind': kind,
                    'title': t.get('title') or info['title'], **labels})

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
                                    'title': t.get('title') or info['title'], 'retry': True, **labels})
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

    # ── Level 5 discovery ───────────────────────────────────────────────────

    def _discover_ready(self):
        if self.thread is not None:
            self.thread.join(2.0)
        if self.busy():
            return "Testing is busy; stop the run first"
        if not self.target:
            return "connect to a chip first"
        return ''

    def discover_run(self, campaign, fresh=False, extra=(), confirmed=False):
        """Run (fresh: from the start) or resume one campaign on this
        connection, through the command line's own code.  Campaign F needs
        `confirmed` (the window's confirmation) and a checked flash backup.
        Returns why it can't start, or ''."""
        from ..discover.cases import ORDER
        if campaign not in ORDER:
            return f"there is no campaign called {campaign!r}"
        err = self._discover_ready()
        if err:
            return err
        if campaign == 'flashed':
            from ..discover.overview import flash_status
            fs = flash_status()
            if not confirmed:
                return "Campaign F writes the board's flash: confirm its steps first"
            if not fs['ready']:
                return fs['message']
        argv = [campaign, '--target', self.target.kind, '--speed', str(int(self.target.args[3] or 5000))]
        argv += (['--fresh'] if fresh else []) + [str(x) for x in extra]
        if campaign == 'flashed':
            argv.append('--yes')          # the window asked already

        def work():
            self._discover(campaign, argv, fresh)
            self.status(busy=False)
        self._start(work)
        return ''

    def _discover(self, campaign, argv, fresh):
        from ..discover.__main__ import parser, run_campaign
        from ..discover.campaign import read_status
        from ..discover.flashguard import FlashGuardError
        from ..discover.overview import row
        from ..discover.runner import RunError
        kind = self.target.kind
        self.discovering = campaign
        self.q.put({'type': 'hw', 'event': 'discover_start', 'campaign': campaign, 'fresh': fresh})

        def log(text):
            self.q.put({'type': 'hw', 'event': 'discover_log', 'campaign': campaign, 'text': str(text)})
            if self.target:
                self.target.log(f"discover {campaign}: {text}")

        def progress(done, total):
            try:
                step = read_status(campaign, 'standin' if kind == 'standin' else 'openocd').get('step', '')
            except (OSError, ValueError):
                step = ''
            self.q.put({'type': 'hw', 'event': 'discover_progress', 'campaign': campaign,
                        'done': done, 'total': total, 'step': step})
        ok, msg, restore = False, '', False
        try:
            a = parser().parse_args(argv)
            path = run_campaign(a, self.target, log=log, progress=progress, stop_flag=self.stop_flag,
                                ask=self._ask)
            ok = True
            msg = f"facts: {path}" if path else ''
        except SystemExit as e:              # argparse refused an option
            msg = f"bad options for the campaign ({e})"
        except FlashGuardError as e:
            msg, restore = str(e), True
        except RunError as e:
            msg = str(e)
        except (TargetError, RSPError) as e:
            msg = str(e)
            if _lost(e):
                msg = "The connection to the chip was lost. " + msg + "\n\n" + self.target.explain()
                self.error = f"Connection lost. Full log: {self.target.log_path}"
                self._close()
            else:
                msg += f"\n\nFull log: {self.target.log_path}"
        except Exception as e:               # noqa: BLE001 - report it; keep the session alive
            msg = f"internal error: {e!r}"
        self.discovering = ''
        self.q.put({'type': 'hw', 'event': 'discover_done', 'campaign': campaign, 'ok': ok,
                    'stopped': self.stop_flag.is_set(), 'message': msg, 'restore': restore,
                    'row': row(campaign, kind)})

    def discover_flash(self, action):
        """Campaign F's flash backup ('backup') or restore ('restore'), with
        flashguard, on this connection."""
        err = self._discover_ready()
        if err:
            return err
        if action not in ('backup', 'restore'):
            return f"unknown flash action {action!r}"

        def work():
            from ..discover import flashguard as G
            name = 'flashed'
            self.discovering = name
            self.q.put({'type': 'hw', 'event': 'discover_start', 'campaign': name, 'flash': action})

            def log(text):
                self.q.put({'type': 'hw', 'event': 'discover_log', 'campaign': name, 'text': str(text)})
            ok, msg = False, ''
            try:
                g = G.FlashGuard.from_target(self.target, ask=self._ask, log=log)
                if action == 'backup':
                    b = g.backup()
                    msg = f"Backed up and checked: {b.bin_path} (copy: {b.cache_bin_path})"
                else:
                    r = g.restore()
                    msg = (f"Restored. Backup SHA-256 {r.backup_sha256}; read back {r.readback_sha256}. "
                           f"Boot check: {'ok' if r.boot_ok else 'NOT confirmed'} ({r.boot_detail})")
                ok = True
            except G.FlashGuardError as e:
                msg = str(e)
            except Exception as e:               # noqa: BLE001
                msg = f"internal error: {e!r}"
            self.discovering = ''
            from ..discover.overview import row
            self.q.put({'type': 'hw', 'event': 'discover_done', 'campaign': name, 'ok': ok,
                        'stopped': self.stop_flag.is_set(), 'message': msg, 'flash': action,
                        'restore': False, 'row': row(name, self.target.kind if self.target else 'openocd')})
            self.status(busy=False)
        self._start(work)
        return ''

    def _ask(self, message):
        """A campaign needs the person: ask in the window, wait for the answer."""
        self._answer.clear()
        self.q.put({'type': 'hw', 'event': 'discover_ask', 'campaign': self.discovering, 'message': str(message)})
        while not self._answer.wait(0.2):
            pass
        if self.stop_flag.is_set():
            raise RuntimeError("stopped while waiting for you")
        return ''

    def answer(self):
        self._answer.set()

    def drain(self, limit=200):
        out = []
        try:
            while len(out) < limit:
                out.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return out

    # ── the report ──────────────────────────────────────────────────────────

    def export(self, path, local_paths=False):
        """Write the latest results: a Markdown report and the full data as JSON
        next to it.  Returns the paths written.

        Each result carries the provenance it was stamped with when it ran
        (provenance.py), so what is connected now does not change it.
        Source files are named by their path in the picosim checkout or
        their file name, with their SHA-256; local_paths adds each one's
        full path on this computer (leave it off to share a report)."""
        if not self.results:
            raise ValueError("no results yet: run a test first")
        base = os.path.splitext(path)[0]
        md, js = base + '.md', base + '.json'
        os.makedirs(os.path.dirname(os.path.abspath(md)), exist_ok=True)
        data = report_data([self.results[p] for p in self.order], local_paths)
        with open(js, 'w') as f:
            json.dump(data, f, indent=1, default=str)
        with open(md, 'w') as f:
            f.write(report_markdown(data))
        return [md, js]


def shareable(result, local_paths=False):
    """A copy of a result for a report: its provenance (or 'unknown' if it
    has none), and its source named without the local path unless asked."""
    r = dict(result)
    prov = r.get('provenance') or provenance.unknown()
    r['provenance'] = prov
    path = r.get('source', '')
    name = prov.get('source', {}).get('name')
    r['source'] = (name if name and name != 'unknown' else provenance.source_name(path)) or 'unknown'
    if local_paths and path:
        r['source_path'] = os.path.abspath(path)
    return r


def report_data(results, local_paths=False):
    rs = [shareable(r, local_paths) for r in results]
    targets = []
    for r in rs:
        t = r['provenance'].get('target', {}).get('description', 'unknown')
        if t not in targets:
            targets.append(t)
    return {'report': 'picosim Testing', 'format': 2,
            'exported_at': provenance.now(),
            'picosim': dict(provenance.picosim_build()),
            'evidence': sorted({r['provenance'].get('evidence', 'unknown') for r in rs}),
            # the targets the results ran on (each result says which is its own)
            'targets': targets,
            'target': targets[0] if len(targets) == 1 else 'several: see each result',
            'local_paths': bool(local_paths),
            'results': rs}


def _hex(v):
    return f"0x{v & 0xFFFFFFFF:08X}"


def _missing(v, what='not recorded'):
    return what if v in (None, '', [], {}) else v


def _build_text(b):
    b = b or {}
    commit = b.get('commit')
    return (f"picosim {_missing(b.get('version'), 'version unknown')}, commit "
            f"{commit[:12] if commit else 'unknown'}"
            + {True: " (with uncommitted changes)", False: '', None: " (uncommitted changes: unknown)"}
            .get(b.get('dirty'), ''))


def _provenance_lines(r):
    p = r['provenance']
    t = p.get('target', {})
    src = p.get('source', {})
    out = [f"- Evidence: **{p.get('evidence_label', 'unknown')}**",
           f"- Ran on: {t.get('description', 'unknown')}: {t.get('executed_on', 'unknown')}",
           f"- Run: {p.get('run_id', 'unknown')} at {p.get('run_at', 'unknown')}",
           f"- Source: `{r['source']}` (SHA-256 {_missing(src.get('sha256'), 'unknown')})"
           + (" with settings changed in the GUI (assembled file SHA-256 "
              f"{src.get('assembled_sha256')})" if src.get('settings_changed') else ''),
           f"- Image: {_missing(p.get('image') or r.get('image'), 'unknown')} (SHA-1 of what was written to the target, first 12 hex digits)",
           f"- Build: {_build_text(p.get('picosim'))}"]
    if r.get('source_path'):
        out.append(f"- Local path: `{r['source_path']}`")
    cal = p.get('calibration')
    if r['kind'] == 'bench':
        if cal:
            ov = ', '.join(f"{v} (bench_stop at offset {k} in its word)" for k, v in cal.get('overhead', {}).items())
            out.append(f"- Calibration: {ov} cycles of measuring overhead, Table 81 predicts "
                       f"{cal.get('predicted')}; {cal.get('status', 'status unknown')}; taken "
                       f"{cal.get('measured_at', 'at an unknown time')}")
        else:
            out.append("- Calibration: unknown")
        for k, v in (p.get('conditions') or {}).items():
            out.append(f"- {k.replace('_', ' ').capitalize()}: {v}")
    if p.get('compared'):
        out.append(f"- Compared: {p['compared']}")
    model = p.get('model') or {}
    if model.get('assumptions'):
        out.append("- Model assumptions: " + ' '.join(model['assumptions']))
    return out


def report_markdown(data):
    results = data['results']
    sides = []
    for r in results:
        sd = provenance.side(r.get('provenance'))
        if sd not in sides:
            sides.append(sd)
    title = ' and '.join(sides) if sides else 'target'
    out = [f"# picosim vs. {title}: test report", "",
           f"- Exported: {data.get('exported_at', 'unknown')}",
           f"- Report made by: {_build_text(data.get('picosim'))}",
           f"- Targets: {', '.join(data.get('targets') or ['unknown'])} (each result below says which it ran on)"]
    if 'standin' in data.get('evidence', []):
        out.append("- The stand-in is an emulator, not the real chip: its results are not measurements of an RP2040.")
    out.append("")
    out.append("| Test | Kind | Ran on | Result | Detail |")
    out.append("| --- | --- | --- | --- | --- |")
    for r in results:
        sd = provenance.side(r.get('provenance'))
        if r['kind'] == 'trace':
            c = r['counts']
            detail = (f"{r['steps']} steps: {c['same']} identical, {c['call']} OS calls, "
                      f"{c['io']} I/O differences, {c['diff']} differences; memory "
                      f"{r['memory']['different_words']} different words")
            result = r.get('headline') or r['verdict']
        else:
            regs = r['regions']
            detail = f"{sum(1 for g in regs if g.get('diff') == 0)} of {len(regs)} regions exact"
            result = r['verdict']
        out.append(f"| {r['title']} | {r['kind']} | {sd} | {result} | {detail} |")
    for r in results:
        sd = provenance.side(r.get('provenance'))
        out += ["", f"## {r['title']}", ""]
        if r['kind'] == 'trace' and r.get('headline'):
            out += [f"**{r['headline']}**", ""]
        out += _provenance_lines(r)
        out += ["", r.get('note', '')]
        if r['kind'] == 'trace':
            if r.get('stop') or r.get('normalization'):
                out += ["", f"- Stop reason: {(r.get('stop') or {}).get('text', 'unknown')}",
                        f"- Rows compared: {r.get('rows_compared', r.get('steps', 'unknown'))}",
                        f"- Normalized: {(r.get('normalization') or {}).get('text', 'unknown')}"]
            amap = (r['provenance'].get('address_map') or {})
            out += ["", "Addresses: " + provenance.address_text(sd if sd != 'target' else 'target')
                    if amap else "Addresses: unknown"]
            if r['output']['sim'] or r['output']['hw']:
                out += ["", f"Output: picosim {r['output']['sim']!r}, {sd} {r['output']['hw']!r}"]
            if r['memory']['different_words']:
                out += ["", f"Memory words that differ (picosim address: picosim / {sd}):", ""]
                for d in r['memory']['first'][:20]:
                    out.append(f"- 0x{d['addr']:08X}: {_hex(d['sim'])} / {_hex(d['hw'])}")
            if r['gpio'].get('diffs'):
                out += ["", "GPIO registers that differ: " + ', '.join(r['gpio']['diffs'])]
            if r['gpio'].get('info'):
                out += ["", "Registers the program did not write whose values differ (not counted): " +
                        ', '.join(r['gpio']['info'][:12]) + (' …' if len(r['gpio']['info']) > 12 else '')]
        else:
            tl = r.get('timing') or provenance.timing_labels(r['provenance'], r.get('stretches'),
                                                              r.get('calibration'))
            out += ["", "Timing sources and scope:", "",
                    f"- {tl['picosim']}", f"- {tl['target']}", f"- Scope: {tl['scope']}",
                    f"- Unit: {tl['unit']}"]
            out += ["", f"| Region | picosim cycles (Table 81 model) | {sd} cycles | Difference | "
                        f"Per instruction | Code (picosim → {sd}) | Notes |",
                    "| --- | ---: | ---: | ---: | ---: | --- | --- |"]
            for g in r['regions']:
                per = f"{g['per']:+.2f}" if 'per' in g else ''
                diff = f"{g['diff']:+d}" if g.get('diff') is not None else ''
                site, stop = g.get('site'), g.get('stop_site')
                addr = ''
                if isinstance(site, int) and isinstance(stop, int):
                    addr = (f"0x{site:08X}–0x{stop:08X} → 0x{img_to_hw(site):08X}–0x{img_to_hw(stop):08X}"
                            f" ({'last instruction' if r.get('stretches') else 'bl bench_stop'} "
                            f"{'word' if stop % 4 == 0 else 'halfword'}-aligned)")
                out.append(f"| {g['name']} | {g.get('sim', '')} | {g.get('hw', '')} | {diff} | {per} | "
                           f"{addr} | {' '.join(g.get('notes', []))} |")
    for r in results:
        rows = r.get('diff_rows') or []
        sd = provenance.side(r.get('provenance'))
        if rows:
            out += ["", f"### Differences in {r['title']}", ""]
            for row in rows[:30]:
                out.append(f"- step {row['n']}, 0x{row['addr']:08X} (0x{img_to_hw(row['addr']):08X} on the "
                           f"{sd}) `{row['text']}` (line {row['line']}): {', '.join(row['diffs'])}")
                for k in ('sim', 'hw'):
                    if not row.get(k):
                        continue
                    regs = row[k]['regs']
                    raw = (f" (raw PC after the step on the {sd}: 0x{row[k]['raw_pc']:08X})"
                           if k == 'hw' and row[k].get('raw_pc') is not None else '')
                    out.append(f"  - {'picosim' if k == 'sim' else sd}: " +
                               ' '.join(f"R{i}={_hex(regs[i])}" for i in range(16)) +
                               f" {fmt_flags(row[k]['flags'])}{raw}")
    return '\n'.join(out) + '\n'


def img_to_hw(addr):
    from .image import to_hw
    return to_hw(addr)
