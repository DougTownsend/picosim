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
            run.go()
        run.close()
        if not run.finished:
            raise R.RunError(f"{name} stopped before the end ({len(run.state['done'])} of "
                             f"{run.state['n_batches']} batches); run the campaign again to resume")
        if getattr(self.opts, 'repeat', False):
            self._repeat(name, cases, run, layout, kw)
        return R.load(run.path)

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
        again.go()
        again.close()
        fields = R.BEHAVIOUR_FIELDS if not run.state.get('timing') else R.BEHAVIOUR_FIELDS + ['systick']
        diff = R.compare_runs(run.path, again.path, [f for f in fields if f not in ('msp', 'psp', 'frame')]
                              if run.state.get('timing') else None)
        run.state['repeat'] = {'run_id': rid, 'disagree': len(diff), 'examples': diff[:20]}
        with open(os.path.join(run.path, 'state.json'), 'w') as f:
            json.dump(run.state, f, indent=1)
        self.log(f"{name}: the repeat run {'agrees' if not diff else f'disagrees on {len(diff)} cases'}")

    def provenance(self, state, **kw):
        runs = 2 if state and state.get('repeat') else 1
        agreed = (state['repeat']['disagree'] == 0) if state and state.get('repeat') else None
        p = facts.provenance(state or self.bare_state(), runs=runs, agreed=agreed,
                             openocd=self.t.openocd_version() if self.t else None,
                             swd_khz=getattr(self.opts, 'speed', None), extra=kw)
        p['chip_label'] = self.label
        return p

    def bare_state(self):
        """Provenance for campaigns that only use the debugger."""
        return {'chip': R.chip_identity(self.t), 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
                'picosim': R.picosim_commit(), 'agent': None, 'campaign': None, 'run_id': None,
                'target': self.kind}

    def write_facts(self, name, kind, prov, fs, **tables):
        if self.label != 'chip1':
            name = os.path.join('chips', self.label, name)
        path = facts.write(name, kind, prov, fs, self.kind, **tables)
        self.log(f"facts: {path}")
        return path

    def status(self, campaign, state, **fields):
        d = os.path.join(self.root, campaign)
        os.makedirs(d, exist_ok=True)
        s = {'campaign': campaign, 'state': state, 'updated': time.strftime('%Y-%m-%d %H:%M:%S'), **fields}
        with open(os.path.join(d, 'status.json'), 'w') as f:
            json.dump(s, f, indent=1)
        return s


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
    """Take blocks out of reset (RESETS CLR alias) and wait for RESET_DONE."""
    from .safety import check_write
    check_write(0x4000F000, 4, mask, why='release from reset')
    t.rsp.write32(0x4000F000, mask)
    for _ in range(50):
        if t.rsp.read32(0x4000C008) & mask == mask:
            return True
    return False


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
