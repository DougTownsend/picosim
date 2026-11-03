"""
A stand-in RP2040 for Testing without hardware: a GDB server whose CPU is
Unicorn (an emulator built from QEMU, independent of picosim's C++ core).

It lets the whole Testing pipeline run with nothing plugged in, and checks
picosim's instruction behaviour against a second implementation of ARMv6-M.
It is NOT the real chip:

  * peripherals (RESETS, IO_BANK0, PADS_BANK0, SIO GPIO) are picosim's own
    model (gpio.py), with the RP2040's atomic SET/CLR/XOR register aliases;
  * SysTick counts cycles from RP2040 datasheet Table 81 (the model picosim
    uses), so its benchmark numbers agree with picosim by construction;
  * a HardFault stops in a fault loop without stacking an exception frame.

Like the real chip, it refuses debugger access to the SIO (0xD0000000),
which only the cores can reach.

Only real hardware can confirm timing and peripheral behaviour.

    srv = StandIn(); srv.start()      # then connect an RSP client to srv.port
"""

import socket
import threading

try:
    import unicorn as U
    import unicorn.arm_const as A
except ImportError:          # optional: pip install unicorn
    U = A = None

from ..gpio import GPIO
from ..timing import pico_row, is_wide, APB_LOAD_EXTRA, APB_STORE_EXTRA

ROM_END = 0x4000
SRAM = (0x20000000, 0x42000)
FAULT_LOOP = 0x000000C0             # "HardFault handler": a b . in ROM
RESET_PC = 0x000000EE
PERIPH = {                           # base -> size (with the atomic aliases)
    0x4000C000: 0x4000,              # RESETS
    0x40014000: 0x4000,              # IO_BANK0
    0x4001C000: 0x4000,              # PADS_BANK0
}
SIO = (0xD0000000, 0x1000)
PPB = (0xE000E000, 0x1000)
SYST_CSR, SYST_RVR, SYST_CVR = 0xE000E010, 0xE000E014, 0xE000E018

REGS = ['r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8', 'r9', 'r10', 'r11', 'r12',
        'sp', 'lr', 'pc', 'xpsr', 'msp', 'psp', 'primask', 'control']

# sizes as OpenOCD describes a Cortex-M0+ (PRIMASK 1 bit, CONTROL 3 bits)
BITS = {'primask': 1, 'control': 3}
TARGET_XML = ('<?xml version="1.0"?><target version="1.0"><architecture>arm</architecture>'
              '<feature name="org.gnu.gdb.arm.m-profile">' +
              ''.join(f'<reg name="{n}" bitsize="{BITS.get(n, 32)}" regnum="{i}"/>' for i, n in enumerate(REGS)) +
              '</feature></target>')


class HangUp(Exception):
    """What OpenOCD does with some bad requests: drop the connection."""


def available():
    return U is not None


class Chip:
    """The stand-in RP2040: Unicorn plus peripherals and a SysTick."""

    def __init__(self):
        uc = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_THUMB | U.UC_MODE_MCLASS)
        uc.ctl_set_cpu_model(A.UC_CPU_ARM_CORTEX_M0)
        uc.mem_map(0, ROM_END)
        uc.mem_write(FAULT_LOOP, b'\xfe\xe7')       # b .
        uc.mem_write(RESET_PC, b'\xfe\xe7')
        uc.mem_map(*SRAM)
        for base, size in PERIPH.items():
            uc.mmio_map(base, size,
                        lambda u, off, sz, ud, b=base: self._periph_read(b + off, sz), None,
                        lambda u, off, sz, v, ud, b=base: self._periph_write(b + off, sz, v), None)
        uc.mmio_map(SIO[0], SIO[1], self._sio_read, None, self._sio_write, None)
        uc.mmio_map(PPB[0], PPB[1], self._ppb_read, None, self._ppb_write, None)
        uc.hook_add(U.UC_HOOK_MEM_READ | U.UC_HOOK_MEM_WRITE, self._check_align)
        uc.hook_add(U.UC_HOOK_INTR, self._intr)
        self.uc = uc
        self.reset()

    def reset(self):
        self.gpio = GPIO()
        self.cycles = 0
        self.syst = {'csr': 0, 'rvr': 0, 'c0': 0, 'v0': 0}
        for r in REGS[:13]:
            self.uc.reg_write(getattr(A, 'UC_ARM_REG_' + r.upper()), 0)
        self.uc.reg_write(A.UC_ARM_REG_SP, SRAM[0] + SRAM[1])
        self.uc.reg_write(A.UC_ARM_REG_LR, 0xFFFFFFFF)
        self.uc.reg_write(A.UC_ARM_REG_PC, RESET_PC)
        self.uc.reg_write(A.UC_ARM_REG_XPSR, 0x01000000)
        self.ipsr = 0

    # ── peripherals ─────────────────────────────────────────────────────────

    def _periph_read(self, addr, size):
        base = addr & ~0x3FFF
        reg = base | (addr & 0xFFF)
        v = self.gpio.read32(reg & ~3)
        return (v >> (8 * (addr & 3))) & ((1 << (8 * size)) - 1)

    def _periph_write(self, addr, size, value):
        """RP2040 datasheet §2.1.2: +0x1000 XOR, +0x2000 SET, +0x3000 CLR."""
        alias = (addr >> 12) & 3
        reg = (addr & ~0x3FFF) | (addr & 0xFFF)
        reg &= ~3
        if alias:
            old = self.gpio.read32(reg)
            value = (old ^ value, old | value, old & ~value)[alias - 1]
        self.gpio.write32(reg, value & 0xFFFFFFFF)

    def _sio_read(self, uc, off, size, ud):
        return self.gpio.read32(SIO[0] + (off & ~3)) if off != 0 else 0   # CPUID = 0 (core 0)

    def _sio_write(self, uc, off, size, value, ud):
        self.gpio.write32(SIO[0] + (off & ~3), value)

    def _cvr(self):
        s = self.syst
        if not s['csr'] & 1:
            return s['v0']
        return (s['v0'] - (self.cycles - s['c0'])) % (s['rvr'] + 1)

    def _ppb_read(self, uc, off, size, ud):
        a = PPB[0] + off
        if a == SYST_CSR:
            return self.syst['csr']
        if a == SYST_RVR:
            return self.syst['rvr']
        if a == SYST_CVR:
            return self._cvr()
        return 0

    def _ppb_write(self, uc, off, size, value, ud):
        a, s = PPB[0] + off, self.syst
        if a == SYST_CSR:
            s['v0'], s['c0'] = self._cvr(), self.cycles
            s['csr'] = value & 7
        elif a == SYST_RVR:
            s['rvr'] = value & 0xFFFFFF
        elif a == SYST_CVR:
            s['v0'], s['c0'] = 0, self.cycles

    # ── faults ──────────────────────────────────────────────────────────────

    def _check_align(self, uc, access, addr, size, value, ud):
        if SIO[0] <= addr < SIO[0] + SIO[1]:
            self._sio_access = True
        elif 0x40000000 <= addr < 0x50000000:
            self._apb_access = 'w' if access == U.UC_MEM_WRITE else 'r'
        if size > 1 and addr % size:
            self._fault = f"unaligned {size}-byte access at 0x{addr:08X}"
            if access == U.UC_MEM_WRITE:
                self._undo = (addr, bytes(uc.mem_read(addr, size))) if self._mapped(addr) else None
            uc.emu_stop()

    def _mapped(self, addr):
        return addr < ROM_END or SRAM[0] <= addr < SRAM[0] + SRAM[1]

    def _intr(self, uc, intno, ud):
        self._intno = intno
        uc.emu_stop()

    # ── execution ───────────────────────────────────────────────────────────

    def pc(self):
        return self.uc.reg_read(A.UC_ARM_REG_PC)

    def step(self):
        """Run one instruction.  Returns 'ok', 'bkpt' (stopped on a BKPT,
        not executed) or 'fault'."""
        uc = self.uc
        if self.ipsr:
            return 'fault'
        pc = self.pc()
        try:
            hw1 = int.from_bytes(uc.mem_read(pc, 2), 'little')
            hw2 = int.from_bytes(uc.mem_read(pc + 2, 2), 'little') if is_wide(hw1) else 0
        except U.UcError:
            return self._enter_fault(f"fetch from 0x{pc:08X}")
        if (hw1 & 0xFF00) == 0xBE00:
            return 'bkpt'
        taken = self._cond_passes(hw1) if (hw1 & 0xF000) == 0xD000 and (hw1 >> 8) & 0xF < 0xE else None
        ctx = uc.context_save()
        self._fault, self._undo, self._intno = None, None, None
        self._sio_access = False
        self._apb_access = None
        try:
            uc.emu_start(pc | 1, 0xFFFFFFFF, count=1)
        except U.UcError as e:
            self._fault = self._fault or str(e)
        if self._fault or self._intno is not None:
            uc.context_restore(ctx)
            if self._undo:
                uc.mem_write(*self._undo)
            return self._enter_fault(self._fault or f"exception {self._intno}")
        # Unicorn keeps PSP's bottom bits; on ARMv6-M they always read 0
        psp = uc.reg_read(A.UC_ARM_REG_PSP)
        if psp & 3:
            uc.reg_write(A.UC_ARM_REG_PSP, psp & ~3)
        self.cycles += self._cost(hw1, hw2, taken)
        return 'ok'

    def _cond_passes(self, hw1):
        """Would B<cond> (encoding hw1) be taken with the current flags?"""
        f = self.uc.reg_read(A.UC_ARM_REG_XPSR) >> 28
        n, z, c, v = bool(f & 8), bool(f & 4), bool(f & 2), bool(f & 1)
        base = ((z, c, n, v, c and not z, n == v, n == v and not z)[((hw1 >> 8) & 0xF) >> 1])
        return base != bool((hw1 >> 8) & 1)

    def _cost(self, hw1, hw2, taken):
        group, row, rule, n, pc_write = pico_row(hw1, hw2)
        if rule == 'fixed':
            return n
        if rule == 'ldst':
            if self._sio_access:
                return 1                            # the SIO is single-cycle
            if self._apb_access:                    # picosim's (measured) APB cost
                return 2 + (APB_LOAD_EXTRA if self._apb_access == 'r' else APB_STORE_EXTRA)
            return 2
        if rule == 'bcond':
            return 2 if taken else 1
        return 1

    def _enter_fault(self, why):
        self.fault_reason = why
        self.ipsr = 3                                  # HardFault
        self.uc.reg_write(A.UC_ARM_REG_PC, FAULT_LOOP)
        return 'fault'

    def xpsr(self):
        v = self.uc.reg_read(A.UC_ARM_REG_XPSR)
        return (v & 0xF0000000) | 0x01000000 | self.ipsr

    # ── debugger access ─────────────────────────────────────────────────────

    def read_reg(self, n):
        if n == 16:
            return self.xpsr()
        name = REGS[n] if n < len(REGS) else None
        if name is None:
            return 0
        return self.uc.reg_read(getattr(A, 'UC_ARM_REG_' + name.upper())) & 0xFFFFFFFF

    def write_reg(self, n, v):
        if n >= len(REGS):
            return
        if n == 16:
            self.ipsr = v & 0x3F
            self.uc.reg_write(A.UC_ARM_REG_XPSR, (v & ~0x3F) | 0x01000000)
            return
        self.uc.reg_write(getattr(A, 'UC_ARM_REG_' + REGS[n].upper()), v & 0xFFFFFFFF)

    def read_mem(self, addr, n):
        if self._plain(addr, n):
            return bytes(self.uc.mem_read(addr, n))
        return bytes(self._debug_byte(a) for a in range(addr, addr + n))

    def _plain(self, addr, n):
        return (addr + n <= ROM_END) or (SRAM[0] <= addr and addr + n <= SRAM[0] + SRAM[1])

    def _debug_byte(self, a):
        if self._plain(a, 1):
            return self.uc.mem_read(a, 1)[0]
        w = a & ~3
        if any(b <= w < b + s for b, s in PERIPH.items()):
            v = self._periph_read(w, 4)
        elif SIO[0] <= w < SIO[0] + SIO[1]:
            raise ValueError(a)      # the SIO is on the core's I/O port: not reachable by a debugger
        elif PPB[0] <= w < PPB[0] + PPB[1]:
            v = self._ppb_read(None, w - PPB[0], 4, None)
        else:
            raise ValueError(a)
        return (v >> (8 * (a & 3))) & 0xFF

    def write_mem(self, addr, data):
        if self._plain(addr, len(data)):
            self.uc.mem_write(addr, bytes(data))
            # Unicorn keeps translated code; new code there must not run the old
            self.uc.ctl_remove_cache(addr, addr + len(data))
            return
        if addr % 4 or len(data) % 4:
            raise ValueError(addr)
        for i in range(0, len(data), 4):
            a, v = addr + i, int.from_bytes(data[i:i + 4], 'little')
            if any(b <= a < b + s for b, s in PERIPH.items()):
                self._periph_write(a, 4, v)
            elif SIO[0] <= a < SIO[0] + SIO[1]:
                raise ValueError(a)  # as on the RP2040: the debugger cannot reach the SIO
            elif PPB[0] <= a < PPB[0] + PPB[1]:
                self._ppb_write(None, a - PPB[0], 4, v, None)
            else:
                raise ValueError(a)


class StandIn:
    """A GDB server for one client at a time, on 127.0.0.1:port."""

    def __init__(self, port=0):
        if U is None:
            raise RuntimeError("the stand-in needs Unicorn: pip install unicorn")
        self.chip = Chip()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', port))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.bps = set()
        self.ack = True
        self._stop = False

    def start(self):
        threading.Thread(target=self.serve, daemon=True).start()
        return self

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    def serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.ack = True
            try:
                self._session(conn)
            except (OSError, ConnectionError):
                pass
            finally:
                conn.close()

    def _session(self, conn):
        buf = b''
        self.conn = conn
        while True:
            data = conn.recv(65536)
            if not data:
                return
            buf += data
            while True:
                buf = buf.lstrip(b'+-')
                if buf.startswith(b'\x03'):
                    buf = buf[1:]
                    continue
                i = buf.find(b'$')
                j = buf.find(b'#', i)
                if i < 0 or j < 0 or len(buf) < j + 3:
                    break
                pkt = buf[i + 1:j].decode('latin-1')
                buf = buf[j + 3:]
                if self.ack:
                    conn.sendall(b'+')
                try:
                    reply = self.handle(pkt)
                except HangUp:
                    return                 # like OpenOCD: drop the connection
                except Exception:          # a bad request: answer it, keep serving
                    reply = 'E02'
                if reply is not None:
                    if pkt.startswith('qRcmd') or pkt == 'c':
                        self._send('O')      # OpenOCD's keep-alive, which a client must skip
                    self._send(reply)
                if pkt == 'QStartNoAckMode':
                    self.ack = False

    def _send(self, text):
        b = text.encode('latin-1')
        self.conn.sendall(b'$' + b + b'#%02x' % (sum(b) & 0xFF))

    def _interrupted(self):
        self.conn.setblocking(False)
        try:
            data = self.conn.recv(1, socket.MSG_PEEK)
            if data == b'\x03':
                self.conn.recv(1)
                return True
        except (BlockingIOError, OSError):
            pass
        finally:
            self.conn.setblocking(True)
        return False

    def handle(self, p):
        chip = self.chip
        if p.startswith('qSupported'):
            return 'PacketSize=4000;qXfer:features:read+;QStartNoAckMode+;hwbreak+'
        if p == 'QStartNoAckMode':
            return 'OK'
        if p.startswith('qXfer:features:read:target.xml:'):
            off, n = (int(x, 16) for x in p.rsplit(':', 1)[1].split(','))
            part = TARGET_XML[off:off + n]
            return ('l' if off + n >= len(TARGET_XML) else 'm') + part
        if p == '?':
            return 'S05'
        if p == 'g':
            return ''.join(chip.read_reg(i).to_bytes(4, 'little').hex() for i in range(17))
        if p.startswith('p'):
            return chip.read_reg(int(p[1:], 16)).to_bytes(4, 'little').hex()
        if p.startswith('P'):
            n, v = p[1:].split('=')
            n = int(n, 16)
            name = REGS[n] if n < len(REGS) else ''
            if len(v) * 4 != ((BITS.get(name, 32) + 7) // 8) * 8:
                raise HangUp(f"gdb sent {len(v) * 4} bits for a {BITS.get(name, 32)}-bit register ({name})")
            chip.write_reg(n, int.from_bytes(bytes.fromhex(v), 'little'))
            return 'OK'
        if p.startswith('m'):
            a, n = (int(x, 16) for x in p[1:].split(','))
            try:
                return chip.read_mem(a, n).hex()
            except (ValueError, U.UcError):
                return 'E01'
        if p.startswith('M'):
            head, data = p[1:].split(':')
            a, n = (int(x, 16) for x in head.split(','))
            try:
                chip.write_mem(a, bytes.fromhex(data))
            except (ValueError, U.UcError):
                return 'E01'
            return 'OK'
        if p == 's':
            chip.step()
            return 'S05'
        if p == 'c':
            return self._cont()
        if p[:2] in ('Z1', 'z1', 'Z0', 'z0'):
            a = int(p.split(',')[1], 16)
            (self.bps.add if p[0] == 'Z' else self.bps.discard)(a)
            return 'OK'
        if p.startswith('qRcmd,'):
            cmd = bytes.fromhex(p[6:]).decode()
            if cmd.startswith('reset'):
                chip.reset()
            return 'OK'
        if p in ('D', 'k'):
            return 'OK'
        return ''

    def _cont(self):
        chip = self.chip
        first = True
        n = 0
        while True:
            if not first and chip.pc() in self.bps:
                return 'S05'
            first = False
            r = chip.step()
            if r != 'ok':
                return 'S05'
            n += 1
            if n % 4096 == 0 and self._interrupted():
                return 'S02'
