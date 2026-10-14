"""
Cycle timing of three machines that run the same ARMv6-M program.

  multicycle  the course's multi-cycle teaching datapath (the CPU diagram).
              Exact: the cycles the C++ FSM actually clocks.
  pico2       the RP2040's Cortex-M0+, a 2-stage pipeline (fetch | decode +
              execute).  Cycle counts are RP2040 datasheet Table 81
              ("Cortex-M0+ instruction summary"), which assumes zero wait
              states: code and data in SRAM (or the XIP cache hitting).
  pipe3       a 3-stage teaching pipeline (fetch | decode | execute) with the
              same execute stage as the Cortex-M0+.  A taken branch has to
              refill two stages instead of one, so it costs one more cycle.

All three execute the program identically (the C++ core), so only timing
differs.  Timing is computed from the core's execution profile: for every
completed instruction, its encoding, whether it branched, whether a B<cond>
was taken, and where its data accesses went (RAM, the SIO's single-cycle
IOPORT, APB peripherals, other AHB space).

Not modelled, and reported instead of guessed:
  * SVC and BKPT: their cost depends on exception entry/return and debug
    configuration (Table 81 note e); picosim also runs SVC calls directly.
  * APB peripheral accesses (IO_BANK0, PADS_BANK0 …) have extra bus cycles on
    the real chip (datasheet §2.1.3); Table 81 counts exclude them.
  * Encodings that are not ARMv6-M (the simulator refuses to run them, so
    they only appear here if a scratch core was used).
"""

from functools import lru_cache

from ._picosim_core import CPUCore

REGIONS = ('RAM', 'SIO', 'APB', 'AHB')
RG_SIO, RG_APB = 1, 2
MACHINES = ('multicycle', 'pico2', 'pipe3')
MACHINE_NAMES = {'multicycle': 'Multi-cycle (teaching datapath)',
                 'pico2': '2-stage pipeline (RP2040 Cortex-M0+)',
                 'pipe3': '3-stage pipeline (teaching)'}
DEPTH = {'pico2': 2, 'pipe3': 3}
PICO_CLOCK_MHZ = 125          # the Pico SDK's default system clock for RP2040


def _popcount(v):
    return bin(v).count('1')


def is_wide(hw1):
    return (hw1 >> 11) in (0x1D, 0x1E, 0x1F)


@lru_cache(maxsize=None)
def pico_row(hw1, hw2=0):
    """Classify an encoding by its row in RP2040 datasheet Table 81.

    Returns (group, row, rule, n, pc_write):
      group    instruction class for the reports
      row      the Table 81 row, as printed there
      rule     'fixed' (n cycles), 'ldst' (2, or 1 if to the single-cycle I/O
               port), 'mul' (multiplier), 'bcond' (1, or 2 if taken),
               or None when Table 81 gives no number
      n        cycles for 'fixed'
      pc_write the instruction always writes the PC (so a pipeline refills)
    """
    if is_wide(hw1):
        if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
            return ('call/return', 'BL <label>', 'fixed', 3, True)
        if hw1 == 0xF3BF and (hw2 & 0xFF00) == 0x8F00 and 4 <= (hw2 >> 4) & 0xF <= 6:
            return ('system', ('DSB', 'DMB', 'ISB')[((hw2 >> 4) & 0xF) - 4], 'fixed', 3, False)
        if hw1 == 0xF3EF and (hw2 & 0xF000) == 0x8000:
            return ('system', 'MRS Rd, <specreg>', 'fixed', 3, False)
        if (hw1 & 0xFFF0) == 0xF380 and (hw2 & 0xFF00) == 0x8800:
            return ('system', 'MSR <specreg>, Rn', 'fixed', 3, False)
        return ('not ARMv6-M', 'not a Cortex-M0+ instruction', None, 0, False)
    top5, top4 = hw1 >> 11, hw1 >> 12
    top6, top7, top8 = hw1 >> 10, hw1 >> 9, hw1 >> 8
    if top5 <= 2:
        return ('ALU', ('LSLS', 'LSRS', 'ASRS')[top5] + ' Rd, Rm, #<shift>', 'fixed', 1, False)
    if top5 == 3:
        return ('ALU', ('ADDS', 'SUBS')[(hw1 >> 9) & 1] + ' Rd, Rn, ' +
                ('Rm' if not (hw1 >> 10) & 1 else '#<imm>'), 'fixed', 1, False)
    if top5 <= 7:
        return ('ALU', ('MOVS Rd, #<imm>', 'CMP Rn, #<imm>', 'ADDS Rd, Rd, #<imm>',
                        'SUBS Rd, Rd, #<imm>')[top5 - 4], 'fixed', 1, False)
    if top6 == 0b010000:
        op = (hw1 >> 6) & 0xF
        names = ('ANDS', 'EORS', 'LSLS', 'LSRS', 'ASRS', 'ADCS', 'SBCS', 'RORS',
                 'TST', 'RSBS', 'CMP', 'CMN', 'ORRS', 'MULS', 'BICS', 'MVNS')
        if op == 0xD:
            return ('multiply', 'MULS Rd, Rm, Rd', 'mul', 1, False)
        return ('ALU', names[op] + ' (register)', 'fixed', 1, False)
    if top6 == 0b010001:
        op = (hw1 >> 8) & 3
        rd = (((hw1 >> 7) & 1) << 3) | (hw1 & 7)
        if op == 3:
            return ('call/return', 'BLX Rm' if (hw1 >> 7) & 1 else 'BX Rm', 'fixed', 2, True)
        if op == 1:
            return ('ALU', 'CMP Rn, Rm', 'fixed', 1, False)
        if rd == 15:
            return ('branch', 'ADD PC, PC, Rm' if op == 0 else 'MOV PC, Rm', 'fixed', 2, True)
        return ('ALU', 'ADD Rd, Rd, Rm' if op == 0 else 'MOV Rd, Rm', 'fixed', 1, False)
    if top5 == 0b01001:
        return ('load', 'LDR Rd, <label>', 'ldst', 2, False)
    if top4 == 0b0101:
        opa = (hw1 >> 9) & 7
        row = ('STR', 'STRH', 'STRB', 'LDRSB', 'LDR', 'LDRH', 'LDRB', 'LDRSH')[opa] + ' Rd, [Rn, Rm]'
        return ('store' if opa <= 2 else 'load', row, 'ldst', 2, False)
    if top4 in (0b0110, 0b0111, 0b1000):
        load = (hw1 >> 11) & 1
        w = {0b0110: '', 0b0111: 'B', 0b1000: 'H'}[top4]
        return ('load' if load else 'store', ('LDR' if load else 'STR') + w + ' Rd, [Rn, #<imm>]',
                'ldst', 2, False)
    if top4 == 0b1001:
        load = (hw1 >> 11) & 1
        return ('load' if load else 'store', ('LDR' if load else 'STR') + ' Rd, [SP, #<imm>]',
                'ldst', 2, False)
    if top5 == 0b10100:
        return ('ALU', 'ADR Rd, <label>', 'fixed', 1, False)
    if top5 == 0b10101:
        return ('ALU', 'ADD Rd, SP, #<imm>', 'fixed', 1, False)
    if top8 == 0b10110000:
        return ('ALU', ('SUB' if (hw1 >> 7) & 1 else 'ADD') + ' SP, SP, #<imm>', 'fixed', 1, False)
    if top8 == 0b10110010:
        return ('ALU', ('SXTH', 'SXTB', 'UXTH', 'UXTB')[(hw1 >> 6) & 3] + ' Rd, Rm', 'fixed', 1, False)
    if top8 == 0b10111010 and ((hw1 >> 6) & 3) != 2:
        return ('ALU', ('REV', 'REV16', '', 'REVSH')[(hw1 >> 6) & 3] + ' Rd, Rm', 'fixed', 1, False)
    if top7 == 0b1011010:
        n = _popcount(hw1 & 0x1FF)
        return ('push/pop', 'PUSH {<loreglist>, LR}' if hw1 & 0x100 else 'PUSH {<loreglist>}',
                'fixed', 1 + n, False)
    if top8 == 0b10111100:
        return ('push/pop', 'POP {<loreglist>}', 'fixed', 1 + _popcount(hw1 & 0xFF), False)
    if top8 == 0b10111101:
        return ('call/return', 'POP {<loreglist>, PC}', 'fixed', 3 + _popcount(hw1 & 0x1FF), True)
    if top8 == 0b10111110:
        return ('OS / debug', 'BKPT #<imm>', None, 0, False)
    if top8 == 0b10111111 and not hw1 & 0xF:
        op = (hw1 >> 4) & 0xF
        # WFE / WFI: 2 cycles "excluding time spent waiting" (nothing waits here)
        return ('system', ('NOP', 'YIELD', 'WFE', 'WFI', 'SEV')[op] if op < 5 else 'NOP (hint)',
                'fixed', 2 if op in (2, 3) else 1, False)
    if (hw1 & 0xFFEF) == 0xB662:
        return ('system', 'CPSID i' if hw1 & 0x10 else 'CPSIE i', 'fixed', 1, False)
    if top5 == 0b11000:
        return ('push/pop', 'STM Rn!, {<loreglist>}', 'fixed', 1 + _popcount(hw1 & 0xFF), False)
    if top5 == 0b11001:
        rn = (hw1 >> 8) & 7
        row = 'LDM Rn, {<loreglist>}' if (hw1 >> rn) & 1 else 'LDM Rn!, {<loreglist>}'
        return ('push/pop', row, 'fixed', 1 + _popcount(hw1 & 0xFF), False)
    if top4 == 0b1101:
        cond = (hw1 >> 8) & 0xF
        if cond == 0xF:
            return ('OS / debug', 'SVC #<imm>', None, 0, False)
        if cond == 0xE:
            return ('not ARMv6-M', 'UDF', None, 0, False)
        return ('branch', 'B<cc> <label>', 'bcond', 1, False)
    if top5 == 0b11100:
        return ('branch', 'B <label>', 'fixed', 2, True)
    return ('not ARMv6-M', 'not a Cortex-M0+ instruction', None, 0, False)


@lru_cache(maxsize=None)
def multicycle_profile(hw1, hw2=0):
    """The multi-cycle FSM's states for one encoding, clocked on a scratch core.

    Returns {'cycles', 'phases': {phase: n}, 'bus': cycles with a gate on the
    bus, 'mem': cycles with MEM.EN (fetch reads included), 'alu': cycles the
    ALU computes, 'mul_exec': EXECUTE_COMMIT cycles of a MULS} or None.
    Every count here is a function of the encoding alone.
    """
    c = CPUCore()
    c.strict = False          # the states depend on the encoding alone, not on these values
    c.write16(0x100, hw1)
    c.write16(0x102, hw2)
    for r in range(8):
        c.set_reg(r, 0x400)
    c.set_reg(14, 0x201)
    c.sp = 0x800
    c.pc = 0x100
    phases, bus, mem, alu, mul = {}, 0, 0, 0, 0
    n = 0
    try:
        while True:
            info = c.step_cycle()
            n += 1
            sig = info['signals']
            phases[info['phase']] = phases.get(info['phase'], 0) + 1
            if any(s.startswith('Gate') for s in sig):
                bus += 1
            if 'MEM.EN' in sig:
                mem += 1
            if info['state'] != 'DECODE' and any(s.startswith('ALUK=') for s in sig):
                alu += 1
            if info['state'] == 'EXECUTE_COMMIT' and 'ALUK=MUL' in sig:
                mul += 1
            if info['last'] or c.halted:
                break
    except (RuntimeError, ValueError):
        return None
    return {'cycles': n, 'phases': phases, 'bus': bus, 'mem': mem, 'alu': alu, 'mul_exec': mul}


def _agg(e):
    """Normalise one record (or an address's totals) from the core."""
    return {'execs': e.get('execs', 1),
            'taken': e.get('taken', 1 if e.get('cond_taken') else 0),
            'sio': e['reads'][RG_SIO] + e['writes'][RG_SIO],
            'apb_r': e['reads'][RG_APB], 'apb_w': e['writes'][RG_APB]}


def cycles(machine, hw1, hw2, e, mul_cycles=1, wait=0):
    """Total cycles of `e` (a record, or an address's totals) on `machine`.

    None when the machine's timing for this encoding is not defined (SVC,
    BKPT, non-ARMv6-M).  `mul_cycles` is the multiplier's latency (1 = the
    RP2040's fast multiplier, 32 = the Cortex-M0+'s small iterative option);
    `wait` is memory wait states per access, for the multi-cycle machine.
    """
    a = _agg(e)
    x = a['execs']
    if machine == 'multicycle':
        p = multicycle_profile(hw1, hw2)
        if p is None:
            return None
        return x * (p['cycles'] + wait * p['mem'] + (mul_cycles - 1) * p['mul_exec'])
    group, row, rule, n, pc_write = pico_row(hw1, hw2)
    if rule is None:
        return None
    if rule == 'fixed':
        t = n * x
    elif rule == 'ldst':
        t = 2 * x - a['sio']          # "2 if to AHB interface or SCS, 1 if to single-cycle I/O port"
    elif rule == 'mul':
        t = mul_cycles * x
    else:                             # 'bcond': "2 if taken, 1 if not-taken"
        t = x + a['taken']
    if machine == 'pipe3':
        t += refills(hw1, hw2, e)     # one more stage to refill per taken branch
    return t


def refills(hw1, hw2, e):
    """How many times these executions redirected the PC (pipeline refills)."""
    a = _agg(e)
    group, row, rule, n, pc_write = pico_row(hw1, hw2)
    if pc_write:
        return a['execs']
    if rule == 'bcond':
        return a['taken']
    return 0


GROUPS = ('ALU', 'multiply', 'load', 'store', 'push/pop', 'branch', 'call/return',
          'system', 'OS / debug', 'not ARMv6-M')


def summarize(profile, mul_cycles=1, wait=0, asm=None):
    """Whole-run metrics from CPUCore.profile() for every machine."""
    asm = asm or {}
    out = {m: {'cycles': 0, 'untimed': 0} for m in MACHINES}
    insns = 0
    groups = {g: {'insns': 0, **{m: 0 for m in MACHINES}} for g in GROUPS}
    phases, bus, mem, alu = {}, 0, 0, 0
    fetch_reads = 0
    data = {'reads': [0, 0, 0, 0], 'writes': [0, 0, 0, 0]}
    branches = {'cond': 0, 'cond_taken': 0, 'pc_writes': 0}
    rows = []
    for e in profile:
        hw1, hw2, x = e['hw1'], e['hw2'], e['execs']
        insns += x
        group, row, rule, n, pc_write = pico_row(hw1, hw2)
        groups[group]['insns'] += x
        for i in range(4):
            data['reads'][i] += e['reads'][i]
            data['writes'][i] += e['writes'][i]
        if rule == 'bcond':
            branches['cond'] += x
            branches['cond_taken'] += e['taken']
        branches['pc_writes'] += refills(hw1, hw2, e)
        p = multicycle_profile(hw1, hw2)
        if p:
            for k, v in p['phases'].items():
                phases[k] = phases.get(k, 0) + v * x
            bus += p['bus'] * x
            mem += p['mem'] * x
            alu += p['alu'] * x
            fetch_reads += (2 if is_wide(hw1) else 1) * x
        row_c = {}
        for m in MACHINES:
            t = cycles(m, hw1, hw2, e, mul_cycles, wait)
            row_c[m] = t
            if t is None:
                out[m]['untimed'] += x
            else:
                out[m]['cycles'] += t
                groups[group][m] += t
        rows.append({'addr': e['addr'], 'text': asm.get(e['addr'], '?'), 'row': row,
                     'group': group, 'execs': x, 'cycles': row_c})
    for m in MACHINES:
        timed = insns - out[m]['untimed']
        out[m]['cpi'] = out[m]['cycles'] / timed if timed else 0.0
        out[m]['insns_timed'] = timed
    mc = out['multicycle']['cycles']
    rows.sort(key=lambda r: -(r['cycles']['multicycle'] or 0))
    return {'insns': insns, 'machines': out, 'groups': groups,
            'phases': phases, 'bus_cycles': bus, 'mem_cycles': mem, 'alu_cycles': alu,
            'fetch_reads': fetch_reads, 'data': data, 'branches': branches,
            'multicycle_total': mc, 'top': rows[:8], 'mul_cycles': mul_cycles, 'wait': wait}


# ── pipeline occupancy ───────────────────────────────────────────────────────

def schedule(machine, recs, mul_cycles=1, asm=None, next_addr=None, t0=0):
    """Lay out `recs` (CPUCore.recent()) on a pipeline, cycle by cycle.

    Each instruction i starts executing at cycle s_i and executes for c_i
    cycles; the next one starts at s_i + c_i.  The cycles just before s_i hold
    it in the earlier stages (Fetch; Fetch then Decode for 3 stages).  When an
    instruction redirects the PC, the instructions behind it in the pipeline
    were fetched from the wrong (sequential) addresses: they are shown in the
    branch's last execute cycle and then flushed, and the branch's remaining
    cycles refill the pipeline.  `next_addr` (the PC) adds the instruction
    that is in the earlier stages after the last completed one.

    The cycle in which the real Cortex-M0+ fetches an instruction is not
    published (it fetches up to 32 bits ahead); F marks the cycle in which an
    instruction is in the fetch stage of this model.  The execute-stage
    cycle counts are Table 81's.

    `t0` is the cycle in which the first instruction starts executing, so a
    long run can be laid out a few instructions at a time.
    """
    asm = asm or {}
    depth = DEPTH[machine]
    rows, t = [], t0
    for r in recs:
        hw1, hw2 = r['hw1'], r['hw2']
        c = cycles(machine, hw1, hw2, r, mul_cycles)
        timed = c is not None
        if not timed:
            c = 1
        ref = refills(hw1, hw2, r) * (depth - 1) if timed else 0
        stages = {}
        for k in range(1, depth):
            stages[t - k] = 'F' if k == depth - 1 else 'D'
        for k in range(c):
            stages[t + k] = 'E'
        group, row, rule, n, pc_write = pico_row(hw1, hw2)
        rows.append({'seq': r['seq'], 'addr': r['addr'], 'text': asm.get(r['addr'], '?'),
                     'row': row, 'start': t, 'cycles': c, 'exec': c - ref, 'refill': ref,
                     'timed': timed, 'stages': stages, 'flushed': False,
                     'sio': r['reads'][RG_SIO] + r['writes'][RG_SIO],
                     'apb': r['reads'][RG_APB] + r['writes'][RG_APB]})
        if ref:
            # the wrong-path instructions behind the branch, at its last execute cycle
            size = 4 if is_wide(hw1) else 2
            at = t + c - ref - 1
            for k in range(1, depth):
                a = r['addr'] + size + 2 * (k - 1)
                st = {}
                for j in range(depth - k):
                    st[at - j] = 'F' if j == depth - k - 1 else 'D'
                rows.append({'seq': None, 'addr': a, 'text': asm.get(a, '(next halfword)'),
                             'row': '', 'start': at + 1, 'cycles': 0, 'exec': 0, 'refill': 0,
                             'timed': True, 'stages': st, 'flushed': True, 'sio': 0, 'apb': 0})
        t += c
    if next_addr is not None and recs:
        # the instruction that executes next is already in the earlier stages
        st = {t - k: ('F' if k == depth - 1 else 'D') for k in range(1, depth)}
        rows.append({'seq': None, 'addr': next_addr, 'text': asm.get(next_addr, '?'), 'row': '',
                     'start': t, 'cycles': 0, 'exec': 0, 'refill': 0, 'timed': True,
                     'stages': st, 'flushed': False, 'pending': True, 'sio': 0, 'apb': 0})
    return {'machine': machine, 'depth': depth, 'rows': rows, 'total': t}
