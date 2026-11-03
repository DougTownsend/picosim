"""
The RP2040 under test, reached through a GDB server.

Three ways to reach one:

  openocd   picosim starts OpenOCD for a Raspberry Pi Debug Probe (or a
            second Pico running the debugprobe firmware) wired to the target
            Pico's SWD pins: interface/cmsis-dap.cfg + target/rp2040.cfg.
  gdb       a GDB server that is already running (OpenOCD started by hand,
            pyOCD, a J-Link GDB server …), at host:port.
  standin   no hardware: standin.py's emulator (see its caveats).

Target.prepare() puts the chip in a known state, the one picosim starts in:
core 0 halted, IO_BANK0 and PADS_BANK0 freshly reset and released (as the
Pico SDK leaves them), the SIO's GPIO outputs off, SysTick set to count
processor clock cycles, and the program's 64 KiB image written to SRAM at
0x20000000 and read back to check it.

The debugger cannot reach the SIO (0xD0000000): it sits on each core's
private I/O port, not on the bus the debug port uses.  So the SIO is set
and read by a few instructions run on the core itself (run_code()).

Every packet and everything OpenOCD prints goes to a session log
(log_path), which errors point to.
"""

import glob
import os
import shutil
import socket
import subprocess
import threading
import time

from .rsp import RSP, RSPError
from . import image as img

RESETS = 0x4000C000
RESETS_SET, RESETS_CLR = RESETS + 0x2000, RESETS + 0x3000
RESET_DONE = RESETS + 0x8
GPIO_RESETS = (1 << 5) | (1 << 8)          # IO_BANK0, PADS_BANK0
SIO_OUT_CLR, SIO_OE_CLR = 0xD0000018, 0xD0000028
SYST_CSR, SYST_RVR, SYST_CVR = 0xE000E010, 0xE000E014, 0xE000E018
ALL_PINS = (1 << 30) - 1
SIO = 0xD0000000
CODE = 0x20002C00                           # scratch code and data in the OS's part of SRAM
DATA = 0x20002E00
# Thumb code run on the core (the debugger cannot reach the SIO), r0 = SIO:
SIO_CLEAR = bytes.fromhex('8162' '8161' '00be')      # str r1,[r0,#0x28] (OE_CLR); str r1,[r0,#0x18] (OUT_CLR); bkpt
SIO_READ = bytes.fromhex('0169' '1160' '016a' '5160' '00be')   # ldr r1,[r0,#0x10]; str r1,[r2]; ldr r1,[r0,#0x20]; str r1,[r2,#4]; bkpt


class TargetError(Exception):
    pass


def find_openocd():
    """OpenOCD's executable and its scripts folder (None if it finds them itself)."""
    cands = [shutil.which('openocd')]
    cands += ['/opt/homebrew/bin/openocd', '/usr/local/bin/openocd', '/usr/bin/openocd']
    # the Raspberry Pi Pico VS Code extension's copy (Raspberry Pi's own fork)
    cands += sorted(glob.glob(os.path.expanduser('~/.pico-sdk/openocd/*/openocd')), reverse=True)
    cands += sorted(glob.glob(os.path.expanduser('~/.pico-sdk/openocd/*/openocd.exe')), reverse=True)
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            scripts = os.path.join(os.path.dirname(c), 'scripts')
            return c, (scripts if os.path.isdir(scripts) else None)
    return None, None


LOGS_KEPT = 20


def log_dir():
    from .image import _cache_dir
    d = os.path.join(_cache_dir(), 'logs')
    os.makedirs(d, exist_ok=True)
    return d


def new_log_path():
    """A log file of its own for each connection (two picosim windows, or a
    test run beside the GUI, never write into each other's), keeping the
    newest LOGS_KEPT."""
    d = log_dir()
    old = sorted(f for f in os.listdir(d) if f.startswith('session-') and f.endswith('.log'))
    for f in old[:max(0, len(old) - LOGS_KEPT + 1)]:
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    return os.path.join(d, f"session-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log")


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class OpenOCD:
    """An OpenOCD process for a CMSIS-DAP probe and an RP2040."""

    def __init__(self, exe=None, speed_khz=5000, log=None):
        self.write_log = log or (lambda text: None)
        found, scripts = find_openocd()
        self.exe = exe or found
        if not self.exe:
            raise TargetError(
                "OpenOCD was not found. Install it (macOS: brew install open-ocd; "
                "Linux: your package manager's openocd; Windows: the Raspberry Pi Pico "
                "VS Code extension installs it) and try again.")
        self.port = _free_port()
        args = [self.exe]
        if scripts and not exe:
            args += ['-s', scripts]
        args += ['-c', 'set USE_CORE 0',            # one GDB port, for core 0
                 '-f', 'interface/cmsis-dap.cfg',
                 '-f', 'target/rp2040.cfg',
                 '-c', f'adapter speed {int(speed_khz)}',
                 '-c', f'gdb_port {self.port}',
                 '-c', 'tcl_port disabled', '-c', 'telnet_port disabled']
        self.args = args
        self.log = []
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, errors='replace')
        except OSError as e:
            raise TargetError(f"could not start OpenOCD ({e})")
        threading.Thread(target=self._read_log, daemon=True).start()
        self._wait_ready()

    def _read_log(self):
        for line in self.proc.stdout:
            self.log.append(line.rstrip())
            self.write_log('  (openocd) ' + line.rstrip())
        self.write_log(f'  (openocd exited with status {self.proc.wait()})')

    def _wait_ready(self, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                time.sleep(0.1)
                raise TargetError("OpenOCD stopped: " + self.explain())
            if any('for gdb connections' in l for l in self.log):
                return
            time.sleep(0.05)
        self.close()
        raise TargetError("OpenOCD did not get ready in 10 s: " + self.explain())

    def explain(self):
        text = '\n'.join(self.log[-30:])
        low = text.lower()
        hints = []
        if 'cmsis-dap' in low and ('unable to find' in low or 'no device' in low or 'not found' in low):
            hints.append("No debug probe was found. Is the probe Pico plugged in by USB and "
                         "running the debugprobe firmware (debugprobe_on_pico.uf2)?")
        if 'dap_connect' in low or 'error connecting dp' in low or 'failed to connect' in low \
                or 'cannot read idr' in low:
            hints.append("The probe is there but cannot reach the target. Check the three SWD "
                         "wires (probe GP2 → target SWCLK, probe GP3 → target SWDIO, GND → GND) "
                         "and that the target Pico is powered.")
        if "can't find" in low and '.cfg' in low:
            hints.append("This OpenOCD has no rp2040 support; use OpenOCD 0.12 or newer, or "
                         "Raspberry Pi's build.")
        return (' '.join(hints) + '\n\n' if hints else '') + (text or '(no output)')

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Target:
    """A halted RP2040 core 0 with a program loaded, through an RSP connection."""

    def __init__(self, kind='openocd', host='127.0.0.1', port=3333, openocd=None, speed_khz=5000):
        self.kind = kind
        self.ocd = None
        self.standin = None
        self.rsp = None
        self.args = (host, port, openocd, speed_khz)
        self.log_path = new_log_path()
        try:
            self._log = open(self.log_path, 'w', buffering=1, encoding='utf-8', errors='replace')
        except OSError:
            self._log = None
        self.log(f"picosim Testing session: {kind}")
        self._start()
        self.loaded = None                 # digest of the image in SRAM

    LOG_MAX = 50_000_000      # bytes: a very long run keeps its start, which has the setup

    def log(self, text):
        if self._log:
            try:
                if self._log.tell() > self.LOG_MAX:
                    self._log.write("(log full: later packets are not recorded)\n")
                    self._log.close()
                    self._log = None
                    return
                self._log.write(f"{time.strftime('%H:%M:%S')} {text}\n")
            except (OSError, ValueError):
                pass

    def _start(self):
        host, port, openocd, speed_khz = self.args
        if self.kind == 'openocd':
            if not (self.ocd and self.ocd.proc.poll() is None):
                self.ocd = OpenOCD(openocd, speed_khz, self.log)
            port = self.ocd.port
        elif self.kind == 'standin':
            if not self.standin:
                from .standin import StandIn
                self.standin = StandIn().start()
            port = self.standin.port
        try:
            self.rsp = RSP(host, port, timeout=10.0, log=self.log)
        except RSPError as e:
            self.close()
            raise TargetError(str(e) + "\n\n" + self.explain())
        self.xpsr_n = self.rsp.regnum('xpsr', 16)
        self.primask_n = self.rsp.regnum('primask')
        self.control_n = self.rsp.regnum('control')

    def reconnect(self):
        """After a lost connection: a new one (and a new OpenOCD if it stopped)."""
        self.log("reconnecting")
        if self.rsp:
            self.rsp.close()
        self.rsp = None
        self._start()
        self.reset_halt()

    def explain(self):
        """What OpenOCD said last, and where the full log is."""
        out = self.ocd.explain() if self.ocd else ''
        if self.ocd and self.ocd.proc.poll() is not None:
            out = f"OpenOCD has stopped (exit status {self.ocd.proc.returncode}).\n" + out
        return (out + "\n\n" if out else '') + f"Full log: {self.log_path}"

    @property
    def description(self):
        if self.kind == 'standin':
            return "Stand-in RP2040 (emulator, no hardware)"
        if self.kind == 'openocd':
            return f"RP2040 through OpenOCD ({os.path.basename(self.ocd.exe)}, SWD)"
        return "RP2040 through a GDB server"

    def close(self):
        try:
            if getattr(self, 'rsp', None):
                self.rsp.close()
        finally:
            if self.ocd:
                self.ocd.close()
            if self.standin:
                self.standin.close()
            self.log("closed")
            if self._log:
                self._log.close()
                self._log = None

    # ── preparing the chip ──────────────────────────────────────────────────

    def reset_halt(self):
        try:
            said = self.rsp.monitor('reset halt', timeout=15.0)
            self.log("reset halt: " + ' '.join(said.split())[:300])
        except RSPError as e:
            if 'in time' in str(e) or 'connection' in str(e):
                # a late answer would put every later one out of step
                raise TargetError(f"'reset halt' failed: {e}\n\n" + self.explain())
            # this GDB server has no monitor commands: at least stop the core
            self.log(f"no 'monitor reset halt' ({e}); interrupting instead")
            self.rsp.interrupt()
            try:
                self.rsp.wait_stop(2.0)
            except RSPError:
                pass

    def prepare(self, hwimg, bench=False):
        """Reset, set up the peripherals and load `hwimg` (an image.HwImage)."""
        r = self.rsp
        self.reset_halt()
        # IO_BANK0 and PADS_BANK0: through reset, so their registers hold
        # reset values, then out of it, as the SDK leaves them before main
        r.write32(RESETS_SET, GPIO_RESETS)
        r.write32(RESETS_CLR, GPIO_RESETS)
        for _ in range(50):
            if r.read32(RESET_DONE) & GPIO_RESETS == GPIO_RESETS:
                break
        else:
            raise TargetError("IO_BANK0/PADS_BANK0 did not come out of reset")
        self.run_code(SIO_CLEAR, {0: SIO, 1: ALL_PINS})
        # SysTick: off for traces; for benchmarks counting processor clocks
        # down from 2^24-1 (CLKSOURCE=1, ENABLE=1, no interrupt)
        r.write32(SYST_CSR, 0)
        r.write32(SYST_RVR, 0xFFFFFF)
        r.write32(SYST_CVR, 0)
        r.write_mem(img.OFFSET, hwimg.image)
        back = r.read_mem(img.OFFSET, len(hwimg.image))
        if back != hwimg.image:
            bad = next(i for i in range(len(back)) if back[i] != hwimg.image[i])
            raise TargetError(f"SRAM did not keep the program: 0x{img.OFFSET + bad:08X} reads "
                              f"0x{back[bad]:02X}, not 0x{hwimg.image[bad]:02X}. Check the SWD "
                              "wiring and lower the adapter speed.")
        self.loaded = hwimg.digest
        # PRIMASK and CONTROL: reset halt has just set them to 0, as picosim starts
        if bench:
            r.write32(SYST_CSR, 5)

    # ── state ───────────────────────────────────────────────────────────────

    def regs(self):
        """[R0..R15, xPSR]."""
        return self.rsp.read_core_regs() + [self.rsp.read_reg(self.xpsr_n)]

    def set_regs(self, values, xpsr):
        for i, v in enumerate(values):
            self.rsp.write_reg(i, v)
        self.rsp.write_reg(self.xpsr_n, xpsr)

    def step(self):
        return self.rsp.step(timeout=5.0)

    def run_code(self, code, regs, timeout=2.0):
        """Run a few Thumb instructions (ending in BKPT) on the core, with
        `regs` {number: value} set first.  Overwrites registers."""
        r = self.rsp
        r.write_mem(CODE, code)
        for n, v in regs.items():
            r.write_reg(n, v)
        r.write_reg(self.xpsr_n, 0x01000000)
        r.write_reg(15, CODE)
        if self.run(timeout) != 'halted':
            raise TargetError("a few instructions run on the core did not finish")
        pc = r.read_core_regs()[15]
        if pc != CODE + len(code) - 2:
            raise TargetError(f"the core stopped at 0x{pc:08X}, not at the end of its "
                              f"instructions (0x{CODE + len(code) - 2:08X})")

    def read_sio(self):
        """(GPIO_OUT, GPIO_OE), read by the core.  Overwrites registers."""
        self.run_code(SIO_READ, {0: SIO, 2: DATA})
        d = self.rsp.read_mem(DATA, 8)
        return int.from_bytes(d[:4], 'little'), int.from_bytes(d[4:], 'little')

    def run(self, timeout, stop_flag=None):
        """Continue until the core halts.  Returns 'halted', or 'timeout' when
        it was still running after `timeout` seconds (it is stopped then)."""
        r = self.rsp
        r.resume()
        end = time.monotonic() + timeout
        while True:
            try:
                r.wait_stop(min(0.25, max(0.01, end - time.monotonic())))
                return 'halted'
            except RSPError as e:
                if 'in time' not in str(e):
                    raise
            if time.monotonic() >= end or (stop_flag and stop_flag.is_set()):
                r.interrupt()
                r.wait_stop(5.0)
                return 'timeout'
