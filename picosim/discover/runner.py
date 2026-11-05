"""
Run cases on the chip through the discovery agent, in batches, with a
checkpoint after every batch.

    run = Run.start(target, 'encodings-B1', cases)        # or Run.resume(target, path)
    run.go()                                              # every batch not done yet
    for case, result in run.records(): ...

A batch is one upload: the header, the case records and their code, at the
layout's BATCH address.  The agent runs it and stops on BKPT; the runner
reads back exactly the results produced, checks the agent's own checksum,
the sled and the guard, and saves

    <run>/batch-NNNN.in     the bytes uploaded (cases + code: enough to
                            replay every case without the generator)
    <run>/batch-NNNN.bin    one 128-byte result per case, in case order
    <run>/batch-NNNN.json   ids, footer, timing, and what went wrong
    <run>/state.json        the run: chip, agent, batches done
    <run>/log.txt

A case the agent could not finish gets a result whose ipsr is one of the
MARK_* values (real exception numbers are below 48).  When something goes
wrong the runner climbs a ladder: halt and read DHCSR (a lockup is recorded
for the case running), restart the agent after that case, `reset halt` and
reload everything, reconnect to OpenOCD, and finally ask for the board to
be power-cycled.  A case that fails twice is recorded and skipped.
"""

import hashlib
import json
import os
import random
import struct
import subprocess
import time

from . import formats as F
from . import safety
from .build import agent as build_agent, cache_dir

MARK_LOCKUP = 0x100        # the core locked up running the case
MARK_HANG = 0x101          # still running at the timeout, not locked up
MARK_SKIPPED = 0x102       # failed twice: not run again
MARK_HALTED = 0x103        # the case stopped the core (a BKPT, a debug event)
MARK_CORRUPTS = 0x104      # running it alone damaged the agent, sled or guard
MARK_NAMES = {MARK_LOCKUP: 'lockup', MARK_HANG: 'hang', MARK_SKIPPED: 'skipped',
              MARK_HALTED: 'halted', MARK_CORRUPTS: 'corrupts the agent'}
RESULT_CAP = (F.SHARED['RESULTS_END'] - F.SHARED['RESULTS']) // F.RESULT_SIZE
QUIET_START = 0.25         # s: a timed batch runs this long (+ a little a case) before the debugger polls
QUIET_PER_CASE = 0.002
QUIET_MAX = 4.0


class RunError(Exception):
    pass


def make_case(id, code, kind='behaviour', slot=None, regs=None, lr=None, psp=None, nzcv=0,
              primask=0, control=0, seed=0, layout='hi', **meta):
    """A case as the runner takes it.  regs: R0-R12 (missing ones 0)."""
    lay = F.LAYOUTS[layout]
    regs = list(regs or [])
    regs += [0] * (13 - len(regs))
    c = {'id': id, 'kind': kind, 'code': code, 'slot': lay['SLOT'] if slot is None else slot,
         'lr': (lay['SLED'] + 0x100) | 1 if lr is None else lr,
         'psp': lay['PSP_DEFAULT'] if psp is None else psp,
         'xpsr': (nzcv & 0xF) << 28, 'primask': primask, 'control': control, 'seed': seed}
    for i, v in enumerate(regs[:13]):
        c[f'r{i}'] = v & 0xFFFFFFFF
    c.update(meta)
    return c


def _code_bytes(case, addrs=None):
    code = case['code']
    return code(addrs or [0] * len(case.get('blobs', ()))) if callable(code) else code


def ending(case_rec, res, lay, sites=None):
    """How a case ended, from where its exception came: (kind, detail).
    kind: 'fault' (at the slot), 'fell-through', 'sled' (a branch landed at
    detail), 'svc', 'watchdog', 'exception' (detail = number), 'elsewhere'
    (a HardFault at detail outside the slot and sleds), or a MARK name."""
    ipsr = res['ipsr']
    if ipsr in MARK_NAMES:
        return MARK_NAMES[ipsr], None
    pc, slot, n = res['f_pc'], case_rec['slot'], case_rec['code_len']
    if ipsr == 11:
        return 'svc', pc
    if ipsr == 15 and case_rec.get('watchdog'):
        return 'watchdog', pc
    if ipsr != 3:
        return 'exception', ipsr
    if pc == slot:
        return 'fault', pc
    if pc == slot + n:
        return 'fell-through', pc
    sites = sites or [(lay['SLED'], lay['SLED'] + lay['SLED_SIZE'])]
    if any(lo <= pc < hi for lo, hi in sites):
        return 'sled', pc
    return 'elsewhere', pc


# ── batches ─────────────────────────────────────────────────────────────────

def _align(n, a=4):
    return (n + a - 1) // a * a


class Batch:
    """Cases packed for one upload."""

    def __init__(self, cases, lay, flags, scratch_words, sled, wd_reload, start=0):
        self.cases = cases
        base = lay['BATCH']
        n = len(cases)
        a = base + F.HEADER_SIZE + n * F.CASE_SIZE
        records = []
        blobs = []
        for c in cases:
            addrs = []
            for align, data in c.get('blobs', ()):
                a = _align(a, align)
                addrs.append(a)
                blobs.append((a, data))
                a += _align(len(data))
            code = _code_bytes(c, addrs)
            if len(code) % 2:
                raise RunError(f"case {c['id']}: its code is not a whole number of halfwords")
            blobs.append((a, code))
            rec = {**c, 'code': a, 'code_len': len(code), 'kind': F.KINDS.get(c['kind'], 0)}
            records.append(rec)
            a += _align(len(code))
        if a > lay['BATCH_END']:
            raise RunError(f"a batch of {n} cases needs {a - base} bytes; the batch area has "
                           f"{lay['BATCH_END'] - base}")
        self.records = records
        hdr = {'magic': F.MAGIC, 'version': F.FORMAT_VERSION, 'n_cases': n, 'start': start,
               'cases': base + F.HEADER_SIZE, 'results': lay['RESULTS'], 'scratch': lay['SCRATCH'],
               'scratch_words': scratch_words, 'flags': flags, 'guard': lay['GUARD'],
               'guard_words': lay['GUARD_SIZE'] // 4, 'sled': sled[0], 'sled_words': (sled[1] - sled[0]) // 4,
               'sled_fill': F.SLED_FILL, 'wd_reload': wd_reload or 0}
        self.header = hdr
        blob = bytearray(a - base)
        blob[:F.HEADER_SIZE] = F.pack_words(F.HEADER, hdr)
        for i, r in enumerate(records):
            o = F.HEADER_SIZE + i * F.CASE_SIZE
            blob[o:o + F.CASE_SIZE] = F.pack_case(r)
        for addr, data in blobs:
            blob[addr - base:addr - base + len(data)] = data
        self.blob = bytes(blob)
        self.base = base

    def with_start(self, start):
        """The same upload, beginning at case `start`."""
        b = bytearray(self.blob)
        struct.pack_into('<I', b, 4 * F.HEADER.index('start'), start)
        return bytes(b)


def case_size(c):
    n = F.CASE_SIZE + _align(len(_code_bytes(c)))
    for align, data in c.get('blobs', ()):
        n += align + _align(len(data))
    return n


def plan(cases, lay, sites, max_cases=None):
    """Split cases (an iterable) into lists, one per batch: as many as fit
    the batch area and the results buffer, all using one sled site."""
    room = lay['BATCH_END'] - lay['BATCH'] - F.HEADER_SIZE
    cap = min(RESULT_CAP, max_cases or RESULT_CAP)
    out, cur, used, site = [], [], 0, None
    for c in cases:
        s = site_of(c, sites)
        need = case_size(c)
        if cur and (used + need > room or len(cur) >= cap or s != site):
            out.append(cur)
            cur, used = [], 0
        cur.append(c)
        used += need
        site = s
    if cur:
        out.append(cur)
    return out


def site_of(c, sites):
    for s in sites:
        if s[0] <= c['slot'] < s[1]:
            return s
    raise RunError(f"case {c['id']}: slot 0x{c['slot']:08X} is in no sled site")


# ── provenance ─────────────────────────────────────────────────────────────

def picosim_commit():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
                              text=True, timeout=5).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root,
                                    capture_output=True, text=True, timeout=5).stdout.strip())
        return {'commit': head or None, 'dirty': dirty}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'dirty': None}


def chip_identity(target):
    """What says which chip this is: SYSINFO CHIP_ID, the ROM version and
    CPUID, read through the debugger."""
    r = target.rsp
    out = {}
    for name, addr in (('chip_id', 0x40000000), ('cpuid', 0xE000ED00)):
        try:
            out[name] = f"0x{r.read32(addr):08X}"
        except Exception:
            out[name] = None
    try:
        out['rom_version'] = r.read_mem(0x13, 1)[0]
    except Exception:
        out['rom_version'] = None
    return out


# ── the run ─────────────────────────────────────────────────────────────────

class Run:
    """One campaign run: its batches, its checkpoint folder and the board."""

    def __init__(self, target, path, state, cases_by_batch, log=None, ask=None, sites=None,
                 pins=frozenset(), stop_flag=None, progress=None, on_prepare=None):
        self.on_prepare = on_prepare        # called after every prepare (a reset undoes peripheral setup)
        self.t = target
        self.path = path
        self.state = state
        self.batches = cases_by_batch
        self.lay = F.LAYOUTS[state['layout']]
        self.agent = build_agent(state['layout'])
        self.sites = [tuple(s) for s in (sites or state['sites'])]
        self.pins = frozenset(pins)
        self.stop_flag = stop_flag
        self.progress = progress or (lambda done, total: None)
        self._ask = ask or (lambda msg: input(msg + " Press Enter when done. "))
        self._logf = open(os.path.join(path, 'log.txt'), 'a', buffering=1, encoding='utf-8')
        self._echo = log or (lambda text: None)
        self.ready = False
        self.integrity_streak = 0
        self.quiet = QUIET_START

    # creating and resuming

    @classmethod
    def start(cls, target, campaign, cases, layout='hi', run_id=None, flags=F.HF_SYSTICK,
              scratch_words=64, sites=None, wd_reload=None, timing=False, seed=0, repeat_of=None,
              max_cases=None, pins=frozenset(), root=None, order=None, allow_aircr=False, **kw):
        lay = F.LAYOUTS[layout]
        sites = [tuple(s) for s in (sites or [(lay['SLED'], lay['SLED'] + lay['SLED_SIZE'])])]
        if wd_reload:
            flags |= F.HF_WATCHDOG
        cases = list(cases)
        for c in cases:
            if wd_reload:
                c['watchdog'] = True
            safety.check_case({**c, 'code_len': len(_code_bytes(c))}, lay, pins, sites, allow_aircr)
        batches = plan(cases, lay, sites, max_cases)
        run_id = run_id or time.strftime('%Y%m%d-%H%M%S')
        path = os.path.join(root or cache_dir(), campaign, run_id)
        os.makedirs(path, exist_ok=True)
        if os.path.exists(os.path.join(path, 'state.json')):
            raise RunError(f"{path} already has a run; resume it or choose another run id")
        n = len(batches)
        if order is None:
            order = list(range(n))
            if repeat_of:
                random.Random(f"repeat|{seed}").shuffle(order)
        state = {'format_version': F.FORMAT_VERSION, 'campaign': campaign, 'run_id': run_id,
                 'layout': layout, 'flags': flags, 'scratch_words': scratch_words,
                 'sites': [list(s) for s in sites], 'wd_reload': wd_reload or 0, 'timing': timing,
                 'seed': seed, 'repeat_of': repeat_of, 'n_cases': len(cases), 'n_batches': n,
                 'max_cases': max_cases,
                 'order': order, 'done': [], 'cases_digest': _digest(batches),
                 'batch_sizes': [len(b) for b in batches],
                 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'target': target.kind,
                 'agent': build_agent(layout).sha1, 'picosim': picosim_commit(),
                 'chip': chip_identity(target), 'pins': sorted(pins), 'allow_aircr': allow_aircr}
        run = cls(target, path, state, batches, sites=sites, pins=pins, **kw)
        run._save_state()
        run.log(f"new run {campaign}/{run_id}: {len(cases)} cases in {n} batches")
        return run

    @classmethod
    def resume(cls, target, path, cases, **kw):
        """Carry on a run.  `cases` must be what the run was started with
        (the campaign generators are deterministic); the digest is checked,
        and so are the chip and the agent."""
        with open(os.path.join(path, 'state.json')) as f:
            state = json.load(f)
        lay = F.LAYOUTS[state['layout']]
        cases = list(cases)
        if state.get('wd_reload'):
            for c in cases:
                c['watchdog'] = True
        batches = plan(cases, lay, [tuple(s) for s in state['sites']], state.get('max_cases'))
        if [len(b) for b in batches] != state['batch_sizes'] or _digest(batches) != state['cases_digest']:
            raise RunError("these are not the cases this run was started with (the generator changed?)")
        chip = chip_identity(target)
        if chip.get('chip_id') != state['chip'].get('chip_id'):
            raise RunError(f"this run was started on chip {state['chip'].get('chip_id')}, but the board "
                           f"connected now reads {chip.get('chip_id')}")
        if state.get('flash_uid') and kw.get('flash_uid') and kw['flash_uid'] != state['flash_uid']:
            raise RunError("this run was started on a board with another flash unique ID")
        if build_agent(state['layout']).sha1 != state['agent']:
            raise RunError("the agent has changed since this run started; start a new run")
        run = cls(target, path, state, batches, pins=state.get('pins', ()), **{k: v for k, v in kw.items() if k != 'flash_uid'})
        run.log(f"resuming: {len(state['done'])} of {state['n_batches']} batches done")
        return run

    def _save_state(self):
        p = os.path.join(self.path, 'state.json')
        with open(p + '.tmp', 'w') as f:
            json.dump(self.state, f, indent=1)
        os.replace(p + '.tmp', p)

    def log(self, text):
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self._logf.write(line + '\n')
        self._echo(text)

    def close(self):
        self._logf.close()

    @property
    def finished(self):
        return len(self.state['done']) == self.state['n_batches']

    # the board

    def prepare(self, reset=True):
        """Reset, vector catch off, the agent, the sleds and the guard."""
        t = self.t
        if reset:
            t.reset_halt()
        demcr = t.vector_catch('none')
        self.log(f"DEMCR 0x{demcr:08X} (vector catch off)")
        self._write(self.agent.base, self.agent.image)
        if t.read_mem_fast(self.agent.base, len(self.agent.image)) != self.agent.image:
            raise RunError("the agent did not read back as written: check the SWD wiring")
        for lo, hi in self.sites:
            self._write(lo, struct.pack('<I', F.SLED_FILL) * ((hi - lo) // 4))
        self._write(self.lay['GUARD'], F.guard_bytes(self.lay['GUARD'], self.lay['GUARD_SIZE'] // 4))
        t.rsp.write32(0xE000ED08, self.agent.symbols['vectors'])
        if self.on_prepare:
            self.on_prepare(t)
        self._uploaded = None
        self.ready = True

    def _write(self, addr, data):
        """Debugger writes go through the safety check too."""
        for a in range(addr, addr + len(data), 0x1000):
            safety.check_write(a, 4, 0, self.pins, why='upload')
        self.t.write_mem_fast(addr, data)

    def _timeout(self, n):
        if self.t.kind == 'standin':
            return 8.0 + n * (0.1 + self.state['scratch_words'] * 0.0004)
        return 5.0 + n * 0.01 + (self.state['wd_reload'] or 0) / 4e6 * n

    def _launch(self, blob):
        t, r = self.t, self.t.rsp
        up = getattr(self, '_uploaded', None)
        if up is not None and len(up) == len(blob) and up[F.HEADER_SIZE:] == blob[F.HEADER_SIZE:]:
            self._write(self.lay['BATCH'], blob[:F.HEADER_SIZE])     # only the start index changed
        else:
            self._write(self.lay['BATCH'], blob)
        self._uploaded = blob
        r.write_reg(t.primask_n, 0)
        r.write_reg(t.control_n, 0)
        r.write_reg(13, self.lay['MSP_TOP'])
        r.write_reg(t.xpsr_n, 0x01000000)
        r.write_reg(15, self.agent.symbols['agent_start'])
        n = struct.unpack_from('<I', blob, 8)[0]
        if not self.state['timing']:
            return t.run(self._timeout(n), self.stop_flag)
        # timed: no debugger traffic while the cases run (OpenOCD's polling
        # shares the bus with the core), then polling on to see the halt
        quiet = self.quiet + n * QUIET_PER_CASE
        how = t.run(self._timeout(n) + quiet, self.stop_flag, quiet=quiet)
        if how == 'halted' and getattr(t, 'polled_running', False) and self.quiet < QUIET_MAX:
            self.quiet = min(QUIET_MAX, 2 * self.quiet)
            self.log(f"a timed batch outlasted its quiet time ({quiet:.2f} s); "
                     f"the quiet time is now {self.quiet:.2f} s + {QUIET_PER_CASE * 1000:.0f} ms a case")
        return how

    def _vars(self):
        return F.unpack_words(F.VARS, self.t.rsp.read_mem(self.lay['VARS'], 4 * len(F.VARS)))

    def _results(self, first, upto):
        n = upto - first
        if n <= 0:
            return []
        data = self.t.read_mem_fast(self.lay['RESULTS'] + first * F.RESULT_SIZE, n * F.RESULT_SIZE)
        return [data[i * F.RESULT_SIZE:(i + 1) * F.RESULT_SIZE] for i in range(n)]

    def _mark(self, case_rec, mark):
        return F.pack_words(F.RESULT, {'id': case_rec['id'], 'ipsr': mark, 'n_changed': F.NOT_CHECKED})

    # one batch

    def run_batch(self, k):
        """Run batch k (with recovery).  Returns (results bytes per case, notes)."""
        cases = self.batches[k]
        b = self._pack(cases)
        out = [None] * len(cases)
        notes = []
        start = 0
        failures = {}
        while start < len(cases):
            if not self.ready:
                self.prepare()
            t0 = time.monotonic()
            how = self._launch(b.with_start(start))
            if how == 'timeout' and self.stop_flag and self.stop_flag.is_set():
                # stopped by request mid-batch: nothing of it is kept, and a
                # resume runs the whole batch again
                self.ready = False
                raise RunError("stopped by request")
            v = self._vars()
            for _ in range(3):
                # stopped by the timeout while the agent itself was working: slow, not stuck
                if how != 'timeout' or v['state'] not in (F.STATE_BUSY, F.STATE_START):
                    break
                self.log(f"batch {k}: still running after the timeout (case {v['index']}); waiting longer")
                how = self.t.run(self._timeout(len(cases)), self.stop_flag)
                v = self._vars()
            index = v['index']
            for i, raw in enumerate(self._results(start, min(index, len(cases))), start):
                out[i] = raw
            if v['state'] == F.STATE_DONE and index == len(cases):
                if how != 'halted':
                    # every case ran and the agent checked itself; only the
                    # halt at its final BKPT went unseen
                    self.log(f"batch {k}: finished, but its halt was not reported in time")
                bad = self._integrity(v)
                if bad:
                    return self._isolate(k, cases, bad)
                self.integrity_streak = 0
                notes.append({'ran': len(cases) - start, 'seconds': round(time.monotonic() - t0, 3)})
                break
            # something stopped the batch at case `index`
            case = b.records[index] if index < len(cases) else None
            if v['state'] == F.STATE_AGENT_FAULT:
                raise RunError(f"the agent itself faulted at 0x{v['fault_pc']:08X} (LR 0x{v['fault_lr']:08X}); "
                               "this is a bug in agent.s, not a fact about the chip")
            if case is None or v['state'] != F.STATE_RUNNING:
                raise RunError(f"the agent stopped between cases (state {F.STATE_NAMES.get(v['state'], v['state'])}, "
                               f"case {index} of {len(cases)}, {how})")
            if how == 'timeout':
                st = self.t.dhcsr()
                mark = MARK_LOCKUP if st['lockup'] else MARK_HANG
            else:
                mark = MARK_HALTED                  # a BKPT or debug event inside the case
            failures[index] = failures.get(index, 0) + 1
            self.log(f"batch {k} case {case['id']}: {MARK_NAMES[mark]} (try {failures[index]})")
            if mark != MARK_HALTED:
                self._recover()
            if failures[index] >= 2 or mark == MARK_HALTED:
                out[index] = self._mark(case, mark)
                notes.append({'case': case['id'], 'what': MARK_NAMES[mark]})
                start = index + 1
            else:
                start = index
        return out, notes, b

    def _pack(self, cases):
        s = self.state
        return Batch(cases, self.lay, s['flags'], s['scratch_words'], site_of(cases[0], self.sites), s['wd_reload'])

    def _integrity(self, v):
        bad = []
        if v['sum'] != self.agent.checksum:
            bad.append(f"agent checksum 0x{v['sum']:08X}, expected 0x{self.agent.checksum:08X}")
        if v['sled_bad']:
            bad.append(f"{v['sled_bad']} sled words changed, first at 0x{v['sled_first']:08X}")
        if v['guard_bad']:
            bad.append(f"{v['guard_bad']} guard words changed, first at 0x{v['guard_first']:08X}")
        return bad

    def _full_scratch(self, b, out):
        """Cases that changed more scratch words than a result holds: run
        each again alone, leaving the window as the case left it, and read
        all of it.  {id: [[offset, value], ...]}"""
        s = self.state
        if s['flags'] & F.HF_NO_SCRATCH:
            return {}
        full = {}
        for i, raw in enumerate(out):
            if raw is None:
                continue
            r = F.unpack_result(raw)
            if not r['overflow']:
                continue
            c = b.cases[i]
            one = Batch([c], self.lay, s['flags'] | F.HF_KEEP_SCRATCH, s['scratch_words'],
                        site_of(c, self.sites), s['wd_reload'])
            how = self._launch(one.blob)
            if how != 'halted' or self._vars()['state'] != F.STATE_DONE:
                self._recover()
                continue
            words = s['scratch_words']
            now = self.t.read_mem_fast(self.lay['SCRATCH'], 4 * words)
            pat = F.pattern(c['seed'], words)
            full[str(c['id'])] = [[o, int.from_bytes(now[o:o + 4], 'little')]
                                  for o in range(0, 4 * words, 4) if now[o:o + 4] != pat[o:o + 4]]
            # the window is not the pattern now: make the next case refill it
            self.t.rsp.write32(self.lay['VARS'] + 4 * F.VARS.index('filled'), 0)
        return full

    def _isolate(self, k, cases, bad):
        """The batch damaged the agent, a sled or the guard: run each case
        alone, from a fresh upload, to find which."""
        self.integrity_streak += 1
        self.log(f"batch {k}: {'; '.join(bad)}; running its cases one at a time")
        if self.integrity_streak > 2:
            raise RunError("the agent's integrity check failed twice in a row: " + '; '.join(bad))
        out, notes = [], [{'integrity': bad}]
        for c in cases:
            self.ready = False
            b = self._pack([c])
            self.prepare()
            how = self._launch(b.blob)
            v = self._vars()
            if how == 'halted' and v['state'] == F.STATE_DONE:
                worse = self._integrity(v)
                if worse:
                    out.append(self._mark(b.records[0], MARK_CORRUPTS))
                    notes.append({'case': c['id'], 'what': 'corrupts', 'detail': worse})
                    continue
                out.append(self._results(0, 1)[0])
            else:
                st = self.t.dhcsr() if how == 'timeout' else {}
                out.append(self._mark(b.records[0], MARK_LOCKUP if st.get('lockup') else MARK_HANG))
                self._recover()
        self.ready = False
        self.integrity_streak = 0 if not any('what' in n for n in notes[1:]) else self.integrity_streak
        return out, notes, self._pack(cases)

    def _recover(self):
        """The ladder after a hang: reset halt and reload; then a new OpenOCD;
        then a power cycle by hand."""
        self.ready = False
        for step in ('reset', 'reconnect', 'power'):
            try:
                if step == 'reset':
                    self.t.reset_halt()
                elif step == 'reconnect':
                    self.log("reset halt did not help: restarting OpenOCD")
                    self.t.reconnect()
                else:
                    self._ask("The board does not answer. Unplug the TARGET Pico's USB (not the probe), "
                              "plug it back in,")
                    self.t.reconnect()
                self.prepare(reset=False)
                return
            except Exception as e:                  # noqa: BLE001 - every rung is worth trying
                self.log(f"recovery step '{step}' failed: {e}")
        raise RunError("the board could not be recovered")

    # the whole run

    def go(self, max_batches=None):
        done = set(self.state['done'])
        todo = [k for k in self.state['order'] if k not in done]
        if max_batches is not None:
            todo = todo[:max_batches]
        for k in todo:
            if self.stop_flag and self.stop_flag.is_set():
                self.log("stopped by request")
                break
            try:
                out, notes, b = self.run_batch(k)
            except RunError:
                if self.stop_flag and self.stop_flag.is_set():
                    self.log(f"stopped by request during batch {k}; it will run again on resume")
                    break
                raise
            full = self._full_scratch(b, out)
            if full:
                notes.append({'full_changes': full})
            self._save_batch(k, out, notes, b)
            self.state['done'].append(k)
            self._save_state()
            self.progress(len(self.state['done']), self.state['n_batches'])
        if self.finished:
            self.state['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
            self.state['throughput'] = getattr(self.t, 'throughput', {})
            self._save_state()
        return self

    def _save_batch(self, k, out, notes, b):
        stem = os.path.join(self.path, f"batch-{k:04d}")
        with open(stem + '.in.tmp', 'wb') as f:
            f.write(b.blob)
        os.replace(stem + '.in.tmp', stem + '.in')
        with open(stem + '.bin.tmp', 'wb') as f:
            f.write(b''.join(out))
        os.replace(stem + '.bin.tmp', stem + '.bin')
        with open(stem + '.json.tmp', 'w') as f:
            json.dump({'ids': [c['id'] for c in b.records], 'notes': notes,
                       'time': time.strftime('%Y-%m-%d %H:%M:%S')}, f)
        os.replace(stem + '.json.tmp', stem + '.json')


def _digest(batches):
    h = hashlib.sha1()
    for cases in batches:
        for c in cases:
            h.update(F.pack_case({**c, 'code': 0, 'code_len': 0, 'kind': F.KINDS.get(c['kind'], 0)}))
            h.update(_code_bytes(c))
    return h.hexdigest()


# ── reading a run back ─────────────────────────────────────────────────────

def load(path):
    """Every case of a saved run: [(case record dict with 'code_bytes',
    result dict)], in batch order, plus the run's state."""
    with open(os.path.join(path, 'state.json')) as f:
        state = json.load(f)
    lay = F.LAYOUTS[state['layout']]
    out = []
    for k in sorted(state['done']):
        stem = os.path.join(path, f"batch-{k:04d}")
        with open(stem + '.in', 'rb') as f:
            blob = f.read()
        with open(stem + '.bin', 'rb') as f:
            res = f.read()
        hdr = F.unpack_words(F.HEADER, blob)
        full = {}
        if os.path.exists(stem + '.json'):
            with open(stem + '.json') as f:
                for n in json.load(f).get('notes', []):
                    full.update(n.get('full_changes', {}))
        for i in range(hdr['n_cases']):
            o = F.HEADER_SIZE + i * F.CASE_SIZE
            c = F.unpack_words(F.CASE, blob[o:o + F.CASE_SIZE])
            co = c['code'] - lay['BATCH']
            c['code_bytes'] = blob[co:co + c['code_len']]
            c['batch'] = k
            c['watchdog'] = bool(hdr['flags'] & F.HF_WATCHDOG)
            r = F.unpack_result(res[i * F.RESULT_SIZE:(i + 1) * F.RESULT_SIZE])
            if str(c['id']) in full:
                r['changed_full'] = [tuple(x) for x in full[str(c['id'])]]
            out.append((c, r))
    return out, state


BEHAVIOUR_FIELDS = [f for f in F.RESULT if f not in ('systick',)]


def compare_runs(path_a, path_b, fields=None):
    """Cases where two runs of the same cases disagree: [(id, [fields])]."""
    a, _ = load(path_a)
    b, _ = load(path_b)
    fields = fields or BEHAVIOUR_FIELDS
    rb = {c['id']: r for c, r in b}
    out = []
    for c, r in a:
        o = rb.get(c['id'])
        if o is None:
            out.append((c['id'], ['missing']))
            continue
        d = [f for f in fields if r[f] != o[f]]
        if d:
            out.append((c['id'], d))
    return out
