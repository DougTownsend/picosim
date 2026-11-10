"""
Campaign D: how long things take on the chip, and how exceptions behave.

Every timed body runs between two SysTick reads (campaign.timing_case:
`ldr r6,[r7]` ... `ldr r5,[r7]`, R7 = SYST_CVR), so a body never touches
R6 or R7 (a body that must, saves them in R8/R9 and puts them back: the
"wrap", timed on its own).  An empty body gives the overhead, which every
batch measures for each code site and alignment, and which is subtracted.

    D1  single forms: about 70 instruction forms (ALU, shifts, MULS, B,
        B<cond> taken and not, BL, BX/BLX, loads and stores at each width,
        LDM/STM/PUSH/POP with 1-8 registers, ADR, ...), each repeated in its
        body (16 times; 8 for the multi-register forms; once for BX, BLX and
        MOV PC), x code site S0-S3 (striped SRAM, SRAM4, SRAM5, non-striped
        SRAM) x the body starting word- or halfword-aligned x data region
        (striped SRAM, SRAM4, SRAM5, non-striped SRAM, APB, SIO, PPB, ROM
        for loads)
    D2  ordered pairs of 40 forms that share one register setting, so that
        load-use, store-then-load, MULS chains and flag-set-then-B<cond>
        all occur; each pair 8 times in its body
    D3  the special instructions: DMB, DSB, ISB, MRS, MSR, CPSID, CPSIE,
        NOP, YIELD, SEV (and SEV then WFE).  The SVC instruction is in D4
        (it ends in an exception, so it is timed there)
    D4  exceptions (exceptions.json), as scenario cases: see below
    D5  polling: part of D1 again with OpenOCD's polling left on

Each stage holds every case RUNS (5) times, as copies with their own ids in
different batches.  A number is 'measured' when all five agree; otherwise it
is stored as 'varies' with every reading.

D4 scenarios.  Each case carries its own 256-byte-aligned vector table as a
blob: every entry points at the agent's `capture`, except the exceptions
under test, which point at handlers inside the case's own code (the slot is
known, so their addresses are too).  The case's code writes VTOR (the agent
puts it back for the next case), the priorities (SHPR2, SHPR3, NVIC IPR6
and IPR7: every scenario writes all four, since the agent does not reset
them) and whatever it needs (NVIC ISER, SysTick), then triggers.  The code
is built here in Python, halfword by halfword (_Asm: a few Thumb encodings,
labels and a literal pool); there are no separate scenario sources.

    latency     own handler that stamps SysTick on entry (B) and just before
                BX LR (C), and copies EXC_RETURN, IPSR, the frame address,
                the stacked PC and xPSR to registers that survive; the
                Thread code stamps before the trigger (A) and after the
                return (D), then falls into the sled.  entry = A - B less
                the overhead, the trigger and the NOPs that ran before the
                exception was taken (from the stacked PC); exit = C - D
                less the overhead and the NOPs left to run (it includes
                the BX LR).  SVC, PendSV, IRQ 26-31 pended in NVIC ISPR,
                SysTick (from its reload: (RVR + 1 - B) mod (RVR + 1)), on
                PSP, PSP misaligned, MSP, and SysTick waking WFI.
    tail-chain  two exceptions pended by one store, both with own handlers.
    frame       the exception's vector is `capture` itself, which records
                the whole frame, EXC_RETURN, IPSR, MSP/PSP: SVC, PendSV,
                IRQs, SysTick, from PSP/MSP, aligned or not.
    priority    two pended at once (both to `capture`): which is taken;
                PRIMASK holding PendSV until CPSIE; SVC with PRIMASK set;
                the implemented priority bits; an IRQ pended but disabled;
                a nested preemption (EXC_RETURN 0xFFFFFFF1).
    interrupted SysTick fired k cycles into LDM/STM/PUSH/POP of 7-8
                registers (RVR swept; a stub handler turns TICKINT off and
                jumps to `capture`): from the stacked PC, the registers and
                the scratch window, was the instruction not started,
                abandoned part-way, or completed?
    wfi         WFI with PRIMASK set wakes on a SysTick it does not take.

Output: timing.json (D1-D3, D5) and exceptions.json (D4), and replay files
for D1 (one per code site and alignment), D2 and D3 so `check` compares
picosim's timing with them.  The layout is 'lo' (the agent at the start of
SRAM), so SRAM4 and SRAM5 are free to be code sites; data addresses sit
outside every site.  At most 400 cases per batch keep the results below the
non-striped data and site (which alias striped SRAM at 0x2003xxxx).
"""

import json
import re
import struct
from collections import Counter

from .. import formats as F
from .. import runner as R
from ..campaign import h, timing_case, release, IO_PADS

NAME, TITLE, AGENT_BASED = 'timing', 'Timing: instruction cycles, pairs, specials and exceptions', True
LAYOUT = 'lo'
LAY = F.LAYOUTS[LAYOUT]
MAX_CASES = 400
RUNS = 5
REPS, MULTI_REPS, PAIR_REPS = 16, 8, 8
ID_RUN = 1_000_000                       # case id = run * ID_RUN + index
CALIB_EVERY = 40                         # a calibration case at least this often
SCRATCH = LAY['SCRATCH']
CVR = F.PPB['SYST_CVR']
STAGES = ('D1', 'D2', 'D3', 'D4', 'D5')

# code sites (the same as campaign A's): data addresses are never inside them
SITES = {'S0': (LAY['SLED'], LAY['SLED'] + LAY['SLED_SIZE']),
         'S1': (0x20040800, 0x20041000),          # SRAM4
         'S2': (0x20041800, 0x20042000),          # SRAM5
         'S3': (0x2100C000, 0x2100E000)}          # non-striped SRAM0
SITE_NAMES = {'S0': 'striped SRAM', 'S1': 'SRAM4', 'S2': 'SRAM5', 'S3': 'non-striped SRAM'}
ALIGNS = ('word', 'halfword')


def slot_for(site, align):
    """The slot whose body (after the 2-byte SysTick read) starts word- or
    halfword-aligned."""
    lo, hi = SITES[site]
    base = LAY['SLOT'] if site == 'S0' else (lo + hi) // 2
    return base + (2 if align == 'word' else 0)


# data regions: (load address, store address, store value, widths, word-only)
DATA = {
    'striped': (SCRATCH + 0x100, SCRATCH + 0x100, 0x12345678, False),
    'sram4': (0x20040100, 0x20040100, 0x12345678, False),
    'sram5': (0x20041100, 0x20041100, 0x12345678, False),
    'nonstriped': (0x2100F100, 0x2100F100, 0x12345678, False),
    'rom': (0x00000100, None, None, False),
    'apb': (0x40014000, 0x4001C05C, 0x56, False),      # IO_BANK0 GPIO0_STATUS; PADS_BANK0 GPIO22
    'sio': (0xD0000004, 0xD000001C, 0, False),         # GPIO_IN; GPIO_OUT_XOR with 0 (no change)
    'ppb': (0xE000E014, 0xE000E014, 0x00FFFFFF, True),  # SYST_RVR: the value the agent sets
}
RAM_REGIONS = ('striped', 'sram4', 'sram5', 'nonstriped')

# ── D1 and D3 forms ─────────────────────────────────────────────────────────
# Default registers: r0 = 7, r1 = 3, r2 = 5, r3 = 9, r4 = 11, r8 = 0x100.
ALU = [
    ('MOVS r0,#1', 0x2001), ('MOV r0,r1', 0x4608), ('MOV r8,r0', 0x4680),
    ('ADDS r0,r1,r2', 0x1888), ('ADDS r0,r1,#1', 0x1C48), ('ADDS r0,#1', 0x3001),
    ('SUBS r0,r1,r2', 0x1A88), ('ADD r0,r8', 0x4440), ('ADCS r0,r1', 0x4148), ('SBCS r0,r1', 0x4188),
    ('RSBS r0,r1,#0', 0x4248), ('CMP r0,r1', 0x4288), ('CMP r0,#1', 0x2801), ('CMN r0,r1', 0x42C8),
    ('ANDS r0,r1', 0x4008), ('ORRS r0,r1', 0x4308), ('EORS r0,r1', 0x4048), ('BICS r0,r1', 0x4388),
    ('MVNS r0,r1', 0x43C8), ('TST r0,r1', 0x4208), ('MULS r0,r1,r0', 0x4348),
    ('LSLS r0,r1,#1', 0x0048), ('LSRS r0,r1,#1', 0x0848), ('ASRS r0,r1,#1', 0x1048),
    ('LSLS r0,r1', 0x4088), ('LSRS r0,r1', 0x40C8), ('ASRS r0,r1', 0x4108), ('RORS r0,r1', 0x41C8),
    ('SXTB r0,r1', 0xB248), ('UXTH r0,r1', 0xB288), ('REV r0,r1', 0xBA08), ('REVSH r0,r1', 0xBAC8),
    ('ADR r0,.+4', 0xA000), ('ADD r0,sp,#4', 0xA801), ('ADD sp,#0', 0xB000), ('NOP', 0xBF00),
]
LOADS = [('LDR r0,[r1]', 0x6808, 4), ('LDRH r0,[r1]', 0x8808, 2), ('LDRB r0,[r1]', 0x7808, 1),
         ('LDR r0,[r1,r2]', 0x5888, 4), ('LDRH r0,[r1,r2]', 0x5A88, 2), ('LDRB r0,[r1,r2]', 0x5C88, 1),
         ('LDRSH r0,[r1,r2]', 0x5E88, 2), ('LDRSB r0,[r1,r2]', 0x5688, 1)]
STORES = [('STR r0,[r1]', 0x6008, 4), ('STRH r0,[r1]', 0x8008, 2), ('STRB r0,[r1]', 0x7008, 1),
          ('STR r0,[r1,r2]', 0x5088, 4), ('STRH r0,[r1,r2]', 0x5288, 2), ('STRB r0,[r1,r2]', 0x5488, 1)]
SPECIALS = [  # (name, halfwords of one instance, registers)
    ('DMB', (0xF3BF, 0x8F5F), {}), ('DSB', (0xF3BF, 0x8F4F), {}), ('ISB', (0xF3BF, 0x8F6F), {}),
    ('MRS r0,APSR', (0xF3EF, 0x8000), {}), ('MRS r0,IPSR', (0xF3EF, 0x8005), {}),
    ('MRS r0,PRIMASK', (0xF3EF, 0x8010), {}), ('MRS r0,CONTROL', (0xF3EF, 0x8014), {}),
    ('MRS r0,MSP', (0xF3EF, 0x8008), {}), ('MRS r0,PSP', (0xF3EF, 0x8009), {}),
    ('MSR APSR,r0', (0xF380, 0x8800), {0: 0}), ('MSR PRIMASK,r0', (0xF380, 0x8810), {0: 0}),
    ('MSR CONTROL,r0', (0xF380, 0x8814), {0: 2}),          # SPSEL stays 1, nPRIV 0: no change
    ('MSR MSP,r0', (0xF380, 0x8808), {0: LAY['MSP_TOP']}),   # the agent reloads MSP anyway
    ('CPSID i', (0xB672,), {}), ('CPSIE i', (0xB662,), {}),
    ('NOP', (0xBF00,), {}), ('YIELD', (0xBF10,), {}), ('SEV', (0xBF40,), {}),
    ('SEV+WFE', (0xBF40, 0xBF20), {}),                      # WFE finds the event: does not sleep
]
WRAP_IN, WRAP_OUT = (0x46B0, 0x46B9), (0x464F, 0x4646)    # mov r8,r6; mov r9,r7 / mov r7,r9; mov r6,r8


def _regs(**kw):
    r = [7, 3, 5, 9, 11, 0, 0, CVR, 0x100, 0, 0, 0, 0]
    for k, v in kw.items():
        r[int(k[1:])] = v & 0xFFFFFFFF
    return r


def _form(name, group, one, count=REPS, region=None, regs=None, nzcv=0, psp=None, wrap=False,
          reads=(), writes=(), slot_dep=None):
    return {'name': name, 'group': group, 'one': tuple(one), 'count': count, 'region': region,
            'regs': regs or _regs(), 'nzcv': nzcv, 'psp': psp, 'wrap': wrap, 'reads': list(reads),
            'writes': list(writes), 'slot_dep': slot_dep}


def d1_forms():
    """Every D1 form (a dict), whatever the site."""
    out = [_form(n, 'ALU', (hw,)) for n, hw in ALU]
    out += [_form('B .+2', 'branch', (0xE7FF,)),
            _form('BEQ .+2 (taken)', 'branch', (0xD0FF,), nzcv=0b0100),
            _form('BEQ .+2 (not taken)', 'branch', (0xD0FF,), nzcv=0),
            _form('BL .+4', 'branch', (0xF000, 0xF800)),
            # once each: the target is the SysTick read after the body
            _form('BX r0', 'branch', (0x4700,), count=1, slot_dep=lambda s: {0: (s + 4) | 1}),
            _form('BLX r0', 'branch', (0x4780,), count=1, slot_dep=lambda s: {0: (s + 4) | 1}),
            _form('MOV pc,r0', 'branch', (0x4687,), count=1, slot_dep=lambda s: {0: s + 4}),
            _form('LDR r0,[sp]', 'load', (0x9800,), region='stack'),
            _form('STR r0,[sp]', 'store', (0x9000,), region='stack'),
            _form('LDR r0,[pc,#128]', 'load', (0x4820,), region='code')]
    for region, (la, sa, sv, word_only) in DATA.items():
        for n, hw, w in LOADS:
            if word_only and w != 4:
                continue
            out.append(_form(n, 'load', (hw,), region=region, regs=_regs(r1=la, r2=0), reads=[la]))
        if sa is None:
            continue
        for n, hw, w in STORES:
            if word_only and w != 4:
                continue
            v = sv & ((1 << (8 * w)) - 1)
            out.append(_form(n, 'store', (hw,), region=region, regs=_regs(r0=v, r1=sa, r2=0),
                             writes=[(sa, w, v)]))
    for n in range(1, 9):
        lst = (1 << n) - 1                                    # r0..r(n-1)
        out.append(_form(f'PUSH {n} regs', 'push/pop', (0xB400 | lst,), count=MULTI_REPS, region='stack',
                         psp=LAY['PSP_HI']))
        out.append(_form(f'POP {n} regs', 'push/pop', (0xBC00 | lst,), count=MULTI_REPS, region='stack',
                         psp=LAY['PSP_LO'] + 64, wrap=n >= 7))
    for region in RAM_REGIONS + ('rom',):
        a = DATA[region][0]
        for n in range(1, 9):
            if n < 8:                                         # ldm r0!, {r1..rn}
                one, count, wrap = (0xC800 | (((1 << n) - 1) << 1),), MULTI_REPS, n >= 6
            else:                                             # ldm r0, {r0-r7}: no writeback, once
                one, count, wrap = (0xC8FF,), 1, True
            out.append(_form(f'LDM {n} regs', 'push/pop', one, count=count, region=region, wrap=wrap,
                             regs=_regs(r0=a), reads=[a]))
        if region == 'rom':
            continue
        for n in range(1, 9):                                 # stm r0!, {r0..r(n-1)}: r0 lowest, so defined
            out.append(_form(f'STM {n} regs', 'push/pop', (0xC000 | ((1 << n) - 1),), count=MULTI_REPS,
                             region=region, regs=_regs(r0=a),
                             writes=[(a + 4 * i, 4, 0) for i in range(n * MULTI_REPS)]))
    return out


def d3_forms():
    return [_form(n, 'system', one, regs=_regs(**{f'r{k}': v for k, v in rg.items()}))
            for n, one, rg in SPECIALS]


def _body(form, slot):
    hws = list(form['one']) * form['count']
    if form['wrap']:
        hws = list(WRAP_IN) + hws + list(WRAP_OUT)
    return h(*hws)


def _entry(form, site, align, role='form'):
    slot = slot_for(site, align)
    regs = list(form['regs'])
    if form.get('slot_dep'):
        for k, v in form['slot_dep'](slot).items():
            regs[k] = v
    return {**form, 'site': site, 'align': align, 'slot': slot, 'role': role, 'regs': regs,
            'body': _body(form, slot)}


CALIB = _form('(empty)', 'calibration', (), count=0)
WRAP_CAL = _form('(wrap: 4 MOVs)', 'calibration', (), count=0, wrap=True)


def _with_calibration(entries, site, align):
    """Calibration (empty body, and the wrap alone) first and then every
    CALIB_EVERY cases, so every batch has its own."""
    out = []
    for i, e in enumerate(entries):
        if i % CALIB_EVERY == 0:
            out += [_entry(CALIB, site, align, 'calib'), _entry(WRAP_CAL, site, align, 'wrap')]
        out.append(e)
    return out


def d1_entries(sites=tuple(SITES), aligns=ALIGNS):
    forms = d1_forms()
    out = []
    for site in sites:
        for align in aligns:
            out += _with_calibration([_entry(f, site, align) for f in forms], site, align)
    return out


def d3_entries():
    out = []
    for align in ALIGNS:
        out += _with_calibration([_entry(f, 'S0', align) for f in d3_forms()], 'S0', align)
    return out


# ── D2: pairs ───────────────────────────────────────────────────────────────
PAIR_FORMS = [
    ('MOVS r0,#1', (0x2001,)), ('ADDS r0,r0,r3', (0x18C0,)), ('ADDS r0,#1', (0x3001,)),
    ('SUBS r0,r0,r3', (0x1AC0,)), ('CMP r0,r3', (0x4298,)), ('CMP r0,#0', (0x2800,)),
    ('ANDS r0,r3', (0x4018,)), ('EORS r0,r3', (0x4058,)), ('ORRS r0,r3', (0x4318,)),
    ('MVNS r0,r3', (0x43D8,)), ('ADCS r0,r3', (0x4158,)), ('LSLS r0,r0,#1', (0x0040,)),
    ('LSRS r0,r0,#1', (0x0840,)), ('LSLS r0,r3', (0x4098,)), ('RORS r0,r3', (0x41D8,)),
    ('MULS r0,r3,r0', (0x4358,)), ('MULS r4,r0,r4', (0x4344,)), ('MOV r4,r0', (0x4604,)),
    ('MOV r8,r0', (0x4680,)), ('ADD r0,r8', (0x4440,)), ('UXTB r0,r0', (0xB2C0,)), ('REV r0,r0', (0xBA00,)),
    ('LDR r0,[r1]', (0x6808,)), ('LDR r3,[r1]', (0x680B,)), ('LDR r0,[r1,r2]', (0x5888,)),
    ('LDRB r0,[r1]', (0x7808,)), ('LDRH r0,[r1]', (0x8808,)), ('LDR r4,[r1,#4]', (0x684C,)),
    ('STR r0,[r1]', (0x6008,)), ('STR r3,[r1]', (0x600B,)), ('STRB r0,[r1]', (0x7008,)),
    ('STR r0,[r1,r2]', (0x5088,)), ('LDR r0,[sp]', (0x9800,)), ('STR r0,[sp]', (0x9000,)),
    ('LDR r0,[pc,#64]', (0x4810,)), ('B .+2', (0xE7FF,)), ('BEQ .+2', (0xD0FF,)), ('BNE .+2', (0xD1FF,)),
    ('BL .+4', (0xF000, 0xF800)), ('NOP', (0xBF00,)),
]
PAIR_DATA = SCRATCH + 0x100
PAIR_REGS = [3, PAIR_DATA, 0, 5, 7, 0, 0, CVR, 0x10, 0, 0, 0, 0]   # no form writes r1 or r2


def d2_entries():
    out = []
    pairs = []
    for a_name, a in PAIR_FORMS:
        for b_name, b in PAIR_FORMS:
            f = _form(f'{a_name} ; {b_name}', 'pair', a + b, count=PAIR_REPS, region='striped',
                      regs=list(PAIR_REGS), nzcv=0b0100,
                      reads=[PAIR_DATA], writes=[(PAIR_DATA, 4, 0)])
            f['pair'] = (a_name, b_name)
            pairs.append(_entry(f, 'S0', 'word'))
    out += _with_calibration(pairs, 'S0', 'word')
    return out


def d5_entries():
    """Part of D1 again, with polling left on: S0, word-aligned."""
    keep = {'ADDS r0,r1,r2', 'MULS r0,r1,r0', 'B .+2', 'BL .+4', 'LDR r0,[r1]', 'STR r0,[r1]',
            'PUSH 8 regs', 'LDM 4 regs', 'NOP'}
    forms = [f for f in d1_forms() if f['name'] in keep and f['region'] in (None, 'stack', 'striped', 'apb', 'sio')]
    return _with_calibration([_entry(f, 'S0', 'word') for f in forms], 'S0', 'word')


def _timing_cases(entries, runs=RUNS):
    """Each entry RUNS times, run by run (so the copies are in other batches)."""
    out = []
    for run in range(runs):
        for i, e in enumerate(entries):
            out.append(timing_case(run * ID_RUN + i, e['body'], regs=e['regs'], layout=LAYOUT, slot=e['slot'],
                                   nzcv=e['nzcv'], psp=e['psp'], reads=e['reads'], writes=e['writes']))
    return out


# ── D4: the scenario code ──────────────────────────────────────────────────
VTOR, ICSR = F.PPB['VTOR_REG'], F.PPB['ICSR_REG']
SHPR2, SHPR3, IPR6, IPR7 = 0xE000ED1C, 0xE000ED20, 0xE000E418, 0xE000E41C
ISER, ISPR = 0xE000E100, 0xE000E200
SYST_CSR, SYST_RVR = F.PPB['SYST_CSR'], F.PPB['SYST_RVR']
PENDSVSET, PENDSTSET = 1 << 28, 1 << 26
SVCALL, PENDSV, SYSTICK = 11, 14, 15
N_VECTORS = 48
MARK = {2: 0xA2A2A2A2, 3: 0xA3A3A3A3, 12: 0xACACACAC}


class _Asm:
    """Enough of a Thumb assembler for the D4 scenarios: halfwords, labels,
    forward literal loads (one pool at the end) and branches.  The code runs
    at a known slot, so `base` makes every address real."""

    def __init__(self, base):
        self.base = base
        self.hw = []
        self.labels = {}
        self.lits = []          # (index, rd, value)
        self.branches = []      # (index, cond or None, label)

    def at(self):
        return self.base + 2 * len(self.hw)

    def label(self, name):
        self.labels[name] = self.at()

    def raw(self, *hws):
        self.hw.extend(hws)

    def ldr(self, rd, value):
        self.lits.append((len(self.hw), rd, value & 0xFFFFFFFF))
        self.hw.append(0)

    def b(self, label, cond=None):
        self.branches.append((len(self.hw), cond, label))
        self.hw.append(0)

    def store(self, addr, value):
        """*addr = value, through r0 and r1 (flags untouched)."""
        self.ldr(0, addr)
        self.ldr(1, value)
        self.raw(0x6001)                                  # str r1, [r0]

    def nops(self, n):
        self.raw(*[0xBF00] * n)

    def finish(self):
        if self.at() % 4:
            self.raw(F.SLED_HALF)                         # padding, never run
        pool, words = {}, []
        for _, _, v in self.lits:
            if v not in pool:
                pool[v] = self.at() + 4 * len(words)
                words.append(v)
        for i, rd, v in self.lits:
            pc = self.base + 2 * i
            off = pool[v] - ((pc + 4) & ~3)
            if not 0 <= off < 1024:
                raise ValueError(f"a literal is out of reach ({off} bytes)")
            self.hw[i] = 0x4800 | (rd << 8) | (off >> 2)
        for w in words:
            self.raw(w & 0xFFFF, w >> 16)
        self.labels['END'] = self.at()
        for i, cond, label in self.branches:
            off = self.labels[label] - (self.base + 2 * i + 4)
            if cond is None:
                assert -2048 <= off < 2048
                self.hw[i] = 0xE000 | ((off >> 1) & 0x7FF)
            else:
                assert -256 <= off < 256
                self.hw[i] = 0xD000 | (cond << 8) | ((off >> 1) & 0xFF)
        return h(*self.hw)


def _capture():
    from ..build import agent
    return agent(LAYOUT).symbols['capture']


def _table(handlers):
    """The vector table blob: `capture` everywhere, own handlers where given."""
    cap = _capture() | 1
    words = [LAY['MSP_TOP']] + [cap] * (N_VECTORS - 1)
    for n, addr in handlers.items():
        words[n] = addr | 1
    return struct.pack(f'<{N_VECTORS}I', *words)


def _prologue(a, table, prio, iser=0):
    """VTOR, the four priority registers (always: the agent does not reset
    them), NVIC enables; a DSB/ISB so they are in force."""
    a.store(VTOR, table)
    a.store(SHPR2, prio.get('shpr2', 0))
    a.store(SHPR3, prio.get('shpr3', 0))
    a.store(IPR6, prio.get('ipr6', 0))
    a.store(IPR7, prio.get('ipr7', 0))
    if iser:
        a.store(ISER, iser)
    a.raw(0xF3BF, 0x8F4F, 0xF3BF, 0x8F6F)                 # dsb; isb


def _prologue_writes(prio, iser=0, extra=()):
    w = [(VTOR, 4, 0), (SHPR2, 4, prio.get('shpr2', 0)), (SHPR3, 4, prio.get('shpr3', 0)),
         (IPR6, 4, prio.get('ipr6', 0)), (IPR7, 4, prio.get('ipr7', 0))]
    if iser:
        w.append((ISER, 4, iser))
    return w + list(extra)


def _to_msp(a, msp):
    """Thread mode moves to MSP (CONTROL.SPSEL = 0), at `msp`."""
    a.ldr(0, msp)
    a.raw(0xF380, 0x8808)                                 # msr msp, r0
    a.ldr(0, 0)
    a.raw(0xF380, 0x8814, 0xF3BF, 0x8F6F)                 # msr control, r0; isb


def _latency_handler(a, stack, systick=False):
    """Stamp B (R11) on entry and C (R4) before BX LR; EXC_RETURN to R8, IPSR
    to R9, the frame address to R10; the stacked PC and xPSR into the stacked
    R0 and R1, so Thread mode gets them back."""
    a.raw(0x683C, 0x46A3)                                 # ldr r4,[r7] (B); mov r11, r4
    a.raw(0x46F0)                                         # mov r8, lr
    a.raw(0xF3EF, 0x8005, 0x4681)                         # mrs r0, ipsr; mov r9, r0
    if systick:
        a.ldr(0, SYST_CSR)
        a.raw(0x2105, 0x6001)                             # movs r1,#5; str r1,[r0]: TICKINT off
    a.raw(0xF3EF, 0x8009 if stack == 'psp' else 0x8008, 0x4682)   # mrs r0, psp|msp; mov r10, r0
    a.raw(0x6981, 0x69C2, 0x6001, 0x6042)                 # ldr r1,[r0,#24]; ldr r2,[r0,#28]; str r1,[r0]; str r2,[r0,#4]
    a.raw(0x683C, 0x4770)                                 # ldr r4,[r7] (C); bx lr


def _stub(a):
    """A SysTick handler that turns TICKINT off and goes on to `capture`
    (which then records this exception's frame as if it were the vector)."""
    a.ldr(0, SYST_CSR)
    a.raw(0x2105, 0x6001)                                 # movs r1,#5; str r1,[r0]
    a.ldr(0, _capture() | 1)
    a.raw(0x4700)                                         # bx r0


TRIGGERS = {  # name: (exception number, register, value, NVIC enable)
    'SVC': (SVCALL, None, None, 0),
    'PendSV': (PENDSV, ICSR, PENDSVSET, 0),
    **{f'IRQ{n}': (16 + n, ISPR, 1 << n, 1 << n) for n in range(26, 32)},
}
SLIDE = 16                    # NOPs after a trigger, for an exception taken late
ST_RVR, ST_SLIDE = 120, 200   # SysTick latency: reload value, NOPs it fires among


def _sc_latency(trigger, stack='psp', misalign=False):
    """Entry and exit latency (and EXC_RETURN, IPSR, frame) for one source."""
    def build(slot, table):
        a = _Asm(slot)
        n, reg, val, en = TRIGGERS.get(trigger, (SYSTICK, None, None, 0))
        _prologue(a, table, {}, en)
        if stack == 'msp':
            _to_msp(a, LAY['MSP_TOP'] - (4 if misalign else 0))
        if trigger in ('SysTick', 'SysTick+WFI'):
            a.store(SYST_RVR, ST_RVR)
            a.store(SYST_CSR, 7)                          # ENABLE, TICKINT, processor clock
            a.raw(0x683E, 0x603E)                         # ldr r6,[r7] (A); str r6,[r7]: clear, reload next
            if trigger == 'SysTick+WFI':
                a.raw(0xBF30)                             # wfi
                a.label('slide')
                a.nops(SLIDE)
            else:
                a.label('slide')
                a.nops(ST_SLIDE)
        else:
            if reg is not None:
                a.ldr(0, reg)
                a.ldr(1, val)
            a.raw(0x683E)                                 # ldr r6,[r7] (A)
            a.raw(0xDF00 if reg is None else 0x6001)      # svc #0 | str r1,[r0]
            a.label('slide')
            a.nops(SLIDE)
        a.label('after')
        a.raw(0x683D)                                     # ldr r5,[r7] (D)
        a.b('END')
        a.label('handler')
        _latency_handler(a, stack, systick=trigger.startswith('SysTick'))
        code = a.finish()
        return code, {n: a.labels['handler']}, a.labels
    writes = []
    if trigger in TRIGGERS and TRIGGERS[trigger][1]:
        writes.append((TRIGGERS[trigger][1], 4, TRIGGERS[trigger][2]))
    if trigger.startswith('SysTick'):
        writes += [(SYST_RVR, 4, ST_RVR), (SYST_CSR, 4, 7), (CVR, 4, 0)]
    en = TRIGGERS.get(trigger, (0, 0, 0, 0))[3]
    name = f"latency {trigger}" + (f" from {stack.upper()}" if stack != 'psp' or misalign else '') + \
        (' misaligned' if misalign else '')
    return {'name': name, 'type': 'latency', 'trigger': trigger, 'stack': stack, 'misalign': misalign,
            'build': build, 'writes': _prologue_writes({}, en, writes),
            'psp': LAY['PSP_DEFAULT'] - (4 if misalign and stack == 'psp' else 0)}


def _sc_tail(kind):
    """Two exceptions pended by one store; both handlers are the case's own."""
    if kind == 'PendSV+SysTick':
        first, second, reg, val, en = PENDSV, SYSTICK, ICSR, PENDSVSET | PENDSTSET, 0
    else:
        first, second, reg, val, en = 42, 43, ISPR, (1 << 26) | (1 << 27), (1 << 26) | (1 << 27)

    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, {}, en)
        a.ldr(0, reg)
        a.ldr(1, val)
        a.raw(0x683E, 0x6001)                             # ldr r6,[r7] (A); str r1,[r0]
        a.label('slide')
        a.nops(SLIDE)
        a.label('after')
        a.raw(0x683D)                                     # ldr r5,[r7] (D)
        a.b('END')
        a.label('h1')                                     # B1 -> r11, A -> r10, EXC_RETURN -> r8, C1 -> r6
        a.raw(0x683C, 0x46A3, 0x46B2, 0x46F0)             # ldr r4,[r7]; mov r11,r4; mov r10,r6; mov r8,lr
        a.raw(0xF3EF, 0x8009, 0x6981, 0x6001)             # mrs r0,psp; ldr r1,[r0,#24]; str r1,[r0]
        a.raw(0x683E, 0x4770)                             # ldr r6,[r7] (C1); bx lr
        a.label('h2')                                     # B2 -> r9, C2 -> r4
        a.raw(0x683C, 0x46A1, 0x683C, 0x4770)             # ldr r4,[r7]; mov r9,r4; ldr r4,[r7]; bx lr
        code = a.finish()
        return code, {first: a.labels['h1'], second: a.labels['h2']}, a.labels
    return {'name': f"tail-chain {kind}", 'type': 'tail', 'trigger': kind, 'build': build,
            'writes': _prologue_writes({}, en, [(reg, 4, val)]), 'first': first, 'second': second}


def _sc_frame(trigger, stack='psp', misalign=False, nzcv=0b1010):
    """The exception's vector is `capture`: it records the frame itself."""
    def build(slot, table):
        a = _Asm(slot)
        n, reg, val, en = TRIGGERS.get(trigger, (SYSTICK, None, None, 0))
        _prologue(a, table, {}, en)
        if stack == 'msp':
            _to_msp(a, LAY['MSP_TOP'] - (4 if misalign else 0))
        handlers = {}
        if trigger == 'SysTick':
            a.store(SYST_RVR, 40)
            a.store(SYST_CSR, 7)
            a.ldr(0, 0xA0A0A0A0)
            a.ldr(1, 0xA1A1A1A1)
            a.raw(0x603E)                                 # str r6,[r7]: clear, reload next
            a.label('slide')
            a.nops(64)
        else:
            a.ldr(0, reg if reg else 0xA0A0A0A0)
            a.ldr(1, val if reg else 0xA1A1A1A1)
            a.label('trigger')
            a.raw(0xDF00 if reg is None else 0x6001)
            a.label('slide')
            a.nops(SLIDE)
        a.b('END')
        if trigger == 'SysTick':
            a.label('stub')
            _stub(a)
        code = a.finish()
        if trigger == 'SysTick':
            handlers[SYSTICK] = a.labels['stub']
        return code, handlers, a.labels
    n, reg, val, en = TRIGGERS.get(trigger, (SYSTICK, None, None, 0))
    writes = [(reg, 4, val)] if reg else []
    if trigger == 'SysTick':
        writes += [(SYST_RVR, 4, 40), (SYST_CSR, 4, 7), (CVR, 4, 0)]
    name = f"frame {trigger} from {stack.upper()}" + (' misaligned' if misalign else '')
    return {'name': name, 'type': 'frame', 'trigger': trigger, 'stack': stack, 'misalign': misalign,
            'exc': n, 'build': build, 'writes': _prologue_writes({}, en, writes), 'nzcv': nzcv,
            'psp': LAY['PSP_DEFAULT'] - (4 if misalign and stack == 'psp' else 0)}


def _sc_priority(name, pend, prio, expect, en=0):
    """Two pended while PRIMASK holds them, then CPSIE i; every vector is
    `capture`: which is taken first."""
    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, prio, en)
        for reg, val in pend:
            a.store(reg, val)
        a.raw(0xF3BF, 0x8F4F, 0xB662)                     # dsb; cpsie i
        a.nops(SLIDE)
        a.b('END')
        return a.finish(), {}, a.labels
    return {'name': f"priority {name}", 'type': 'priority', 'build': build, 'expect': expect, 'primask': 1,
            'writes': _prologue_writes(prio, en, [(r, 4, v) for r, v in pend])}


def _sc_primask():
    """PRIMASK set: PendSV waits; where is it taken after CPSIE i?"""
    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, {})
        a.store(ICSR, PENDSVSET)
        a.nops(4)
        a.label('cpsie')
        a.raw(0xB662)
        a.label('slide')
        a.nops(SLIDE)
        a.b('END')
        return a.finish(), {}, a.labels
    return {'name': 'PRIMASK holds PendSV until CPSIE', 'type': 'primask', 'build': build, 'primask': 1,
            'writes': _prologue_writes({}, 0, [(ICSR, 4, PENDSVSET)])}


def _sc_svc_masked():
    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, {})
        a.label('trigger')
        a.raw(0xDF00)
        a.nops(4)
        a.b('END')
        return a.finish(), {}, a.labels
    return {'name': 'SVC with PRIMASK set', 'type': 'svc-masked', 'build': build, 'primask': 1,
            'writes': _prologue_writes({})}


def _sc_prio_bits():
    """All ones written to SHPR2, SHPR3 and IPR6, read back into R4-R6."""
    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, {})
        for reg, rd in ((SHPR2, 4), (SHPR3, 5), (IPR6, 6)):
            a.store(reg, 0xFFFFFFFF)
            a.raw(0x6800 | rd)                            # ldr rd, [r0]
        for reg in (SHPR2, SHPR3, IPR6):
            a.store(reg, 0)
        a.b('END')
        return a.finish(), {}, a.labels
    return {'name': 'implemented priority bits', 'type': 'prio-bits', 'build': build,
            'writes': _prologue_writes({}, 0, [(r, 4, v) for r in (SHPR2, SHPR3, IPR6) for v in (0xFFFFFFFF, 0)])}


def _sc_disabled_irq():
    """IRQ 26 pended but not enabled: not taken; ISPR reads it back (R4)."""
    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, {})
        a.store(ISPR, 1 << 26)
        a.raw(0x6804)                                     # ldr r4, [r0]
        a.nops(SLIDE)
        a.b('END')
        return a.finish(), {}, a.labels
    return {'name': 'IRQ26 pended, not enabled', 'type': 'disabled-irq', 'build': build,
            'writes': _prologue_writes({}, 0, [(ISPR, 4, 1 << 26)])}


def _sc_nested():
    """PendSV (lowest priority, own handler) pends IRQ 26 (highest, own
    handler), which preempts it: EXC_RETURN 0xFFFFFFF1, the frame on MSP
    with IPSR 14 stacked."""
    prio = {'shpr3': 0xC0 << 16, 'ipr6': 0}

    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, prio, 1 << 26)
        a.store(ICSR, PENDSVSET)
        a.nops(SLIDE)
        a.b('END')
        a.label('pendsv')
        a.store(ISPR, 1 << 26)
        a.label('preempted')
        a.nops(4)
        a.raw(0x4674)                                     # mov r4, lr
        a.raw(0xF3EF, 0x8009, 0x4661, 0x6101)             # mrs r0,psp; mov r1,r12; str r1,[r0,#16]
        a.raw(0x4770)
        a.label('irq')
        a.raw(0x46F0, 0xF3EF, 0x8005, 0x4681)             # mov r8,lr; mrs r0,ipsr; mov r9,r0
        a.raw(0xF3EF, 0x8008, 0x4683)                     # mrs r0,msp; mov r11,r0
        a.raw(0x69C1, 0x468A, 0x6981, 0x6101)             # ldr r1,[r0,#28]; mov r10,r1; ldr r1,[r0,#24]; str r1,[r0,#16]
        a.raw(0x4770)
        code = a.finish()
        return code, {PENDSV: a.labels['pendsv'], 42: a.labels['irq']}, a.labels
    return {'name': 'nested: IRQ26 preempts PendSV', 'type': 'nested', 'build': build,
            'writes': _prologue_writes(prio, 1 << 26, [(ICSR, 4, PENDSVSET), (ISPR, 4, 1 << 26)])}


def _sc_wfi_masked():
    """WFI with PRIMASK set: SysTick wakes it but is not taken; D right after."""
    rvr = 200

    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, {})
        a.store(SYST_RVR, rvr)
        a.store(SYST_CSR, 7)
        a.raw(0x683E, 0x603E, 0xBF30, 0x683D)             # ldr r6,[r7]; str r6,[r7]; wfi; ldr r5,[r7]
        a.store(SYST_CSR, 5)                              # TICKINT off; SysTick stays pending (masked)
        a.b('END')
        return a.finish(), {}, a.labels
    return {'name': 'WFI wakes with PRIMASK set', 'type': 'wfi-masked', 'build': build, 'primask': 1, 'rvr': rvr,
            'writes': _prologue_writes({}, 0, [(SYST_RVR, 4, rvr), (SYST_CSR, 4, 7), (CVR, 4, 0), (SYST_CSR, 4, 5)])}


INTERRUPTED = {  # name: (halfword, registers loaded/stored, needs PSP in the scratch window, cost per Table 81)
    'LDM r0!,{r1-r7}': (0xC8FE, 7, False, 8),
    'STM r0!,{r1-r7}': (0xC0FE, 7, False, 8),
    'PUSH {r0-r7}': (0xB4FF, 8, True, 9),
    'POP {r0-r7}': (0xBCFF, 8, True, 9),
}
INT_PRE = 6                     # NOPs between the SysTick clear and the instruction
INT_DATA = SCRATCH + 0x40
INT_PSP = SCRATCH + 0x100
INT_SEED = 0x5EED0000
INT_MARK = [0xB0B0B0B0 + 0x01010101 * i for i in range(8)]


def _sc_interrupted(form, k):
    """SysTick fires k cycles into the instruction (k <= 0: before it), by the
    Table 81 model of the cycles from the SysTick clear: the clear's own STR
    (2), INT_PRE NOPs, then the instruction.  RVR = k + 1 + INT_PRE (the
    counter reaches 0 on cycle RVR + 1 after the clear starts)."""
    hw, n, stacky, cost = INTERRUPTED[form]
    rvr = k + 1 + INT_PRE

    def build(slot, table):
        a = _Asm(slot)
        _prologue(a, table, {})
        a.store(SYST_RVR, rvr)
        a.store(SYST_CSR, 7)
        for r in range(7):                                # markers (r0 = the data address for LDM/STM)
            a.ldr(r, INT_DATA if (r == 0 and not stacky) else INT_MARK[r])
        a.raw(0x603E)                                     # str r6,[r7]: clear SysTick, reload next cycle
        a.nops(INT_PRE)
        a.label('insn')
        a.raw(hw)
        a.label('after')
        a.nops(SLIDE)
        a.b('END')
        a.label('stub')
        _stub(a)
        code = a.finish()
        return code, {SYSTICK: a.labels['stub']}, a.labels
    writes = [(SYST_RVR, 4, rvr), (SYST_CSR, 4, 7), (CVR, 4, 0)]
    if form.startswith('STM'):
        writes += [(INT_DATA + 4 * i, 4, 0) for i in range(n)]
    return {'name': f"interrupted {form} k={k}", 'type': 'interrupted', 'form': form, 'k': k, 'rvr': rvr,
            'build': build, 'writes': _prologue_writes({}, 0, writes),
            'psp': INT_PSP if stacky else LAY['PSP_DEFAULT']}


def d4_scenarios():
    s = []
    for trig in ['SVC', 'PendSV'] + [f'IRQ{n}' for n in range(26, 32)] + ['SysTick', 'SysTick+WFI']:
        s.append(_sc_latency(trig))
    s += [_sc_latency('PendSV', misalign=True), _sc_latency('PendSV', 'msp'),
          _sc_latency('PendSV', 'msp', misalign=True), _sc_latency('SVC', 'msp')]
    s += [_sc_tail('PendSV+SysTick'), _sc_tail('IRQ26+IRQ27')]
    for trig in ['SVC', 'PendSV', 'SysTick'] + [f'IRQ{n}' for n in range(26, 32)]:
        s.append(_sc_frame(trig))
    s += [_sc_frame('SVC', misalign=True), _sc_frame('PendSV', misalign=True),
          _sc_frame('PendSV', 'msp'), _sc_frame('PendSV', 'msp', misalign=True), _sc_frame('SVC', 'msp')]
    both = [(ICSR, PENDSVSET | PENDSTSET)]
    irqs = [(ISPR, (1 << 26) | (1 << 31))]
    s += [_sc_priority('PendSV+SysTick, equal', both, {}, PENDSV),
          _sc_priority('PendSV+SysTick, SysTick higher', both, {'shpr3': 0x40 << 16}, SYSTICK),
          _sc_priority('PendSV+SysTick, PendSV higher', both, {'shpr3': 0x80 << 24}, PENDSV),
          _sc_priority('IRQ26+IRQ31, equal', irqs, {}, 42, (1 << 26) | (1 << 31)),
          _sc_priority('IRQ26+IRQ31, IRQ31 higher', irqs, {'ipr6': 0x40 << 16}, 47, (1 << 26) | (1 << 31)),
          _sc_priority('PendSV+IRQ26, equal', [(ISPR, 1 << 26), (ICSR, PENDSVSET)], {}, PENDSV, 1 << 26),
          _sc_priority('PendSV+IRQ26, IRQ26 higher', [(ISPR, 1 << 26), (ICSR, PENDSVSET)],
                       {'shpr3': 0x80 << 16, 'ipr6': 0x40 << 16}, 42, 1 << 26)]
    s += [_sc_primask(), _sc_svc_masked(), _sc_prio_bits(), _sc_disabled_irq(), _sc_nested(), _sc_wfi_masked()]
    for form, (hw, n, stacky, cost) in INTERRUPTED.items():
        for k in range(-2, cost + 3):
            s.append(_sc_interrupted(form, k))
    return s


class _Code:
    """A scenario's code, made once its vector table's address is known (a
    callable, as runner.Batch expects).  It has a len() as well, because
    safety.check_case evaluates len(case['code']) even when it is given
    code_len."""

    def __init__(self, sc, slot):
        self.sc, self.slot = sc, slot
        self.n = len(sc['build'](slot, 0)[0])

    def __call__(self, addrs):
        return self.sc['build'](self.slot, addrs[0])[0]

    def __len__(self):
        return self.n


def _scenario_case(id, sc, slot=None):
    slot = slot or LAY['SLOT']
    _, handlers, _ = sc['build'](slot, 0)
    table = _table(handlers)
    regs = [0, 0, MARK[2], MARK[3], 0, 0, 0, CVR, 0, 0, 0, 0, MARK[12]]
    return R.make_case(id, _Code(sc, slot), kind='scenario', slot=slot,
                       regs=regs, psp=sc.get('psp', LAY['PSP_DEFAULT']), nzcv=sc.get('nzcv', 0),
                       primask=sc.get('primask', 0), seed=INT_SEED, blobs=[(256, table)],
                       writes=sc['writes'])


def d4_entries():
    """The scenarios, with an empty timing case (the overhead) first."""
    out = [{'role': 'calib', 'body': b'', 'slot': LAY['SLOT']}]
    for sc in d4_scenarios():
        code, handlers, labels = sc['build'](LAY['SLOT'], 0)
        out.append({'role': 'scenario', 'sc': sc, 'labels': labels, 'handlers': handlers,
                    'slot': LAY['SLOT'], 'code_len': len(code)})
    return out


def d4_cases(entries=None, runs=RUNS):
    entries = entries or d4_entries()
    out = []
    for run in range(runs):
        for i, e in enumerate(entries):
            id = run * ID_RUN + i
            if e['role'] == 'calib':
                out.append(timing_case(id, b'', layout=LAYOUT))
            else:
                out.append(_scenario_case(id, e['sc']))
    return out


# ── stages ──────────────────────────────────────────────────────────────────

def _kw(stage):
    kw = {'layout': LAYOUT, 'max_cases': MAX_CASES, 'timing': stage != 'D5'}
    if stage == 'D1':
        kw['sites'] = list(SITES.values())
    if stage == 'D4':
        kw.update(flags=F.HF_SYSTICK, scratch_words=128)       # the interrupted STM/PUSH show in the window
    else:
        kw.update(flags=F.HF_SYSTICK | F.HF_NO_SCRATCH)
    return kw


GENERATORS = {'D1': d1_entries, 'D2': d2_entries, 'D3': d3_entries, 'D4': d4_entries, 'D5': d5_entries}


def stage_cases(stage, entries=None, runs=RUNS):
    entries = entries if entries is not None else GENERATORS[stage]()
    return d4_cases(entries, runs) if stage == 'D4' else _timing_cases(entries, runs)


def _stages(ctx):
    """The stages asked for with --stages.  Names of other campaigns' stages
    are left out (one option serves every campaign); none of ours means all."""
    want = getattr(ctx.opts, 'stages', None) if ctx else None
    mine = [s.strip() for s in (want or '').split(',') if s.strip().startswith('D')]
    for s in mine:
        if s not in STAGES:
            raise ValueError(f"no stage {s}: the stages are {', '.join(STAGES)}")
    return mine or list(STAGES)


def dry_cases(ctx):
    out = []
    for s in _stages(ctx):
        kw = {k: v for k, v in _kw(s).items() if k in ('layout', 'sites', 'max_cases')}
        out.append((s, stage_cases(s), kw))
    return out


# ── reading the results ────────────────────────────────────────────────────

def raw_cycles(r, period=1 << 24):
    """Cycles between the two SysTick reads of a timing case (R6, then R5)."""
    return (r['r6'] - r['r5']) % period


def _by_index(recs):
    """{index: {run: (case, result)}}"""
    out = {}
    for c, r in recs:
        out.setdefault(c['id'] % ID_RUN, {})[c['id'] // ID_RUN] = (c, r)
    return out


def _calibrations(recs, entries):
    """The overhead for each (batch, slot) and each (run, slot); the wrap's
    cost likewise (its raw count less the overhead)."""
    over, wrap = {}, {}
    for c, r in recs:
        e = entries[c['id'] % ID_RUN]
        if e['role'] not in ('calib', 'wrap') or r['ipsr'] != 3:
            continue
        run = c['id'] // ID_RUN
        d = over if e['role'] == 'calib' else wrap
        d.setdefault(('batch', c['batch'], c['slot']), raw_cycles(r))
        d.setdefault(('run', run, c['slot']), raw_cycles(r))
    for k, v in list(wrap.items()):
        ov = over.get(k)
        wrap[k] = None if ov is None else v - ov
    return over, wrap


def _lookup(d, c):
    run = c['id'] // ID_RUN
    v = d.get(('batch', c.get('batch'), c['slot']))
    return v if v is not None else d.get(('run', run, c['slot']))


def _agree(values):
    """('measured', value) if every reading is the same, else ('varies', median)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return 'not measured', None
    if all(v == vals[0] for v in vals):
        return 'measured', vals[0]
    s = sorted(vals, key=lambda x: (x is None, x))
    return 'varies', s[len(s) // 2]


def _num(x):
    return int(x) if isinstance(x, float) and x == int(x) else (round(x, 3) if isinstance(x, float) else x)


def _prediction(c):
    """picosim's RP2040-model cycles for the whole body (timing.py m0plus),
    or None if picosim could not run it to the end.  (The replay puts the
    body alone at its slot, so a BX to the instruction after the body lands
    in the sled there: that is an end too.)"""
    from ..replay import body_cycles
    try:
        total, r = body_cycles(c, LAY)
    except Exception:                                  # noqa: BLE001 - no prediction, not a failure
        return None
    return total if r['end'][0] in ('fell-through', 'sled') else None


def timing_rows(recs, entries):
    """One row per timed entry: its readings over the runs, per instance."""
    over, wrap = _calibrations(recs, entries)
    rows = []
    for i, runs in sorted(_by_index(recs).items()):
        e = entries[i]
        per, raws, notes = [], [], []
        for run, (c, r) in sorted(runs.items()):
            end = R.ending(c, r, LAY, [tuple(s) for s in SITES.values()])[0]
            if end != 'fell-through':
                notes.append(f"run {run}: {end}")
                per.append(None)
                raws.append(None)
                continue
            raw = raw_cycles(r)
            raws.append(raw)
            ov = _lookup(over, c)
            if e['role'] == 'calib':
                per.append(raw)
                continue
            if ov is None:
                per.append(None)
                continue
            if e['role'] == 'wrap':
                per.append(raw - ov)
                continue
            w = _lookup(wrap, c) if e['wrap'] else 0
            per.append(None if w is None else _num((raw - ov - w) / e['count']))
        status, value = _agree(per)
        c0 = runs[min(runs)][0]
        pred = None
        if e['role'] == 'form':
            total = _prediction(c0)
            if total is not None:
                pred = _num((total - (4 if e['wrap'] else 0)) / e['count'])
        elif e['role'] == 'calib':
            pred = 2                                    # LDR: 2 cycles (Table 81)
        elif e['role'] == 'wrap':
            pred = 4                                    # 4 x MOV: 1 cycle each
        rows.append({'index': i, 'name': e['name'], 'group': e['group'], 'role': e['role'],
                     'site': e['site'], 'align': e['align'], 'region': e.get('region'), 'count': e['count'],
                     'wrap': e['wrap'], 'pair': e.get('pair'), 'status': status, 'value': value,
                     'readings': per, 'raw': raws, 'prediction': pred, 'notes': notes})
    return rows


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _fact_for(row, prefix, from_):
    from .. import facts as FX
    where = f"{row['site']}.{row['align']}"
    rid = f"{prefix}.{_slug(row['name'])}" + (f"@{row['region']}" if row['region'] else '') + f".{where}"
    pred = None if row['prediction'] is None else {'value': row['prediction'], 'from': from_}
    extra = {'count': row['count'], 'raw': row['raw']}
    if row['status'] == 'varies':
        extra['readings'] = row['readings']
    return FX.fact(rid, row['value'], unit='cycles', source='varies' if row['status'] == 'varies' else 'measured',
                   prediction=pred, **extra)


def single_facts(rows, prefix):
    """A fact for each form at S0 with a word-aligned body, and for any other
    site or alignment where the number is not the same."""
    base = {(r['name'], r['region']): r for r in rows if r['site'] == 'S0' and r['align'] == 'word'}
    fs, diffs = [], 0
    for r in rows:
        if r['status'] == 'not measured':
            continue
        if r['role'] == 'form' and r['prediction'] is not None and r['value'] != r['prediction']:
            diffs += 1
        b = base.get((r['name'], r['region']))
        if r is b or b is None or r['value'] != b['value'] or r['status'] == 'varies':
            if r['role'] == 'calib':
                fs.append(_fact_for(r, f"{prefix}.overhead", 'RP2040 datasheet Table 81'))
            elif r['role'] == 'wrap':
                fs.append(_fact_for(r, f"{prefix}.wrap", 'RP2040 datasheet Table 81'))
            else:
                fs.append(_fact_for(r, prefix, 'timing.py m0plus'))
    seen, out = set(), []
    for f in fs:                                       # calibrations repeat every CALIB_EVERY cases
        if f['id'] not in seen:
            seen.add(f['id'])
            out.append(f)
    return out, diffs


def _dedupe_calibration(rows):
    """Calibrations repeat within a group: keep one row each, all readings together."""
    out, seen = [], {}
    for r in rows:
        if r['role'] in ('calib', 'wrap'):
            k = (r['role'], r['site'], r['align'])
            if k in seen:
                seen[k]['all'] = seen[k].get('all', [seen[k]['value']]) + [r['value']]
                if r['status'] == 'varies' or r['value'] != seen[k]['value']:
                    seen[k]['status'] = 'varies'
                continue
            seen[k] = r
        out.append(r)
    return out


def pair_table(rows):
    """Each ordered pair, per iteration: the chip, and picosim's prediction
    (which adds Table 81's numbers for the two, so a difference is the
    chip's pairing effect)."""
    out = []
    for r in rows:
        if r['role'] != 'form':
            continue
        a, b = r['pair']
        out.append({'first': a, 'second': b, 'status': r['status'], 'cycles': r['value'],
                    'predicted': r['prediction'], 'readings': r['readings'] if r['status'] == 'varies' else None,
                    'additive': r['value'] == r['prediction'] if r['prediction'] is not None else None})
    return out


# ── D4: reading the scenarios ──────────────────────────────────────────────

def scenario_result(e, runs, overhead):
    """What one scenario showed in each run: a dict per run."""
    sc, lab = e['sc'], e['labels']
    out = []
    for run, (c, r) in sorted(runs.items()):
        if r['ipsr'] in R.MARK_NAMES:
            out.append({'ended': R.MARK_NAMES[r['ipsr']]})
            continue
        ov = overhead.get(run, overhead.get('any'))
        t = sc['type']
        d = {}
        if t == 'latency':
            period = (ST_RVR + 1) if sc['trigger'].startswith('SysTick') else (1 << 24)
            A, B, C, D = r['r6'], r['r11'], r['r4'], r['r5']
            spc = r['f_r0']                                  # the stacked return address, via stacked R0
            k = (spc - lab['slide']) // 2 if lab['slide'] <= spc <= lab['after'] else None
            d.update(exc_return=r['r8'], ipsr=r['r9'], frame=r['r10'], stacked_pc=spc,
                     stacked_xpsr=r['f_r1'], nops_before=k,
                     sp_after=(r['frame'] + 32 + (4 if r['f_xpsr'] & 0x200 else 0)) & 0xFFFFFFFF)
            if sc['trigger'].startswith('SysTick'):
                d['entry'] = (ST_RVR + 1 - B) % period
            elif ov is not None and k is not None:
                trig = 0 if sc['trigger'] == 'SVC' else 2
                d['entry'] = (A - B) % period - ov - trig - k
            if ov is not None and k is not None:
                left = (lab['after'] - spc) // 2
                d['exit'] = (C - D) % period - ov - left
            d['ended'] = R.ending(c, r, LAY)[0]
        elif t == 'tail':
            A, B1, C1, B2, C2, D = r['r10'], r['r11'], r['r6'], r['r9'], r['r4'], r['r5']
            spc = r['f_r0']
            k = (spc - lab['slide']) // 2 if lab['slide'] <= spc <= lab['after'] else None
            d.update(exc_return=r['r8'], stacked_pc=spc, nops_before=k)
            if ov is not None:
                d['tail_chain'] = (C1 - B2) % (1 << 24) - ov
                if k is not None:
                    d['entry'] = (A - B1) % (1 << 24) - ov - 2 - k
                    d['exit'] = (C2 - D) % (1 << 24) - ov - (lab['after'] - spc) // 2
            d['ended'] = R.ending(c, r, LAY)[0]
        elif t == 'frame':
            d.update(ipsr=r['ipsr'], exc_return=r['exc_return'], frame=r['frame'],
                     stacked={k2: r[f'f_{k2}'] for k2 in ('r0', 'r1', 'r2', 'r3', 'r12', 'lr', 'pc', 'xpsr')},
                     msp=r['msp'], psp=r['psp'], control=r['control'], primask=r['primask'])
            if sc['trigger'] == 'SysTick':
                d['stacked']['pc'] = 'in the NOP slide' if lab['slide'] <= r['f_pc'] < lab['END'] else r['f_pc']
        elif t == 'priority':
            d.update(taken=r['ipsr'], stacked_pc=r['f_pc'])
        elif t == 'primask':
            pc = r['f_pc']
            d.update(taken=r['ipsr'], after_cpsie=(pc - lab['cpsie'] - 2) // 2 if pc >= lab['cpsie'] else None)
        elif t == 'svc-masked':
            d.update(taken=r['ipsr'], stacked_pc=r['f_pc'] - lab['trigger'])
        elif t == 'prio-bits':
            d.update(shpr2=r['r4'], shpr3=r['r5'], ipr6=r['r6'])
        elif t == 'disabled-irq':
            d.update(taken=r['ipsr'], ispr=r['r4'], ended=R.ending(c, r, LAY)[0])
        elif t == 'nested':
            d.update(irq_exc_return=r['r8'], irq_ipsr=r['r9'], stacked_ipsr=r['r10'] & 0x3F,
                     frame_on_msp=r['r11'], pendsv_exc_return=r['r4'],
                     preempted_after=(r['f_r12'] - lab['preempted']) // 2, ended=R.ending(c, r, LAY)[0])
        elif t == 'wfi-masked':
            d.update(wake=(sc['rvr'] + 1 - r['r5']) % (sc['rvr'] + 1), taken=r['ipsr'])
        elif t == 'interrupted':
            d.update(_interrupted(sc, lab, c, r))
        out.append(d)
    return out


def _interrupted(sc, lab, c, r):
    """Not started, abandoned part-way, or completed: from the stacked PC,
    the registers and the scratch window."""
    form = sc['form']
    hw, n, stacky, cost = INTERRUPTED[form]
    regs = [r['f_r0'], r['f_r1'], r['f_r2'], r['f_r3']] + [r[f'r{i}'] for i in range(4, 8)]
    pc = r['f_pc']
    sp = (r['frame'] + 32 + (4 if r['f_xpsr'] & 0x200 else 0)) & 0xFFFFFFFF
    pat = [F.pattern_word(INT_SEED, i) for i in range(128)]
    changed = r.get('changed_full') if r['overflow'] and r.get('changed_full') is not None else (r['changed'] or [])
    frame_span = range(r['frame'] - SCRATCH, r['frame'] - SCRATCH + 36)
    mem = {o: v for o, v in changed if o not in frame_span}
    effects = []
    if form.startswith('LDM'):
        base = INT_DATA - SCRATCH
        effects = [i for i in range(1, 8) if regs[i] == pat[base // 4 + i - 1]]
        wb = regs[0] - INT_DATA
    elif form.startswith('STM'):
        base = INT_DATA - SCRATCH
        stored = INT_MARK[1:7] + [CVR]                      # r1-r6, and r7 (the SYST_CVR address)
        effects = [i for i in range(7) if mem.get(base + 4 * i) == stored[i]]
        wb = regs[0] - INT_DATA
    elif form.startswith('POP'):
        base = INT_PSP - SCRATCH
        effects = [i for i in range(8) if regs[i] == pat[base // 4 + i]]
        wb = sp - INT_PSP
    else:
        base = INT_PSP - SCRATCH - 32
        pushed = INT_MARK[:7] + [CVR]
        effects = [i for i in range(8) if mem.get(base + 4 * i) == pushed[i]]
        wb = sp - INT_PSP
    if pc < lab['insn']:
        what = 'before'
    elif pc == lab['insn']:
        what = 'abandoned (part done)' if effects else 'not started'
    elif pc == lab['after']:
        what = 'completed'
    else:
        what = f'later (+{(pc - lab["after"]) // 2} NOPs)'
    return {'outcome': what, 'taken': r['ipsr'], 'effects': len(effects), 'writeback': wb}


def _expected_frame(sc, e):
    """The ARMv6-M frame for a frame scenario, from what the code set."""
    lab = e['labels']
    trig = sc['trigger']
    n, reg, val, en = TRIGGERS.get(trig, (SYSTICK, None, None, 0))
    if sc['stack'] == 'psp':
        sp = sc['psp']
    else:
        sp = LAY['MSP_TOP'] - (4 if sc['misalign'] else 0)
    frame = (sp - 32) & ~7
    realign = frame != sp - 32
    st = {'r0': reg if reg else 0xA0A0A0A0, 'r1': val if reg else 0xA1A1A1A1,
          'r2': MARK[2], 'r3': MARK[3], 'r12': MARK[12], 'lr': (LAY['SLED'] + 0x100) | 1,
          'pc': lab['slide'] if trig != 'SysTick' else 'in the NOP slide',
          'xpsr': (sc['nzcv'] << 28) | (1 << 24) | (0x200 if realign else 0)}
    if trig == 'SysTick':
        st['r0'], st['r1'] = 0xA0A0A0A0, 0xA1A1A1A1
    return {'ipsr': n, 'exc_return': F.EXC_RETURN_PSP if sc['stack'] == 'psp' else F.EXC_RETURN_MSP,
            'frame': frame, 'stacked': st}


def _hexed(v):
    if isinstance(v, dict):
        return {k: _hexed(x) for k, x in v.items()}
    if isinstance(v, int) and not isinstance(v, bool) and (v > 0xFFFF or v < 0):
        return f"0x{v & 0xFFFFFFFF:08X}"
    return v


RESTARTED = 'abandoned and restarted when interrupted part-way'
INTERRUPT_OUTCOMES = ('before', 'not started', 'abandoned (part done)', 'completed')


def interrupted_verdict(by_k):
    """RESTARTED if every k's outcome fits an instruction that is abandoned
    and run again from the start (not started, abandoned part done,
    completed before the interrupt, or the interrupt came later); otherwise
    the outcomes that do not fit."""
    odd = {k: v for k, v in by_k.items()
           if not (isinstance(v, str) and (v in INTERRUPT_OUTCOMES or v.startswith('later')))}
    return RESTARTED if not odd else {'does not fit': odd}


def exception_facts(recs, entries):
    """exceptions.json's facts and tables from the D4 records."""
    from .. import facts as FX
    over = {}
    for c, r in recs:
        if entries[c['id'] % ID_RUN]['role'] == 'calib' and r['ipsr'] == 3:
            over.setdefault(c['id'] // ID_RUN, raw_cycles(r))
            over.setdefault('any', raw_cycles(r))
    fs, table, diffs = [], [], 0
    TRM = 'Arm Cortex-M0+ TRM'
    ARM = 'ARMv6-M ARM'
    interrupted = {}
    # The TRM's 15 cycles run from the interrupt signal at the core to the
    # handler's first instruction; D4 starts at the end of the trigger store
    # (SVC: its start), a later point, so the two are different intervals and
    # are not compared (the chip read 12, SVC 13: 2026-11-10).
    TRM_LATENCY = 'the TRM gives 15 cycles from the interrupt signal; this interval starts later (the end of the trigger)'

    def add(fid, results, key, prediction=None, unit=None, **extra):
        """One fact from every run's reading of `key`: measured if all agree."""
        nonlocal diffs
        vals = [_hexed(x.get(key)) for x in results]
        if all(v is None for v in vals):
            return
        keyed = [json.dumps(v, sort_keys=True) for v in vals]
        same = all(k == keyed[0] for k in keyed)
        value = vals[0] if same else vals[keyed.index(Counter(keyed).most_common(1)[0][0])]
        if prediction is not None and same and value != prediction['value']:
            diffs += 1
        kw = dict(extra)
        if not same:
            kw['readings'] = vals
        fs.append(FX.fact(fid, value, unit=unit, source='measured' if same else 'varies',
                          prediction=prediction, **kw))

    for i, runs in sorted(_by_index(recs).items()):
        e = entries[i]
        if e['role'] != 'scenario':
            continue
        sc = e['sc']
        res = scenario_result(e, runs, over)
        table.append({'scenario': sc['name'], 'type': sc['type'], 'runs': [_hexed(x) for x in res]})
        slug = _slug(sc['name'])
        t = sc['type']
        if t == 'latency':
            add(f"exceptions.entry.{slug}", res, 'entry', None, unit='cycles', trm=TRM_LATENCY,
                note="pending (end of the trigger; for SVC its start) to the handler's first instruction"
                     + ("; SysTick: from the count reaching 0 to the handler's first SysTick read"
                        if sc['trigger'].startswith('SysTick') else ''))
            add(f"exceptions.exit.{slug}", res, 'exit', None, unit='cycles',
                note="from the start of BX LR (EXC_RETURN) to the next Thread instruction, the BX included")
            exp = F.EXC_RETURN_PSP if sc['stack'] == 'psp' else F.EXC_RETURN_MSP
            add(f"exceptions.exc_return.{slug}", res, 'exc_return', {'value': _hexed(exp), 'from': ARM})
            n = TRIGGERS.get(sc['trigger'], (SYSTICK,))[0]
            add(f"exceptions.ipsr.{slug}", res, 'ipsr', {'value': n, 'from': ARM})
            add(f"exceptions.nops_before_taken.{slug}", res, 'nops_before', None, unit='instructions',
                note='NOPs after the trigger that ran before the exception was taken')
        elif t == 'tail':
            add(f"exceptions.tail_chain.{slug}", res, 'tail_chain', None, unit='cycles',
                note="from BX LR in the first handler to the second handler's first instruction, the BX included")
            add(f"exceptions.entry.{slug}", res, 'entry', None, unit='cycles', trm=TRM_LATENCY)
            add(f"exceptions.exit.{slug}", res, 'exit', None, unit='cycles')
        elif t == 'frame':
            exp = _hexed(_expected_frame(sc, e))
            # A pend set by a store takes effect a few instructions later
            # (ARMv6-M ARM: only a DSB/ISB guarantees it at once), so the
            # stacked PC may be any NOP of the slide after the trigger: it is
            # compared as such, and how far in is its own fact.
            slide = e['labels'].get('slide')
            pcs = [(x.get('stacked') or {}).get('pc') for x in res]
            if slide is not None and sc['trigger'] != 'SysTick' and pcs and len(set(pcs)) == 1 and \
                    isinstance(pcs[0], int) and slide <= pcs[0] < slide + 2 * SLIDE and pcs[0] % 2 == 0:
                exp['stacked'] = dict(exp['stacked'], pc=_hexed(pcs[0]))
                add(f"exceptions.frame.{slug}.nops_before_taken", [{'n': (p - slide) // 2} for p in pcs], 'n',
                    None, unit='instructions', note='NOPs after the trigger that ran before the exception was taken')
            for key in ('ipsr', 'exc_return', 'frame', 'stacked'):
                add(f"exceptions.frame.{slug}.{key}", res, key, {'value': exp[key], 'from': ARM})
        elif t == 'priority':
            add(f"exceptions.priority.{slug}", res, 'taken', {'value': sc['expect'], 'from': ARM},
                note='the exception taken first (IPSR)')
        elif t == 'primask':
            add("exceptions.primask.taken_after_cpsie", res, 'after_cpsie', None, unit='instructions',
                note='instructions after CPSIE i before the pended PendSV is taken (the ARMv6-M ARM only '
                     'guarantees a CPS that lowers the priority is seen after an ISB, so 0 is not required)')
        elif t == 'svc-masked':
            add("exceptions.svc_with_primask.taken", res, 'taken', {'value': 3, 'from': ARM},
                note='SVC at a priority that cannot take SVCall escalates to HardFault')
        elif t == 'prio-bits':
            for key, exp in (('shpr2', 0xC0000000), ('shpr3', 0xC0C00000), ('ipr6', 0xC0C0C0C0)):
                add(f"exceptions.priority_bits.{key}", res, key, {'value': _hexed(exp), 'from': ARM},
                    note='all ones written, read back: 2 implemented priority bits per field')
        elif t == 'disabled-irq':
            add("exceptions.disabled_irq.taken", res, 'taken', {'value': 3, 'from': ARM},
                note='3: the case ended in the sled (HardFault): the pended IRQ was not taken')
            # only IRQ26's bit is under test: the boot ROM leaves others pending (XIP, SIO FIFOs)
            add("exceptions.disabled_irq.ispr_irq26",
                [{'b': (x['ispr'] >> 26) & 1 if isinstance(x.get('ispr'), int) else x.get('ispr')} for x in res],
                'b', {'value': 1, 'from': ARM}, note='ISPR bit 26 after pending the disabled IRQ26')
            add("exceptions.disabled_irq.ispr", res, 'ispr', None,
                note='the whole ISPR: its other bits are interrupts the boot ROM left pending')
        elif t == 'nested':
            for key, exp in (('irq_exc_return', 0xFFFFFFF1), ('irq_ipsr', 42), ('stacked_ipsr', PENDSV),
                             ('pendsv_exc_return', F.EXC_RETURN_PSP)):
                add(f"exceptions.nested.{key}", res, key, {'value': _hexed(exp), 'from': ARM})
            add("exceptions.nested.preempted_after", res, 'preempted_after', None, unit='instructions',
                note='handler instructions run after the store that pends the IRQ (a pend takes effect a few '
                     'instructions later; only a DSB/ISB guarantees it at once)')
        elif t == 'wfi-masked':
            add("exceptions.wfi.wake_masked", res, 'wake', None, unit='cycles',
                note="from the SysTick count reaching 0 to the first SysTick read after WFI (PRIMASK set)")
        elif t == 'interrupted':
            outs = [x.get('outcome') for x in res]
            st, v = _agree(outs)
            interrupted.setdefault(sc['form'], {})[str(sc['k'])] = v if st == 'measured' else {'varies': outs}
    for form, by_k in interrupted.items():
        fs.append(FX.fact(f"exceptions.interrupted.{_slug(form)}", by_k,
                          source='varies' if any(isinstance(v, dict) for v in by_k.values()) else 'measured',
                          note='SysTick fired k cycles into the instruction (Table 81 model of the cycles '
                               'since the SysTick clear); k <= 0 fires before it'))
        fs.append(FX.fact(f"exceptions.interrupted.{_slug(form)}.verdict", interrupted_verdict(by_k),
                          source='derived', prediction={'value': RESTARTED, 'from': TRM},
                          note='what every k shows together'))
    return fs, table, diffs


# ── the campaign ────────────────────────────────────────────────────────────

def _hook(t):
    release(t, IO_PADS)                                     # APB loads and stores reach IO_BANK0, PADS_BANK0


def _run_path(ctx, stage):
    import os
    base = os.path.join(ctx.root, f"{NAME}-{stage}")
    runs = sorted(d for d in os.listdir(base) if not d.endswith('-repeat'))
    return os.path.join(base, runs[-1])


def _replay_d1(ctx, recs, state, entries):
    """Run 0 of D1, one file per code site and alignment (each with its own
    calibration first, which `check` takes as the overhead)."""
    from .. import facts as FX
    groups = {}
    for c, r in recs:
        if c['id'] // ID_RUN:
            continue
        e = entries[c['id'] % ID_RUN]
        groups.setdefault((e['site'], e['align']), []).append((c, r))
    for (site, align), g in groups.items():
        FX.write_replay(f"timing-D1-{site}-{align}" + ('' if ctx.label == 'chip1' else f"-{ctx.label}"),
                        g, state, ctx.kind)


def run(ctx):
    from .. import facts as FX
    stages = _stages(ctx)
    state0 = None
    timing_facts, tables, diffs = [], {}, 0      # diffs: against picosim's timing.py
    arm_diffs = 0
    d1_rows = None
    for s in ('D1', 'D2', 'D3'):
        if s not in stages:
            continue
        ctx.status(NAME, 'partial', step=s)
        entries = GENERATORS[s]()
        recs, state = ctx.stage(NAME, s, stage_cases(s, entries), on_prepare=_hook, **_kw(s))
        if not recs:
            continue
        state0 = state0 or state
        rows = _dedupe_calibration(timing_rows(recs, entries))
        prefix = f"timing.{s}"
        if s == 'D2':
            fs, n = single_facts([r for r in rows if r['role'] != 'form'], prefix)
            pairs = pair_table(rows)
            odd = [p for p in pairs if p['additive'] is False or p['status'] == 'varies']
            for p in odd:
                fs.append(FX.fact(f"timing.D2.{_slug(p['first'])}+{_slug(p['second'])}", p['cycles'], unit='cycles',
                                  source='varies' if p['status'] == 'varies' else 'measured',
                                  prediction=None if p['predicted'] is None else
                                  {'value': p['predicted'], 'from': 'timing.py m0plus'},
                                  note='per iteration of the pair'))
            n += sum(1 for p in pairs if p['additive'] is False)
            fs.append(FX.fact('timing.D2.pairs_as_predicted', sum(1 for p in pairs if p['additive']),
                              source='derived', of=len(pairs),
                              note='ordered pairs whose cycles are the sum picosim predicts'))
            tables['pairs'] = pairs
            tables['pair_forms'] = [n_ for n_, _ in PAIR_FORMS]
        else:
            fs, n = single_facts(rows, prefix)
            tables['single' if s == 'D1' else 'specials'] = rows
        if s == 'D1':
            d1_rows = rows
            _replay_d1(ctx, recs, state, entries)
        else:
            FX.write_replay(f"timing-{s}" + ('' if ctx.label == 'chip1' else f"-{ctx.label}"),
                            [(c, r) for c, r in recs if c['id'] // ID_RUN == 0], state, ctx.kind)
        timing_facts += fs
        diffs += n
        FX.record_raw(_run_path(ctx, s), ctx.kind)
        ctx.log(f"{s}: {len(rows)} rows, {n} differ from picosim's prediction")
    if 'D5' in stages:
        ctx.status(NAME, 'partial', step='D5')
        entries = GENERATORS['D5']()
        recs, state = ctx.stage(NAME, 'D5', stage_cases('D5', entries), on_prepare=_hook, **_kw('D5'))
        if recs:
            state0 = state0 or state
            rows = timing_rows(recs, entries)
            off = {(r['name'], r['region']): r for r in (d1_rows or [])
                   if r['site'] == 'S0' and r['align'] == 'word' and r['role'] == 'form'}
            cmp_rows = []
            for r in rows:
                if r['role'] != 'form':
                    continue
                o = off.get((r['name'], r['region']))
                cmp_rows.append({'name': r['name'], 'region': r['region'], 'polling_on': r['readings'],
                                 'polling_off': o['readings'] if o else None,
                                 'same': (o is not None and r['status'] == 'measured' and o['status'] == 'measured'
                                          and r['value'] == o['value'])})
            tables['polling'] = cmp_rows
            timing_facts.append(FX.fact('timing.D5.changed_by_polling',
                                        sum(1 for x in cmp_rows if x['polling_off'] is not None and not x['same']),
                                        source='derived', of=len(cmp_rows),
                                        note='forms whose cycles with OpenOCD polling on differ from D1 (polling off)'))
            FX.record_raw(_run_path(ctx, 'D5'), ctx.kind)
    paths = {}
    if timing_facts:
        prov = ctx.provenance(state0, campaign='D', runs_per_case=RUNS)
        paths['timing'] = ctx.write_facts('timing.json', 'timing', prov, timing_facts,
                                          sites={k: {'range': [f"0x{lo:08X}", f"0x{hi:08X}"], 'name': SITE_NAMES[k],
                                                     'slots': {a: f"0x{slot_for(k, a):08X}" for a in ALIGNS}}
                                                 for k, (lo, hi) in SITES.items()},
                                          regions={k: f"0x{v[0]:08X}" for k, v in DATA.items()}, **tables)
    if 'D4' in stages:
        ctx.status(NAME, 'partial', step='D4')
        entries = GENERATORS['D4']()
        recs, state = ctx.stage(NAME, 'D4', stage_cases('D4', entries), on_prepare=_hook, **_kw('D4'))
        if recs:
            state0 = state0 or state
            fs, table, n = exception_facts(recs, entries)
            arm_diffs += n               # against the Arm TRM: picosim has no exceptions yet
            prov = ctx.provenance(state, campaign='D', runs_per_case=RUNS)
            from ...hwtest import standin
            extra = {'standin_guesses': {'entry': standin.ENTRY_CYCLES, 'exit': standin.EXIT_CYCLES}} \
                if ctx.kind == 'standin' else {}
            paths['exceptions'] = ctx.write_facts('exceptions.json', 'exceptions', prov, fs, scenarios=table, **extra)
            FX.record_raw(_run_path(ctx, 'D4'), ctx.kind)
    whole = set(stages) >= set(STAGES) and not ctx.limit
    part = f"only {','.join(stages)}" + (f", {ctx.limit} cases each" if ctx.limit else '')
    ctx.status(NAME, 'done' if whole else 'partial', step=None if whole else part,
               facts=paths.get('timing') or paths.get('exceptions'), files=paths, differences=diffs + arm_diffs,
               picosim_differences=diffs, compared_with='picosim' if diffs else 'datasheet')
    return paths
