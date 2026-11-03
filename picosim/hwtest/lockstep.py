"""
Lockstep: run a program one instruction at a time in picosim and on the
RP2040 at once, and compare after every instruction.

After each instruction both machines are compared on
  * R0-R15 (a value that is an address may differ by image.OFFSET, see
    image.py; it is shown as picosim's address),
  * the N, Z, C and V flags,
  * every byte the instruction wrote to memory (read back from the chip),
  * whether the instruction faulted (a HardFault on the chip).
When the program ends: all of the program's memory (0x3000-0xFFFF, data,
stack and code), what it printed, and the GPIO registers it may have set.

Calls into the built-in OS (putchar, getchar, bench_start, bench_stop) are
one "call" step: picosim runs its OS; on the chip the test runner does the
same I/O and returns to the caller, so no OS code runs there.

A value loaded from a peripheral (a pin level, a register the chip has but
picosim models differently) is shown as an I/O difference; picosim then takes
the chip's value and carries on, so one input does not spoil the rest.  After
any other difference picosim also takes the chip's state (registers, flags,
the bytes written) unless the run stops at the first difference.
"""

import os
import struct

from ..cpu import SimulatorError, MEM_SIZE
from ..loader import load_program, LoadError
from .. import settings as prog_settings
from . import image as img
from .rsp import RSPError
from .target import TargetError

USER = img.USER_BASE
RG_RAM, RG_SIO, RG_APB, RG_AHB = range(4)
FLAG_NAMES = 'NZCV'


class SimSide:
    """picosim's machine for a test: the program loaded and run to main,
    with putchar/getchar going to strings instead of the console."""

    def __init__(self, source, input_text=''):
        try:
            self.prog = load_program(source)
        except LoadError as e:
            raise TestError(str(e))
        self.core = self.prog.cpu._core
        self.inp = list(input_text.encode('latin-1', 'replace'))
        self.out = bytearray()
        self.core.svc_handler = self._svc
        self.prog.run_to_main()

    def _svc(self, num):
        c = self.core
        if num == 2:
            self.out.append(c.get_reg(0) & 0xFF)
        elif num == 3:
            c.set_reg(0, self.inp.pop(0) if self.inp else 0xFFFFFFFF)
        else:
            raise SimulatorError(f"SVC #{num} is not one Testing can run on the chip; "
                                 "use bl putchar / bl getchar")

    def regs(self):
        c = self.core
        return [c.get_reg(i) for i in range(16)]

    def flags(self):
        c = self.core
        return (c.N << 3) | (c.Z << 2) | (c.C << 1) | int(c.V)

    def set_flags(self, nzcv):
        c = self.core
        c.N, c.Z, c.C, c.V = bool(nzcv & 8), bool(nzcv & 4), bool(nzcv & 2), bool(nzcv & 1)

    def text(self, addr):
        return ' '.join(self.prog.asm_map.get(addr, '?').split())

    def line(self, addr):
        n, src = self.prog.source.get(addr, (0, ''))
        return n, src

    def step_insn(self):
        """Run one instruction.  Returns (writes, io_writes, record, fault):
        writes [(addr, width, value)] to RAM, io_writes likewise to
        peripherals, the core's record of it (timing.py's format), and the
        fault message if it faulted."""
        c = self.core
        writes, io = [], []
        try:
            while True:
                info = c.step_cycle()
                for ch in info['changes']:
                    if ch['kind'] == 'mem':
                        writes.append((ch['addr'], ch['width'], ch['new']))
                    elif ch['kind'] == 'io':
                        io.append((ch['addr'], ch['width'], ch['new']))
                if not c.in_insn or c.halted:
                    break
        except (RuntimeError, SimulatorError) as e:
            return writes, io, None, str(e)
        c.check_halt()
        rec = c.recent(1)
        return writes, io, (rec[0] if rec else None), None


class TestError(Exception):
    pass


def effective(source):
    return prog_settings.effective_source(source)


def start_chip(target, sim, hwimg, bench=False):
    """Load the chip and give it picosim's registers at main (addresses moved)."""
    target.prepare(hwimg, bench=bench)
    regs = sim.regs()
    hw = list(regs)
    for i in (13, 14, 15):
        hw[i] = img.to_hw(regs[i]) if regs[i] <= MEM_SIZE else regs[i]
    if regs[0] == sim.prog.main_addr | 1:
        hw[0] = img.to_hw(regs[0])
    target.set_regs(hw, (sim.flags() << 28) | 0x01000000)


def _flags(xpsr):
    return (xpsr >> 28) & 0xF


def _fmt_flags(v):
    return ''.join(FLAG_NAMES[i] if v & (8 >> i) else '-' for i in range(4))


class Lockstep:
    """One lockstep run.  emit(kind, data) gets 'step' rows as they happen
    and one 'summary' at the end."""

    def __init__(self, target, source, input_text='', max_steps=20000,
                 stop_on_diff=False, emit=None, stop_flag=None, name=None):
        self.target = target
        self.source = source
        self.input_text = input_text
        self.max_steps = max_steps
        self.stop_on_diff = stop_on_diff
        self.emit = emit or (lambda kind, data: None)
        self.stop_flag = stop_flag
        self.name = name or os.path.basename(source)
        self.rows = []
        self.counts = {'same': 0, 'io': 0, 'diff': 0, 'call': 0}
        self.hw_out = bytearray()
        self.io_written = set()          # peripheral registers the program wrote
        self.fault_sp = None             # SP when both machines faulted

    # ── the run ─────────────────────────────────────────────────────────────

    def run(self):
        src = effective(self.source)
        sim = self.sim = SimSide(src, self.input_text)
        try:
            hwimg = self.hwimg = img.build(src, sim.prog)
        except img.ImageError as e:
            raise TestError(str(e))
        self.hw_inp = list(self.input_text.encode('latin-1', 'replace'))
        start_chip(self.target, sim, hwimg)
        end = 'halted'
        n = 0
        while True:
            if sim.core.halted:
                break
            if n >= self.max_steps:
                end = 'limit'
                break
            if self.stop_flag and self.stop_flag.is_set():
                end = 'stopped'
                break
            pc = sim.core.pc
            if pc in hwimg.calls.values():
                row = self._call(n, pc)
            else:
                row = self._step(n, pc)
            n += 1
            self.rows.append(row)
            self.counts[row['status']] += 1
            self.emit('step', row)
            if row.get('fault'):
                end = 'fault'
                break
            if row['status'] == 'diff' and self.stop_on_diff:
                end = 'diff'
                break
        summary = self._finish(end, n)
        self.emit('summary', summary)
        return summary

    def _hw_state(self):
        r = self.target.regs()
        return r[:16], r[16]

    def _compare(self, sim_regs, sim_flags, hw_regs, hw_xpsr):
        diffs = []
        for i in range(16):
            if not img.same(sim_regs[i], hw_regs[i]):
                diffs.append(('R%d' % i) if i < 13 else ('SP', 'LR', 'PC')[i - 13])
        hf = _flags(hw_xpsr)
        for k in range(4):
            if (sim_flags ^ hf) & (8 >> k):
                diffs.append(FLAG_NAMES[k])
        return diffs

    def _row(self, n, pc, kind):
        line, src = self.sim.line(pc)
        return {'n': n, 'addr': pc, 'text': self.sim.text(pc), 'line': line, 'src': src.strip(),
                'kind': kind}

    def _step(self, n, pc):
        sim, t = self.sim, self.target
        row = self._row(n, pc, 'insn')
        hw1 = sim.core.read16(pc)
        bkpt = (hw1 & 0xFF00) == 0xBE00
        writes, io, rec, fault = sim.step_insn()
        hw_fault = None
        if not bkpt:                        # the chip stops at a BKPT without running it
            try:
                t.step()
            except RSPError as e:
                raise TestError(f"the chip did not step: {e}")
        hw_regs, hw_xpsr = self._hw_state()
        if hw_xpsr & 0x3F:
            hw_fault = f"exception {hw_xpsr & 0x3F}" + (" (HardFault)" if hw_xpsr & 0x3F == 3 else '')
        sim_regs, sim_flags = sim.regs(), sim.flags()
        if bkpt:
            # picosim's PC moves past a BKPT; the chip stops on it (the debugger halts it)
            sim_regs[15] = pc
            row['note'] = "BKPT: both stop here."
        row['sim'] = {'regs': sim_regs, 'flags': sim_flags, 'writes': writes, 'io': io}
        hw_writes = self._read_back(writes)
        row['hw'] = {'regs': [img.to_sim(v) for v in hw_regs], 'flags': _flags(hw_xpsr),
                     'writes': hw_writes, 'raw_pc': hw_regs[15]}
        for a, _, _ in io:
            self.io_written.add(self._unalias(a))
        if fault or hw_fault:
            row['fault'] = {'sim': fault, 'hw': hw_fault}
            same = bool(fault) == bool(hw_fault)
            if same:
                self.fault_sp = sim_regs[13]
            row['status'] = 'same' if same else 'diff'
            row['diffs'] = [] if same else ['fault']
            row['note'] = ("Both faulted." if same else
                           ("picosim faulted but the chip did not: " + fault if fault else
                            "The chip took a HardFault but picosim did not."))
            return row
        diffs = self._compare(sim_regs, sim_flags, hw_regs, hw_xpsr)
        for (a, w, v), (_, _, hv) in zip(writes, hw_writes):
            if hv is None or not (img.same(v, hv) if w == 4 else v == hv):
                diffs.append(f"[0x{a:04X}]")
        row['diffs'] = diffs
        io_read = rec is not None and any(rec['reads'][k] for k in (RG_SIO, RG_APB, RG_AHB))
        if not diffs:
            row['status'] = 'same'
        elif io_read and all(not d.startswith('[') for d in diffs):
            row['status'] = 'io'
            row['note'] = ("This instruction read a peripheral register and got a different value "
                           "on the chip. picosim takes the chip's value and carries on.")
        else:
            row['status'] = 'diff'
        if diffs and not (row['status'] == 'diff' and self.stop_on_diff):
            self._resync(hw_regs, hw_xpsr, writes, hw_writes)
        return row

    def _read_back(self, writes):
        """The chip's bytes at each address picosim wrote (one read for them all)."""
        ram = [(a, w) for a, w, _ in writes if a + w <= MEM_SIZE]
        if not ram:
            return [(a, w, None) for a, w, _ in writes]
        lo, hi = min(a for a, _ in ram), max(a + w for a, w in ram)
        try:
            data = self.target.rsp.read_mem(img.to_hw(lo), hi - lo)
        except RSPError:
            data = None
        out = []
        for a, w, _ in writes:
            if data is None or a + w > MEM_SIZE:
                out.append((a, w, None))
            else:
                out.append((a, w, int.from_bytes(data[a - lo:a - lo + w], 'little')))
        return out

    def _resync(self, hw_regs, hw_xpsr, writes, hw_writes):
        c = self.sim.core
        for i in range(16):
            v = img.to_sim(hw_regs[i])
            if c.get_reg(i) != v:
                c.set_reg(i, v)
        self.sim.set_flags(_flags(hw_xpsr))
        for (a, w, _), (_, _, hv) in zip(writes, hw_writes):
            if hv is None:
                continue
            v = img.to_sim(hv) if w == 4 else hv
            for k in range(w):
                c.set_mem_byte(a + k, (v >> (8 * k)) & 0xFF)

    def _call(self, n, pc):
        """A call into the built-in OS: picosim runs it; the chip is told the result."""
        sim, t, calls = self.sim, self.target, self.hwimg.calls
        name = next(k for k, v in calls.items() if v == pc)
        row = self._row(n, pc, 'call')
        row['text'] = f"{name} (built-in OS)"
        ret = sim.core.lr & ~1
        out_before = len(sim.out)
        for _ in range(64):
            if sim.core.halted or sim.core.pc == ret:
                break
            _, _, _, fault = sim.step_insn()
            if fault:
                row.update(status='diff', diffs=['fault'], fault={'sim': fault, 'hw': None},
                           note="picosim faulted inside the built-in OS: " + fault)
                return row
        hw_regs, hw_xpsr = self._hw_state()
        if name == 'putchar':
            self.hw_out.append(hw_regs[0] & 0xFF)
        elif name == 'getchar':
            hw_regs[0] = self.hw_inp.pop(0) if self.hw_inp else 0xFFFFFFFF
            t.rsp.write_reg(0, hw_regs[0])
        hw_regs[15] = hw_regs[14] & ~1
        t.rsp.write_reg(15, hw_regs[15])
        sim_regs, sim_flags = sim.regs(), sim.flags()
        diffs = self._compare(sim_regs, sim_flags, hw_regs, hw_xpsr)
        printed = bytes(sim.out[out_before:]).decode('latin-1')
        row['sim'] = {'regs': sim_regs, 'flags': sim_flags, 'writes': [], 'io': [], 'printed': printed}
        row['hw'] = {'regs': [img.to_sim(v) for v in hw_regs], 'flags': _flags(hw_xpsr), 'writes': [],
                     'raw_pc': hw_regs[15],
                     'printed': chr(hw_regs[0] & 0xFF) if name == 'putchar' else ''}
        row['diffs'] = diffs
        row['status'] = 'call' if not diffs else 'diff'
        if diffs and not self.stop_on_diff:
            self._resync(hw_regs, hw_xpsr, [], [])
        return row

    # ── the end ─────────────────────────────────────────────────────────────

    def _finish(self, end, n):
        sim, t = self.sim, self.target
        summary = {'name': self.name, 'source': self.source, 'kind': 'trace', 'end': end,
                   'steps': n, 'counts': dict(self.counts), 'image': self.hwimg.digest}
        # all of the program's memory
        top = MEM_SIZE
        hw = t.rsp.read_mem(img.to_hw(USER), top - USER)
        mine = sim.core.get_mem_slice(USER, top - USER)
        diffs, mapped = [], 0
        # a HardFault stacks 8 words (36 bytes with alignment) below SP on the
        # chip; picosim stops without stacking, so that part is left out
        skip = (self.fault_sp - 36, self.fault_sp) if self.fault_sp is not None else (0, 0)
        for off in range(0, top - USER, 4):
            if skip[0] <= USER + off < skip[1]:
                continue
            a, b = mine[off:off + 4], hw[off:off + 4]
            if a == b:
                continue
            s, h = struct.unpack('<I', a)[0], struct.unpack('<I', b)[0]
            if img.same(s, h):
                mapped += 1
                continue
            diffs.append({'addr': USER + off, 'sim': s, 'hw': h})
        summary['memory'] = {'start': USER, 'end': top,
                             'compared': top - USER - (skip[1] - skip[0]),
                             'different_words': len(diffs), 'first': diffs[:40],
                             'address_words': mapped,
                             'skipped': list(skip) if skip[1] else None}
        summary['output'] = {'sim': bytes(sim.out).decode('latin-1'),
                             'hw': bytes(self.hw_out).decode('latin-1')}
        hw_regs, hw_xpsr = self._hw_state()       # before _gpio_compare runs code on the core
        summary['gpio'] = self._gpio_compare()
        summary['final'] = {'sim': {'regs': sim.regs(), 'flags': sim.flags()},
                            'hw': {'regs': [img.to_sim(v) for v in hw_regs], 'flags': _flags(hw_xpsr)}}
        identical = (self.counts['diff'] == 0 and not diffs and
                     summary['output']['sim'] == summary['output']['hw'] and
                     not summary['gpio']['diffs'] and end in ('halted', 'limit', 'fault'))
        summary['verdict'] = 'identical' if identical else 'different'
        summary['diff_rows'] = [r for r in self.rows if r['status'] in ('diff', 'io')][:200]
        summary['note'] = {
            'halted': "The program ran to its end on both.",
            'limit': f"Stopped after {n} steps (the step limit); everything up to there was compared.",
            'stopped': "Stopped by you.",
            'fault': ("Both faulted at the same instruction, as they should. The exception "
                      "frame the chip pushed on the stack is left out of the memory comparison."
                      if self.fault_sp is not None else "Stopped at a fault."),
            'diff': "Stopped at the first difference.",
        }[end]
        return summary

    def _gpio_compare(self):
        """SIO GPIO_OUT / GPIO_OE and the pins' IO_BANK0 CTRL and PADS registers."""
        g = self.sim.prog.cpu.gpio
        r = self.target.rsp
        rows, diffs = [], []
        try:
            pairs = [('GPIO_OUT', 0xD0000010), ('GPIO_OE', 0xD0000020)]
            pairs += [(f'GPIO{i}_CTRL', 0x40014004 + 8 * i) for i in range(30)]
            pairs += [(f'PADS GPIO{i}', 0x4001C004 + 4 * i) for i in range(30)]
            ctrl = r.read_mem(0x40014000, 30 * 8)
            pads = r.read_mem(0x4001C004, 30 * 4)
            out, oe = self.target.read_sio()     # the debugger cannot reach the SIO
            hwv = {'GPIO_OUT': out, 'GPIO_OE': oe}
            for i in range(30):
                hwv[f'GPIO{i}_CTRL'] = int.from_bytes(ctrl[8 * i + 4:8 * i + 8], 'little')
                hwv[f'PADS GPIO{i}'] = int.from_bytes(pads[4 * i:4 * i + 4], 'little')
        except (RSPError, TargetError) as e:
            return {'rows': [], 'diffs': [], 'error': str(e)}
        info = []
        for name, addr in pairs:
            s = g.read32(addr)
            h = hwv[name]
            # GPIO_OUT/OE start cleared on both; the pins' CTRL and pad
            # registers count only if the program wrote them (picosim's
            # model of the others' reset values is not under test here)
            counts = addr in (0xD0000010, 0xD0000020) or addr in self.io_written
            rows.append({'name': name, 'addr': addr, 'sim': s, 'hw': h, 'written': counts})
            if s != h:
                (diffs if counts else info).append(name)
        return {'rows': rows, 'diffs': diffs, 'info': info}

    @staticmethod
    def _unalias(addr):
        from ..gpio import GPIO
        return GPIO._unalias(addr)[0]


def fmt_flags(v):
    return _fmt_flags(v)
