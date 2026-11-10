"""
Campaign A: the memory map, as the chip has it.

1. Probe list: every region of addressmap.h (first, middle and last word,
   and just past its end), every SVD peripheral (first and last register,
   the first offset past it, the end of its 4 KB window, and its
   +0x1000/+0x2000/+0x3000 alias windows), and the known holes.
2. Each probe is read through the debugger first (a DAP error can be
   recovered from; an access that hangs is recorded and never tried by the
   core), then by the core at 8, 16 and 32 bits.  Writes (store, then load
   back) only where safety.py allows them: free SRAM, the ROM and holes.
3. Gap scan: every word of each readable peripheral's 4 KB window, through
   the debugger, skipping registers whose read has a side effect.
4. SRAM structure: tags written through the non-striped aliases
   (0x21000000 + bank * 0x10000) and read back through the striped window
   give the striping function; SRAM4/5 aliases; what is past 0x20042000.
5. Access timing: 32 loads (and stores where allowed) to each region class,
   and 32 NOPs run from each kind of SRAM, between two SysTick reads.

This runs with the agent linked low (layout 'lo'), so SRAM4 and SRAM5 are
free to probe and to run code in.  Output: memory_map.json.
"""

import re

from .. import formats as F
from .. import safety
from ..campaign import h, ldr_probe, timing_case, release, IO_PADS

NAME, TITLE, AGENT_BASED = 'memmap', 'Memory map: regions, widths, aliases, access timing', True
LAYOUT = 'lo'
LAY = F.LAYOUTS[LAYOUT]
MAX_CASES = 400                      # keeps results below 0x20027000: 0x2002xxxx+ stays free

# free RAM (in layout 'lo', results well below 0x20030000) for core write probes
RAM_WRITE_OK = [0x20030000, 0x20038000, 0x2003FFFC, 0x20040000, 0x20040800, 0x20040FFC,
                0x20041000, 0x20041FFC, 0x2100C000, 0x2101C000, 0x2102C000, 0x2103FFFC]
SITES = {'striped': (LAY['SLED'], LAY['SLED'] + LAY['SLED_SIZE']),
         'sram4': (0x20040800, 0x20041000), 'sram5': (0x20041800, 0x20042000),
         'nonstriped': (0x2100C000, 0x2100E000)}      # data accesses go below/after these, never into them
REGIONS = [  # (name, base, size, kind)
    ('ROM', 0x00000000, 0x4000, 'rom'), ('XIP', 0x10000000, 0x1000000, 'xip'),
    ('XIP_NOALLOC', 0x11000000, 0x1000000, 'xip'), ('XIP_NOCACHE', 0x12000000, 0x1000000, 'xip'),
    ('XIP_NOCACHE_NOALLOC', 0x13000000, 0x1000000, 'xip'), ('XIP_CTRL', 0x14000000, 0x20, 'ahb'),
    ('XIP_SRAM', 0x15000000, 0x4000, 'xip'), ('XIP_SSI', 0x18000000, 0x100, 'apb-flash'),
    ('SRAM (striped)', 0x20000000, 0x40000, 'sram'), ('SRAM4', 0x20040000, 0x1000, 'sram'),
    ('SRAM5', 0x20041000, 0x1000, 'sram'), ('SRAM0', 0x21000000, 0x10000, 'sram'),
    ('SRAM1', 0x21010000, 0x10000, 'sram'), ('SRAM2', 0x21020000, 0x10000, 'sram'),
    ('SRAM3', 0x21030000, 0x10000, 'sram'), ('USB DPRAM', 0x50100000, 0x1000, 'ahb'),
    ('SIO', 0xD0000000, 0x180, 'sio'), ('PPB', 0xE0000000, 0x100000, 'ppb'),
]
TIMED = [  # (name, address, store value or None)
    ('striped SRAM', LAY['SCRATCH'] + 0x100, 0x12345678), ('SRAM4', 0x20040100, 0x12345678),
    ('SRAM5', 0x20041100, 0x12345678), ('non-striped SRAM', 0x2100F100, 0x12345678),
    ('ROM', 0x00000100, None), ('APB (IO_BANK0 GPIO0_STATUS)', 0x40014000, None),
    ('APB (PADS_BANK0 GPIO22)', 0x4001C05C, 0x56), ('AHB (XIP_CTRL CTRL)', 0x14000000, None),
    ('SIO (GPIO_IN)', 0xD0000004, None), ('SIO (GPIO_OUT_XOR)', 0xD000001C, 0),
    ('PPB (SYST_RVR)', 0xE000E014, None),
]
REPEATS = 32


def probes():
    """{address: why} for the probe list."""
    out = {}

    def add(a, why):
        a &= 0xFFFFFFFC
        out.setdefault(a, why)
    for name, base, size, kind in REGIONS:
        for a, w in ((base, 'first'), (base + size // 2, 'middle'), (base + size - 4, 'last'), (base + size, 'past')):
            add(a, f"{name} {w}")
    try:
        from .. import svd
        for pname, p in svd.peripherals().items():
            regs = sorted(r['offset'] for r in p['registers'])
            if not regs:
                continue
            b = p['base']
            for a, w in ((b + regs[0], 'first register'), (b + regs[-1], 'last register'),
                         (b + regs[-1] + 4, 'past its registers'), (b + 0xFFC, 'end of its 4 KB')):
                add(a, f"{pname} {w}")
            if b < 0xD0000000:
                for k in (1, 2, 3):
                    add(b + k * 0x1000 + regs[0], f"{pname} {('XOR', 'SET', 'CLR')[k - 1]} alias")
    except FileNotFoundError:
        pass
    for a in safety.HOLES:
        add(a, 'a hole (proposal §6.A)')
    for a in range(0xD0000180, 0xD0000200, 0x20):
        add(a, 'past the SIO')
    return out


def _can_read(a, flashed=False):
    try:
        safety.check_read(a, flashed=flashed)
        return True
    except safety.SafetyError:
        return False


def core_cases(addresses):
    cases, n = [], 0
    for a in sorted(addresses):
        if not _can_read(a):
            continue
        for width in (4, 2, 1):
            cases.append(ldr_probe(a, width, LAYOUT, id=n, target_addr=a, access_width=width))
            n += 1
    for a in RAM_WRITE_OK + [0x00000100, 0x00003FFC] + [x for x in safety.HOLES]:
        for width in (4, 2, 1):
            try:
                safety.check_write(a, width, 0xA5)
            except safety.SafetyError:
                continue
            cases.append(ldr_probe(a, width, LAYOUT, write={4: 0xA5C3965A, 2: 0x965A, 1: 0x5A}[width],
                                   id=n, target_addr=a, access_width=width))
            n += 1
    return cases


def timing_cases():
    cases = [timing_case(0, b'', layout=LAYOUT)]                          # calibration
    n = 1
    for name, addr, store in TIMED:
        for width, (ld, st) in ((4, (0x6808, 0x6008)), (2, (0x8808, 0x8008)), (1, (0x7808, 0x7008))):
            if not _can_read(addr):
                continue
            cases.append(timing_case(n, h(*[ld] * REPEATS), regs=[0, addr], layout=LAYOUT, region=name,
                                     width=width, op='load', reads=[addr]))
            n += 1
            if store is not None:
                cases.append(timing_case(n, h(*[st] * REPEATS), regs=[store & ((1 << (8 * width)) - 1), addr],
                                         layout=LAYOUT, region=name, width=width, op='store',
                                         writes=[(addr, width, store)]))
                n += 1
    for site, (lo, hi) in SITES.items():
        for off in (0x100, 0x102):
            slot = (lo + hi) // 2 + off - 0x100
            cases.append(timing_case(n, h(*[0xBF00] * REPEATS), layout=LAYOUT, slot=slot, region=site,
                                     op='fetch', align='word' if off == 0x100 else 'halfword'))
            n += 1
            cases.append(timing_case(n, b'', layout=LAYOUT, slot=slot, region=site, op='calibration'))
            n += 1
    return cases


def dry_cases(ctx):
    return [('core-probes', core_cases(probes()), {'layout': LAYOUT, 'max_cases': MAX_CASES}),
            ('timing', timing_cases(), {'layout': LAYOUT, 'max_cases': MAX_CASES,
                                        'sites': list(SITES.values())})]


# ── the debugger's part ────────────────────────────────────────────────────

def debugger_read(t, a):
    """'0x...' value, 'error' (a DAP fault) or 'hang' (no answer; reconnected)."""
    from ...hwtest.rsp import RSPError
    try:
        v = t.rsp.read32(a)
    except RSPError as e:
        if 'in time' in str(e) or 'connection' in str(e) or 'out of step' in str(e):
            t.reconnect()
            return 'hang'
        return 'error'
    if getattr(t, 'check_link', None):
        t.check_link()           # a dead debug port reads as 0: stop rather than record it
    return f"0x{v:08X}"


def debugger_write(t, a, v):
    """'ok', 'error' (the bus refused it: a DAP fault, OpenOCD's E0E) or
    'hang' (no answer; reconnected, which resets the chip)."""
    from ...hwtest.rsp import RSPError
    try:
        t.rsp.write32(a, v)
    except RSPError as e:
        if 'in time' in str(e) or 'connection' in str(e) or 'out of step' in str(e):
            t.reconnect()
            return 'hang'
        return 'error'
    if getattr(t, 'check_link', None):
        t.check_link()
    return 'ok'


def gap_scan(t, base, side):
    """Every word of a 4 KB window: {'values': {offset: value}, 'errors': [...]}."""
    vals, errs = {}, []
    a = base
    while a < base + 0x1000:
        if a in side:
            a += 4
            continue
        end = a
        while end < base + 0x1000 and end not in side and end - a < 0x400:
            end += 4
        try:
            data = t.rsp.read_mem(a, end - a)
            for i in range(0, end - a, 4):
                v = int.from_bytes(data[i:i + 4], 'little')
                if v:
                    vals[a + i - base] = v
        except Exception:                                # noqa: BLE001 - split and retry word by word
            for w in range(a, end, 4):
                r = debugger_read(t, w)
                if r in ('error', 'hang'):
                    errs.append(w - base)
                elif int(r, 16):
                    vals[w - base] = int(r, 16)
        a = end
    return vals, errs


def sram_structure(t, log):
    """The striping function, from tags written through the non-striped
    aliases into free SRAM, read back through the striped window."""
    r = t.rsp
    k0 = 0x3000                                          # word 0x3000 of each bank: striped 0x20030000+
    saved = {b: r.read_mem(0x21000000 + b * 0x10000 + 4 * k0, 64) for b in range(4)}
    for b in range(4):
        r.write_mem(0x21000000 + b * 0x10000 + 4 * k0,
                    b''.join(((0xB0000000 | (b << 16) | k).to_bytes(4, 'little')) for k in range(16)))
    striped = r.read_mem(0x20030000, 256)
    seen = [int.from_bytes(striped[i:i + 4], 'little') for i in range(0, 256, 4)]
    for b in range(4):
        r.write_mem(0x21000000 + b * 0x10000 + 4 * k0, saved[b])
    mapping = []
    ok = True
    for i, v in enumerate(seen):
        a = 0x20030000 + 4 * i
        bank, word = (v >> 16) & 0xF, v & 0xFFFF
        mapping.append({'striped': f"0x{a:08X}", 'bank': bank if v >> 28 == 0xB else None,
                        'word': (k0 + word) if v >> 28 == 0xB else None})
        if v >> 28 != 0xB or bank != (a >> 2) & 3 or k0 + word != ((a - 0x20000000) >> 4):
            ok = False
    rule = ("striped address A is bank (A >> 2) & 3, word (A - 0x20000000) >> 4 of that bank "
            "(non-striped 0x21000000 + bank * 0x10000 + word * 4)")
    out = {'rule': rule, 'confirmed': ok, 'sample': mapping[:16]}
    # SRAM4/5 and what is past them
    for a in (0x20042000, 0x20042FFC, 0x20043000, 0x20080000, 0x21040000, 0x21040FFC):
        out.setdefault('past', {})[f"0x{a:08X}"] = debugger_read(t, a)
    log(f"SRAM striping: {'as the datasheet says' if ok else 'NOT as predicted'}")
    return out


def hole_writes(t):
    """A tag written through the debugger to each hole: does it read back,
    and does it show up at the start of SRAM (a mirror)?"""
    out = {}
    r = t.rsp
    before = r.read_mem(0x20000000, 16)
    for a in safety.HOLES:
        if a < 0x4000:
            continue
        try:
            r.write32(a, 0x5EED0000 | (a >> 20))
            w = 'written'
        except Exception:                               # noqa: BLE001
            w = 'error'
        rb = debugger_read(t, a)
        mirror = r.read_mem(0x20000000, 16) != before
        out[f"0x{a:08X}"] = {'write': w, 'read_back': rb, 'mirrors_sram_start': mirror}
        if mirror:
            r.write_mem(0x20000000, before)
    return out


# ── the campaign ────────────────────────────────────────────────────────────

def run(ctx):
    from .. import facts as FX
    from .. import runner as R
    t = ctx.t
    ctx.status(NAME, 'partial', step='debugger probes')
    t.reset_halt()
    t.vector_catch('none')
    release(t, IO_PADS)
    probe = probes()
    dbg = {}
    for a in sorted(probe):
        if a >= 0xD0000000 and a < 0xE0000000:
            dbg[a] = 'not reachable (SIO)'
        elif not _can_read(a):
            dbg[a] = 'not read (side effect or flash)'
        else:
            dbg[a] = debugger_read(t, a)
    hung = {a for a, v in dbg.items() if v == 'hang'}
    side = set(safety.read_side_effects())
    gaps = {}
    try:
        from .. import svd
        for pname, p in svd.peripherals().items():
            b = p['base']
            if b >= 0xD0000000 or b < 0x40000000 or not _can_read(b) or b in hung or \
                    any(safety._in(b, lo, hi) for _, lo, hi, _ in safety.NEVER_RANGES if lo >= 0x50000000):
                continue
            vals, errs = gap_scan(t, b, side)
            reg_offs = {r['offset'] for r in p['registers']}
            gaps[pname] = {'nonzero_outside_registers': {f"0x{o:03X}": f"0x{v:08X}" for o, v in vals.items()
                                                         if o not in reg_offs},
                           'errors': len(errs), 'registers': len(reg_offs)}
    except FileNotFoundError:
        ctx.log("no Pico SDK: the gap scan is skipped")
    ctx.status(NAME, 'partial', step='SRAM structure')
    try:
        sram = sram_structure(t, ctx.log)
    except Exception as e:                                # noqa: BLE001 - recorded, not fatal
        sram = {'confirmed': None, 'error': str(e)}
    holes = hole_writes(t)
    # the core's part
    ctx.status(NAME, 'partial', step='core probes')
    hook = lambda tt: release(tt, IO_PADS)                # noqa: E731
    cases = [c for c in core_cases(probe) if c['target_addr'] not in hung]
    recs, state = ctx.stage(NAME, 'core-probes', cases, layout=LAYOUT, max_cases=MAX_CASES,
                            flags=F.HF_NO_SCRATCH, on_prepare=hook)
    core = {}
    for c, r in recs:
        end = R.ending(c, r, LAY)[0]
        a, width = c['r1'], {0x78: 1, 0x88: 2, 0x68: 4, 0x70: 1, 0x80: 2, 0x60: 4}[c['code_bytes'][1]]
        kind = 'write' if c['kind'] == F.KINDS['probe-write'] else 'read'
        if end == 'fell-through':
            v = r['f_r2'] if kind == 'write' else r['f_r0']
            res = {'ok': True, 'value': f"0x{v:0{2 * width}X}"}
            if kind == 'write':
                res['stored'] = v == c['r0']
        else:
            res = {'ok': False, 'ended': end}
        core.setdefault(f"0x{a:08X}", {})[f"{kind}{8 * width}"] = res
    tim_recs, tstate = ctx.stage(NAME, 'timing', timing_cases(), layout=LAYOUT, max_cases=MAX_CASES,
                                 sites=list(SITES.values()), timing=True, on_prepare=hook)
    timing = _timing_table(tim_recs)
    regions = _regions(dbg, core)
    fs = [FX.fact(f"memmap.{k}", v) for k, v in (
        ('sram.striping_as_datasheet', sram['confirmed']),
        ('debugger.hangs', len(hung)),
        ('probes', len(probe)))]
    for row in timing:
        fs.append(FX.fact(f"timing.access.{row['region']}.{row['op']}{row.get('width', '')}",
                          row['per_access'], unit='cycles', raw=row['raw']))
    prov = ctx.provenance(state, campaign='A')
    path = ctx.write_facts('memory_map.json', 'memory_map', prov, fs, regions=regions,
                           probes={f"0x{a:08X}": {'why': probe[a], 'debugger': dbg[a],
                                                 'core': core.get(f"0x{a:08X}")} for a in sorted(probe)},
                           sram=sram, holes=holes, gap_scan=gaps, timing=timing,
                           core_writes={k: v for k, v in core.items() if any(x.startswith('write') for x in v)})
    ctx.status(NAME, 'done', facts=path, differences=None)
    return path


def _timing_table(recs):
    from .. import runner as R
    over = {}
    rows = []
    for c, r in recs:
        if c['code_len'] == 4 and r['ipsr'] == 3:
            over[c['slot']] = (r['r6'] - r['r5']) % (1 << 24)
    base_over = next(iter(over.values()), None)
    for c, r in recs:
        if c['code_len'] == 4 or R.ending(c, r, LAY)[0] != 'fell-through':
            continue
        raw = (r['r6'] - r['r5']) % (1 << 24)
        ov = over.get(c['slot'], base_over)
        body = c['code_bytes'][2:-2]
        hw1 = int.from_bytes(body[:2], 'little')
        op = 'fetch' if hw1 == 0xBF00 else ('store' if hw1 & 0x0800 == 0 else 'load')
        width = {0x68: 4, 0x60: 4, 0x88: 2, 0x80: 2, 0x78: 1, 0x70: 1}.get(body[1])
        rows.append({'slot': f"0x{c['slot']:08X}", 'address': f"0x{c['r1']:08X}" if op != 'fetch' else None,
                     'region': _region_name(c, op), 'op': op, 'width': None if op == 'fetch' else 8 * width,
                     'raw': raw, 'overhead': ov,
                     'per_access': None if ov is None else round((raw - ov) / REPEATS, 3)})
    return rows


def _region_name(c, op):
    if op == 'fetch':
        for name, (lo, hi) in SITES.items():
            if lo <= c['slot'] < hi:
                return f"{name}{'' if c['slot'] % 4 == 0 else '-halfword'}"
    for name, addr, _ in TIMED:
        if addr == c['r1']:
            return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return f"0x{c['r1']:08X}"


def _regions(dbg, core):
    """One row per addressmap region: how it answered."""
    out = []
    for name, base, size, kind in REGIONS:
        row = {'name': name, 'base': f"0x{base:08X}", 'size': size, 'kind': kind}
        first = f"0x{base:08X}"
        row['debugger'] = dbg.get(base)
        row['core'] = core.get(first)
        row['past_end'] = {'debugger': dbg.get(base + size), 'core': core.get(f"0x{base + size:08X}")}
        out.append(row)
    return out
