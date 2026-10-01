"""
JSON-lines backend for the Godot GUI.

The GUI connects over TCP (127.0.0.1) and sends one JSON object per line:
    {"cmd": "step_cycle"}
The server answers with events, also one JSON object per line:
    {"type": "state", ...}         full machine state after every change
    {"type": "program", ...}       disassembly listing after a load
    {"type": "console", "text"}    program (or Pico) output
    {"type": "walkthrough", ...}   the user guide's program walkthrough
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
import select
import socket
import sys
import threading
import time
from collections import deque

from .loader import load_program, LoadError
from .cpu import SimulatorError

REG_NAMES = ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
             'R8', 'R9', 'R10', 'R11', 'R12', 'SP', 'LR', 'PC']
MAX_SPEED = 0            # cps value meaning "as fast as possible"
FRAME = 1 / 30           # state broadcast interval while running
HEX_DEFAULT = (0x3000, 256)
PICO_VID = 0x2E8A


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
        self.mem_changed = set()   # byte addresses written by the last action
        self.hexview = HEX_DEFAULT
        self.exit_code = None
        self.gpio_before = []      # pin states at the start of the last action
        self.dirty = False         # a state update is pending

        self.mode = 'sim'          # 'sim' or 'pico'
        self.serial = None
        self.flash_thread = None
        self.flash_result = None

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
        if self.path:
            self.cmd_load({'path': self.path})
        else:
            self.send_state()
        while self.conn:
            busy = self.running and not self.waiting_input
            timeout = 0 if busy else 0.02
            r, _, _ = select.select([self.conn], [], [], timeout)
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
            self.poll_serial()
            self.poll_flash()
            if busy:
                self.run_slice()
            if self.dirty:
                self._send_state_now()
        self.close_serial()

    def dispatch(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return self.error(f"bad JSON: {line[:80]!r}")
        cmd = msg.get('cmd', '')
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
            return self.send({'type': 'state', 'loaded': False, 'mode': self.mode,
                              'serial': self.serial.port if self.serial else None})
        pc = c.pc
        sp = c.sp
        stack_lo = sp & ~3
        hx_addr, hx_len = self.hexview
        cur = c.insn_addr if c.in_insn else pc
        pins = self.gpio_pins()
        before = {p['pin']: p for p in self.gpio_before}
        gpio_changed = [p['pin'] for p in pins
                        if p['pin'] in before and
                        (p['level'], p['dir']) != (before[p['pin']]['level'], before[p['pin']]['dir'])]
        self.send({
            'type': 'state', 'loaded': True, 'path': self.prog.path,
            'regs': list(c.regs), 'flags': {'N': c.N, 'Z': c.Z, 'C': c.C, 'V': c.V},
            'datapath': c.datapath,
            'pc': pc, 'halted': c.halted, 'exit_code': self.exit_code,
            'steps': c.steps, 'cycles': c.cycles,
            'in_insn': c.in_insn, 'next_state': c.next_state(),
            'cycle_index': c.cycle_index, 'insn_total': c.insn_total,
            'insn_addr': cur, 'insn_text': self.prog.asm_map.get(cur, '???'),
            'running': self.running, 'cps': self.cps,
            'waiting_input': self.waiting_input,
            'breakpoints': sorted(self.breakpoints),
            'history': self.history,
            'changed': sorted(self.changed),
            'mem_changed': sorted(self.mem_changed),
            'hexview': {'addr': hx_addr, 'data': self.mem_hex(hx_addr, hx_len)},
            'stack': {'addr': stack_lo, 'data': self.mem_hex(stack_lo, 64)},
            'gpio': pins,
            'gpio_changed': gpio_changed,
            'mode': self.mode,
            'serial': self.serial.port if self.serial else None,
        })

    def send_program(self):
        p = self.prog
        c = self.core
        listing = []
        for addr in sorted(p.asm_map):
            hw = c.read16(addr)
            wide = c.is_32bit_thumb(hw)
            raw = f"{hw:04X}" + (f" {c.read16(addr + 2):04X}" if wide else '')
            listing.append({'addr': addr, 'raw': raw, 'text': p.asm_map[addr],
                            'label': p.sym_map.get(addr, '')})
        self.send({'type': 'program', 'path': p.path, 'main': p.main_addr,
                   'listing': listing,
                   'labels': {k: v for k, v in p.label_map.items()}})

    # ── SVC handling for the GUI console ─────────────────────────────────────

    def _svc(self, num):
        c = self.core
        if num == 0:
            data = c.get_mem_slice(c.get_reg(1) & 0xFFFF, c.get_reg(2))
            self.console(data.decode('latin-1'))
        elif num == 1:
            self.exit_code = c.get_reg(0)
            c.halted = True
            self.console(f"\n[program exited with code {self.exit_code}]\n", 'system')
        elif num == 2:
            self.console(chr(c.get_reg(0) & 0xFF))
        elif num == 3:
            ch = self.inp.popleft() if self.inp else 0xFFFFFFFF
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
        return c.read16(c.pc) == 0xDF03

    # ── execution primitives ─────────────────────────────────────────────────

    def _record(self, info):
        if info['index'] == 0:
            self.history = []
        self.history.append(info)
        for ch in info['changes']:
            if ch['kind'] == 'mem':
                self.mem_changed.update(range(ch['addr'], ch['addr'] + ch['width']))
            else:
                self.changed.add(ch['name'])

    def gpio_pins(self):
        g = self.prog.cpu.gpio if self.prog else None
        return g.pin_states() if g else []

    def _begin_action(self):
        self.changed = set()
        self.mem_changed = set()
        self.gpio_before = self.gpio_pins()

    def _fault(self, e):
        self.core.halted = True
        self.running = False
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
            except RuntimeError as e:
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

    # ── run loop ─────────────────────────────────────────────────────────────

    def run_slice(self):
        c = self.core
        now = time.monotonic()
        start = now
        if c is None or c.halted:
            self.running = False
            self.send_state()
            return
        hit = False
        if self.cps == MAX_SPEED:
            before = list(c.regs)
            while time.monotonic() - start < 0.01:
                for _ in range(2000):
                    if not self.one_insn(record=False):
                        break
                    if c.pc in self.breakpoints:
                        hit = True
                        break
                else:
                    continue
                break
            after = c.regs
            self.changed |= {REG_NAMES[i] for i in range(16) if before[i] != after[i]}
        else:
            self.cycle_credit += (now - self.last_tick) * self.cps
            self.cycle_credit = min(self.cycle_credit, self.cps * 0.1 + 1)
            while self.cycle_credit >= 1:
                self.cycle_credit -= 1
                if not self.one_cycle():
                    break
                if not c.in_insn and c.pc in self.breakpoints:
                    hit = True
                    break
        self.last_tick = now
        if hit or c.halted:
            self.running = False
        if not self.running or now - self.last_frame >= FRAME or self.waiting_input:
            self.last_frame = now
            self.send_state()

    # ── commands ─────────────────────────────────────────────────────────────

    def cmd_load(self, msg):
        path = msg.get('path') or self.path
        if not path:
            return self.error("no program to load")
        path = os.path.abspath(os.path.expanduser(path))
        try:
            prog = load_program(path)
        except LoadError as e:
            self.send({'type': 'load_error', 'path': path, 'message': str(e)})
            return
        self.prog, self.path = prog, path
        self.core.svc_handler = self._svc
        self.running = False
        self.waiting_input = False
        self.history = []
        self.exit_code = None
        self._begin_action()
        self.breakpoints &= set(prog.asm_map)
        self.hexview = (max(prog.main_addr, 0x3000) & ~0xF, HEX_DEFAULT[1])
        self.send_program()
        self.console(f"[loaded {os.path.basename(path)}]\n", 'system')
        self.send_state()

    def cmd_reload(self, msg):
        self.cmd_load({'path': self.path})

    cmd_reset = cmd_reload

    def cmd_step_cycle(self, msg):
        if not self.core:
            return
        self.running = False
        self._begin_action()
        self.one_cycle()
        self.send_state()

    def cmd_step_insn(self, msg):
        if not self.core:
            return
        self.running = False
        self._begin_action()
        self.one_insn(record=True)
        self.send_state()

    def cmd_run(self, msg):
        if not self.core or self.core.halted:
            return self.send_state()
        self.cps = max(0, int(msg.get('cps', self.cps)))
        self._begin_action()
        self.running = True
        self.last_tick = time.monotonic()
        self.cycle_credit = 1.0
        # step off a breakpoint we are sitting on
        c = self.core
        if not c.in_insn and c.pc in self.breakpoints:
            if self.cps == MAX_SPEED:
                self.one_insn(record=False)
            else:
                self.one_cycle()
        self.send_state()

    def cmd_set_speed(self, msg):
        self.cps = max(0, int(msg.get('cps', 0)))
        self.send_state()

    def cmd_pause(self, msg):
        self.running = False
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
        self._begin_action()
        self.changed.add(REG_NAMES[n & 15])
        self.send_state()

    def cmd_set_flag(self, msg):
        name = msg['flag']
        if name not in 'NZCV' or len(name) != 1:
            return self.error(f"bad flag {name}")
        setattr(self.core, name, 1 if msg['value'] else 0)
        self._begin_action()
        self.changed.add(name)
        self.send_state()

    def cmd_write_mem(self, msg):
        addr = int(msg['addr'])
        data = bytes.fromhex(msg['data'])
        if addr < 0 or addr + len(data) > 0x10000:
            return self.error("write_mem out of range")
        for i, b in enumerate(data):
            self.core.set_mem_byte(addr + i, b)
        self._begin_action()
        self.mem_changed = set(range(addr, addr + len(data)))
        self.send_state()

    def cmd_view_mem(self, msg):
        self.hexview = (int(msg['addr']) & 0xFFFF, int(msg.get('len', HEX_DEFAULT[1])))
        self.send_state()

    def cmd_gpio_set(self, msg):
        """Drive an input pin from outside: value 0, 1 or null (floating)."""
        pin = int(msg['pin'])
        v = msg.get('value')
        self._begin_action()
        try:
            self.prog.cpu.gpio.set_external(pin, None if v is None else bool(v))
        except ValueError as e:
            return self.error(str(e))
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

    def cmd_walkthrough(self, msg):
        """Explain the loaded program step by step for the user guide.  Runs
        on a fresh copy, so the machine the GUI shows is not changed."""
        if not self.path:
            return self.send({'type': 'walkthrough', 'path': '', 'steps': [],
                              'stopped': 'No program is loaded.', 'output': '', 'limit': 0})
        from .walkthrough import build, DEFAULT_LIMIT
        self.send(build(self.path, int(msg.get('limit', DEFAULT_LIMIT))))

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
            self.console("[serial disconnected]\n", 'system')

    def poll_serial(self):
        if not self.serial:
            return
        try:
            n = self.serial.in_waiting
            if n:
                self.console(self.serial.read(n).decode('latin-1'), 'pico')
        except OSError as e:
            self.serial = None
            self.console(f"[serial lost: {e}]\n", 'system')
            self.send_state()

    def cmd_flash(self, msg):
        if not self.path or not self.path.endswith('.s'):
            return self.error("flashing needs a loaded .s file")
        if self.flash_thread and self.flash_thread.is_alive():
            return self.error("flash already in progress")
        self.close_serial()
        self.console(f"[building and flashing {os.path.basename(self.path)}...]\n", 'system')
        path = self.path

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
