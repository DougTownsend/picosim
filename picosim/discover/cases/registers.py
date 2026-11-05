"""
Campaign C: every register's reset value and real writable bits.

The prediction is the Pico SDK's RP2040.svd (svd.py); the chip decides.

States read:
  S1  after `reset halt`, with IO_BANK0 and PADS_BANK0 released, as Testing
      starts (the debugger reads everything it can reach; the SIO, which it
      cannot, is read by the core)
  S3  after every allowed block has been put through RESETS and released:
      pure reset values
  S2  comes from campaign F (the flashed program's state)

Registers whose read has a side effect (safety.read_side_effects(): the
SVD's readAction, the headers' RF/RWF, and the hand list) are never read
here; campaign G tests them on purpose.

Writable masks, on the allowlist only: 0, 0xFFFFFFFF, 0x55555555 and
0xAAAAAAAA are written through the normal address, and set/cleared/toggled
through the XOR, SET and CLR aliases (not in the PPB or SIO, which have no
aliases: there +0x1000 is another register), read back each time, and the
original value is restored.  The Pico W's GP23/24/25/29 CTRL and PADS
registers, RESETS, ICSR (it can pend an NMI) and the debug registers are
left out; IO_BANK0 CTRL patterns keep FUNCSEL at NULL and OEOVER off, and
SIO output enables are limited to --pins.
Output: registers.json.
"""

from .. import formats as F
from .. import safety
from .. import svd
from ..campaign import release, IO_PADS, h, R_make
from .memmap import debugger_read

NAME, TITLE, AGENT_BASED = 'registers', 'Registers: reset values and writable bits', True
PATTERNS = (0x00000000, 0xFFFFFFFF, 0x55555555, 0xAAAAAAAA)
ALLOWED_BLOCKS = ('IO_BANK0', 'PADS_BANK0', 'TIMER', 'PWM', 'UART0', 'UART1', 'SPI0', 'SPI1', 'I2C0', 'I2C1',
                  'ADC', 'RTC', 'BUSCTRL')
RESET_BITS = {'ADC': 0, 'BUSCTRL': 1, 'I2C0': 3, 'I2C1': 4, 'IO_BANK0': 5, 'PADS_BANK0': 8, 'PWM': 14,
              'RTC': 15, 'SPI0': 16, 'SPI1': 17, 'TIMER': 21, 'UART0': 22, 'UART1': 23}
SIO_SWEEP = ('GPIO_OUT', 'GPIO_HI_OUT', 'GPIO_OE', 'INTERP0_ACCUM0', 'INTERP0_ACCUM1', 'INTERP0_BASE0',
             'INTERP0_BASE1', 'INTERP0_BASE2', 'INTERP0_CTRL_LANE0', 'INTERP0_CTRL_LANE1', 'INTERP1_ACCUM0',
             'INTERP1_ACCUM1', 'INTERP1_BASE0', 'INTERP1_BASE1', 'INTERP1_BASE2', 'INTERP1_CTRL_LANE0',
             'INTERP1_CTRL_LANE1', 'DIV_UDIVIDEND', 'DIV_UDIVISOR', 'DIV_SDIVIDEND', 'DIV_SDIVISOR')
PPB_SKIP = {0xE000ED04, 0xE000ED0C, 0xE000EDF0, 0xE000EDF4, 0xE000EDF8, 0xE000EDFC}


def _side():
    return set(safety.read_side_effects())


def readable(r, side):
    return r['addr'] not in side and r['access'] != 'write-only' and r['periph'] not in ('SSI',) \
        and not (0x10000000 <= r['addr'] < 0x14000000)


def sio_regs():
    return [r for r in svd.peripherals()['SIO']['registers']]


def adjust(r, value, pins):
    """The pattern made safe for this register, or None to skip it."""
    a = r['addr']
    if r['periph'] == 'IO_BANK0' and r['name'].endswith('_CTRL') and r['name'].startswith('GPIO'):
        pin = int(r['name'][4:-5])
        if pin in safety.PICO_W_PINS:
            return None
        if pin not in pins:
            return (value & ~0x301F) | 0x1F            # FUNCSEL NULL, OEOVER normal
    if r['periph'] == 'PADS_BANK0' and r['name'].startswith('GPIO'):
        pin = int(r['name'][4:])
        if pin in safety.PICO_W_PINS:
            return None
    if r['periph'] == 'SIO' and r['name'] in ('GPIO_OE',):
        return value & sum(1 << p for p in pins)
    try:
        safety.check_write(a, 4, value, pins)
    except safety.SafetyError:
        return None
    return value


def fixed_bits(r, pins):
    """Bits adjust() pins for safety (they are not tested)."""
    if r['periph'] == 'IO_BANK0' and r['name'].startswith('GPIO') and r['name'].endswith('_CTRL') \
            and int(r['name'][4:-5]) not in pins:
        return 0x301F
    if r['periph'] == 'SIO' and r['name'] == 'GPIO_OE':
        return ~sum(1 << p for p in pins) & 0xFFFFFFFF
    return 0


def sweep_list(pins):
    out = []
    for r in svd.registers():
        if r['periph'] in ALLOWED_BLOCKS or (r['periph'] == 'PPB' and r['addr'] not in PPB_SKIP):
            if r['access'] == 'read-only' or r['addr'] in _side():
                continue
            if r['periph'] == 'BUSCTRL' and not r['name'].startswith('PERF'):
                continue
            if adjust(r, 0, pins) is None:
                continue
            out.append(r)
    return out


def core_read_cases():
    """The SIO's registers, read by the core (the debugger cannot)."""
    side = _side()
    from ..campaign import ldr_probe
    return [ldr_probe(r['addr'], 4, id=i, reg=r['name']) for i, r in enumerate(sio_regs())
            if r['addr'] not in side and r['access'] != 'write-only']


def core_write_cases(pins):
    """SIO write masks: store a pattern, load it back (r2), then restore r3."""
    cases, n = [], 0
    by = {r['name']: r for r in sio_regs()}
    for name in SIO_SWEEP:
        r = by.get(name)
        if r is None:
            continue
        for p in PATTERNS:
            v = adjust(r, p, pins)
            if v is None:
                continue
            # str r0,[r1]; ldr r2,[r1]; str r3,[r1] (r3 = 0: the reset value of every swept SIO register)
            cases.append(R_make(n, h(0x6008, 0x680A, 0x600B), kind='probe-write', regs=[v, r['addr'], 0, 0],
                                writes=[(r['addr'], 4, v), (r['addr'], 4, 0)], reg=name, pattern=p))
            n += 1
    return cases


def dry_cases(ctx):
    return [('sio-read', core_read_cases(), {}), ('sio-masks', core_write_cases(ctx.pins), {})]


def read_all(t, regs, side):
    out = {}
    for r in regs:
        if r['periph'] == 'SIO':
            continue
        out[r['addr']] = debugger_read(t, r['addr']) if readable(r, side) else 'not read'
    return out


def mask_sweep(t, r, pins, log):
    """{'normal': {pattern: read}, 'xor'/'set'/'clr': ..., 'writable', 'fixed1'}"""
    a = r['addr']
    rd = lambda: _v(debugger_read(t, a))                        # noqa: E731
    orig = rd()
    if orig is None:
        return {'error': 'not readable'}
    res = {'original': f"0x{orig:08X}", 'normal': {}}
    seen = {}
    try:
        for p in PATTERNS:
            v = adjust(r, p, pins)
            if v is None:
                continue
            t.rsp.write32(a, v)
            got = rd()
            res['normal'][f"0x{p:08X}"] = None if got is None else f"0x{got:08X}"
            seen[v] = got
        if 0 in seen and 0xFFFFFFFF in seen and None not in (seen[0], seen[0xFFFFFFFF]):
            z, o = seen[0], seen[0xFFFFFFFF]
            res['writable'] = f"0x{(~z & o) & 0xFFFFFFFF:08X}"
            res['fixed1'] = f"0x{z & o:08X}"
        res['tested'] = f"0x{~fixed_bits(r, pins) & 0xFFFFFFFF:08X}"
        if r['periph'] != 'PPB' and r['periph'] != 'SIO':
            for k, alias in ((1, 'xor'), (2, 'set'), (3, 'clr')):
                p = adjust(r, 0x55555555, pins)
                base = adjust(r, 0 if alias != 'clr' else 0xFFFFFFFF, pins)
                if p is None or base is None:
                    continue
                p &= ~fixed_bits(r, pins)
                try:
                    safety.check_write(a + k * 0x1000, 4, p, pins)
                except safety.SafetyError:
                    continue
                t.rsp.write32(a, base)
                before = rd()
                t.rsp.write32(a + k * 0x1000, p)
                after = rd()
                if before is None or after is None:
                    res[alias] = 'error'
                    continue
                want = {'xor': before ^ p, 'set': before | p, 'clr': before & ~p}[alias]
                w = int(res.get('writable', '0xFFFFFFFF'), 16)
                res[alias] = 'works' if (after & w) == (want & w) else \
                    ('ignored' if after == before else f"0x{before:08X} -> 0x{after:08X}")
    finally:
        try:
            t.rsp.write32(a, orig)
        except Exception as e:                                  # noqa: BLE001
            log(f"could not restore 0x{a:08X}: {e}")
    return res


def _v(s):
    return int(s, 16) if isinstance(s, str) and s.startswith('0x') else None


def run(ctx):
    from .. import facts as FX
    from .. import runner as R
    t = ctx.t
    side = _side()
    regs = svd.registers()
    with open_side_file(ctx, side) as path:
        ctx.log(f"read side effects: {len(side)} registers listed in {path}")
    ctx.status(NAME, 'partial', step='S1')
    t.reset_halt()
    t.vector_catch('none')
    release(t, IO_PADS)
    s1 = read_all(t, regs, side)
    # SIO through the core (S1)
    recs, state = ctx.stage(NAME, 'sio-read', core_read_cases(), flags=F.HF_NO_SCRATCH,
                            on_prepare=lambda tt: release(tt, IO_PADS))
    sio_s1 = {}
    for c, r in recs:
        if R.ending(c, r, F.LAYOUTS['hi'])[0] == 'fell-through':
            sio_s1[c['r1']] = f"0x{r['f_r0']:08X}"
        else:
            sio_s1[c['r1']] = 'fault'
    # S3: every allowed block through reset and out
    ctx.status(NAME, 'partial', step='S3')
    mask = sum(1 << RESET_BITS[b] for b in ALLOWED_BLOCKS)
    t.rsp.write32(0x4000E000, mask)                     # SET: into reset
    release(t, mask)
    s3 = read_all(t, regs, side)
    # write masks
    ctx.status(NAME, 'partial', step='write masks')
    masks = {}
    for r in sweep_list(ctx.pins):
        masks[r['addr']] = mask_sweep(t, r, ctx.pins, ctx.log)
    t.rsp.write32(0xE000E180, 0xFFFFFFFF)               # nothing left enabled or pending in the NVIC
    t.rsp.write32(0xE000E280, 0xFFFFFFFF)
    recs, _ = ctx.stage(NAME, 'sio-masks', core_write_cases(ctx.pins), flags=F.HF_NO_SCRATCH,
                        on_prepare=lambda tt: release(tt, IO_PADS))
    for c, r in recs:
        m = masks.setdefault(c['r1'], {'normal': {}})
        ok = R.ending(c, r, F.LAYOUTS['hi'])[0] == 'fell-through'
        m['normal'][f"0x{c['r0']:08X}"] = f"0x{r['f_r2']:08X}" if ok else 'fault'
    for a, m in masks.items():
        n = m.get('normal', {})
        if 'writable' not in m and '0x00000000' in n and '0xFFFFFFFF' in n and \
                all(_v(n[k]) is not None for k in ('0x00000000', '0xFFFFFFFF')):
            z, o = _v(n['0x00000000']), _v(n['0xFFFFFFFF'])
            m['writable'], m['fixed1'] = f"0x{(~z & o) & 0xFFFFFFFF:08X}", f"0x{z & o:08X}"
    # the table, with the SVD's prediction beside each measurement
    rows, diffs = [], 0
    for r in regs:
        a = r['addr']
        v1 = sio_s1.get(a) if r['periph'] == 'SIO' else s1.get(a)
        v3 = s3.get(a)
        row = {'periph': r['periph'], 'name': r['name'], 'addr': f"0x{a:08X}",
               'svd_reset': f"0x{r['reset']:08X}", 'svd_access': r['access'],
               'svd_writable': f"0x{svd.predicted_mask(r):08X}", 'S1': v1, 'S3': v3,
               'side_effect_read': a in side}
        if a in masks:
            row['masks'] = masks[a]
        d = []
        defined = svd.defined_mask(r) or 0xFFFFFFFF
        for st, v in (('S3', v3), ('S1', v1)):
            if _v(v) is not None and (_v(v) & defined) != (r['reset'] & defined):
                d.append(f"{st} reset value 0x{_v(v):08X}, SVD 0x{r['reset']:08X}")
                break
        wm = _v(masks.get(a, {}).get('writable'))
        tested = _v(masks.get(a, {}).get('tested')) or 0xFFFFFFFF
        if wm is not None and (wm & tested) != (svd.predicted_mask(r) & tested):
            d.append(f"writable 0x{wm & tested:08X}, SVD predicts 0x{svd.predicted_mask(r) & tested:08X}"
                     + (f" (bits 0x{tested:08X} tested)" if tested != 0xFFFFFFFF else ''))
        if d:
            row['differences'] = d
            diffs += 1
        rows.append(row)
    ppb = []
    for name, a, reset in svd.PPB_REGISTERS:
        ppb.append({'name': name, 'addr': f"0x{a:08X}", 'expected': None if reset is None else f"0x{reset:08X}",
                    'S1': s1.get(a) or debugger_read(t, a), 'masks': masks.get(a)})
    fs = [FX.fact('registers.count', len(rows)), FX.fact('registers.differences', diffs),
          FX.fact('registers.read_side_effects', len(side))]
    for name, a, _ in svd.PPB_REGISTERS:
        m = masks.get(a)
        if m and m.get('writable'):
            fs.append(FX.fact(f"registers.ppb.{name}.writable", m['writable']))
    prov = ctx.provenance(state, campaign='C')
    path = ctx.write_facts('registers.json', 'registers', prov, fs, registers=rows, ppb=ppb)
    ctx.status(NAME, 'done', facts=path, differences=diffs)
    return path


class open_side_file:
    """Writes hwfacts/read_side_effects.json (the list C skips and G tests)."""

    def __init__(self, ctx, side):
        self.ctx, self.side = ctx, side

    def __enter__(self):
        import json
        import os
        from .. import facts as FX
        p = os.path.join(FX.root(self.ctx.kind), 'read_side_effects.json')
        full = safety.read_side_effects()
        with open(p, 'w') as f:
            json.dump({f"0x{a:08X}": why for a, why in sorted(full.items())}, f, indent=1)
        return p

    def __exit__(self, *a):
        return False
