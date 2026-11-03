"""
Benchmarks: how many clock cycles the RP2040 really takes for a region of a
program, next to what picosim's RP2040 model (timing.py, datasheet Table 81)
says it takes.

A program marks each region to time with

        bl      bench_start     @ ADDS Rd, Rn, #imm x 64
        ...                     @ the instructions to time
        bl      bench_stop

(the comment names the region; "x 64" says it repeats one instruction 64
times, so the report also gives the difference per instruction).

On the chip the program runs at full speed from SRAM (no wait states, as
Table 81 assumes) and bench_start / bench_stop read the SysTick counter,
which counts processor clock cycles (os_hw.s).  The cycles between the two
reads include a fixed cost of measuring; calibrate() measures it once per
connection with empty regions (calibrate.s) and every region subtracts it.

In picosim the same program runs instruction by instruction and each
region's cycles are added up from the model, counting exactly the
instructions between the return from bench_start and the bl bench_stop.
"""

import os
import re

from .. import timing
from . import image as img
from .lockstep import SimSide, TestError, effective, start_chip
from .rsp import RSPError

HERE = os.path.dirname(os.path.abspath(__file__))
CALIBRATION = os.path.join(HERE, 'calibrate.s')
# Table 81 cycles from one SysTick read to the next across an empty region:
# the rest of hw_bench_start after its read (LDR =, STR, POP {r0,r1}, BX LR:
# 2+2+3+2), BL bench_stop (3), B hw_bench_stop (2), PUSH {r0,r1} (3), LDR =
# (2), and the reading LDR itself (2).
PREDICTED_OVERHEAD = 21
MAX_SIM_INSNS = 3_000_000


def _reps(name):
    m = re.search(r'(?:x|×)\s*(\d+)\b', name or '')
    return int(m.group(1)) if m else 0


def _site_name(prog, site):
    n, src = prog.source.get(site, (0, ''))
    if '@' in src:
        return src.split('@', 1)[1].strip()
    return f"region at line {n}" if n else f"region at 0x{site:04X}"


class Bench:
    def __init__(self, target, source, input_text='', emit=None, stop_flag=None,
                 name=None, timeout=20.0):
        self.target = target
        self.source = source
        self.input_text = input_text
        self.emit = emit or (lambda kind, data: None)
        self.stop_flag = stop_flag
        self.name = name or os.path.basename(source)
        self.timeout = timeout

    def run(self):
        cal = calibrate(self.target, self.stop_flag)
        return self._run(effective(self.source), cal)

    def _run(self, src, cal):
        sim = SimSide(src, self.input_text)
        try:
            hwimg = img.build(src, sim.prog)
        except img.ImageError as e:
            raise TestError(str(e))
        start_chip(self.target, sim, hwimg, bench=True)
        sim_regions, sim_end = sim_regions_of(sim, hwimg)
        pairs, hw_out, hw_end = run_chip(self.target, hwimg, self.input_text,
                                         self.timeout, self.stop_flag)
        rows = []
        for k in range(max(len(sim_regions), len(pairs))):
            s = sim_regions[k] if k < len(sim_regions) else None
            raw = ((pairs[k][0] - pairs[k][1]) & 0xFFFFFF) if k < len(pairs) else None
            row = {'k': k + 1}
            if s:
                row.update(name=_site_name(sim.prog, s['site']), site=s['site'],
                           line=sim.prog.source.get(s['site'], (0, ''))[0],
                           sim=s['cycles'], multicycle=s['multicycle'], insns=s['insns'],
                           mix=sorted(s['mix'].items(), key=lambda kv: -kv[1])[:6],
                           apb=s['apb_r'] + s['apb_w'], apb_r=s['apb_r'], apb_w=s['apb_w'],
                           sio=s['sio'], untimed=s['untimed'], calls=s['calls'])
            else:
                row.update(name=f"region {k + 1}", sim=None)
            if raw is not None and cal and s:
                over = cal['overhead'].get(s['stop_site'] % 4, cal['overhead'].get(0))
                row['raw'] = raw
                row['hw'] = raw - over
            elif raw is not None:
                row['raw'] = raw
                row['hw'] = None
            else:
                row['hw'] = None
            if row.get('sim') is not None and row.get('hw') is not None:
                row['diff'] = row['hw'] - row['sim']
                reps = _reps(row.get('name'))
                if reps:
                    row['reps'] = reps
                    row['per'] = row['diff'] / reps
            row['notes'] = _notes(row)
            rows.append(row)
            self.emit('region', row)
        same = all(r.get('diff') == 0 for r in rows) and rows
        summary = {'name': self.name, 'source': self.source, 'kind': 'bench', 'regions': rows,
                   'end': hw_end, 'sim_end': sim_end, 'image': hwimg.digest,
                   'output': {'sim': bytes(sim.out).decode('latin-1'), 'hw': hw_out},
                   'calibration': cal,
                   'verdict': 'identical' if same else ('different' if rows else 'no regions')}
        if not rows:
            summary['note'] = ("This program has no bench_start / bench_stop regions, so there is "
                               "nothing to time. Add bl bench_start and bl bench_stop around the "
                               "code to time.")
        elif hw_end != 'halted':
            summary['note'] = {'timeout': "The program was still running when the time limit "
                                          "ran out; the regions it finished are shown.",
                               'fault': "The chip took a HardFault; the regions before it are shown.",
                               'stopped': "Stopped by you."}.get(hw_end, '')
        self.emit('summary', summary)
        return summary


def _notes(row):
    notes = []
    if row.get('apb_r'):
        notes.append(f"{row['apb_r']} load(s) from APB peripherals: picosim counts each as "
                     f"{2 + timing.APB_LOAD_EXTRA} cycles, as measured on a real RP2040 "
                     "(Table 81 says 2; the bus bridge adds wait cycles).")
    if row.get('apb_w'):
        notes.append(f"{row['apb_w']} store(s) to APB peripherals: picosim counts each as "
                     f"{2 + timing.APB_STORE_EXTRA} cycles, as measured on a real RP2040 "
                     "(Table 81 says 2; the bus bridge adds wait cycles).")
    if row.get('untimed'):
        notes.append(f"{row['untimed']} instruction(s) that Table 81 gives no time for.")
    if row.get('calls'):
        notes.append("The region calls putchar/getchar: on the chip the debugger stops the core "
                     "there, so its time is not meaningful.")
    if row.get('hw') is not None and row.get('hw') < 0:
        notes.append("Negative: the region is shorter than the calibration's region, or the "
                     "counter wrapped (regions must be under 16 777 215 cycles).")
    return notes


def sim_regions_of(sim, hwimg):
    """Run picosim to the end, adding up each region's cycles."""
    c = sim.core
    calls = hwimg.calls
    start, stop = calls['bench_start'], calls['bench_stop']
    io = (calls['putchar'], calls['getchar'])
    regions, cur, last = [], None, None
    n = 0
    end = 'halted'

    def run_stub():
        ret = c.lr & ~1
        for _ in range(64):
            if c.halted or c.pc == ret:
                return
            c.step()
            c.check_halt()

    while not c.halted:
        if n >= MAX_SIM_INSNS:
            end = 'limit'
            break
        pc = c.pc
        if pc == start:
            cur = {'site': (c.lr & ~1) - 4, 'cycles': 0, 'multicycle': 0, 'insns': 0, 'mix': {},
                   'apb_r': 0, 'apb_w': 0, 'sio': 0, 'untimed': 0, 'calls': 0}
            last = None
            run_stub()
            continue
        if pc == stop:
            if cur is not None:
                cur['stop_site'] = (c.lr & ~1) - 4
                if last:                           # the bl bench_stop is not part of the region
                    t, mc, row = last
                    cur['cycles'] -= t or 0
                    cur['multicycle'] -= mc or 0
                    cur['insns'] -= 1
                    cur['mix'][row] -= 1
                    if not cur['mix'][row]:
                        del cur['mix'][row]
                regions.append(cur)
            cur = None
            run_stub()
            continue
        if pc in io:
            if cur is not None:
                cur['calls'] += 1
            run_stub()
            continue
        try:
            c.step()
        except Exception:
            end = 'fault'
            break
        c.check_halt()
        n += 1
        if cur is not None:
            rec = c.recent(1)[0]
            hw1, hw2 = rec['hw1'], rec['hw2']
            t = timing.cycles('m0plus', hw1, hw2, rec)
            mc = timing.cycles('multicycle', hw1, hw2, rec)
            row = timing.pico_row(hw1, hw2)[1]
            cur['insns'] += 1
            cur['mix'][row] = cur['mix'].get(row, 0) + 1
            if t is None:
                cur['untimed'] += 1
            else:
                cur['cycles'] += t
            cur['multicycle'] += mc or 0
            cur['apb_r'] += rec['reads'][2]
            cur['apb_w'] += rec['writes'][2]
            cur['sio'] += rec['reads'][1] + rec['writes'][1]
            last = (t, mc, row)
    return regions, end


def run_chip(target, hwimg, input_text, timeout, stop_flag):
    """Run the loaded program on the chip at full speed; returns the
    [(start, stop)] SysTick pairs, what it printed and how it ended."""
    calls = hwimg.calls
    inp = list(input_text.encode('latin-1', 'replace'))
    out = bytearray()
    r = target.rsp
    end = 'halted'
    import time
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            end = 'timeout'
            break
        st = target.run(left, stop_flag)
        if st == 'timeout':
            end = 'stopped' if stop_flag and stop_flag.is_set() else 'timeout'
            break
        regs = target.regs()
        if regs[16] & 0x3F:
            end = 'fault'
            break
        pc = img.to_sim(regs[15])
        if pc == calls['putchar']:
            out.append(regs[0] & 0xFF)
        elif pc == calls['getchar']:
            r.write_reg(0, inp.pop(0) if inp else 0xFFFFFFFF)
        else:
            break                  # main returned (or the program's own BKPT)
        r.write_reg(15, regs[14] & ~1)
    try:
        table = r.read_mem(img.to_hw(img.BENCH_TABLE), 8 + 8 * img.BENCH_MAX)
    except RSPError as e:
        raise TestError(f"could not read the results from the chip: {e}")
    count = min(int.from_bytes(table[:4], 'little'), img.BENCH_MAX)
    pairs = [(int.from_bytes(table[8 * k:8 * k + 4], 'little'),
              int.from_bytes(table[8 * k + 4:8 * k + 8], 'little')) for k in range(1, count + 1)]
    return pairs, out.decode('latin-1'), end


def calibrate(target, stop_flag=None, force=False):
    """The chip's cycles for an empty region, by alignment of the bl bench_stop."""
    if getattr(target, 'calibration', None) and not force:
        return target.calibration
    sim = SimSide(CALIBRATION)
    hwimg = img.build(CALIBRATION, sim.prog)
    start_chip(target, sim, hwimg, bench=True)
    regions, _ = sim_regions_of(sim, hwimg)
    pairs, _, end = run_chip(target, hwimg, '', 10.0, stop_flag)
    if end != 'halted' or len(pairs) != len(regions) or not pairs:
        raise TestError("calibration did not run to its end on the chip "
                        f"({end}, {len(pairs)} of {len(regions)} regions)")
    by_align = {}
    for s, (a, b) in zip(regions, pairs):
        by_align.setdefault(s['stop_site'] % 4, []).append((a - b) & 0xFFFFFF)
    cal = {'overhead': {k: min(v) for k, v in by_align.items()},
           'samples': {k: v for k, v in by_align.items()},
           'predicted': PREDICTED_OVERHEAD,
           'steady': all(len(set(v)) == 1 for v in by_align.values())}
    target.calibration = cal
    return cal
