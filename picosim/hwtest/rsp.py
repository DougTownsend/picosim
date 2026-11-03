"""
A small GDB Remote Serial Protocol client: what Testing uses to drive a real
RP2040 through a debug server (OpenOCD with a Raspberry Pi Debug Probe, or
any other GDB server), and the stand-in (standin.py).

Only the packets a debugger needs to load, step and inspect a Cortex-M0+:
registers (g, p, P), memory (m, M), single step (s), continue (c, with an
interrupt), hardware breakpoints (Z1/z1) and monitor commands (qRcmd).
"""

import re
import socket
import time
import xml.etree.ElementTree as ET


class RSPError(Exception):
    pass


def _checksum(data):
    return sum(data) & 0xFF


def _unescape(data):
    """Undo binary escaping ('}' x^0x20) and run-length encoding ('*')."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x7D and i + 1 < len(data):
            out.append(data[i + 1] ^ 0x20)
            i += 2
        elif b == 0x2A and out and i + 1 < len(data):
            out.extend(out[-1:] * (data[i + 1] - 29))
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def _brief(text, n=120):
    return text if len(text) <= n else f"{text[:n]}… ({len(text)} chars)"


def le32(hexstr):
    return int.from_bytes(bytes.fromhex(hexstr), 'little')


class RSP:
    """One connection to a GDB server."""

    PACKET = 4096          # bytes of memory per m/M packet

    def __init__(self, host='127.0.0.1', port=3333, timeout=5.0, log=None):
        self.log = log or (lambda text: None)    # gets every packet, for the session log
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise RSPError(f"could not connect to the debug server at {host}:{port} ({e})")
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.timeout = timeout
        self.buf = b''
        self.ack = True
        self.console = []                  # text the server printed (O packets)
        self.regnums = {}                  # register name -> regnum, from target.xml
        self.regbits = {}                  # regnum -> size in bits, from target.xml
        feats = self.cmd('qSupported:multiprocess-;swbreak+;hwbreak+;xmlRegisters=arm')
        m = re.search(r'PacketSize=([0-9a-fA-F]+)', feats)
        if m:
            self.PACKET = max(256, min(self.PACKET, (int(m.group(1), 16) - 32) // 2))
        if 'QStartNoAckMode+' in feats and self.cmd('QStartNoAckMode') == 'OK':
            self.ack = False
        if 'qXfer:features:read+' in feats:
            self._read_target_xml()
        self.cmd('?')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    # ── packets ─────────────────────────────────────────────────────────────

    def _raw_send(self, data):
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise RSPError(f"the debug server closed the connection ({e})")

    def send(self, payload):
        if isinstance(payload, str):
            payload = payload.encode('latin-1')
        self.log('→ ' + _brief(payload.decode('latin-1')))
        pkt = b'$' + payload + b'#%02x' % _checksum(payload)
        for _ in range(3):
            self._raw_send(pkt)
            if not self.ack:
                return
            c = self._read_byte(self.timeout)
            if c == b'+':
                return
        raise RSPError("the debug server keeps rejecting packets")

    def _read_byte(self, timeout):
        while not self.buf:
            self._fill(timeout)
        c, self.buf = self.buf[:1], self.buf[1:]
        return c

    def _fill(self, timeout):
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(65536)
        except socket.timeout:
            raise RSPError("the debug server did not answer in time")
        except OSError as e:
            raise RSPError(f"lost the debug server ({e})")
        if not data:
            raise RSPError("the debug server closed the connection")
        self.buf += data

    def recv(self, timeout=None):
        """The next packet's payload (as text).  Console output (O packets) is
        collected in self.console."""
        timeout = self.timeout if timeout is None else timeout
        end = time.monotonic() + timeout
        while True:
            while True:
                i = self.buf.find(b'$')
                if i >= 0:
                    self.buf = self.buf[i:]
                    j = self.buf.find(b'#')
                    if j >= 0 and len(self.buf) >= j + 3:
                        break
                elif self.buf:
                    self.buf = b''        # stray acks between packets
                left = end - time.monotonic()
                if left <= 0:
                    raise RSPError("the debug server did not answer in time")
                self._fill(left)
            body, cs = self.buf[1:j], self.buf[j + 1:j + 3]
            self.buf = self.buf[j + 3:]
            ok = cs.lower() == b'%02x' % _checksum(body)
            if self.ack:
                self._raw_send(b'+' if ok else b'-')
            if not ok:
                continue
            text = _unescape(body).decode('latin-1')
            if text == 'O':
                # an empty console packet: OpenOCD's keep-alive while a command
                # takes long.  Taking it for the answer would put every later
                # answer one behind.
                self.log('  (keep-alive)')
                continue
            if text.startswith('O') and text != 'OK' and re.fullmatch(r'O([0-9a-fA-F]{2})+', text):
                said = bytes.fromhex(text[1:]).decode('latin-1')
                self.console.append(said)
                self.log('  (server) ' + said.rstrip())
                continue
            self.log('← ' + _brief(text))
            return text

    def cmd(self, payload, timeout=None):
        self.send(payload)
        return self.recv(timeout)

    def _check(self, reply, what):
        if reply.startswith('E') and len(reply) == 3:
            raise RSPError(f"{what} failed (error {reply[1:]})")
        if reply == '':
            raise RSPError(f"{what}: the debug server does not support it")
        return reply

    def _hex(self, reply, what):
        """The bytes of a hex reply.  Anything else is the answer to some other
        request: the connection is out of step and must be remade."""
        self._check(reply, what)
        try:
            return bytes.fromhex(reply)
        except ValueError:
            raise RSPError(f"{what}: the reply {reply[:40]!r} is out of step with the requests")

    # ── target description ──────────────────────────────────────────────────

    def _xfer(self, annex):
        out, off = '', 0
        while True:
            r = self.cmd(f'qXfer:features:read:{annex}:{off:x},{0xFFF:x}')
            if not r or r[0] not in 'ml':
                return None
            out += r[1:]
            off += len(r) - 1
            if r[0] == 'l':
                return out

    def _read_target_xml(self):
        try:
            text = self._xfer('target.xml')
            if not text:
                return
            docs = [text]
            for inc in re.findall(r'<xi:include\s+href="([^"]+)"', text):
                sub = self._xfer(inc)
                if sub:
                    docs.append(sub)
            n = 0
            for doc in docs:
                doc = re.sub(r'<!DOCTYPE[^>]*>', '', doc)
                for reg in ET.fromstring(doc).iter('reg'):
                    n = int(reg.get('regnum', n))
                    self.regnums[reg.get('name', '').lower()] = n
                    self.regbits[n] = int(reg.get('bitsize', 32))
                    n += 1
        except (RSPError, ET.ParseError, ValueError):
            pass

    def regnum(self, name, default=None):
        return self.regnums.get(name, default)

    # ── registers ───────────────────────────────────────────────────────────

    def read_core_regs(self):
        """R0-R15."""
        b = self._hex(self.cmd('g'), 'reading registers')
        if len(b) < 64:
            raise RSPError(f"reading registers: a short reply ({len(b)} bytes) is out of step with the requests")
        return [int.from_bytes(b[4 * i:4 * i + 4], 'little') for i in range(16)]

    def read_reg(self, n):
        b = self._hex(self.cmd(f'p{n:x}'), f'reading register {n}')
        return int.from_bytes(b[:4], 'little')

    def write_reg(self, n, value):
        # exactly the register's size: OpenOCD hangs up on "32 bits for a
        # 1-bit register" (PRIMASK is 1 bit in its description)
        size = max(1, (self.regbits.get(n, 32) + 7) // 8)
        value &= (1 << (8 * size)) - 1
        r = self.cmd(f'P{n:x}={value.to_bytes(size, "little").hex()}')
        if r != 'OK':
            raise RSPError(f"writing register {n} failed ({r or 'not supported'})")

    # ── memory ──────────────────────────────────────────────────────────────

    def read_mem(self, addr, n):
        out = bytearray()
        while n > 0:
            k = min(n, self.PACKET)
            data = self._hex(self.cmd(f'm{addr:x},{k:x}'), f'reading memory at 0x{addr:08X}')
            if not data:
                raise RSPError(f"reading memory at 0x{addr:08X} returned nothing")
            out += data
            addr += len(data)
            n -= len(data)
        return bytes(out)

    def read32(self, addr):
        return int.from_bytes(self.read_mem(addr, 4), 'little')

    def write_mem(self, addr, data):
        for i in range(0, len(data), self.PACKET):
            chunk = data[i:i + self.PACKET]
            r = self.cmd(f'M{addr + i:x},{len(chunk):x}:{chunk.hex()}')
            if r != 'OK':
                raise RSPError(f"writing memory at 0x{addr + i:08X} failed ({r or 'not supported'})")

    def write32(self, addr, value):
        self.write_mem(addr, (value & 0xFFFFFFFF).to_bytes(4, 'little'))

    # ── execution ───────────────────────────────────────────────────────────

    def step(self, timeout=None):
        return self._check(self.cmd('s', timeout), 'single step')

    def resume(self):
        self.send('c')

    def wait_stop(self, timeout):
        return self.recv(timeout)

    def interrupt(self):
        self.log('→ (interrupt)')
        self._raw_send(b'\x03')

    def breakpoint(self, addr, on=True):
        r = self.cmd(f"{'Z' if on else 'z'}1,{addr:x},2")
        if r != 'OK':
            raise RSPError(f"{'setting' if on else 'clearing'} a breakpoint at 0x{addr:08X} failed "
                           f"({r or 'not supported'})")

    def monitor(self, text, timeout=None):
        self.console = []
        r = self.cmd('qRcmd,' + text.encode().hex(), timeout)
        if r.startswith('E') and len(r) == 3:
            raise RSPError(f"monitor command '{text}' failed ({r})")
        if r not in ('OK', '') and not re.fullmatch(r'([0-9a-fA-F]{2})+', r):
            raise RSPError(f"monitor command '{text}': the reply {r[:40]!r} is out of step with the requests")
        out = ''.join(self.console)
        if r not in ('OK', ''):
            try:
                out += bytes.fromhex(r).decode('latin-1')
            except ValueError:
                out += r
        return out
