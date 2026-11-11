"""
What every campaign shares: the board connection, the safety settings, its
runs (started, resumed or reused) and its status.

A campaign module (picosim/discover/cases/*.py) has
    NAME, TITLE, AGENT_BASED (True if it runs cases through the agent)
    run(ctx)            do the campaign and write its facts
    dry_cases(ctx)      every case list it would run, for --dry-run's safety check

Each stage that runs cases is one runner.Run, named <campaign>-<stage>.  A
finished run of the same cases on the same chip is reused; an unfinished
one is resumed; --fresh starts again.  --repeat runs a stage a second time
with its batches in another order and compares the two.
"""

import json
import os
import time

from . import formats as F
from . import runner as R
from .runner import make_case as R_make  # noqa: F401  (for the campaign modules)
from . import facts
from .build import cache_dir

STATUS_NAMES = ('not run', 'partial', 'done')


class Context:
    def __init__(self, target, opts, log=print, ask=None, stop_flag=None, progress=None):
        self.t = target
        self.opts = opts
        self.kind = target.kind if target else getattr(opts, 'target', 'openocd')
        self.pins = frozenset(getattr(opts, 'pins', ()) or ())
        self.log = log
        self.ask = ask
        self.stop_flag = stop_flag
        self.progress = progress
        self.label = getattr(opts, 'chip_label', None) or 'chip1'
        self.limit = getattr(opts, 'limit', None)
        self.offline = bool(getattr(target, 'offline', False))

    @property
    def root(self):
        return cache_dir(_sub(self.kind, self.label))

    def stage(self, campaign, stage, cases, layout='hi', **kw):
        """Run (or resume, or reuse) one stage.  Returns (records, state)."""
        name = f"{campaign}-{stage}"
        cases = list(cases)
        if self.limit:
            cases = cases[:self.limit]
        if not cases:
            return [], None
        base = os.path.join(self.root, name)
        if self.offline:
            return self._stage_offline(name, base, cases)
        fresh = getattr(self.opts, 'fresh', False)
        existing = sorted(os.listdir(base)) if os.path.isdir(base) else []
        run = None
        for rid in reversed([e for e in existing if not e.endswith('-repeat')]):
            path = os.path.join(base, rid)
            if fresh or not os.path.exists(os.path.join(path, 'state.json')):
                continue
            try:
                run = R.Run.resume(self.t, path, cases, log=self.log, stop_flag=self.stop_flag,
                                   progress=self.progress, ask=self.ask, on_prepare=kw.get('on_prepare'))
                break
            except R.RunError as e:
                self.log(f"not resuming {rid}: {e}")
        if run is None:
            run = R.Run.start(self.t, name, cases, layout=layout, root=self.root, pins=self.pins, log=self.log,
                              stop_flag=self.stop_flag, progress=self.progress, ask=self.ask, **kw)
        if not run.finished:
            try:
                run.go()
            finally:
                run.close()
                self._record_raw(run.path)          # even a part-run's batches are measurements
        else:
            run.close()
        if not run.finished:
            raise R.RunError(f"{name} stopped before the end ({len(run.state['done'])} of "
                             f"{run.state['n_batches']} batches); run the campaign again to resume")
        if getattr(self.opts, 'repeat', False):
            self._repeat(name, cases, run, layout, kw)
        self._record_raw(run.path)
        return R.load(run.path)

    def _record_raw(self, path):
        """Every run's raw files go in raw_index.json with their SHA-256, and
        (chip runs) get their second copy: decision D4.  Until 2026-11-11 only
        B and D did this, so A's, C's, E's and G's agent runs on the chip had
        no index entry and no backup."""
        if not os.path.exists(os.path.join(path, 'state.json')):
            return
        try:
            facts.record_raw(path, self.kind)
        except Exception as e:                       # never lose a run's results over its backup
            self.log(f"could not record the raw files of {os.path.relpath(path, self.root)}: {e}")

    def _repeat(self, name, cases, run, layout, kw):
        rid = run.state['run_id'] + '-repeat'
        path = os.path.join(self.root, name, rid)
        if os.path.exists(os.path.join(path, 'state.json')):
            again = R.Run.resume(self.t, path, cases, log=self.log, stop_flag=self.stop_flag, ask=self.ask,
                                 on_prepare=kw.get('on_prepare'))
        else:
            again = R.Run.start(self.t, name, cases, layout=layout, root=self.root, pins=self.pins, log=self.log,
                                run_id=rid, repeat_of=run.state['run_id'], seed=1, stop_flag=self.stop_flag,
                                ask=self.ask, **kw)
        try:
            again.go()
        finally:
            again.close()
            self._record_raw(again.path)
        run.state['repeat'] = _repeat_verdict(run.path, again.path, run.state)
        with open(os.path.join(run.path, 'state.json'), 'w') as f:
            json.dump(run.state, f, indent=1)
        n = run.state['repeat']['disagree']
        self.log(f"{name}: the repeat run {'agrees' if not n else f'disagrees on {n} cases'}")

    def _stage_offline(self, name, base, cases):
        """--offline: the newest FINISHED run of exactly these cases, read
        from disk; the board is never touched.  Its repeat, if one finished,
        is compared again with today's code.  The raw files are not changed."""
        existing = sorted(os.listdir(base)) if os.path.isdir(base) else []
        why = []
        for rid in reversed([e for e in existing if not e.endswith('-repeat')]):
            path = os.path.join(base, rid)
            if not os.path.exists(os.path.join(path, 'state.json')):
                continue
            try:
                state, _ = R.same_cases(path, cases)
            except R.RunError as e:
                why.append(f"{rid}: {e}")
                continue
            if len(state['done']) != state['n_batches']:
                why.append(f"{rid}: unfinished ({len(state['done'])} of {state['n_batches']} batches)")
                continue
            recs, state = R.load(path)
            again = os.path.join(base, rid + '-repeat')
            if os.path.exists(os.path.join(again, 'state.json')):
                st2 = json.load(open(os.path.join(again, 'state.json')))
                if len(st2['done']) == st2['n_batches']:
                    state['repeat'] = _repeat_verdict(path, again, state)
            self.log(f"{name}: using the finished run {rid} ({len(recs)} cases, read from disk)")
            return recs, state
        raise R.RunError(f"{name}: no finished run of these cases on disk, so this stage needs the board"
                         + (f" ({'; '.join(why)})" if why else ''))

    def provenance(self, state, **kw):
        runs = 2 if state and state.get('repeat') else 1
        agreed = (state['repeat']['disagree'] == 0) if state and state.get('repeat') else None
        p = facts.provenance(state or self.bare_state(), runs=runs, agreed=agreed,
                             openocd=self.t.openocd_version() if self.t else None,
                             swd_khz=getattr(self.opts, 'speed', None), extra=kw)
        p['chip_label'] = self.label
        if self.offline:
            p['rebuilt_offline'] = {'date': time.strftime('%Y-%m-%d %H:%M:%S'), 'picosim': R.picosim_commit(),
                                    'note': 'measured on the date above; compared with picosim again from '
                                            'the saved raw records, with no board'}
        return p

    def bare_state(self):
        """Provenance for campaigns that only use the debugger."""
        return {'chip': R.chip_identity(self.t), 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                'picosim': R.picosim_commit(), 'agent': None, 'campaign': None, 'run_id': None,
                'target': self.kind}

    def write_facts(self, name, kind, prov, fs, **tables):
        if self.label != 'chip1':
            name = os.path.join('chips', self.label, name)
        if self.offline:
            # a rebuild keeps the history notes of the document it replaces
            # (e.g. 'rescored'), which only that document knows
            old = facts.read(name, self.kind) or {}
            for k, v in (old.get('provenance') or {}).items():
                prov.setdefault(k, v)
        path = facts.write(name, kind, prov, fs, self.kind, **tables)
        self.log(f"facts: {path}")
        return path

    KEPT = ('facts', 'files', 'differences', 'picosim_differences', 'compared_with', 'flashed')

    def status(self, campaign, state, **fields):
        d = os.path.join(self.root, campaign)
        os.makedirs(d, exist_ok=True)
        if state == 'partial' and 'facts' not in fields:
            # a run that has not written facts yet keeps pointing at the last
            # ones, which are still in hwfacts/ (2026-11-10: G was re-run, stopped
            # at its first step, and the window lost G's three facts files)
            prev = read_status(campaign, self.kind, self.label)
            for k in self.KEPT:
                if k in prev:
                    fields.setdefault(k, prev[k])
        s = {'campaign': campaign, 'state': state, 'updated': time.strftime('%Y-%m-%d %H:%M:%S'), **fields}
        with open(os.path.join(d, 'status.json'), 'w') as f:
            json.dump(s, f, indent=1)
        return s


def _repeat_verdict(path, again, state):
    """A run against its repeat (the same cases in another batch order).
    Timing runs compare the cycles too, and leave out MSP, PSP and the
    frame address, which their scenarios set per case."""
    if state.get('timing'):
        fields = [f for f in R.BEHAVIOUR_FIELDS + ['systick'] if f not in ('msp', 'psp', 'frame')]
    else:
        fields = None
    unset = []
    diff = R.compare_runs(path, again, fields, unset=unset)
    return {'run_id': os.path.basename(again), 'disagree': len(diff), 'examples': diff[:20],
            'differ_only_in_unset_memory': len(unset)}


class OfflineTarget:
    """--offline: stands where the board would, for campaigns rebuilt from
    their saved raw records.  Anything that would touch the chip raises."""
    offline = True
    kind = 'openocd'
    description = 'no board (--offline): rebuilding facts from the saved raw records'

    def __init__(self, openocd=None):
        self._openocd = openocd

    def openocd_version(self):
        return self._openocd

    def __getattr__(self, name):
        raise R.RunError(f"--offline: this needs the board ({name})")

    def close(self):
        pass


def recorded_openocd():
    """The OpenOCD version the chip's facts were measured with (the
    identity campaign's provenance), for --offline provenance."""
    doc = facts.read('identity.json')
    return (doc or {}).get('provenance', {}).get('openocd')


def _sub(kind, label):
    if kind == 'standin':
        return 'standin' if label == 'chip1' else f'standin-{label}'
    return label


def read_status(campaign, kind='openocd', label='chip1'):
    p = os.path.join(cache_dir(_sub(kind, label)), campaign, 'status.json')
    if not os.path.exists(p):
        return {'campaign': campaign, 'state': 'not run'}
    with open(p) as f:
        return json.load(f)


# ── helpers for building cases ─────────────────────────────────────────────

def h(*hws):
    return b''.join((x & 0xFFFF).to_bytes(2, 'little') for x in hws)


def release(t, mask):
    """Take blocks out of reset (RESETS CLR alias) and wait for RESET_DONE.
    True if every block came out; see release_ready()."""
    return release_ready(t, mask) == mask


def release_ready(t, mask):
    """Take blocks out of reset and wait for RESET_DONE; returns the bits of
    `mask` that came out.  A block that does not come out (its clock is not
    running: after `reset halt`, clk_peri and clk_adc are off, so UART, SPI
    and the ADC never finish) is put straight back into reset.  On the
    RP2040 an access to a block left half-released stalls the bus, and the
    debug port with it, until the board is power-cycled; a block held in
    reset just reads as 0."""
    from .safety import check_write
    check_write(0x4000F000, 4, mask, why='release from reset')
    t.rsp.write32(0x4000F000, mask)
    done = 0
    for _ in range(50):
        done = t.rsp.read32(0x4000C008) & mask
        if done == mask:
            return mask
    stuck = mask & ~done
    check_write(0x4000E000, 4, stuck, why='back into reset (it did not come out)')
    t.rsp.write32(0x4000E000, stuck)
    return done


IO_PADS = (1 << 5) | (1 << 8)


def ldr_probe(addr, width, layout='hi', write=None, id=0, **kw):
    """A core access to `addr`: a load (r0 <- [r1]) at 8, 16 or 32 bits, or
    a store of `write` then a load back (r2) if write is not None."""
    load = {1: 0x7808, 2: 0x8808, 4: 0x6808}[width]          # ldrb/ldrh/ldr r0,[r1]
    if write is None:
        return R.make_case(id, h(load), kind='probe-read', regs=[0, addr], layout=layout,
                           reads=[addr], **kw)
    store = {1: 0x7008, 2: 0x8008, 4: 0x6008}[width]         # strb/strh/str r0,[r1]
    load2 = {1: 0x780A, 2: 0x880A, 4: 0x680A}[width]         # ldrb/ldrh/ldr r2,[r1]
    return R.make_case(id, h(store, load2), kind='probe-write', regs=[write, addr], layout=layout,
                       writes=[(addr, width, write)], **kw)


TIMING_HEAD, TIMING_TAIL = 0x683E, 0x683D                       # ldr r6,[r7]; ldr r5,[r7]


def timing_case(id, body, regs=None, layout='hi', slot=None, **kw):
    """body (bytes) between two SysTick reads; it must leave R5-R7 alone."""
    regs = list(regs or [0] * 7)
    regs += [0] * (8 - len(regs))
    regs[7] = F.PPB['SYST_CVR'] if 'SYST_CVR' in F.PPB else 0xE000E018
    return R.make_case(id, h(TIMING_HEAD) + body + h(TIMING_TAIL), kind='timing', regs=regs,
                       layout=layout, slot=slot, **kw)
