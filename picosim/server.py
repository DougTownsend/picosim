"""
JSON-lines backend for the Godot GUI.

The GUI connects over TCP (127.0.0.1) and sends one JSON object per line:
    {"cmd": "step_cycle"}
The server answers with events, also one JSON object per line:
    {"type": "state", ...}         full machine state after every change
    {"type": "program", ...}       disassembly listing after a load
    {"type": "console", "text"}    program (or Pico) output
    {"type": "walkthrough", ...}   the program walkthrough (the panel beside the CPU diagram)
    {"type": "pipe_walkthrough", ...}  the same on the 2-stage pipeline
    {"type": "perf", ...}          performance metrics, machine comparison, pipeline
    {"type": "error", "message"}

All CPU access happens on the single server thread, so the GUI never races
the simulator.  Console input for getchar is queued; when the machine is
about to execute `svc #3` with an empty queue it stalls (waiting_input) and
resumes as soon as input arrives.
"""

import contextlib
import io
import json
import os
import re
import select
import socket
import sys
import threading
import time
from collections import deque

from .loader import load_program, LoadError
from . import settings as prog_settings
from . import challenge as challenges
from . import heat
from . import explain
from . import teaching
from .cpu import SimulatorError, svc_write_data
from .pipeline import Pipeline

REG_NAMES = ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
             'R8', 'R9', 'R10', 'R11', 'R12', 'SP', 'LR', 'PC']
MAX_SPEED = 0            # cps value meaning "as fast as possible"
BOOT_MAX = 16            # the OS's start-up is 3 instructions; Skip to main gives up after this many
FRAME = 1 / 30           # state broadcast interval while running
IDLE_POLL = 0.02         # select() timeout while nothing is running
HEX_DEFAULT = (0x3000, 256)
STACK_SHOWN = 128        # bytes of the stack sent with each state (SP up to the top of RAM)
RAM_TOP = 0x10000
PICO_VID = 0x2E8A
MACHINES = ('multicycle', 'm0plus')
PIPE_SYNC = 256          # lay out the pipeline at least this often at max speed

_BRANCH = re.compile(r'^(b|bl|b(?:eq|ne|hs|cs|lo|cc|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al))(\s+)#(0x[0-9a-f]+|\d+)$')


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def symbolic(text, sym_map):
    """`text` as the student wrote it where the disassembler cannot know: a
    branch or call to a labelled address shows the label (bl #0x10 → bl putchar)."""
    m = _BRANCH.match(text.strip())
    if m:
        name = sym_map.get(int(m.group(3), 0))
        if name:
            return f"{m.group(1):<8} {name}"
    return text


def tidy_source(src):
    """A source line with its runs of spaces squeezed, so more of it fits:
    'movs    r0, #'H'        @ R0 = …' → "movs r0, #'H'   @ R0 = …"."""
    for mark in ('@', '/*'):
        i = src.find(mark)
        if i > 0:
            return ' '.join(src[:i].split()) + '   ' + src[i:].strip()
    return ' '.join(src.split())


def heat_model_name(model):
    from .timing import MACHINE_NAMES
    return MACHINE_NAMES['m0plus' if model == 'm0plus' else 'multicycle']


class Server:
    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', port))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.conn = None
        self.rxbuf = b''

        self.prog = None
        self.path = None
        self.breakpoints = set()
        self.inp = deque()
        self.running = False
        self.cps = MAX_SPEED
        self.cycle_credit = 0.0
        self.last_tick = 0.0
        self.last_frame = 0.0
        self.waiting_input = False
        self.history = []          # cycles of the current / last instruction
        self.changed = set()       # names changed by the last user action
        self.writer = {}           # name -> address of the instruction that changed it
        self.action = 'step'       # what the last user action was (see _begin_action)
        self.mem_changed = set()   # byte addresses written by the last action
        self.hexview = HEX_DEFAULT
        self.exit_code = None
        self.fault = None          # why the CPU stopped, if it faulted
        self.gpio_before = []      # pin states at the start of the last action
        self.dirty = False         # a state update is pending
        self.unsent = False        # Run has made progress the GUI has not been sent

        self.mode = 'sim'          # 'sim' or 'pico'
        self.machine = 'multicycle'  # what Step Cycle steps: see MACHINES
        self.pipe = None             # Pipeline of the loaded program
        self.serial = None
        self.serial_buf = ''         # serial text not shown yet (may end in half a pin frame)
        self.board_pins = None       # (levels, output-enables) last reported by a real board
        self.board_changed = []      # pins that changed in the board's last report
        self.flash_thread = None
        self.flash_result = None
        self.watch_mtime = None      # the loaded file's modification time (auto-reload)
        # Level 1 hides the built-in OS: start at main and step over calls into the OS
        self.skip_boot = False
        self.step_over_os = False
        self.stop_at = None          # (return address, SP) while a Step over runs
        self.boot_steps = 0          # OS start-up instructions already run (skip_boot)
        # Step back rebuilds the machine by running a fresh copy up to the
        # wanted point; these make that replay identical to what happened.
        self.keys_used = []          # characters getchar consumed, in order
        self.journal = []            # [(position, kind, data)]: pin clicks and edits
        self.out_text = ''           # what the program printed since it was loaded
        self.challenge = None        # the program's @ Expect checks (picosim/challenge.py)
        self.check_results = None    # their results once the program has halted
        self.heat_overview = heat.Overview()   # coarse heat-map samples past the trace's end
        self.watch_next = 0.0
        # "Explain this step": the machine before the last user action, what
        # that action was, and the explanation built from them (explain.py)
        self.step_kind = 'load'      # load, insn, cycle, call, run, back, edit, pipe
        self.before = None
        self.insn_before = None      # the machine when the current instruction began
        self.explainer = None
        self.explanation = None
        self.explain_due = False
        self.explain_id = 0
        # Memory tab: what put each word on the stack, {word address: (value, note)};
        # a note is shown only while the word still holds that value
        self.mem_notes = {}
        self._push_k = 0             # which register of the current PUSH is being written

    # ── transport ────────────────────────────────────────────────────────────

    def send(self, obj):
        if not self.conn:
            return
        try:
            self.conn.sendall((json.dumps(obj) + '\n').encode())
        except OSError:
            self.conn = None

    def error(self, message):
        self.send({'type': 'error', 'message': message})

    def console(self, text, source='sim'):
        self.send({'type': 'console', 'text': text, 'source': source})

    def serve_forever(self):
        self.conn, _ = self.sock.accept()
        # Blocking sends (a burst of updates must never look like a hang-up);
        # reads only happen after select() reports data, so they never block.
        self.conn.setblocking(True)
        self.send(teaching.facts())
        if self.path:
            self.cmd_load({'path': self.path})
        else:
            self.send_state()
        while self.conn:
            busy = self.running and not self.waiting_input
            r, _, _ = select.select([self.conn], [], [], self._wait())
            if r:
                try:
                    data = self.conn.recv(65536)
                except OSError:
                    break
                if data == b'':
                    break          # GUI closed the connection
                self.rxbuf += data or b''
                while b'\n' in self.rxbuf:
                    line, self.rxbuf = self.rxbuf.split(b'\n', 1)
                    if line.strip():
                        self.dispatch(line)
            try:
                self.poll_serial()
                self.poll_flash()
                self.poll_file()
                if busy:
                    self.run_slice()
                if self.dirty:
                    self._send_state_now()
            except Exception as e:     # keep the server alive for the GUI
                self.running = False
                self.error(f"internal error: {e!r}")
                self.dirty = True
        self.close_serial()

    def _wait(self):
        """How long the loop may sleep in select(): not at all while running
        flat out, until the next clock cycle is due at a set speed, otherwise
        a short poll (serial, flash, file changes).  A command from the GUI
        wakes it at once, so sleeping never delays a key press."""
        if not self.running or self.waiting_input:
            return IDLE_POLL
        if self.cps == MAX_SPEED or self.stop_at is not None:
            return 0
        due = (1.0 - self.cycle_credit) / self.cps - (time.monotonic() - self.last_tick)
        if self.unsent:
            due = min(due, FRAME - (time.monotonic() - self.last_frame))
        return min(max(0.0, due), IDLE_POLL)

    def dispatch(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return self.error(f"bad JSON: {line[:80]!r}")
        if not isinstance(msg, dict):
            return self.error(f"bad command: {line[:80]!r}")
        cmd = str(msg.get('cmd', ''))
        handler = getattr(self, 'cmd_' + cmd, None)
        if handler is None:
            return self.error(f"unknown command '{cmd}'")
        try:
            handler(msg)
        except Exception as e:     # keep the server alive for the GUI
            self.error(f"{cmd}: {e}")

    # ── state snapshot ───────────────────────────────────────────────────────

    @property
    def core(self):
        return self.prog.cpu._core if self.prog else None

    def mem_hex(self, addr, length):
        c = self.core
        addr = max(0, min(addr, 0x10000))
        length = max(0, min(length, 0x10000 - addr))
        return c.get_mem_slice(addr, length).hex() if length else ''

    def send_state(self):
        """Schedule a state update; queued commands produce a single message."""
        self.dirty = True

    def _send_state_now(self):
        self.dirty = False
        c = self.core
        if c is None:
            return self.send({'type': 'state', 'loaded': False, 'mode': self.mode, 'path': self.path or '',
                              'serial': self.serial.port if self.serial else None})
        pc = c.pc
        sp = c.sp
        hx_addr, hx_len = self.hexview
        cur = c.insn_addr if c.in_insn else pc
        pins = self.gpio_pins()
        before = {p['pin']: p for p in self.gpio_before}
        gpio_changed = [p['pin'] for p in pins
                        if p['pin'] in before and
                        (p['level'], p['dir']) != (before[p['pin']]['level'], before[p['pin']]['dir'])]
        if self.board_live():
            gpio_changed = self.board_changed
        self.send({
            'type': 'state', 'loaded': True, 'path': self.prog.path,
            'regs': list(c.regs), 'flags': {'N': c.N, 'Z': c.Z, 'C': c.C, 'V': c.V},
            'datapath': c.datapath,
            'pc': pc, 'halted': c.halted, 'exit_code': self.exit_code, 'fault': self.fault,
            'steps': c.steps, 'cycles': c.cycles, 'boot_steps': self.boot_steps,
            'at_boot': self.at_boot(),
            'can_back': self.mode == 'sim' and (c.in_insn or c.steps > (self.boot_steps if self.skip_boot else 0)),
            'in_insn': c.in_insn, 'next_state': c.next_state(),
            'cycle_index': c.cycle_index, 'insn_total': c.insn_total,
            'insn_addr': cur, 'insn_text': self.prog.asm_map.get(cur, '???'),
            'running': self.running, 'cps': self.cps,
            'waiting_input': self.waiting_input,
            'keys_typed': len(self.keys_used) + len(self.inp),
            'breakpoints': sorted(self.breakpoints),
            'history': self.history,
            'changed': sorted(self.changed),
            'checks': self._checks(),
            'changed_by': {n: a for n, a in self.writer.items() if n in self.changed},
            'action': self.action,
            'mem_changed': sorted(self.mem_changed),
            'hexview': {'addr': hx_addr, 'data': self.mem_hex(hx_addr, hx_len)},
            'stack': self.stack_view(),
            'mem_access': [] if self.running else self.mem_accesses(),
            'gpio': pins,
            'gpio_changed': gpio_changed,
            'gpio_source': 'board' if self.board_live() else 'sim',
            'mode': self.mode,
            'serial': self.serial.port if self.serial else None,
            'machine': self.machine,
            'pipe': self.pipe.view() if self.machine == 'm0plus' else None,
            'explain': self._explanation(),
        })

    def _explanation(self):
        """The "Explain this step" text for the last user action, built once
        when the action is over (never while Run is on: explanations are
        for stepping, and building one every frame would cost time)."""
        if self.running and not self.waiting_input:
            self.explain_due = True           # explain where Run stops
            return {'kind': 'running'}
        if self.explain_due:
            self.explain_due = False
            try:
                self.explanation = explain.build(self)
            except Exception as e:            # never let an explanation break stepping
                self.explanation = {'kind': 'error', 'now': {'head': '', 'simple':
                                    f"(picosim could not explain this step: {e!r})", 'detail': []}, 'next': None}
            self.explain_id += 1
            self.explanation['id'] = self.explain_id
        return self.explanation

    def _checks(self):
        """The challenge's results once the program has finished (not after a
        fault), else None.  Worked out once per halt; Step back clears them."""
        c = self.core
        if not (self.challenge and c.halted and self.fault is None and self.mode == 'sim'):
            self.check_results = None
        elif self.check_results is None:
            self.check_results = challenges.run(self.challenge, c.regs, self.out_text)
        return self.check_results

    def send_program(self):
        p = self.prog
        c = self.core
        listing = []
        uses_io = False          # a literal of the program's own is a peripheral address
        addrs = sorted(p.asm_map)
        for i, addr in enumerate(addrs):
            text = p.asm_map[addr]
            if text.startswith('.'):               # data: .word / .hword / .byte / .ascii
                size = {'.word': 4, '.hword': 2, '.byte': 1}.get(text.split()[0])
                if size:
                    value = int.from_bytes(c.get_mem_slice(addr, size), 'little')
                    raw = f"{value:0{2 * size}X}"
                    uses_io |= size == 4 and addr >= explain.OS_TOP and value >= 0x40000000
                else:
                    n = (addrs[i + 1] if i + 1 < len(addrs) else addr + 16) - addr
                    raw = c.get_mem_slice(addr, min(n, 4)).hex(' ').upper() + ('…' if n > 4 else '')
            else:
                hw = c.read16(addr)
                wide = c.is_32bit_thumb(hw)
                raw = f"{hw:04X}" + (f" {c.read16(addr + 2):04X}" if wide else '')
            line, src = p.source.get(addr, (0, ''))
            if text.startswith('.') and not src.startswith('.'):
                line, src = 0, ''          # a literal pool: not the ldr line that made it
            listing.append({'addr': addr, 'raw': raw, 'text': text,
                            'shown': symbolic(text, p.sym_map),
                            'label': p.sym_map.get(addr, ''), 'line': line, 'source': tidy_source(src),
                            'os': addr < explain.OS_TOP})
        self.send({'type': 'program', 'path': p.path, 'main': p.main_addr,
                   'listing': listing, 'settings': prog_settings.describe(p.path),
                   'regions': self.regions(), 'uses_io': uses_io,
                   'challenge': self.challenge and {'task': self.challenge['task'],
                                                    'checks': [t for t, _ in self.challenge['checks']]},
                   # every label, data ones too, for the Memory tab's Go to box
                   'labels': {**{name: addr for addr, name in p.sym_map.items()},
                              **p.label_map}})

    def regions(self):
        """Where things are in memory, for the Memory tab's map and colours:
        the program's sections, the OS's code, and runs of instruction vs data
        bytes ([start, end, 'insn' | 'lit']) in the code."""
        p = self.prog
        addrs = sorted(a for a in p.asm_map if a < RAM_TOP)
        runs = []
        for i, a in enumerate(addrs):
            text = p.asm_map[a]
            if i + 1 < len(addrs):
                end = addrs[i + 1]
            else:
                end = a + 2
            if not text.startswith('.'):
                hw = self.core.read16(a)
                end = min(end, a + (4 if self.core.is_32bit_thumb(hw) else 2))
            kind = 'lit' if text.startswith('.') else 'insn'
            if runs and runs[-1][2] == kind and runs[-1][1] == a:
                runs[-1][1] = end
            else:
                runs.append([a, end, kind])
        secs = {k: list(v) for k, v in p.sections.items()}
        top = max([v[1] for v in secs.values()] + [0x3000])
        return {'sections': secs, 'runs': runs, 'program_end': top,
                'os_code_end': max([a + 2 for a in addrs if a < 0x3000] + [0])}

    # ── SVC handling for the GUI console ─────────────────────────────────────

    def _svc(self, num):
        c = self.core
        if num == 0:
            text = svc_write_data(c).decode('latin-1')
            self.out_text += text
            self.console(text)
        elif num == 1:
            self.exit_code = c.get_reg(0)
            c.halted = True
            self.console(f"\n[program exited with code {self.exit_code}]\n", 'system')
        elif num == 2:
            self.out_text += chr(c.get_reg(0) & 0xFF)
            self.console(chr(c.get_reg(0) & 0xFF))
        elif num == 3:
            ch = self.inp.popleft() if self.inp else 0xFFFFFFFF
            if ch != 0xFFFFFFFF:
                self.keys_used.append(ch)
            c.set_reg(0, ch)
        else:
            raise SimulatorError(f"Unknown SVC #{num}")

    def needs_input(self):
        """True if the next cycle (or instruction) would block on getchar."""
        if self.inp:
            return False
        c = self.core
        if c.in_insn:
            return c.next_state() == 'SVC_CALL' and (c.datapath['IR'] & 0xFF) == 3
        if c.pc >= 0x10000:
            return False           # not RAM: the fetch itself will fault
        return c.read16(c.pc) == 0xDF03

    # ── execution primitives ─────────────────────────────────────────────────

    def _record(self, info):
        if info['index'] == 0:
            self.history = []
            self._push_k = 0
        self.history.append(info)
        for ch in info['changes']:
            if ch['kind'] == 'mem':
                self.mem_changed.update(range(ch['addr'], ch['addr'] + ch['width']))
                if ch['width'] == 4:
                    self._note_write(info['insn_addr'], ch['addr'], ch['new'])
            else:
                self.changed.add(ch['name'])
                self.writer[ch['name']] = info['insn_addr']

    def _note_write(self, insn, addr, value):
        """Remember which instruction put a word in memory (the Memory tab's
        stack picture says "LR saved by push {r7, lr} (line 20)")."""
        text = ' '.join(self.prog.asm_map.get(insn, '?').split())
        line, src = self.prog.source.get(insn, (0, ''))
        code = ' '.join(src.split('@')[0].split('/*')[0].split()) or text
        where = f" (line {line})" if line and insn >= 0x3000 else (" (built-in OS)" if insn < 0x3000 else '')
        note = f"written by {code}{where}"
        reg = ''
        if text.startswith('push'):
            regs = sorted(re.findall(r'r\d+|lr', text.split(None, 1)[1].lower()),
                          key=lambda r: 14 if r == 'lr' else int(r[1:]))
            if self._push_k < len(regs):
                reg = regs[self._push_k].upper()
                note = f"{reg} saved by {code}{where}"
            self._push_k += 1
        # the function the instruction is in: the nearest label at or before it
        func = ''
        for a in sorted((a for a in self.prog.sym_map if a <= insn), reverse=True):
            name = self.prog.sym_map[a]
            if not name.startswith(('.', '_end')) and not name[0].isdigit():
                func = name
                break
        self.mem_notes[addr] = (value, {'note': note, 'reg': reg, 'func': func, 'insn': insn})

    def mem_accesses(self):
        """The data reads and writes the last instruction made, worked out
        from its clock cycles: [{addr, size, kind, insn}].  Fetching the
        instruction itself is not counted."""
        out = []
        mar = None
        size = 4
        for h in self.history:
            sig = h['signals']
            for sg in sig:
                if sg.startswith('SIZE='):
                    size = {'word': 4, 'halfword': 2, 'byte': 1}.get(sg[5:], 4)
            if 'MEM.EN' in sig and h['phase'] != 'FETCH' and mar is not None:
                ram = mar + size <= RAM_TOP
                out.append({'addr': mar, 'size': size, 'insn': h['insn_addr'],
                            'kind': 'write' if 'R/W=WRITE' in sig else 'read',
                            # the bytes there now (for a read, what was read)
                            'data': self.core.get_mem_slice(mar, size).hex() if ram else '',
                            # an I/O register write: the value written
                            'value': next((ch['new'] for ch in h['changes'] if ch['kind'] == 'io'), None)})
            for ch in h['changes']:
                if ch['name'] == 'MAR':
                    mar = ch['new']
        return out

    def stack_view(self):
        """The words from SP up to the top of RAM (at most STACK_SHOWN bytes),
        with what put each one there, if the program wrote it while stepping."""
        c = self.core
        lo = c.sp & ~3
        if lo >= RAM_TOP or lo < 0x3000:
            return {'addr': lo, 'data': '', 'notes': {}}
        n = min(STACK_SHOWN, RAM_TOP - lo)
        data = c.get_mem_slice(lo, n)
        notes = {}
        for a in range(lo, lo + n, 4):
            note = self.mem_notes.get(a)
            if note and note[0] == int.from_bytes(data[a - lo:a - lo + 4], 'little'):
                notes[str(a)] = note[1]
        return {'addr': lo, 'data': data.hex(), 'notes': notes}

    def board_live(self):
        """True when the Pico board drawing should show a real board's pins."""
        return self.mode == 'pico' and self.serial is not None and self.board_pins is not None

    def gpio_pins(self):
        if self.board_live():
            levels, oe = self.board_pins
            return [{'pin': i, 'dir': 'out' if oe >> i & 1 else 'in', 'level': levels >> i & 1,
                     'pull': None, 'driven': False, 'func': 5 if oe >> i & 1 else 0x1F}
                    for i in range(30)]
        g = self.prog.cpu.gpio if self.prog else None
        return g.pin_states() if g else []

    def _begin_action(self, action='step', kind=None):
        """Start a user action: 'step', 'call' (Step over), 'run', 'back' (Step back) or 'edit'
        (a value typed into a register, flag, memory or pin).  Changes that
        no single instruction made are put down to it.  `kind` says more
        exactly what it is, for the explanation (see step_kind)."""
        self.action = action
        self.writer = {}
        self.changed = set()
        self.mem_changed = set()
        self.gpio_before = self.gpio_pins()
        c = self.core
        if c is not None:
            self.step_kind = kind or action
            self.before = {'pc': c.insn_addr if c.in_insn else c.pc, 'snap': explain.snap(c),
                           'out': len(self.out_text), 'steps': c.steps, 'cycles': c.cycles}
            if not c.in_insn:
                self.insn_before = self.before
            self.explain_due = True

    def _fault(self, e):
        self.core.halted = True
        self.running = False
        self.fault = str(e)
        self.error(f"CPU fault: {e}")

    def one_cycle(self):
        """Clock one cycle.  Returns False if it could not run."""
        c = self.core
        if c.halted:
            return False
        if c.in_insn and self.needs_input():
            self.waiting_input = True
            return False
        self.waiting_input = False
        try:
            info = c.step_cycle()
        except (RuntimeError, SimulatorError) as e:
            self._fault(e)
            return False
        self._record(info)
        if info['last']:
            c.check_halt()
        return True

    def one_insn(self, record=True):
        """Finish the current instruction (or run the next one)."""
        c = self.core
        if c.halted:
            return False
        if not record and not c.in_insn:
            if self.needs_input():
                self.waiting_input = True
                return False
            self.waiting_input = False
            try:
                c.step()
            except (RuntimeError, SimulatorError) as e:
                self._fault(e)
                return False
            c.check_halt()
            self.history = []
            return True
        while True:
            if not self.one_cycle():
                return False
            if not c.in_insn:
                return True

    # ── 2-stage pipeline (machine 'm0plus', see picosim/pipeline.py) ────────

    def _run_insn(self):
        return self.one_insn(record=True)

    def pipe_step(self):
        return self.pipe.step(self._run_insn)

    def pipe_step_insn(self):
        return self.pipe.step_insn(self._run_insn)

    def _pipe_draining(self):
        return self.machine == 'm0plus' and self.pipe is not None and self.pipe.draining()

    # ── run loop ─────────────────────────────────────────────────────────────

    def run_slice(self):
        c = self.core
        now = time.monotonic()
        start = now
        pipe = self.machine == 'm0plus'
        if c is None or (c.halted and not self._pipe_draining()):
            self.running = False
            self.send_state()
            return
        hit = False
        if self.cps == MAX_SPEED or self.stop_at is not None:   # a Step over runs flat out
            before = list(c.regs)
            before_flags = (c.N, c.Z, c.C, c.V)
            while time.monotonic() - start < 0.01:
                for _ in range(PIPE_SYNC if pipe else 2000):
                    if not self.one_insn(record=False):
                        break
                    if c.pc in self.breakpoints or self._returned():
                        hit = True
                        break
                else:
                    if pipe:
                        self.pipe.sync()
                    continue
                break
            if pipe:
                self.pipe.follow()
            if c.itrace_dropped:
                self.heat_overview.maybe(c)   # the trace is full: keep coarse samples
            after = c.regs
            ran = {REG_NAMES[i] for i in range(16) if before[i] != after[i]}
            ran |= {f for f, a, b in zip('NZCV', before_flags, (c.N, c.Z, c.C, c.V)) if a != b}
            self.changed |= ran
            self.unsent = True
        else:
            self.cycle_credit += (now - self.last_tick) * self.cps
            self.cycle_credit = min(self.cycle_credit, self.cps * 0.1 + 1)
            while self.cycle_credit >= 1:
                self.cycle_credit -= 1
                self.unsent = True
                if pipe:
                    if not self.pipe_step():
                        break
                    if self.pipe.at_breakpoint(self.breakpoints):
                        hit = True
                        break
                    continue
                if not self.one_cycle():
                    break
                if not c.in_insn and (c.pc in self.breakpoints or self._returned()):
                    hit = True
                    break
        self.last_tick = now
        if hit or (c.halted and not self._pipe_draining()):
            self.running = False
            self.stop_at = None
        # at a set speed most passes run no cycle at all: send only real progress
        if not self.running or self.waiting_input or (self.unsent and now - self.last_frame >= FRAME):
            self.last_frame = now
            self.unsent = False
            self.send_state()

    # ── commands ─────────────────────────────────────────────────────────────

    def poll_file(self):
        """Reload the program when its file is saved (checked twice a second),
        also after a failed load, so fixing a mistake and saving is enough."""
        now = time.monotonic()
        if not self.path or now < self.watch_next:
            return
        self.watch_next = now + 0.5
        m = _mtime(self.path)
        if m is not None and self.watch_mtime is not None and m != self.watch_mtime \
                and not (self.flash_thread and self.flash_thread.is_alive()):
            self.console(f"[{os.path.basename(self.path)} changed on disk: reloading]\n", 'system')
            self.cmd_load({'path': self.path})

    def cmd_load(self, msg):
        path = msg.get('path') or self.path
        if not path:
            return self.error("no program to load")
        path = os.path.abspath(os.path.expanduser(path))
        self.path = path                  # kept after a failed load: Reload and auto-reload retry it
        self.watch_mtime = _mtime(path)
        try:
            # assemble with the student's program settings filled in (if any)
            src = prog_settings.effective_source(path)
            prog = load_program(src)
        except LoadError as e:
            from . import errors
            raw = str(e)
            self.send({'type': 'load_error', 'path': path, 'message': errors.clean(raw) or raw,
                       'errors': errors.explain(raw, src if src.endswith('.s') else path)})
            self.send_state()             # so the GUI's Reload can retry
            return
        except OSError as e:
            self.send({'type': 'load_error', 'path': path, 'message': str(e)})
            return
        prog.path = path
        prog.cpu._core.itrace_enable(heat.TRACE_CAP)   # the heat map's timeline
        self.heat_overview = heat.Overview()
        self.prog, self.path = prog, path
        self.core.svc_handler = self._svc
        self.running = False
        self.waiting_input = False
        self.history = []
        self.exit_code = None
        self.fault = None
        self.stop_at = None
        self.boot_steps = 0
        self.keys_used, self.journal, self.out_text = [], [], ''
        self.mem_notes = {}
        self.challenge, self.check_results = None, None
        challenge_error = None
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                self.challenge = challenges.parse(f.read())
        except challenges.ChallengeError as e:
            challenge_error = str(e)
        except OSError:
            pass
        if self.skip_boot:
            prog.run_to_main()
            self.boot_steps = self.core.steps   # level 1 counts from main
        self.pipe = Pipeline(self.core, prog.asm_map)
        self.explainer = explain.Explainer(prog)
        self._begin_action(kind='load')
        self.breakpoints &= set(prog.asm_map)
        self.hexview = (max(prog.main_addr, 0x3000) & ~0xF, HEX_DEFAULT[1])
        self.send_program()
        note = ''
        if prog_settings.declared(path):
            note = " — this program has settings: click Settings in the toolbar"
        if not msg.get('quiet'):
            self.console(f"[loaded {os.path.basename(path)}{note}]\n", 'system')
        if challenge_error:
            self.console(f"[the challenge in this file cannot be checked: {challenge_error}]\n", 'system')
        self.send_state()

    def cmd_reload(self, msg):
        self.cmd_load({'path': self.path})

    def cmd_set_settings(self, msg):
        """Store the student's values for the loaded program's settings
        (never in the file itself) and load it again with them."""
        if not self.path:
            return self.error("no program loaded")
        try:
            if msg.get('defaults'):
                prog_settings.clear(self.path)
            else:
                prog_settings.set_values(self.path, msg.get('values') or {})
        except ValueError as e:
            return self.send({'type': 'settings_error', 'message': str(e)})
        except OSError as e:
            return self.send({'type': 'settings_error', 'message': f"could not save settings: {e}"})
        self.cmd_load({'path': self.path})

    cmd_reset = cmd_reload

    def _settle(self):
        """Finish a Step over that is still running when the next step key
        arrives (holding S at level 1), so every press is one whole step.
        A call that takes longer than a moment is left running instead."""
        for _ in range(20):
            if self.stop_at is None or not self.running or self.waiting_input:
                return
            self.run_slice()

    def cmd_step_cycle(self, msg):
        if not self.core:
            return
        self._settle()
        self.running = False
        self._begin_action(kind='pipe' if self.machine == 'm0plus' else 'cycle')
        if self.machine == 'm0plus':
            self.pipe_step()
        else:
            self.one_cycle()
        self.send_state()

    def cmd_step_insn(self, msg):
        if not self.core:
            return
        self._settle()
        if self.step_over_os and self.machine != 'm0plus':
            call = self.call_at_pc()
            if call and call[1] < 0x3000:      # bl putchar / bl getchar: one step
                return self.cmd_step_over(msg)
        self.running = False
        self._begin_action(kind='insn')
        if self.machine == 'm0plus':
            self.pipe_step_insn()
        else:
            self.one_insn(record=True)
            self._finish_in_os()
        self.send_state()

    def _finish_in_os(self):
        """With the OS hidden, a main that has returned runs the OS's last
        instructions (bkpt) at once, so the program simply halts."""
        c = self.core
        if not self.step_over_os:
            return
        for _ in range(16):
            if c.halted or c.in_insn or c.pc >= 0x3000 or self.waiting_input:
                break
            if not self.one_insn(record=True):
                break

    def cmd_run(self, msg):
        c = self.core
        if not c or (c.halted and not self._pipe_draining()):
            return self.send_state()
        self.cps = max(0, int(msg.get('cps', self.cps)))
        self._begin_action('run')
        self.stop_at = None
        self.running = True
        self.last_tick = time.monotonic()
        self.cycle_credit = 1.0
        # step off a breakpoint we are sitting on
        if self.machine == 'm0plus':
            if self.pipe.at_breakpoint(self.breakpoints):
                if self.cps == MAX_SPEED:
                    self.one_insn(record=False)
                    self.pipe.follow()
                else:
                    self.pipe_step()
        elif not c.in_insn and c.pc in self.breakpoints:
            if self.cps == MAX_SPEED:
                self.one_insn(record=False)
            else:
                self.one_cycle()
        self.send_state()

    # ── step back ───────────────────────────────────────────────────────────
    # The machine cannot be wound backwards, so Step back builds a fresh copy
    # of the program and runs it to the wanted point, with the same keys, pin
    # clicks and edits at the same moments.  A position is (instructions
    # completed, cycles into the next one).

    def _pos(self, c=None):
        c = c or self.core
        if c.in_insn:
            return (c.steps - 1, c.cycle_index)
        return (c.steps, 0)

    def _back_target(self):
        """Where one Step back goes, or None at the very start."""
        c = self.core
        done, k = self._pos()
        if k > 1:
            return (done, k - 1)                 # one cycle back, same instruction
        if k == 1:
            return (done, 0)                     # back to its start
        first = self.boot_steps if self.skip_boot else 0
        t = done - 1
        if self.step_over_os:
            # with the OS hidden, go back to the last line of the program itself:
            # the next instruction at state T is record seq T + 1
            addr = {r['seq']: r['addr'] for r in c.recent(512)}
            while t > first and addr.get(t + 1, 0x3000) < 0x3000:
                t -= 1
        return (t, 0) if t >= first else None

    def _replay(self, target, keys=None):
        """A fresh copy of the program run to `target`; returns (program,
        printed text, keys consumed).  `keys` defaults to the keys getchar
        has consumed so far."""
        new = self.prog.clone()
        nc = new.cpu._core
        nc.itrace_enable(heat.TRACE_CAP)
        keys = iter(self.keys_used if keys is None else keys)
        used = []
        out = []

        def svc(num):
            if num == 0:
                out.append(svc_write_data(nc).decode('latin-1'))
            elif num == 1:
                nc.halted = True
            elif num == 2:
                out.append(chr(nc.get_reg(0) & 0xFF))
            elif num == 3:
                ch = next(keys, 0xFFFFFFFF)
                if ch != 0xFFFFFFFF:
                    used.append(ch)
                nc.set_reg(0, ch)
        nc.svc_handler = svc
        if self.skip_boot:
            new.run_to_main()

        def advance(pos):
            done, k = pos
            if nc.in_insn:                       # finish a partial instruction first
                while nc.in_insn:
                    nc.step_cycle()
                nc.check_halt()
            n = done - nc.steps
            if n > 0:
                nc.run(n)
            for _ in range(k):
                info = nc.step_cycle()
                if info['last']:
                    nc.check_halt()

        for pos, kind, data in self.journal:
            if pos > target:
                break
            if pos != self._pos(nc):
                advance(pos)
            if kind == 'gpio':
                new.cpu.gpio.set_external(data[0], None if data[1] is None else bool(data[1]))
            elif kind == 'reg':
                nc.set_reg(*data)
            elif kind == 'flag':
                setattr(nc, data[0], data[1])
            elif kind == 'mem':
                for i, b in enumerate(data[1]):
                    nc.set_mem_byte(data[0] + i, b)
        if self._pos(nc) != target:
            advance(target)
        return new, ''.join(out), used

    def cmd_step_back(self, msg):
        """Undo one instruction (or one clock cycle, partway through one)."""
        c = self.core
        if c is None or self.mode != 'sim':
            return
        self.running = False
        self.stop_at = None
        target = self._back_target()
        if target is None:
            return self.error("Already at the start of the program: there is nothing to step back to.")
        if self._jump(target, 'back'):
            self.send_state()

    def _jump(self, target, kind):
        """Put the machine at `target` (instructions done, cycles into the
        next) by replaying a fresh copy, backwards or forwards: Step back,
        and the walkthrough panel's Go here.  False if it could not."""
        c = self.core
        before_regs = list(c.regs)
        before_flags = {f: getattr(c, f) for f in 'NZCV'}
        before_mem = c.get_memory()
        self._begin_action('back', kind=kind)
        keys = list(self.keys_used) + list(self.inp)      # forwards, queued keys are typed in order
        try:
            new, printed, used = self._replay(target, keys)
        except (RuntimeError, SimulatorError) as e:
            self.error(f"{'step back' if kind == 'back' else 'going there'} failed: {e}")
            return False
        self.inp = deque(keys[len(used):])                 # keys not read yet come again
        self.keys_used = used
        self.journal = [e for e in self.journal if e[0] <= target]
        self.out_text = printed
        # words the replay put back are checked against their value before a
        # note is shown, so notes from the undone step simply stop showing
        new.path = self.prog.path
        nc_cycles = new.cpu._core.cycles
        self.heat_overview.snaps = [x for x in self.heat_overview.snaps if x[0] <= nc_cycles]
        self.prog = new
        self.core.svc_handler = self._svc
        self.fault = None
        self.exit_code = None
        self.waiting_input = False
        self.history = []
        self.pipe = Pipeline(self.core, new.asm_map)
        nc = self.core
        self.changed = {REG_NAMES[i] for i in range(16) if nc.regs[i] != before_regs[i]}
        self.changed |= {f for f in 'NZCV' if getattr(nc, f) != before_flags[f]}
        after = nc.get_memory()
        self.mem_changed = {i for i in range(len(after)) if after[i] != before_mem[i]} \
            if after != before_mem else set()
        self.send({'type': 'console_reset',
                   'note': f"[loaded {os.path.basename(self.path)}]\n", 'text': printed})
        return True

    def cmd_goto(self, msg):
        """The walkthrough panel's Go here: put the machine just before
        instruction `step` (0 = the first one after reset, in the order the
        program runs), then run `cycle` of its clock cycles, recorded as Step
        cycle would, so the CPU diagram shows the last one.  The student
        carries on stepping from there."""
        c = self.core
        if c is None or self.mode != 'sim':
            return
        self._settle()
        self.running = False
        self.stop_at = None
        first = self.boot_steps if self.skip_boot else 0
        n = max(first, int(msg.get('step', 0)))
        k = 0 if self.machine == 'm0plus' else max(0, int(msg.get('cycle', 0)))
        if self._pos() != (n, 0) and not self._jump((n, 0), 'goto'):
            return self.send_state()
        if self._pos() == (n, 0) and k == 0 and self.step_kind != 'goto':
            self._begin_action('back', kind='goto')
        for _ in range(k):
            if not self.one_cycle() or not self.core.in_insn:
                break
        self.send_state()

    # ── step over, and hiding the OS at level 1 ─────────────────────────────

    def cmd_set_options(self, msg):
        """skip_boot: start programs at main.  step_over_os: Step instruction
        runs a call into the built-in OS (bl putchar) as one step.  The GUI
        turns both on at level 1.  A program that has not run past its
        start-up yet is loaded again so the change shows at once."""
        skip = bool(msg.get('skip_boot', self.skip_boot))
        self.step_over_os = bool(msg.get('step_over_os', self.step_over_os))
        c = self.core
        fresh = c is not None and c.steps <= 3 and not c.in_insn and not self.running
        if skip != self.skip_boot:
            self.skip_boot = skip
            if fresh and self.path:
                self.cmd_load({'path': self.path, 'quiet': True})
                return
        self.send_state()

    def at_boot(self):
        """The program has not reached main yet: only the built-in OS's
        start-up has run."""
        c = self.core
        main = self.prog.main_addr if self.prog else None
        return bool(c and main and not c.halted and c.steps < BOOT_MAX
                    and (c.in_insn or c.pc != main) and (c.insn_addr if c.in_insn else c.pc) < explain.OS_TOP)

    def cmd_run_to_main(self, msg):
        """Run the OS's start-up to the first instruction of main, for real
        (so the step and cycle counts stay honest)."""
        self._settle()
        c = self.core
        if not self.at_boot():
            return self.send_state()
        self.running = False
        self._begin_action(kind='run')
        main = self.prog.main_addr
        for _ in range(BOOT_MAX):
            if c.halted or self.fault or (not c.in_insn and c.pc == main):
                break
            if self.machine == 'm0plus':
                self.pipe_step_insn()
            elif not self.one_insn(record=True):
                break
        self.send_state()

    def call_at_pc(self):
        """(return address, call target) if the next instruction is BL or
        BLX Rm, else None."""
        c = self.core
        if c is None or c.in_insn or c.halted:
            return None
        pc = c.pc
        hw = c.read16(pc)
        if (hw & 0xF800) == 0xF000:
            hw2 = c.read16(pc + 2)
            if (hw2 & 0xD000) == 0xD000:          # BL
                s = (hw >> 10) & 1
                j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
                i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
                off = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1)
                if s:
                    off -= 1 << 25
                return pc + 4, (pc + 4 + off) & 0xFFFFFFFF
        if (hw & 0xFF87) == 0x4780:               # BLX Rm
            return pc + 2, c.get_reg((hw >> 3) & 0xF) & ~1
        return None

    def _returned(self):
        """A Step over has come back from its call (the SP check keeps a
        recursive call from stopping too early)."""
        if self.stop_at is None:
            return False
        c = self.core
        return not c.in_insn and c.pc == self.stop_at[0] and c.sp >= self.stop_at[1]

    def cmd_step_over(self, msg):
        """Run a whole call (BL / BLX) and stop at the instruction after it;
        any other instruction is just stepped.  Breakpoints inside the call,
        a getchar waiting for a key and faults all stop it as Run does."""
        self._settle()
        c = self.core
        if not c or c.halted:
            return self.send_state()
        call = self.call_at_pc()
        if call is None:
            return self.cmd_step_insn({}) if not self.step_over_os else self._plain_step()
        self._begin_action('call')
        self.stop_at = (call[0], c.sp)
        self.running = True
        self.last_tick = time.monotonic()
        self.one_insn(record=True)        # the call itself, so its cycles show
        if self.machine == 'm0plus':
            self.pipe.follow()
        if self._returned() or c.halted:
            self.running = False
            self.stop_at = None
        self.send_state()

    def _plain_step(self):
        self.running = False
        self._begin_action(kind='insn')
        if self.machine == 'm0plus':
            self.pipe_step_insn()
        else:
            self.one_insn(record=True)
            self._finish_in_os()
        self.send_state()

    def cmd_set_machine(self, msg):
        """Choose what Step Cycle / Step Insn / Run step: the multi-cycle
        datapath's clock cycles or the RP2040's 2-stage pipeline cycles."""
        m = msg.get('machine', 'multicycle')
        if m not in MACHINES:
            return self.error(f"bad machine {m}")
        if m != self.machine:
            self.running = False
            c = self.core
            if c and m == 'm0plus' and c.in_insn:
                self._begin_action()
                self.one_insn(record=True)    # the pipeline starts between instructions
            self.machine = m
            if self.pipe:
                self.pipe.reset()
        self.send_state()

    def cmd_set_speed(self, msg):
        self.cps = max(0, int(msg.get('cps', 0)))
        self.send_state()

    def cmd_pause(self, msg):
        self.running = False
        self.stop_at = None
        self.send_state()

    def cmd_set_bp(self, msg):
        self.breakpoints.add(int(msg['addr']))
        self.send_state()

    def cmd_clear_bp(self, msg):
        self.breakpoints.discard(int(msg['addr']))
        self.send_state()

    def cmd_toggle_bp(self, msg):
        a = int(msg['addr'])
        self.breakpoints ^= {a}
        self.send_state()

    def cmd_set_reg(self, msg):
        n = int(msg['reg'])
        v = int(msg['value']) & 0xFFFFFFFF
        self.core.set_reg(n, v)
        self.journal.append((self._pos(), 'reg', (n, v)))
        self._begin_action('edit')
        self.changed.add(REG_NAMES[n & 15])
        self.send_state()

    def cmd_set_flag(self, msg):
        name = msg['flag']
        if name not in 'NZCV' or len(name) != 1:
            return self.error(f"bad flag {name}")
        setattr(self.core, name, 1 if msg['value'] else 0)
        self.journal.append((self._pos(), 'flag', (name, 1 if msg['value'] else 0)))
        self._begin_action('edit')
        self.changed.add(name)
        self.send_state()

    def cmd_write_mem(self, msg):
        addr = int(msg['addr'])
        data = bytes.fromhex(msg['data'])
        if addr < 0 or addr + len(data) > 0x10000:
            return self.error("write_mem out of range")
        for i, b in enumerate(data):
            self.core.set_mem_byte(addr + i, b)
        self.journal.append((self._pos(), 'mem', (addr, data)))
        self._begin_action('edit')
        self.mem_changed = set(range(addr, addr + len(data)))
        self.send_state()

    def cmd_view_mem(self, msg):
        self.hexview = (int(msg['addr']) & 0xFFFF, int(msg.get('len', HEX_DEFAULT[1])))
        self.send_state()

    def cmd_gpio_set(self, msg):
        """Drive an input pin from outside: value 0, 1 or null (floating)."""
        pin = int(msg['pin'])
        v = msg.get('value')
        self._begin_action('edit')
        try:
            self.prog.cpu.gpio.set_external(pin, None if v is None else bool(v))
        except ValueError as e:
            return self.error(str(e))
        self.journal.append((self._pos(), 'gpio', (pin, v)))
        self.send_state()

    def cmd_console_input(self, msg):
        text = msg.get('text', '')
        if self.mode == 'pico':
            if self.serial:
                self.serial.write(text.encode('latin-1', 'replace'))
            return
        self.inp.extend(ord(ch) & 0xFF for ch in text)
        if self.waiting_input:
            self.waiting_input = False
            self.last_tick = time.monotonic()
        self.send_state()

    def cmd_state(self, msg):
        self.send_state()

    def cmd_perf(self, msg):
        """Performance report for the run so far (see picosim/timing.py).

        msg: mul ('fast' | 'iterative'), wait (0-3, multi-cycle memory wait
        states), machine ('m0plus' | 'pipe3') and n (instructions in the
        pipeline chart)."""
        from . import timing
        c = self.core
        if c is None:
            return self.send({'type': 'perf', 'loaded': False})
        mul = 32 if msg.get('mul') == 'iterative' else 1
        wait = max(0, min(3, int(msg.get('wait', 0))))
        machine = msg.get('machine', 'm0plus')
        if machine not in timing.DEPTH:
            machine = 'm0plus'
        n = max(1, min(timing_recent_max(), int(msg.get('n', 40))))
        asm = self.prog.asm_map
        prof = c.profile()
        stock = timing.summarize(prof, 1, 0, asm)
        custom = stock if (mul, wait) == (1, 0) else timing.summarize(prof, mul, wait, asm)
        nxt = None if c.halted else (c.insn_addr if c.in_insn else c.pc)
        sched = timing.schedule(machine, c.recent(n), mul, asm, nxt)
        for row in sched['rows']:
            row['stages'] = sorted([k, v] for k, v in row['stages'].items())
        self.send({'type': 'perf', 'loaded': True, 'stock': stock, 'custom': custom,
                   'schedule': sched, 'names': timing.MACHINE_NAMES,
                   'clock_mhz': timing.PICO_CLOCK_MHZ, 'in_insn': c.in_insn,
                   'cycles_in_progress': c.cycle_index if c.in_insn else 0})

    # ── heat map (picosim/heat.py) ──────────────────────────────────────────

    def _baseline_path(self):
        return (self.prog.path or 'program') + '.heat-baseline.json'

    def _load_baseline(self):
        try:
            with open(self._baseline_path(), encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _heat_report(self, msg):
        """The heat map's data for a request (see heat.report); the 'compare'
        view compares the run with the saved baseline."""
        c = self.core
        if msg.get('view') == 'compare':
            base = self._load_baseline()
            model = 'm0plus' if msg.get('model') == 'm0plus' else 'multicycle'
            rep = {'view': 'compare', 'model': model, 'estimate': model == 'm0plus',
                   'baseline': base is not None}
            if base:
                now = heat.baseline_data(c, self.prog.source, self.prog.path)
                per = int(msg.get('per') or 0) or None
                rep.update(heat.compare(now, base, model, bool(msg.get('code_only')), per))
                rep.update(per=per, saved=base.get('saved'), code_only=bool(msg.get('code_only')))
            return rep
        rep = heat.report(c, self.prog.source, msg, self.heat_overview)
        rep['baseline'] = os.path.exists(self._baseline_path())
        return rep

    def _heat_meta(self, rep):
        return {'program': self.prog.path, 'model': heat_model_name(rep.get('model')),
                'values': 'estimates (2-stage pipeline model)' if rep.get('estimate') else 'exact (multi-cycle FSM signals)',
                'view': rep.get('view'), 'cycles': rep.get('cycles', rep.get('after_cycles')),
                'instructions': rep.get('insns', ''),
                'your_code_only': bool(rep.get('code_only')),
                'exported': time.strftime('%Y-%m-%d %H:%M:%S')}

    def cmd_heat(self, msg):
        """Component and register usage for the heat map: see heat.report."""
        if self.core is None:
            return self.send({'type': 'heat', 'loaded': False})
        rep = self._heat_report(msg)
        rep.update(type='heat', loaded=True, req=msg.get('req', 0))
        self.send(rep)

    def cmd_heat_baseline(self, msg):
        """action 'save': keep this run's usage as the baseline (a JSON file
        next to the program) for the heat map's Compare view; 'clear' removes it."""
        if self.core is None:
            return
        path = self._baseline_path()
        try:
            if msg.get('action') == 'clear':
                if os.path.exists(path):
                    os.remove(path)
                note = "[heat map: baseline cleared]\n"
            else:
                data = heat.baseline_data(self.core, self.prog.source, self.prog.path)
                data['saved'] = time.strftime('%Y-%m-%d %H:%M:%S')
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=1)
                note = f"[heat map: baseline saved ({self.core.cycles} cycles) to {os.path.basename(path)}]\n"
        except OSError as e:
            return self.error(f"could not {msg.get('action', 'save')} the baseline: {e}")
        self.console(note, 'system')
        self.send({'type': 'heat_baseline', 'exists': os.path.exists(path)})

    def cmd_heat_export(self, msg):
        """Write the heat map's current view to `path` as CSV or JSON."""
        if self.core is None:
            return
        path = os.path.abspath(os.path.expanduser(msg['path']))
        fmt = 'json' if path.lower().endswith('.json') or msg.get('format') == 'json' else 'csv'
        try:
            rep = self._heat_report(msg)
            heat.write_export(path, fmt, rep, self._heat_meta(rep))
        except OSError as e:
            return self.send({'type': 'heat_exported', 'ok': False, 'path': path, 'error': str(e)})
        self.console(f"[heat map: saved {os.path.basename(path)}]\n", 'system')
        self.send({'type': 'heat_exported', 'ok': True, 'path': path})

    def _walk_msg(self, msg, pipe):
        """The walkthrough of the loaded program (picosim/walkthrough.py), for
        the walkthrough panel beside the CPU diagram.  It runs a fresh copy
        with the keys the student has typed so far, so it follows the same
        run; the machine the GUI shows is not changed.  `compact` leaves out
        the machine snapshots the panel does not need."""
        kind = 'pipe_walkthrough' if pipe else 'walkthrough'
        if not self.path:
            return self.send({'type': kind, 'path': '', 'steps': [], 'stopped': 'No program is loaded.',
                              'output': '', 'limit': 0})
        from . import walkthrough
        limit = int(msg.get('limit', walkthrough.DEFAULT_LIMIT))
        keys = list(self.keys_used) + list(self.inp)
        src = prog_settings.effective_source(self.path)
        wt = (walkthrough.build_pipeline if pipe else walkthrough.build)(src, limit, keys)
        if msg.get('compact'):
            wt = walkthrough.compact(wt)
        wt['path'] = self.path
        wt['keys'] = len(keys)
        wt['edited'] = bool(self.journal)        # values changed by hand: the live run may differ
        self.send(wt)

    def cmd_walkthrough(self, msg):
        """The walkthrough on the multi-cycle datapath (see _walk_msg)."""
        self._walk_msg(msg, False)

    def cmd_pipe_walkthrough(self, msg):
        """The walkthrough on the 2-stage pipeline (see _walk_msg)."""
        self._walk_msg(msg, True)

    # ── real Pico over USB serial ────────────────────────────────────────────

    def cmd_set_mode(self, msg):
        mode = msg.get('mode', 'sim')
        if mode not in ('sim', 'pico'):
            return self.error(f"bad mode {mode}")
        self.mode = mode
        if mode == 'pico':
            self.running = False
        else:
            self.close_serial()
        self.send_state()

    def cmd_serial_list(self, msg):
        try:
            from serial.tools import list_ports
        except ImportError:
            return self.error("pyserial is not installed (pip install pyserial)")
        ports = [{'device': p.device, 'description': p.description or '',
                  'pico': p.vid == PICO_VID}
                 for p in list_ports.comports()]
        ports.sort(key=lambda p: (not p['pico'], p['device']))
        self.send({'type': 'serial_ports', 'ports': ports})

    def cmd_serial_open(self, msg):
        try:
            import serial
        except ImportError:
            return self.error("pyserial is not installed (pip install pyserial)")
        self.close_serial()
        port = msg.get('port')
        if not port:
            port = self._find_pico_port()
            if not port:
                return self.error("no Raspberry Pi Pico serial port found")
        try:
            self.serial = serial.Serial(port, 115200, timeout=0)
        except (OSError, serial.SerialException) as e:
            return self.error(f"could not open {port}: {e}")
        self.console(f"[connected to {port}]\n", 'system')
        self.send_state()

    def cmd_serial_close(self, msg):
        self.close_serial()
        self.send_state()

    def _find_pico_port(self):
        try:
            from serial.tools import list_ports
        except ImportError:
            return None
        for p in list_ports.comports():
            if p.vid == PICO_VID:
                return p.device
        return None

    def close_serial(self):
        if self.serial:
            try:
                self.serial.close()
            except OSError:
                pass
            self.serial = None
            self.serial_buf = ''
            self.board_pins = None
            self.console("[serial disconnected]\n", 'system')
            self.send_state()

    def poll_serial(self):
        if not self.serial:
            return
        try:
            n = self.serial.in_waiting
            if n:
                self.serial_text(self.serial.read(n).decode('latin-1'))
        except OSError as e:
            self.serial = None
            self.serial_buf = ''
            self.board_pins = None
            self.console(f"[serial lost: {e}]\n", 'system')
            self.send_state()

    def serial_text(self, text):
        """Show text from the board, taking out its live-pin frames
        (\\x02P<levels>,<output-enables>\\x03, see wrapper_main.c) and
        updating the board drawing from them."""
        buf = self.serial_buf + text
        shown = []
        while True:
            start = buf.find('\x02')
            if start < 0:
                shown.append(buf)
                buf = ''
                break
            shown.append(buf[:start])
            end = buf.find('\x03', start)
            if end < 0:
                if len(buf) - start > 40:      # not a frame after all: show it
                    shown.append(buf[start:])
                    buf = ''
                else:
                    buf = buf[start:]          # the rest arrives in the next read
                break
            self.board_frame(buf[start + 1:end])
            buf = buf[end + 1:]
        self.serial_buf = buf
        out = ''.join(shown)
        if out:
            self.console(out, 'pico')

    def board_frame(self, body):
        m = re.fullmatch(r'P([0-9a-fA-F]{8}),([0-9a-fA-F]{8})', body)
        if not m:
            return
        new = (int(m.group(1), 16), int(m.group(2), 16))
        old = self.board_pins
        diff = 0 if old is None else (old[0] ^ new[0]) | (old[1] ^ new[1])
        self.board_changed = [i for i in range(30) if diff >> i & 1]
        if new != old:
            self.board_pins = new
            self.send_state()

    def cmd_flash(self, msg):
        if not self.path or not self.path.endswith('.s'):
            return self.error("flashing needs a loaded .s file")
        if self.flash_thread and self.flash_thread.is_alive():
            return self.error("flash already in progress")
        self.close_serial()
        self.console(f"[building and flashing {os.path.basename(self.path)}...]\n", 'system')
        path = prog_settings.effective_source(self.path)   # with the student's settings

        def work():
            from .uf2 import flash_uf2
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    ok = flash_uf2(path)
            except Exception as e:     # report, don't kill the server
                buf.write(f"{e}\n")
                ok = False
            self.flash_result = (ok, buf.getvalue())

        self.flash_thread = threading.Thread(target=work, daemon=True)
        self.flash_thread.start()
        self.send({'type': 'flash_started'})

    def poll_flash(self):
        if self.flash_result is None:
            return
        ok, log = self.flash_result
        self.flash_result = None
        self.console(log, 'system')
        self.send({'type': 'flash_done', 'ok': ok})
        if ok:
            # The Pico re-enumerates after reboot; wait briefly for its port.
            for _ in range(50):
                if self._find_pico_port():
                    break
                time.sleep(0.1)
            self.mode = 'pico'
            self.cmd_serial_open({})


def timing_recent_max():
    return 512   # CPUCore.RECENT


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="picosim GUI backend server")
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('file', nargs='?')
    args = ap.parse_args(argv)
    srv = Server(args.port)
    srv.path = os.path.abspath(args.file) if args.file else None
    print(f"picosim server listening on 127.0.0.1:{srv.port}", flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
