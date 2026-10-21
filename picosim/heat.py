"""
How much each part of the CPU is used: the data behind the heat map.

A component is *used* in a clock cycle when the multi-cycle FSM asserts a
control signal that makes it do something: a register is loaded, a buffer
drives the bus, memory is enabled, a mux's select is set, the ALU computes.
The rules mirror the CPU diagram (gui/scripts/diagram_view.gd), so the heat
map and the diagram light up the same boxes.  Registers count accesses
through the register file's ports: a read is an SR1 / SR2 port read (or the
PC read by fetch and PC-relative addressing), a write is a DR port write.

Which components an instruction uses depends only on its encoding (and, for
B<cond>, on whether it was taken), so every encoding is clocked once on a
scratch core and multiplied by how often it ran.  That keeps the counts
exact without slowing a run down.  The one data-dependent split, memory
accesses to RAM versus I/O, comes from the core's per-address profile.

The 2-stage pipeline (RP2040 Cortex-M0+) has no control signals in picosim:
its usage is an estimate from the datasheet-timed stage model (see
pipeline_usage) and is marked so.
"""

from functools import lru_cache

from ._picosim_core import CPUCore
from . import timing

# ── the components ──────────────────────────────────────────────────────────
# (id, label, group).  Datapath ids are the CPU diagram's box ids.
DATAPATH = [
    ('fsm', 'FSM (every cycle)', 'control'),
    ('ir', 'IR', 'fetch'), ('ir2', 'IR2', 'fetch'),
    ('pc', 'PC', 'fetch'), ('inc', '+2 incrementer', 'fetch'), ('pcmux', 'PCMUX', 'fetch'),
    ('clrt', 'CLR THUMB', 'fetch'),
    ('ext', 'IMM / OFFSET', 'decode'), ('cond', 'COND EVAL', 'decode'),
    ('align', 'PC / Align(PC,4)', 'address'), ('addr1mux', 'ADDR1MUX', 'address'),
    ('addr2mux', 'ADDR2MUX', 'address'), ('adder', 'Address adder', 'address'),
    ('mar', 'MAR', 'memory'), ('marinc', 'MAR +4', 'memory'), ('mem', 'Memory / I-O', 'memory'),
    ('mdr', 'MDR', 'memory'), ('loadext', 'LOAD EXT', 'memory'), ('storealign', 'STORE ALIGN', 'memory'),
    ('regfile', 'Register file', 'execute'), ('sett', 'SET THUMB', 'execute'),
    ('sr2mux', 'SR2MUX', 'execute'), ('alu_a', 'ALU A latch', 'execute'), ('alu_b', 'ALU B latch', 'execute'),
    ('alu', 'ALU', 'execute'), ('flags', 'Flags NZCV', 'execute'), ('vec', 'VECTOR ADDR', 'execute'),
    ('bus', 'Bus (any driver)', 'bus'),
    ('gatePC', 'gatePC', 'bus'), ('gateAdder', 'gateAdder', 'bus'), ('gateMDR', 'gateMDR', 'bus'),
    ('gateALU', 'gateALU', 'bus'), ('gateVector', 'gateVector', 'bus'), ('gateSys', 'gateSys', 'bus'),
]
# memory accesses split by where they went (totals and per line only)
MEM_SPLIT = [('mem:fetch', 'Memory: instruction fetch', 'memory split'),
             ('mem:ram', 'Memory: RAM data', 'memory split'),
             ('mem:sio', 'Memory: SIO (GPIO)', 'memory split'),
             ('mem:apb', 'Memory: APB peripherals', 'memory split'),
             ('mem:ahb', 'Memory: other AHB', 'memory split')]
REG_NAMES = ['R%d' % i for i in range(13)] + ['SP', 'LR', 'PC']
REGISTERS = ([(f'{r}:r', f'{r} reads', 'register reads') for r in REG_NAMES] +
             [(f'{r}:w', f'{r} writes', 'register writes') for r in REG_NAMES])
FLAGS = ([(f'{f}:r', f'{f} reads', 'flags') for f in 'NZCV'] +
         [(f'{f}:w', f'{f} writes', 'flags') for f in 'NZCV'])
MULTICYCLE = DATAPATH + REGISTERS + FLAGS
IDS = [c[0] for c in MULTICYCLE]
INDEX = {cid: i for i, cid in enumerate(IDS)}

PIPELINE = [
    ('p_pc', 'PC', 'fetch'), ('p_inc', '+2/+4 incrementer', 'fetch'), ('p_pcmux', 'PC mux', 'fetch'),
    ('p_imem', 'Instruction memory', 'fetch'), ('p_ifex', 'IF/EX register', 'fetch'),
    ('p_decode', 'Decode', 'execute'), ('p_regfile', 'Register file', 'execute'),
    ('p_alu', 'ALU / multiplier', 'execute'), ('p_flags', 'Flags', 'execute'),
    ('p_resolve', 'Branch resolve', 'execute'), ('p_dmem', 'Data memory / I-O', 'execute'),
    ('p_refill', 'Refill (wasted fetch)', 'stall'),
]
# The pipeline model also counts the program's register and flag accesses
# (the same instructions read and write the same registers on either
# machine); the PC is the pipeline's own p_pc block.
PIPE_ROWS = PIPELINE + [r for r in REGISTERS if not r[0].startswith('PC:')] + FLAGS
PIPE_IDS = [c[0] for c in PIPE_ROWS]
PIPE_INDEX = {cid: i for i, cid in enumerate(PIPE_IDS)}


def table(model='multicycle'):
    """[{'id', 'label', 'group'}] for the GUI and the exports."""
    rows = PIPE_ROWS if model == 'm0plus' else MULTICYCLE + MEM_SPLIT
    return [{'id': i, 'label': l, 'group': g} for i, l, g in rows]


# ── which flags an instruction reads and writes ─────────────────────────────

_FLAG_WRITES = {'ADD': 'NZCV', 'SUB': 'NZCV', 'ADC': 'NZCV', 'SBC': 'NZCV', 'CMP': 'NZCV',
                'CMN': 'NZCV', 'NEG': 'NZCV', 'LSL': 'NZC', 'LSR': 'NZC', 'ASR': 'NZC',
                'ROR': 'NZC'}            # every other flag-setting op writes N and Z
_COND_READS = {'EQ': 'Z', 'NE': 'Z', 'CS': 'C', 'HS': 'C', 'CC': 'C', 'LO': 'C', 'MI': 'N',
               'PL': 'N', 'VS': 'V', 'VC': 'V', 'HI': 'CZ', 'LS': 'CZ', 'GE': 'NV',
               'LT': 'NV', 'GT': 'ZNV', 'LE': 'ZNV', 'AL': ''}


def cycle_components(state, signals, wide=False, cond=None):
    """The component ids one clock cycle uses (a list: a register read on
    both ports counts twice).  `cond` is the instruction's condition code,
    as named by DECODE's COND= signal; `wide` marks a 32-bit encoding."""
    sig = list(signals)
    s = set(sig)

    def pre(p):
        return [x[len(p):] for x in sig if x.startswith(p)]

    out = ['fsm']
    if state == 'DECODE':
        # decoding reads IR (and IR2) and extracts the immediates; the
        # register and ALU selects it lists only take effect later
        out += ['ir', 'ext'] + (['ir2'] if wide else [])
        return out
    if state == 'FETCH2_ADDR':
        out.append('ir')                         # the FSM sees a 32-bit first half
    gates = {'GatePC': 'gatePC', 'GateADDR': 'gateAdder', 'GateMDR': 'gateMDR',
             'GateALU': 'gateALU', 'GateVEC': 'gateVector', 'GateSYS': 'gateSys'}
    driven = [g for x, g in gates.items() if x in s]
    if driven:
        out.append('bus')
        out += driven
    if 'GatePC' in s:
        out += ['pc', 'PC:r']
    if 'GateADDR' in s:
        out.append('adder')
    if 'GateMDR' in s:
        out += ['mdr', 'loadext']
    if 'GateALU' in s:
        out.append('alu')
    if 'GateVEC' in s:
        out.append('vec')
    if 'LD.MAR' in s or 'MEM.EN' in s or 'MAR+4' in s:
        out.append('mar')
    if 'MAR+4' in s:
        out.append('marinc')
    if 'MEM.EN' in s:
        out.append('mem')
    if 'LD.MDR' in s or 'R/W=WRITE' in s or 'R/W=READ' in s:
        out.append('mdr')
    if 'STORE ALIGN' in s or ('LD.MDR' in s and 'GateALU' in s):
        out.append('storealign')
    if 'LD.IR' in s:
        out.append('ir')
    if 'LD.IR2' in s:
        out.append('ir2')
    if pre('PCINC='):
        out += ['inc', 'pc']
        if 'GatePC' not in s:
            out.append('PC:r')
    if pre('PCMUX='):
        out.append('pcmux')
    if 'LD.PC' in s:
        out += ['pc', 'PC:w']
    if 'PCMUX=BUS' in s or 'CLR THUMB BIT' in s:
        out.append('clrt')
    a1 = pre('ADDR1MUX=')
    if a1:
        out += ['addr1mux', 'adder']
        if a1[0] in ('PC', 'Align(PC,4)'):
            out.append('align')
            if 'GatePC' not in s and not pre('PCINC='):
                out.append('PC:r')
    a2 = pre('ADDR2MUX=')
    if a2:
        out += ['addr2mux', 'adder']
        if a2[0][:1] in ('Z', 'S'):
            out.append('ext')
    if 'SR2MUX=IMM' in s:
        out += ['ext', 'sr2mux']
    if pre('SR2=') and state == 'FETCH_OPERANDS':
        out.append('sr2mux')
    if 'LD.ALUA' in s:
        out.append('alu_a')
    if 'LD.ALUB' in s:
        out.append('alu_b')
    alu = pre('ALUK=')
    if alu:
        out.append('alu')
    if 'SET THUMB BIT' in s:
        out.append('sett')
    if 'BranchTaken=0' in s or 'BranchTaken=1' in s:
        out += ['cond', 'flags'] + [f + ':r' for f in _COND_READS.get(cond or '', '')]
    if 'LD.CC' in s:
        out.append('flags')
        out += [f + ':w' for f in _FLAG_WRITES.get(alu[0] if alu else '', 'NZ')]
    if alu and alu[0] in ('ADC', 'SBC') and state == 'EXECUTE_COMMIT':
        out.append('C:r')
    # the register file's ports
    reads = pre('SR1=') + pre('SR2=') + pre('SR=')
    writes = []
    dr = pre('DR=')
    if 'LD.REG' in s and dr:
        writes.append(dr[0])
    if 'LD.SP' in s:
        writes.append('SP')
    if 'LD.LR' in s:
        writes.append('LR')
    if reads or writes or 'SET THUMB BIT' in s:
        out.append('regfile')
    out += [r + ':w' for r in writes]
    # each component once per cycle, except a register read through both
    # ports (adds r0, r0, r0), which is two reads
    return [c for c in dict.fromkeys(out) if c in INDEX] + \
        [r + ':r' for r in reads if r + ':r' in INDEX]


def _wide(hw1):
    return timing.is_wide(hw1)


def _clock(hw1, hw2, flags=None):
    """Clock one encoding on a scratch core: [(state, [component ids])] per
    cycle and whether a branch was taken (None for non-branches)."""
    c = CPUCore()
    c.strict = False          # the states depend on the encoding alone, not on these values
    c.write16(0x100, hw1)
    c.write16(0x102, hw2)
    for r in range(8):
        c.set_reg(r, 0x400)
    c.set_reg(14, 0x201)
    c.sp = 0x800
    c.pc = 0x100
    if flags is not None:
        c.N, c.Z, c.C, c.V = (flags >> 3) & 1, (flags >> 2) & 1, (flags >> 1) & 1, flags & 1
    wide = _wide(hw1)
    cycles, cond, taken = [], None, None
    while True:
        info = c.step_cycle()
        sig = info['signals']
        for x in sig:
            if x.startswith('COND='):
                cond = x[5:]
            if x == 'BranchTaken=1':
                taken = True
            elif x == 'BranchTaken=0':
                taken = False
        cycles.append((info['state'], cycle_components(info['state'], sig, wide, cond)))
        if info['last'] or c.halted:
            break
    return cycles, taken


@lru_cache(maxsize=None)
def encoding_usage(hw1, hw2=0):
    """Per-cycle component ids of one encoding: {'taken': [...],
    'not_taken': [...]} for B<cond> (the two paths differ), else
    {'always': [...]}.  Each list holds one tuple of ids per clock cycle.
    None when the encoding cannot run (not ARMv6-M)."""
    try:
        cycles, taken = _clock(hw1, hw2)
        if taken is None:
            return {'always': [tuple(ids) for _, ids in cycles]}
        paths = {}
        for flags in range(16):          # find a flag setting for each outcome
            cyc, t = _clock(hw1, hw2, flags)
            paths.setdefault('taken' if t else 'not_taken', [tuple(ids) for _, ids in cyc])
            if len(paths) == 2:
                break
        paths.setdefault('taken', paths.get('not_taken'))       # B<AL>-like: one path
        paths.setdefault('not_taken', paths['taken'])
        return paths
    except (RuntimeError, ValueError):
        return None


def _add(acc, cycles, times):
    for ids in cycles:
        for cid in ids:
            acc[cid] = acc.get(cid, 0) + times


def address_usage(rec):
    """{component id: count} for one address's profile record (all its runs)."""
    u = encoding_usage(rec['hw1'], rec['hw2'])
    acc = {}
    if u is None:
        return acc
    x = rec['execs']
    if 'always' in u:
        _add(acc, u['always'], x)
    else:
        t = rec.get('taken', 0)
        _add(acc, u['taken'], t)
        _add(acc, u['not_taken'], x - t)
    # memory: fetches versus data accesses, and where the data went
    data = [rec['reads'][i] + rec['writes'][i] for i in range(4)]
    mem = acc.get('mem', 0)
    acc['mem:fetch'] = mem - sum(data)
    for i, key in enumerate(('mem:ram', 'mem:sio', 'mem:apb', 'mem:ahb')):
        if data[i]:
            acc[key] = data[i]
    acc['_cycles'] = acc.get('fsm', 0)           # the FSM runs every cycle
    acc['_execs'] = x
    return acc


def totals(profile, code_only=False, model='multicycle'):
    """{component id: count} over the whole run, plus '_cycles' and '_execs'."""
    acc = {}
    for rec in profile:
        if code_only and rec['addr'] < 0x3000:
            continue
        for k, v in address_usage_model(model, rec).items():
            acc[k] = acc.get(k, 0) + v
    return acc


def per_line(profile, source, code_only=False, model='multicycle'):
    """[{'line', 'text', 'addrs', 'usage'}] for each source line that ran:
    the address × component counts folded onto the line that produced them.
    `source` maps an address to (line, text), as Program.source does; the
    built-in OS's lines are named "OS line N"."""
    lines = {}
    for rec in sorted(profile, key=lambda r: r['addr']):
        a = rec['addr']
        if code_only and a < 0x3000:
            continue
        line, text = source.get(a, (0, ''))
        key = ('os' if a < 0x3000 else 'user', line if line else a)
        e = lines.setdefault(key, {'line': line, 'text': text.strip(), 'os': a < 0x3000,
                                   'addrs': [], 'usage': {}})
        e['addrs'].append(a)
        for k, v in address_usage_model(model, rec).items():
            e['usage'][k] = e['usage'].get(k, 0) + v
    return sorted(lines.values(), key=lambda e: (not e['os'], e['addrs'][0]))


# ── the timeline (needs the core's instruction trace) ───────────────────────

def ids(model='multicycle'):
    return PIPE_IDS if model == 'm0plus' else IDS


def set_table(core, model='multicycle'):
    """Give the core the per-cycle component indices of every instruction
    that has run, keyed by its trace code, so heat_timeline can count them.
    For the pipeline a load/store to the SIO takes its 1-cycle path when
    most of that address's accesses went there."""
    entries = []
    index = PIPE_INDEX if model == 'm0plus' else INDEX
    for rec in core.profile():
        sio = rec['reads'][1] + rec['writes'][1] > rec['execs'] // 2
        u = _usage(model, rec, sio)
        if u is None:
            continue
        code = ((rec['addr'] >> 1) & 0x7FFF) << 1
        idx = {k: [[index[c] for c in ids] for ids in cyc] for k, cyc in u.items()}
        if 'always' in idx:
            entries += [(code, idx['always']), (code | 1, idx['always'])]
        else:
            entries += [(code, idx['not_taken']), (code | 1, idx['taken'])]
    core.heat_set_table(entries, len(index))


def timeline(core, start, end, bucket, refresh=True, model='multicycle'):
    """Component use per bucket of `bucket` cycles over cycles [start, end)
    of the traced run: a list of buckets, each a list of len(ids(model))
    counts.  Pipeline cycles are the estimate's cycles."""
    if refresh:
        set_table(core, model)
    flat = core.heat_timeline(start, end, bucket)
    n = len(ids(model))
    return [flat[i:i + n] for i in range(0, len(flat), n)]


# ── the 2-stage pipeline: an estimate ───────────────────────────────────────
# picosim times the Cortex-M0+ from RP2040 datasheet Table 81 but has no
# control signals for it, so its block usage is estimated per instruction:
#   fetch blocks (PC, incrementer, PC mux, instruction memory): once per
#     halfword fetched, in the instruction's first cycle(s), and again in a
#     refill cycle after the PC is redirected;
#   IF/EX register and decode: the first execute cycle;
#   register file: when the instruction reads or writes registers;
#   ALU: ALU and multiply instructions, and the address of a load/store;
#   data memory: one cycle per data access (load/store, PUSH/POP, LDM/STM);
#   flags: when they are read or written; branch resolve: every PC write
#   and every conditional branch; refill: the cycle a redirect costs.
# Its cycle count per instruction is the one the Performance window uses.

def _arch_accesses(cycles):
    """Register and flag accesses from the multi-cycle cycles, without the PC."""
    regs = [c for ids in cycles for c in ids
            if (c.endswith(':r') or c.endswith(':w')) and not c.startswith('PC:')]
    flags_r = any(c in ('N:r', 'Z:r', 'C:r', 'V:r') for c in regs)
    flags_w = any(c in ('N:w', 'Z:w', 'C:w', 'V:w') for c in regs)
    return regs, flags_r, flags_w


def _pipe_path(hw1, hw2, mc_cycles, taken, sio):
    group, row, rule, n, pc_write = timing.pico_row(hw1, hw2)
    if rule == 'fixed':
        t = n
    elif rule == 'ldst':
        t = 1 if sio else 2
    elif rule == 'mul':
        t = 1
    elif rule == 'bcond':
        t = 2 if taken else 1
    else:
        t = 1                          # SVC / BKPT: not timed, counted as one cycle
    redirect = pc_write or (rule == 'bcond' and taken)
    cyc = [[] for _ in range(t)]
    fetch = ['p_pc', 'p_inc', 'p_pcmux', 'p_imem']
    cyc[0] += fetch + ['p_ifex', 'p_decode']
    if timing.is_wide(hw1) and t > 1:
        cyc[1] += fetch
    regs, fr, fw = _arch_accesses(mc_cycles)
    reads = [c for c in regs if c.endswith(':r') and c[0] != 'N' and c not in ('Z:r', 'C:r', 'V:r')]
    writes = [c for c in regs if c.endswith(':w') and c not in ('N:w', 'Z:w', 'C:w', 'V:w')]
    flags = [c for c in regs if c[:2] in ('N:', 'Z:', 'C:', 'V:')]
    last = t - 1 - (1 if redirect and t > 1 else 0)
    if reads:
        cyc[0].append('p_regfile')
    cyc[0] += reads
    if writes:
        cyc[max(0, last)].append('p_regfile')
    cyc[max(0, last)] += writes
    if group in ('ALU', 'multiply') or rule == 'ldst':
        cyc[0].append('p_alu')
    if group in ('load', 'store', 'push/pop') or (group == 'call/return' and row.startswith('POP')):
        mem = 1 if rule == 'ldst' else max(1, n - 1 - (2 if redirect else 0))
        for k in range(mem):
            cyc[min(t - 1, 1 + k) if t > 1 else 0].append('p_dmem')
    if fr or fw:
        cyc[0].append('p_flags')
    cyc[0] += flags
    if redirect or rule == 'bcond':
        cyc[0].append('p_resolve')
    if redirect and t > 1:
        cyc[t - 1] += ['p_refill', 'p_pc', 'p_pcmux', 'p_imem']
    # each block once per cycle; register reads keep their repeats (two ports)
    return [tuple(dict.fromkeys(c for c in ids if c in PIPE_INDEX and not c.endswith(':r'))) +
            tuple(c for c in ids if c.endswith(':r') and c in PIPE_INDEX)
            for ids in cyc]


@lru_cache(maxsize=None)
def pipeline_usage(hw1, hw2=0, sio=False):
    """Estimated per-cycle block ids of one encoding on the 2-stage pipeline,
    in the same shape as encoding_usage."""
    mc = encoding_usage(hw1, hw2)
    if mc is None:
        return None
    if 'always' in mc:
        return {'always': _pipe_path(hw1, hw2, mc['always'], False, sio)}
    return {'taken': _pipe_path(hw1, hw2, mc['taken'], True, sio),
            'not_taken': _pipe_path(hw1, hw2, mc['not_taken'], False, sio)}


def _usage(model, rec, sio=False):
    if model == 'm0plus':
        return pipeline_usage(rec['hw1'], rec['hw2'], sio)
    return encoding_usage(rec['hw1'], rec['hw2'])


def address_usage_model(model, rec):
    """address_usage for either model (the pipeline's is an estimate)."""
    if model != 'm0plus':
        return address_usage(rec)
    acc = {}
    x = rec['execs']
    sio = rec['reads'][1] + rec['writes'][1]
    group, row, rule, n, pc_write = timing.pico_row(rec['hw1'], rec['hw2'])
    runs = [(False, x)]
    if rule == 'ldst' and sio:
        runs = [(True, min(x, sio)), (False, x - min(x, sio))]
    cycles = 0
    for fast, times in runs:
        u = pipeline_usage(rec['hw1'], rec['hw2'], fast)
        if u is None or not times:
            continue
        if 'always' in u:
            _add(acc, u['always'], times)
            cycles += len(u['always']) * times
        else:
            t = rec.get('taken', 0)
            _add(acc, u['taken'], t)
            _add(acc, u['not_taken'], times - t)
            cycles += len(u['taken']) * t + len(u['not_taken']) * (times - t)
    acc['_cycles'] = cycles
    acc['_execs'] = x
    return acc


# ── past the trace: coarse snapshots ────────────────────────────────────────

TRACE_CAP = 20_000_000        # instructions kept for the 1-cycle timeline (2 bytes each)


class Overview:
    """Cumulative totals sampled every `every` cycles once the trace is
    full, so a very long run still has a (coarse) timeline.  At most MAX
    samples are kept: when full, every other one is dropped and the
    interval doubles."""
    MAX = 4096

    def __init__(self):
        self.every = 4096
        self.snaps = []           # (cycle, {model: [counts]}, {model: model cycles})

    def maybe(self, core):
        if self.snaps and core.cycles - self.snaps[-1][0] < self.every:
            return
        prof = core.profile()
        counts, cyc = {}, {}
        for model in ('multicycle', 'm0plus'):
            t = totals(prof, model=model)
            counts[model] = [t.get(i, 0) for i in ids(model)]
            cyc[model] = t.get('_cycles', 0)
        self.snaps.append((core.cycles, counts, cyc))
        if len(self.snaps) > self.MAX:
            self.snaps = self.snaps[::2]
            self.every *= 2

    def columns(self, model, start, end):
        """[(c0, c1, counts)] between consecutive samples inside [start, end),
        in the model's own cycles."""
        out = []
        for a, b in zip(self.snaps, self.snaps[1:]):
            c0, c1 = a[2][model], b[2][model]
            if c1 <= start or c0 >= end:
                continue
            out.append((c0, c1, [y - x for x, y in zip(a[1][model], b[1][model])]))
        return out


# ── reports for the GUI and the exports ─────────────────────────────────────

MAX_COLS = 4096               # timeline columns per request


def _rows(model, with_split):
    return [r for r in table(model) if with_split or not r['id'].startswith('mem:')]


def report(core, source, msg, overview=None):
    """The heat map's data for one request.

    msg: model ('multicycle' | 'm0plus'), view ('totals' | 'lines' |
    'timeline'), code_only, and for the timeline start, end and bucket
    (cycles; the bucket is raised so there are at most MAX_COLS columns).
    Returns {'rows': [{id, label, group}], 'cols': [...], 'values': one
    list per row, 'cycles', 'insns', 'estimate', ...}."""
    model = 'm0plus' if msg.get('model') == 'm0plus' else 'multicycle'
    view = msg.get('view', 'totals')
    code_only = bool(msg.get('code_only'))
    prof = core.profile()
    tot = totals(prof, code_only, model)
    out = {'model': model, 'view': view, 'code_only': code_only, 'estimate': model == 'm0plus',
           'cycles': tot.get('_cycles', 0), 'insns': tot.get('_execs', 0),
           'all_cycles': totals(prof, False, model).get('_cycles', 0) if code_only else tot.get('_cycles', 0)}
    if view == 'lines':
        rows = _rows(model, True)
        lines = per_line(prof, source, code_only, model)
        out['rows'] = rows
        out['cols'] = [{'line': l['line'], 'text': l['text'], 'os': l['os'],
                        'addr': l['addrs'][0], 'cycles': l['usage'].get('_cycles', 0),
                        'execs': l['usage'].get('_execs', 0)} for l in lines]
        out['values'] = [[l['usage'].get(r['id'], 0) for l in lines] for r in rows]
        return out
    if view == 'timeline':
        rows = _rows(model, False)
        set_table(core, model)
        traced = core.heat_trace_cycles()
        total = out['all_cycles']
        start = max(0, int(msg.get('start', 0)))
        end = int(msg.get('end', total) or total)
        end = max(start + 1, min(end, max(total, traced)))
        bucket = max(1, int(msg.get('bucket', 1)))
        bucket = max(bucket, -(-(end - start) // MAX_COLS))
        cols, values = [], [[] for _ in rows]
        t_end = min(end, traced)
        if start < t_end:
            flat = core.heat_timeline(start, t_end, bucket)
            n = len(ids(model))
            for b in range(0, len(flat), n):
                c0 = start + (b // n) * bucket
                cols.append([c0, min(c0 + bucket, t_end)])
                for i in range(len(rows)):
                    values[i].append(flat[b + i])
        if overview is not None and end > traced:
            for c0, c1, counts in overview.columns(model, max(start, traced), end):
                cols.append([c0, c1])
                for i in range(len(rows)):
                    values[i].append(counts[i])
        out.update(rows=rows, cols=cols, values=values, start=start, end=end, bucket=bucket,
                   traced_cycles=traced, trace_dropped=core.itrace_dropped, code_only=False)
        return out
    rows = _rows(model, model == 'multicycle')
    out['rows'] = rows
    out['cols'] = ['total']
    out['values'] = [[tot.get(r['id'], 0)] for r in rows]
    return out


def baseline_data(core, source, path):
    """What Save baseline keeps: totals for both models (and per line)."""
    prof = core.profile()
    data = {'file': path, 'saved': None, 'models': {}}
    for model in ('multicycle', 'm0plus'):
        t = totals(prof, False, model)
        mine = totals(prof, True, model)
        data['models'][model] = {
            'cycles': t.get('_cycles', 0), 'insns': t.get('_execs', 0),
            'totals': {r['id']: t.get(r['id'], 0) for r in table(model)},
            'code_only': {r['id']: mine.get(r['id'], 0) for r in table(model)},
            'code_cycles': mine.get('_cycles', 0)}
    return data


def compare(now, base, model, code_only=False, per=None):
    """Rows of the before/after comparison: for each component, the
    baseline and current counts, the change, and the change in %.  With
    per=N both are first scaled to "per N cycles" of their own run."""
    a, b = now['models'][model], base['models'][model]
    key = 'code_only' if code_only else 'totals'
    ca = a['code_cycles'] if code_only else a['cycles']
    cb = b['code_cycles'] if code_only else b['cycles']
    rows = []
    for r in table(model):
        x, y = b[key].get(r['id'], 0), a[key].get(r['id'], 0)
        if per:
            x = x * per / cb if cb else 0
            y = y * per / ca if ca else 0
        d = y - x
        rows.append({'id': r['id'], 'label': r['label'], 'group': r['group'], 'before': x,
                     'after': y, 'change': d, 'pct': (100.0 * d / x) if x else (None if d else 0.0)})
    return {'rows': rows, 'before_cycles': cb, 'after_cycles': ca}


def write_export(path, fmt, rep, meta):
    """Write a report (from report() or compare()) as CSV or JSON.  CSV
    files start with '#' lines describing the run, so they make sense alone."""
    import csv
    import json
    if fmt == 'json':
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, **rep}, f, indent=1)
        return
    with open(path, 'w', newline='', encoding='utf-8') as f:
        for k, v in meta.items():
            f.write(f'# {k}: {v}\n')
        w = csv.writer(f)
        view = rep.get('view')
        if view == 'compare':
            w.writerow(['id', 'label', 'group', 'before', 'after', 'change', 'change_pct'])
            for r in rep['rows']:
                w.writerow([r['id'], r['label'], r['group'], r['before'], r['after'], r['change'],
                            '' if r['pct'] is None else round(r['pct'], 3)])
        elif view == 'lines':
            w.writerow(['line', 'source', 'address', 'os', 'cycles', 'executions'] + [r['id'] for r in rep['rows']])
            for j, col in enumerate(rep['cols']):
                w.writerow([col['line'] or '', col['text'], f"0x{col['addr']:04X}", int(col['os']),
                            col['cycles'], col['execs']] + [v[j] for v in rep['values']])
        elif view == 'timeline':
            w.writerow(['cycle_start', 'cycle_end'] + [r['id'] for r in rep['rows']])
            for j, (c0, c1) in enumerate(rep['cols']):
                w.writerow([c0, c1] + [v[j] for v in rep['values']])
        else:
            cyc = rep.get('cycles') or 0
            w.writerow(['id', 'label', 'group', 'count', 'percent_of_cycles'])
            for r, v in zip(rep['rows'], rep['values']):
                w.writerow([r['id'], r['label'], r['group'], v[0],
                            round(100.0 * v[0] / cyc, 4) if cyc else ''])
