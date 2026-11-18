"""
Every address of the RP2040's 4 GB, measured: campaign A's `sweep` stage.

The sweep reads the whole address space so that every address picosim can
be asked about belongs to a measured range (and picosim never has to guess
what the Pico does there).

  1. A grid of sample addresses: 4 KB apart in and next to the regions and
     peripherals the datasheet lists, 64 KB apart in the 256 MB blocks that
     hold devices (0x1, 0x2, 0x4, 0x5, 0xD, 0xE), 1 MB apart in the big
     empty spans; plus the first and last word of every span the sweep does
     not read (below).
  2. Each sample is read through the debugger (a value, a DAP error, or a
     hang: then the debugger is reconnected and the core never tries it),
     then by the core (a 32-bit load: it works, or how it ends: a fault, a
     hang).  Two neighbouring samples that answered differently are
     bisected, round after round, down to the word where the answer
     changes.
  3. On both sides of every boundary found, and at the first, middle and
     last sample of every range, the core also loads 16 and 8 bits (the
     core and the debugger, and the widths, can differ).

Never read (safety.check_read and the sweep's own guards), and recorded as
"not read: <why>" ranges, so they are known and justified, not unknown:

  * flash (XIP, 0x10000000-0x13FFFFFF): read only in campaign F's flashed
    state; here, with no flash set up, a read may hang;
  * XIP_AUX (0x50400000-0x504FFFFF): the XIP stream FIFO and whatever
    mirrors it (a read pops it);
  * every register whose read has a side effect (safety.read_side_effects)
    and its atomic aliases (+0x1000/+0x2000/+0x3000: a read through an alias
    is a read of the register);
  * the guards: inside a peripheral's bus slot, every word that would be a
    side-effect register if the peripheral repeated itself every P bytes
    (P: its registers' span rounded up to a power of two; XIP_CTRL repeats
    every 0x20 bytes on the chip).  The grid steps past them; one left at a
    boundary is recorded as its own range;
  * the debugger never reads the SIO (0xD0000000-0xDFFFFFFF, core-local).

Output (memory_map.json "coverage"): contiguous ranges that cover
0x00000000-0xFFFFFFFF with no gap, each {'lo', 'hi' (exclusive), 'debugger',
'core': {'read32', 'read16', 'read8'}, 'how', 'samples', 'example'}.

The sweep logic talks to a "prober" (debugger(addrs), core(name, [(addr,
width)])) so it is tested without a board (tests/test_discover_coverage.py),
and the same code runs on the chip and the stand-in.
"""

import bisect
import json
import os
import random

from . import safety

TOP = 1 << 32
KB, MB = 1 << 10, 1 << 20

# (lo, hi, step): later lines win (the finest grid that covers an address)
PHYSICAL_GRID = (
    [(0, TOP, MB)]
    + [(lo, hi, 64 * KB) for lo, hi in ((0x14000000, 0x20000000), (0x20000000, 0x30000000),
                                        (0x40000000, 0x60000000), (0xD0000000, 0xF0000000))]
    + [(lo, hi, 4 * KB) for lo, hi in ((0x00000000, 0x00100000), (0x14000000, 0x14100000),
                                       (0x15000000, 0x15100000), (0x18000000, 0x18100000),
                                       (0x20000000, 0x20100000), (0x21000000, 0x21100000),
                                       (0x40000000, 0x40100000), (0x50000000, 0x50500000),
                                       (0xD0000000, 0xD0100000), (0xE0000000, 0xE0200000))]
)
# The stand-in's routine run (no board): the same code, a sparse grid.
SMOKE_GRID = (
    [(0, TOP, 256 * MB)]
    + [(lo, hi, 4 * KB) for lo, hi in ((0x00000000, 0x00008000), (0x20040000, 0x20044000),
                                       (0x40064000, 0x40074000), (0xD0000000, 0xD0002000),
                                       (0xE0000000, 0xE0004000))]
)
PHYSICAL = {'name': 'physical', 'grid': PHYSICAL_GRID, 'max_rounds': 40}
SMOKE = {'name': 'stand-in smoke', 'grid': SMOKE_GRID, 'max_rounds': 40}

# Spans the sweep never reads, by either the debugger or the core.
STATIC_NOT_READ = (
    (0x10000000, 0x14000000, "flash (XIP): read only in campaign F's flashed state; with no flash "
                             "set up a read may hang"),
    (0x50400000, 0x50500000, "XIP_AUX: the XIP stream FIFO and its mirrors (a read pops it; "
                             "an empty one may hold the bus)"),
)
SIO_BLOCK = (0xD0000000, 0xE0000000)
DEBUGGER_NOT_READ_SIO = "not read: the SIO is core-local (the debugger cannot reach it)"
# side effects the sweep adds to safety.read_side_effects()
EXTRA_SIDE = {
    0xE000EDF0: "DHCSR: reading clears its sticky S_RETIRE_ST and S_RESET_ST bits, which the debugger uses",
    0x18000060: "SSI DR0: reading pops the flash interface's receive FIFO",
}
MAX_HANGS = 16                      # debugger hangs before the sweep stops (resume it after a look)

# Board time, from earlier sessions: OpenOCD answered 700-1400 single-word
# reads a second (2026-11-10, campaign G), and the agent ran about 330 cases
# a second (B2: 389,584 cases in 19.5 minutes).
DEBUGGER_READS_PER_S = 700
CORE_CASES_PER_S = 330
ROUND_OVERHEAD_S = 5


# ── what is never read ─────────────────────────────────────────────────────

def _next_pow2(n):
    p = 4
    while p < n:
        p <<= 1
    return p


def _slot(addr):
    """The bus slot a peripheral address belongs to (where it could repeat)."""
    if 0x40000000 <= addr < 0x50000000:
        return addr & ~0x3FFF, (addr & ~0x3FFF) + 0x4000          # APB: 4 KB and its 3 aliases
    if 0x50000000 <= addr < 0x60000000:
        return addr & ~0xFFFFF, (addr & ~0xFFFFF) + MB            # AHB-Lite: 1 MB per slave
    if 0x14000000 <= addr < 0x20000000:
        return addr & ~0xFFFFFF, (addr & ~0xFFFFFF) + 16 * MB     # XIP_CTRL, XIP SRAM, SSI
    if 0xD0000000 <= addr < 0xE0000000:
        return SIO_BLOCK
    if 0xE0000000 <= addr < 0xE0100000:
        return 0xE0000000, 0xE0100000                              # the PPB
    return addr & ~0xFFF, (addr & ~0xFFF) + 0x1000


_GUARDS = None
_SIDE = None


def side_effects():
    """{address: why}: safety.read_side_effects() with the sweep's extras,
    and (APB) each register's three atomic aliases."""
    global _SIDE
    if _SIDE is None:
        side = dict(safety.read_side_effects())
        side.update(EXTRA_SIDE)
        out = {}
        for a, why in side.items():
            a &= ~3
            out[a] = why
            if 0x40000000 <= a < 0x50000000:
                for k in (1, 2, 3):
                    out.setdefault((a & ~0x3000) + k * 0x1000, f"an atomic alias of a register: {why}")
        _SIDE = out
    return _SIDE


def guards():
    """[(slot lo, slot hi, period, offset, why)]: words never read because
    they would be a side-effect register if its peripheral repeated every
    `period` bytes inside its slot."""
    global _GUARDS
    if _GUARDS is None:
        spans = []
        try:
            from . import svd
            for p in svd.peripherals().values():
                regs = [r['offset'] for r in p['registers']]
                spans.append((p['base'], p['base'] + (max(regs) + 4 if regs else 4)))
        except Exception:                               # noqa: BLE001 - no SDK: every guard is 0x100
            pass
        out = set()
        for a, why in side_effects().items():
            base = a & ~0x3000 if 0x40000000 <= a < 0x50000000 else a
            span = next((hi - lo for lo, hi in spans if lo <= base < hi), None)
            period = 0x20 if 0x14000000 <= a < 0x14100000 else _next_pow2(span) if span else 0x100
            lo, hi = _slot(a)
            out.add((lo, hi, period, a & (period - 1), why))
        _GUARDS = sorted(out)
    return _GUARDS


def why_not_read(addr):
    """None if the sweep may read the word at `addr` (debugger and core),
    else why not."""
    addr &= ~3
    for lo, hi, why in STATIC_NOT_READ:
        if lo <= addr < hi:
            return why
    side = side_effects()
    if addr in side:
        return f"side effect ({side[addr]})"
    try:
        safety.check_read(addr)
    except safety.SafetyError as e:
        return str(e)
    for lo, hi, period, off, why in guards():
        if lo <= addr < hi and addr & (period - 1) == off:
            return f"guard: would be a side-effect register if the peripheral repeats every 0x{period:X} bytes ({why})"
    return None


def static_ranges():
    return [(lo, hi, why) for lo, hi, why in STATIC_NOT_READ]


# ── the grid ──────────────────────────────────────────────────────────────

def grid_step(grid, addr):
    step = None
    for lo, hi, s in grid:
        if lo <= addr < hi:
            step = s
    return step


def _readable_near(a, limit, step=4):
    """The first readable word at or after a, before `limit` (None if none
    within 256 words)."""
    for k in range(256):
        b = a + k * step
        if b >= limit:
            return None
        if why_not_read(b) is None:
            return b
    return None


def grid_addresses(profile):
    """The first round's sample addresses (sorted), each the first readable
    word at or after its grid point, plus the first and last readable word
    of every span the sweep reads (next to a never-read span and at the
    ends of the address space)."""
    grid = profile['grid']
    pts = set()
    for lo, hi, step in grid:
        for a in range(lo, hi, step):
            if grid_step(grid, a) == step:
                pts.add(a)
    out = set()
    for a in pts:
        if any(lo <= a < hi for lo, hi, _ in STATIC_NOT_READ):
            continue
        b = _readable_near(a, a + grid_step(grid, a))
        if b is not None:
            out.add(b)
    edges = [0, TOP]
    for lo, hi, _ in STATIC_NOT_READ:
        edges += [lo, hi]
    edges.sort()
    for lo, hi in zip(edges[::2], edges[1::2]):                # the spans the sweep reads
        first = _readable_near(lo, hi)
        if first is not None:
            out.add(first)
        for k in range(256):
            b = hi - 4 - 4 * k
            if b < lo:
                break
            if why_not_read(b) is None:
                out.add(b)
                break
    return sorted(out)


# ── one sample: what the debugger and the core said ────────────────────────

def dbg_class(v):
    if v is None:
        return None
    if v.startswith('0x'):
        return 'value'
    return v


def signature(s):
    """The debugger's kind of answer and the core's 32-bit load."""
    return dbg_class(s.get('dbg')), s.get('core', {}).get(4)


def differ(sa, sb):
    """Did two samples answer differently?  The debugger's kind of answer,
    and the core's load at every width both were read at (32-bit always;
    16 and 8 inside the 4 KB grid and wherever a boundary is bisected)."""
    if dbg_class(sa.get('dbg')) != dbg_class(sb.get('dbg')):
        return True
    ca, cb = sa.get('core', {}), sb.get('core', {})
    return any(ca[w] != cb[w] for w in (4, 2, 1) if w in ca and w in cb)


# ── the sweep ─────────────────────────────────────────────────────────────

class SweepError(Exception):
    pass


class Sweep:
    """The samples and what to read next.  samples: {addr: {'dbg': text,
    'core': {width: 'ok' | 'fault' | 'hang' | ...}, 'values': {width: '0x..'}}}."""

    def __init__(self, profile, samples=None):
        self.profile = profile
        self.samples = samples if samples is not None else {}
        self.rounds = 0

    # ── reading ──
    def read(self, prober, addrs, name, log=print, widths=None):
        """Read new samples: the debugger, then the core at the widths
        `widths` gives an address ({addr: (4, 2, 1)}; default: all three in
        the 4 KB grid, 32 bits elsewhere)."""
        new = [a for a in addrs if a not in self.samples]
        if not new:
            return
        dbg_addrs = [a for a in new if not SIO_BLOCK[0] <= a < SIO_BLOCK[1]]
        got = prober.debugger(dbg_addrs) if dbg_addrs else {}
        for a in new:
            self.samples[a] = {'dbg': got.get(a, DEBUGGER_NOT_READ_SIO), 'core': {}, 'values': {}}
        hangs = sum(1 for s in self.samples.values() if s['dbg'] == 'hang')
        def ws(a):
            if widths and a in widths:
                return widths[a]
            step = grid_step(self.profile['grid'], a)
            return (4, 2, 1) if step is not None and step <= 4 * KB else (4,)
        core = [(a, w) for a in new if self.samples[a]['dbg'] != 'hang' for w in ws(a)]
        for a in new:
            if self.samples[a]['dbg'] == 'hang':
                for w in (4, 2, 1):
                    self.samples[a]['core'][w] = 'not read (the debugger hung)'
        self._core(prober, core, name)
        if hangs > MAX_HANGS:
            raise SweepError(f"the debugger hung at {hangs} addresses (more than {MAX_HANGS}): the sweep "
                             "stops here; look at the log, then run it again to carry on")

    def _core(self, prober, pairs, name):
        if not pairs:
            return
        res = prober.core(name, pairs)
        for (a, w), r in res.items():
            s = self.samples[a]
            if r.startswith('ok'):
                s['core'][w] = 'ok'
                if ':' in r:
                    s['values'][w] = r.split(':', 1)[1]
            else:
                s['core'][w] = r

    # ── refining ──
    def _segments(self):
        """The spans the sweep reads: (lo, hi) between never-read spans."""
        edges = [0]
        for lo, hi, _ in STATIC_NOT_READ:
            edges += [lo, hi]
        edges.append(TOP)
        return list(zip(edges[::2], edges[1::2]))

    def pairs(self):
        """Neighbouring samples (in one segment) that answered differently."""
        keys = sorted(self.samples)
        out = []
        for lo, hi in self._segments():
            i, j = bisect.bisect_left(keys, lo), bisect.bisect_left(keys, hi)
            seg = keys[i:j]
            for a, b in zip(seg, seg[1:]):
                if differ(self.samples[a], self.samples[b]):
                    out.append((a, b))
        return out

    @staticmethod
    def midpoint(a, b):
        """A readable word strictly between a and b, near the middle; None
        when there is none (the boundary is found: the words between, if
        any, are never read)."""
        if b - a <= 4:
            return None
        mid = ((a + b) // 2) & ~3
        if mid <= a:
            mid = a + 4
        for k in range(0, (b - a) // 4 + 1):
            for c in (mid + 4 * k, mid - 4 * k):
                if a < c < b and why_not_read(c) is None:
                    return c
            if 4 * k > b - a or k > 4096:
                break
        return None

    def refine(self, prober, log=print):
        """Bisect until every boundary is found to the word."""
        while True:
            widths = {}
            for a, b in self.pairs():
                m = self.midpoint(a, b)
                if m is not None:
                    have = set(self.samples[a]['core']) | set(self.samples[b]['core'])
                    widths[m] = tuple(sorted(set(widths.get(m, ())) | have | {4}, reverse=True))
            mids = sorted(widths)
            if not mids:
                return
            if self.rounds >= self.profile['max_rounds']:
                raise SweepError(f"still {len(mids)} boundaries to find after {self.rounds} rounds")
            self.rounds += 1
            log(f"sweep round {self.rounds}: {len(mids)} addresses between samples that differ")
            self.read(prober, mids, f"sweep-{self.rounds}", log, widths)

    def widths(self, prober, log=print):
        """16- and 8-bit loads on both sides of every boundary and at the
        first, middle and last sample of every range."""
        want = set()
        for a, b in self.pairs():
            want.update((a, b))
        for r in self.ranges():
            if r.get('samples'):
                want.update(r['_sample_addrs'])
        pairs = [(a, w) for a in sorted(want) for w in (4, 2, 1)
                 if w not in self.samples[a]['core'] and self.samples[a]['dbg'] != 'hang']
        if pairs:
            log(f"sweep widths: {len(pairs)} loads at the boundaries and inside each range")
            self._core(prober, pairs, 'sweep-widths')

    # ── the result ──
    def ranges(self):
        """Contiguous ranges over the whole 4 GB."""
        side = side_effects()
        keys = sorted(self.samples)
        raw = []                                   # (lo, hi, kind, payload)
        for lo, hi, why in STATIC_NOT_READ:
            raw.append((lo, hi, 'not read', why))
        for slo, shi in self._segments():
            i, j = bisect.bisect_left(keys, slo), bisect.bisect_left(keys, shi)
            seg = keys[i:j]
            if not seg:
                raw.append((slo, shi, 'unread', 'nothing here may be read'))
                continue
            if seg[0] > slo:
                raw.append((slo, seg[0], 'unread', why_not_read(slo) or 'before the first sample'))
            for a, b in zip(seg, seg[1:]):
                if not differ(self.samples[a], self.samples[b]):
                    raw.append((a, b, 'sample', a))
                else:
                    raw.append((a, a + 4, 'sample', a))
                    if b > a + 4:                       # never-read words at the boundary
                        raw.append((a + 4, b, 'unread', why_not_read(a + 4) or 'between two samples'))
            last = seg[-1]
            raw.append((last, last + 4, 'sample', last))
            if last + 4 < shi:
                raw.append((last + 4, shi, 'unread', why_not_read(last + 4) or 'after the last sample'))
        raw.sort()
        # explicit side-effect registers inside measured ranges
        cut = []
        for lo, hi, kind, payload in raw:
            pieces = [(lo, hi, kind, payload)]
            if kind == 'sample':
                inside = [x for x in _side_in(lo, hi)]
                pieces = []
                at = lo
                for x in inside:
                    if x > at:
                        pieces.append((at, x, kind, payload))
                    pieces.append((x, x + 4, 'not read', f"side effect ({side[x]})"))
                    at = x + 4
                if at < hi:
                    pieces.append((at, hi, kind, payload))
            cut += pieces
        # one row per contiguous run of the same answer
        rows = []
        for lo, hi, kind, payload in cut:
            key = ('sample',) if kind == 'sample' else ('not read', payload)
            same = rows and rows[-1]['_key'] == key and rows[-1]['hi'] == lo and (
                kind != 'sample' or not differ(self.samples[rows[-1]['_sample_addrs'][-1]], self.samples[payload]))
            if same:
                rows[-1]['hi'] = hi
                if kind == 'sample':
                    rows[-1]['_sample_addrs'].append(payload)
                continue
            rows.append({'lo': lo, 'hi': hi, '_key': key,
                         '_sample_addrs': [payload] if kind == 'sample' else []})
        out = []
        for r in rows:
            row = {'lo': r['lo'], 'hi': r['hi']}
            if r['_key'][0] == 'not read':
                row.update({'debugger': f"not read: {r['_key'][1]}",
                            'core': {f'read{8 * w}': f"not read: {r['_key'][1]}" for w in (4, 2, 1)},
                            'how': 'not read', 'samples': 0})
            else:
                addrs = r['_sample_addrs']
                first = self.samples[addrs[0]]
                core = {}
                for w in (4, 2, 1):
                    seen = sorted({self.samples[a]['core'][w] for a in addrs if w in self.samples[a]['core']})
                    core[f'read{8 * w}'] = seen[0] if len(seen) == 1 else (
                        'not measured' if not seen else 'mixed: ' + ', '.join(seen))
                step = grid_step(self.profile['grid'], r['lo'])
                row.update({'debugger': dbg_class(first['dbg']), 'core': core,
                            'how': f"grid {_size(step)}, edges bisected to the word",
                            'samples': len(addrs),
                            'example': {'addr': f"0x{addrs[0]:08X}", 'debugger': first['dbg'],
                                        **{f'read{8 * w}': first['values'][w]
                                           for w in (4, 2, 1) if w in first['values']}}})
                mid = addrs[len(addrs) // 2]
                row['_sample_addrs'] = sorted({addrs[0], mid, addrs[-1]})
            out.append(row)
        return out


def _side_in(lo, hi):
    keys = _side_sorted()
    i, j = bisect.bisect_left(keys, lo), bisect.bisect_left(keys, hi)
    return keys[i:j]


_SIDE_SORTED = None


def _side_sorted():
    global _SIDE_SORTED
    if _SIDE_SORTED is None:
        _SIDE_SORTED = sorted(side_effects())
    return _SIDE_SORTED


def _size(n):
    if n is None:
        return '?'
    return f"{n // MB} MB" if n >= MB else f"{n // KB} KB"


def run_sweep(prober, profile, log=print, samples=None):
    """The whole sweep: grid, bisection, widths.  Returns (coverage, sweep)."""
    sw = Sweep(profile, samples)
    grid = grid_addresses(profile)
    log(f"sweep ({profile['name']}): {len(grid)} grid samples; {estimate_text(profile, len(grid))}")
    sw.read(prober, grid, 'sweep-0', log)
    sw.refine(prober, log)
    sw.widths(prober, log)
    return coverage_doc(sw), sw


def coverage_doc(sw):
    rows = sw.ranges()
    for r in rows:
        r.pop('_sample_addrs', None)
    out = [{**r, 'lo': f"0x{r['lo']:08X}", 'hi': f"0x{r['hi']:08X}" if r['hi'] < TOP else '0x100000000'}
           for r in rows]
    gaps = check_ranges(out)
    return {'profile': sw.profile['name'], 'samples': len(sw.samples), 'rounds': sw.rounds,
            'complete': not gaps, 'problems': gaps,
            'guards': len(guards()), 'ranges': out}


def check_ranges(ranges):
    """Problems with a coverage table: gaps, overlaps, unknown answers."""
    problems = []
    at = 0
    for r in ranges:
        lo, hi = _int(r['lo']), _int(r['hi'])
        if lo != at:
            problems.append(f"{'gap' if lo > at else 'overlap'} at 0x{at:08X}")
        if hi <= lo:
            problems.append(f"empty range at 0x{lo:08X}")
        at = hi
        for k, v in (r.get('core') or {}).items():
            if not v or v.startswith('mixed') or v == 'not measured':
                problems.append(f"0x{lo:08X}-0x{hi - 1:08X} {k}: {v or 'unknown'}")
    if at != TOP:
        problems.append(f"the ranges end at 0x{at:08X}, not 0x100000000")
    return problems


def _int(v):
    return int(v, 16) if isinstance(v, str) else int(v)


# ── the board time ─────────────────────────────────────────────────────────

def estimate(profile, n_grid=None, boundaries=150):
    """Seconds the sweep should take on the chip: the grid (the debugger
    outside the SIO; the core at 3 widths in the 4 KB grid, 1 elsewhere),
    about `boundaries` boundaries bisected over 18 rounds at 3 widths, and
    the widths round.  `n_grid`: ignored (kept for callers that pass it)."""
    grid = grid_addresses(profile)
    sio = sum(1 for a in grid if SIO_BLOCK[0] <= a < SIO_BLOCK[1])
    fine = sum(1 for a in grid if (grid_step(profile['grid'], a) or MB) <= 4 * KB)
    rounds = 18
    bis = boundaries * rounds
    dbg = (len(grid) - sio + bis) / DEBUGGER_READS_PER_S
    core = (len(grid) + 2 * fine + 3 * bis + 6 * boundaries) / CORE_CASES_PER_S
    return dbg + core + rounds * ROUND_OVERHEAD_S


def estimate_text(profile, n_grid=None):
    if profile is not PHYSICAL:
        return "a sparse grid for the stand-in (about 25 s there)"
    s = estimate(profile, n_grid)
    return f"about {s / 60:.0f} min on the chip, estimated"


# ── picosim's side ────────────────────────────────────────────────────────

_CPU = None
LOADS = {4: 0x6808, 2: 0x8808, 1: 0x7808}         # ldr/ldrh/ldrb r0, [r1]
PROBE_AT = 0x20000100


def picosim_access(addr, width=4, rom=True):
    """What picosim does with a load of `width` bytes at `addr`, as a
    student's program would see it: ('works', value), or (reason, message)
    with reason cpu.HARDWARE_FAULT / MODEL_LIMIT / TEACHING_STOP.  The ROM
    holds the chip's boot ROM (hwfacts/rom.bin) when `rom` and it is there."""
    global _CPU
    from .. import cpu as C
    if _CPU is None:
        from ..memory import Memory
        from ..memmap import Image, ROM_BASE
        from ..gpio import GPIO
        img = Image()
        if rom:
            from .replay import rom as rom_bytes
            img.write(ROM_BASE, rom_bytes())
        img.write(PROBE_AT, b'\x00' * 4)
        mem = Memory(img)
        c = C.CPU(mem, PROBE_AT, {}, {})
        g = GPIO()
        c.add_peripheral(g)
        _CPU = c
    c = _CPU
    c._core.halted = False
    c._core.write16(PROBE_AT, LOADS[width])
    c._core.write16(PROBE_AT + 2, 0xBF00)
    c.set_reg(1, addr & 0xFFFFFFFF)
    c.set_reg(0, 0)
    c.pc = PROBE_AT
    try:
        c.step()
    except C.SimulatorError as e:
        return (e.reason or C.stop_reason(e) or 'error'), str(e)
    except RuntimeError as e:
        return (C.stop_reason(e) or 'error'), str(e)
    return 'works', c.regs[0]


def agrees(chip, sim_reason):
    """Does picosim's answer (picosim_access()[0]) agree with the chip's
    ('ok', 'fault', 'hang', 'not read: ...')?"""
    from .. import cpu as C
    if chip == 'ok':
        return sim_reason in ('works', C.MODEL_LIMIT)
    if chip == 'fault':
        return sim_reason == C.HARDWARE_FAULT
    if chip == 'hang' or chip.startswith('not read'):
        return sim_reason != C.HARDWARE_FAULT if chip == 'hang' else True
    return False


# ── the coverage table, as recorded ────────────────────────────────────────

def recorded(folder=None):
    """The coverage table in hwfacts/memory_map.json, or None."""
    from .replay import HWFACTS
    p = os.path.join(folder or HWFACTS, 'memory_map.json')
    try:
        with open(p) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    cov = doc.get('coverage')
    return cov if isinstance(cov, dict) and cov.get('ranges') else None


# ── edges for campaign E ───────────────────────────────────────────────────

def edges_from(coverage=None):
    """Sorted addresses where the chip's answer changes: the coverage
    table's range edges, or (before the sweep has run) the regions'
    (memmap.REGIONS), every SVD peripheral's (its first register, the end
    of its registers, its 4 KB window and its slot) and the known holes."""
    out = set()
    if coverage:
        for r in coverage['ranges']:
            out.add(_int(r['lo']))
            out.add(_int(r['hi']) % TOP)
        return sorted(out)
    from .cases.memmap import REGIONS
    for _, base, size, _ in REGIONS:
        out.update((base, base + size))
    try:
        from . import svd
        for p in svd.peripherals().values():
            regs = sorted(r['offset'] for r in p['registers'])
            b = p['base']
            out.update((b, b + (regs[-1] + 4 if regs else 4), b + 0x1000, b + 0x4000))
    except Exception:                                   # noqa: BLE001 - no SDK: regions and holes
        pass
    out.update(safety.HOLES)
    return sorted(a % TOP for a in out)


def edge_targets(rng, edges, n):
    """`n` (address, width) loads near the edges: the first and last word of
    each side, just past it, and 1-3 bytes off (misaligned), every width;
    only addresses the sweep may read."""
    out = []
    tries = 0
    while len(out) < n and tries < 50 * n:
        tries += 1
        e = rng.choice(edges)
        base = (e + rng.choice((-8, -4, 0, 4))) % TOP
        off = rng.choice((0, 0, 0, 1, 2, 3))
        width = rng.choice((4, 2, 1))
        a = (base + off) % TOP
        if a > TOP - width:
            continue
        if any(why_not_read(w) is not None for w in {a & ~3, (a + width - 1) & ~3}):
            continue
        if (a, width) not in out:
            out.append((a, width))
    return out


def seeded_edges(k, n, coverage=None):
    return edge_targets(random.Random(f"E-edges|{k}"), edges_from(coverage), n)
