"""
Flash guard: back up the target Pico's whole 2 MB flash, and put it back
bit for bit (proposal §7.1, plan S15).

Campaign F writes the target's external QSPI flash.  That is the only part
of the board any campaign can change for good, and the condition for doing
it is a guarantee: the original contents always come back, bit for bit, and
the code proves it.  Every check below stops the procedure when it fails
(FlashGuardError, with a plain-English message saying what failed and what
to do).  Nothing is ever a warning that lets the procedure carry on.

Backup (FlashGuard.backup(), before anything is written):
  1. Identify the flash through OpenOCD (`flash probe 0`, `flash banks`):
     it must be the RP2040's flash at 0x10000000 and exactly 2 MiB.
  2. Backup A over SWD: `flash read_bank 0 <file>`, the whole chip.
  3. Backup B over USB, an independent path: you put the TARGET Pico in
     BOOTSEL mode, then `picotool save --all <file> -t bin` (the boot ROM
     reads the chip).
  4. A and B must have the same SHA-256.  If not, both are read once more;
     if they still differ, stop: nothing is written.
  5. Read the flash's 64-bit unique ID (`picotool info -d`, still in
     BOOTSEL).  No ID, no backup.
  6. Store the image twice (~/picosim-flash-backups/ and the cache), each
     with a .sha256 file and a .uf2 (for drag-and-drop restore with no
     probe).  Every file is read back and checked; the .uf2 is decoded and
     must equal the .bin.  Files are named <YYYYMMDD-HHMMSS>-<unique ID>.
  7. Record everything in a JSON state file (written atomically).

Before the campaign writes: mark_written() reads the whole flash again and
checks it still equals the backup, then records state 'written'.  From then
on require_no_pending_restore() stops every other discover command until a
restore succeeds.

Restore (FlashGuard.restore(); also at the start of the next session if one
ended before it):
  8. Refuse a backup whose unique ID differs from the board's.
  9. `flash write_image erase <backup> 0x10000000 bin`, then
     `verify_image <backup> 0x10000000 bin` ("verified N bytes" or stop).
 10. Read the whole chip back (`flash read_bank`); its SHA-256 must equal
     the backup's.  Both are printed side by side.  Only then is the state
     'restored'.
 11. Boot check: `reset run`, then wait for the board's USB serial port.  A
     failed boot check is reported, but the flash is still proven identical.
 12. Backup files are never deleted by this code.

The unique ID and OpenOCD.  OpenOCD 0.12 has no command that reads the
flash's unique ID over SWD, so the ID can only be read with picotool, in
BOOTSEL mode.  restore() therefore takes `board_id` from the caller; if it
is not given it uses a `read_unique_id()` callback, and failing that it asks
you to put the board in BOOTSEL mode and reads the ID with picotool
(unique_id_via_picotool()).  The trade-off: the picotool route costs one
more unplug-and-replug, but is the only route that proves the ID.  Restoring
over SWD while the board is in BOOTSEL mode works (the boot ROM is running,
not the flash), and `reset run` then boots the restored program.

OpenOCD writes the files named in `flash read_bank`, `write_image` and
`verify_image` itself, on the HOST.  Paths are made absolute and passed in
Tcl braces ({...}), so spaces are fine; a path containing a brace, a
backslash or a newline is refused.

Power cycles.  Entering BOOTSEL means unplugging the target, which may drop
the SWD connection.  The optional `reconnect()` callback (Target.reconnect
in production) is called before the first SWD command after each power
cycle.  Note Target.reconnect does `reset halt`, which takes the board out
of BOOTSEL, so all picotool work is done before it is called.
"""

import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass

FLASH_SIZE = 0x200000                 # 2 MiB: the Pico and Pico W's W25Q16
XIP_BASE = 0x10000000

DEFAULT_BACKUP_DIR = '~/picosim-flash-backups'
DEFAULT_CACHE_DIR = '~/.cache/picosim/discover/flash-backups'
DEFAULT_WORK_DIR = '~/.cache/picosim/discover/flash-work'
DEFAULT_STATE_PATH = '~/.cache/picosim/discover/flash_state.json'

STATES = ('backed-up', 'written', 'restored')

# Timeouts (seconds) for slow OpenOCD commands on a 2 MiB chip.
T_SHORT = 15.0
T_READ = 300.0
T_WRITE = 900.0
T_PICOTOOL = 180.0

# UF2 (https://github.com/microsoft/uf2)
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_NOT_MAIN_FLASH = 0x00000001
UF2_FLAG_FAMILY_ID = 0x00002000
RP2040_FAMILY_ID = 0xE48BFF56
UF2_PAYLOAD = 256

RESTORE_HINT = ("Run the restore-flash step (FlashGuard.restore) before anything else; "
                "it puts the original program back and proves it.")


class FlashGuardError(Exception):
    """A check failed; the procedure stopped at that point."""


# ---------------------------------------------------------------------------
# Small helpers

def _now():
    return datetime.datetime.now()


def _iso(t):
    return t.isoformat(timespec='seconds')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _write_new(path, data):
    """Write a file that must not exist yet (a backup is never overwritten)."""
    with open(path, 'xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _write_scratch(path, data):
    with open(path, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _atomic_write_json(path, obj):
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    tmp = path + f'.tmp{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def tcl_path(path):
    """An absolute host path quoted for an OpenOCD (Tcl) command."""
    p = os.path.abspath(path)
    if any(c in p for c in '{}\\\n\r'):
        raise FlashGuardError(
            f"The file path {p!r} contains a brace, backslash or line break, which OpenOCD "
            "cannot be given safely. Use a folder whose name has none of these.")
    return '{' + p + '}'


def normalize_id(text):
    """A unique ID as 16 upper-case hex digits (None if it isn't one)."""
    if text is None:
        return None
    t = str(text).strip()
    if t.lower().startswith('0x'):
        t = t[2:]
    if not re.fullmatch(r'[0-9a-fA-F]{1,16}', t):
        return None
    return t.upper().zfill(16)


def parse_flash_id(output):
    """The flash unique ID in `picotool info -d` output, e.g. a line
    'flash id:  0xE6614C311B4D8F2A'.  Lenient: any line containing 'flash id'
    and a hex number.  None if there is none; FlashGuardError if two
    different IDs appear (two boards in BOOTSEL mode)."""
    found = []
    for line in output.splitlines():
        low = line.lower()
        i = low.find('flash id')
        if i < 0:
            continue
        m = re.search(r'(?:0x)?([0-9a-fA-F]{8,16})\b', line[i + len('flash id'):])
        if m:
            found.append(normalize_id(m.group(1)))
    found = sorted(set(f for f in found if f))
    if len(found) > 1:
        raise FlashGuardError(
            "picotool reported more than one flash ID (" + ', '.join(found) + "), so more than "
            "one Pico seems to be in BOOTSEL mode. " + WHICH_PICO + " Unplug the probe Pico, plug it "
            "back in without holding BOOTSEL, and try again.")
    return found[0] if found else None


_FAIL_WORDS = ('error', 'failed', 'invalid command', 'not halted', 'timed out',
               "couldn't", 'could not', 'unable', 'mismatch', 'differ')


def _looks_failed(output, *paths):
    text = output
    for p in paths:
        text = text.replace(p, '')
    low = text.lower()
    return any(w in low for w in _FAIL_WORDS)


# ---------------------------------------------------------------------------
# UF2

def make_uf2(data, base=XIP_BASE, family=RP2040_FAMILY_ID):
    """The standard UF2 of a flash image: one 512-byte block per 256 bytes."""
    if len(data) % UF2_PAYLOAD:
        raise ValueError("a flash image is a whole number of 256-byte pages")
    n = len(data) // UF2_PAYLOAD
    out = bytearray()
    pad = bytes(476 - UF2_PAYLOAD)
    for i in range(n):
        out += struct.pack('<8I', UF2_MAGIC_START0, UF2_MAGIC_START1, UF2_FLAG_FAMILY_ID,
                           base + i * UF2_PAYLOAD, UF2_PAYLOAD, i, n, family)
        out += data[i * UF2_PAYLOAD:(i + 1) * UF2_PAYLOAD]
        out += pad
        out += struct.pack('<I', UF2_MAGIC_END)
    return bytes(out)


def decode_uf2(blob, base=XIP_BASE, family=RP2040_FAMILY_ID):
    """The flash image in a UF2 made by make_uf2(): every block is checked
    (magic numbers, family, size, number, address in order).  Raises
    FlashGuardError on anything unexpected."""
    if not blob or len(blob) % 512:
        raise FlashGuardError("The .uf2 file is not a whole number of 512-byte UF2 blocks.")
    n = len(blob) // 512
    out = bytearray()
    for i in range(n):
        b = blob[i * 512:(i + 1) * 512]
        m0, m1, flags, addr, size, blockno, nblocks, fam = struct.unpack_from('<8I', b, 0)
        (mend,) = struct.unpack_from('<I', b, 508)
        problem = None
        if (m0, m1, mend) != (UF2_MAGIC_START0, UF2_MAGIC_START1, UF2_MAGIC_END):
            problem = "bad magic numbers"
        elif flags & UF2_FLAG_NOT_MAIN_FLASH or not flags & UF2_FLAG_FAMILY_ID or fam != family:
            problem = "wrong flags or family (not an RP2040 flash block)"
        elif size != UF2_PAYLOAD:
            problem = f"payload of {size} bytes, not {UF2_PAYLOAD}"
        elif blockno != i or nblocks != n:
            problem = f"numbered {blockno} of {nblocks}, expected {i} of {n}"
        elif addr != base + i * UF2_PAYLOAD:
            problem = f"address 0x{addr:08X}, expected 0x{base + i * UF2_PAYLOAD:08X}"
        if problem:
            raise FlashGuardError(f"The .uf2 file is damaged: block {i} has {problem}.")
        out += b[32:32 + UF2_PAYLOAD]
    return bytes(out)


# ---------------------------------------------------------------------------
# Defaults for the injectable pieces

PICOTOOL_MISSING = (
    "picotool was not found, and it is needed for the second, independent backup "
    "(over USB). Install it (macOS: brew install picotool; Linux and Windows: the "
    "Raspberry Pi Pico VS Code extension installs it, or build it from "
    "github.com/raspberrypi/picotool) and try again. Nothing has been written to the board.")


def default_run_picotool(args, timeout=T_PICOTOOL):
    """Run picotool; (return code, stdout and stderr together)."""
    exe = shutil.which('picotool')
    if not exe:
        raise FlashGuardError(PICOTOOL_MISSING)
    try:
        p = subprocess.run([exe] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FlashGuardError(f"picotool {' '.join(args)} did not finish within {timeout:.0f} s. "
                              "Unplug the target Pico and start this step again.")
    except OSError as e:
        raise FlashGuardError(f"picotool could not be started ({e}).")
    return p.returncode, p.stdout


# Said with every prompt that asks for BOOTSEL: on 2026-11-10 BOOTSEL was
# held on both Picos, which stops the probe's firmware and loses the target.
WHICH_PICO = ("Only the TARGET Pico: the one being tested, whose three debug pins (SWCLK, GND, "
              "SWDIO) are wired to the other Pico. Never press BOOTSEL on the PROBE Pico (the one "
              "running the debug probe firmware): its USB cable must stay plugged in the whole time.")
BOOTSEL_HOW = ("Unplug the TARGET Pico's USB cable (leave the three debug wires connected). Hold "
               "down its BOOTSEL button, plug the cable back in, then let go. A drive called "
               "RPI-RP2 appears: leave it alone, and don't copy anything onto it.")
LOST_HINT = ("If a drive called RPI-RP2 is showing, a Pico is in BOOTSEL mode: unplug both Picos, "
             "plug them back in without holding any button, then press Disconnect and Connect.")


def default_ask(message):
    """Show the message and wait for Enter."""
    input(message + "\nPress Enter when done: ")


def _serial_ports():
    if sys.platform == 'darwin':
        pats = ['/dev/cu.usbmodem*']
    elif sys.platform.startswith('linux'):
        pats = ['/dev/ttyACM*']
    else:
        pats = []
    return set(p for pat in pats for p in glob.glob(pat))


class UsbSerialBootCheck:
    """The default boot check: a picosim program's USB serial port appears
    after `reset run`.  before_reset() notes the ports that exist; calling
    the object waits up to `timeout` s for a new one.  Returns
    (ok, description)."""

    def __init__(self, timeout=10.0, poll=0.25):
        self.timeout = timeout
        self.poll = poll
        self.before = set()

    def before_reset(self):
        self.before = _serial_ports()

    def __call__(self):
        if not (sys.platform == 'darwin' or sys.platform.startswith('linux')):
            return False, "The boot check only knows macOS and Linux serial port names."
        end = time.monotonic() + self.timeout
        while True:
            new = sorted(_serial_ports() - self.before)
            if new:
                return True, f"The board's USB serial port appeared: {new[0]}."
            if time.monotonic() >= end:
                return False, (f"No new USB serial port appeared within {self.timeout:.0f} s. "
                               "A program that doesn't use USB serial shows nothing here; check "
                               "by hand that it behaves as before.")
            time.sleep(self.poll)


# ---------------------------------------------------------------------------
# Records and state

@dataclass
class Backup:
    unique_id: str
    size: int
    sha256: str
    bin_path: str
    sha256_path: str
    uf2_path: str
    cache_bin_path: str
    cache_sha256_path: str
    cache_uf2_path: str
    created: str

    @classmethod
    def from_state(cls, st):
        b = st.get('backup') or {}
        try:
            return cls(unique_id=st['unique_id'], size=int(st['size']), sha256=st['sha256'],
                       bin_path=b['bin'], sha256_path=b['sha256_file'], uf2_path=b['uf2'],
                       cache_bin_path=b['cache_bin'], cache_sha256_path=b['cache_sha256_file'],
                       cache_uf2_path=b['cache_uf2'], created=st.get('backed_up_at', ''))
        except (KeyError, TypeError, ValueError) as e:
            raise FlashGuardError(f"The flash state file is missing a field ({e}); it can't be "
                                  "used to restore. The backup files themselves are untouched.")

    def paths(self):
        return {'bin': self.bin_path, 'sha256_file': self.sha256_path, 'uf2': self.uf2_path,
                'cache_bin': self.cache_bin_path, 'cache_sha256_file': self.cache_sha256_path,
                'cache_uf2': self.cache_uf2_path}


@dataclass
class RestoreResult:
    backup_sha256: str
    readback_sha256: str
    boot_ok: bool
    boot_detail: str
    image_used: str


def _state_path(state_path=None):
    return os.path.expanduser(state_path or DEFAULT_STATE_PATH)


def load_state(state_path=None):
    """The state file's contents (None if there is no state file).  An
    unreadable file is an error: the flash's state can't be known."""
    p = _state_path(state_path)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            st = json.load(f)
    except (OSError, ValueError) as e:
        raise FlashGuardError(
            f"The flash state file {p} can't be read ({e}), so it isn't known whether the "
            "target's flash holds its original program. Don't delete it: look at it, and if a "
            "backup is listed, restore it.")
    if not isinstance(st, dict) or st.get('state') not in STATES:
        raise FlashGuardError(
            f"The flash state file {p} has an unknown state ({st.get('state') if isinstance(st, dict) else st!r}). "
            "It isn't known whether the target's flash holds its original program.")
    return st


def require_no_pending_restore(state_path=None):
    """Stop (FlashGuardError) if the flash was written and not yet restored.
    A backup with nothing written yet ('backed-up') is fine."""
    st = load_state(state_path)
    if st and st['state'] == 'written':
        raise FlashGuardError(
            "The target Pico's flash was changed by campaign F "
            f"(at {st.get('written_at') or 'an unknown time'}) and the original program has not "
            f"been put back yet. Backup: {(st.get('backup') or {}).get('bin', '?')}. " + RESTORE_HINT)


# ---------------------------------------------------------------------------

class FlashGuard:
    """Back up and restore the target's whole flash; see the module docstring.

    monitor(cmd, timeout) -> str   sends an OpenOCD command, returns its text
    run_picotool(args, timeout)     -> (returncode, output)
    ask(message)                    blocks until the person has done it
    log(text)                       progress for the person
    boot_check()                    -> (ok, description); before_reset() is called first if it has one
    reconnect()                     optional: re-open SWD after a power cycle
    read_unique_id()                optional: the board's flash unique ID, for restore()
    """

    def __init__(self, monitor, run_picotool=None, ask=None, log=None, boot_check=None,
                 reconnect=None, read_unique_id=None, backup_dir=None, cache_dir=None,
                 work_dir=None, state_path=None, expected_size=FLASH_SIZE, now=None):
        self.monitor = monitor
        self.run_picotool = run_picotool or default_run_picotool
        self.ask = ask or default_ask
        self.log = log or print
        self.boot_check = boot_check or UsbSerialBootCheck()
        self.reconnect = reconnect
        self.read_unique_id = read_unique_id
        self.backup_dir = os.path.expanduser(backup_dir or DEFAULT_BACKUP_DIR)
        self.cache_dir = os.path.expanduser(cache_dir or DEFAULT_CACHE_DIR)
        self.work_dir = os.path.expanduser(work_dir or DEFAULT_WORK_DIR)
        self.state_path = _state_path(state_path)
        self.expected_size = expected_size
        self.now = now or _now

    @classmethod
    def from_target(cls, target, **kw):
        """A FlashGuard for a picosim.hwtest.target.Target."""
        kw.setdefault('reconnect', target.reconnect)
        return cls(lambda cmd, timeout: target.rsp.monitor(cmd, timeout=timeout), **kw)

    # -- OpenOCD -----------------------------------------------------------

    def _mon(self, cmd, timeout=T_SHORT):
        try:
            out = self.monitor(cmd, timeout)
        except FlashGuardError:
            raise
        except Exception as e:
            raise FlashGuardError(
                f"OpenOCD command '{cmd}' failed: {e}. The debugger could not reach the target. "
                + LOST_HINT + " Otherwise check the three debug wires and that the target Pico "
                "has power, then start this step again.")
        return out or ''

    def _halt(self):
        out = self._mon('halt')
        if _looks_failed(out):
            raise FlashGuardError(f"OpenOCD could not halt the target before using its flash:\n{out.strip()}")

    def _reconnect(self):
        if self.reconnect:
            try:
                self.reconnect()
            except Exception as e:
                raise FlashGuardError(
                    f"Could not reconnect to the target over SWD after it was unplugged ({e}). "
                    "Check the debug probe's wires and the target's USB power.")

    def identify(self):
        """Probe the flash through OpenOCD; return its size.  Stops unless it
        is the RP2040's flash at 0x10000000 with the expected size."""
        self._halt()
        out = self._mon('flash probe 0')
        if _looks_failed(out) or not re.search(r'found at 0x0*10000000', out, re.I):
            raise FlashGuardError(
                "OpenOCD could not identify the target's flash chip ('flash probe 0' said: "
                f"{out.strip() or 'nothing'}). Nothing has been read or written. Check the debug "
                "probe's wires and that the target Pico has power.")
        # 'flash banks', not 'flash info 0': OpenOCD 0.12's 'flash info' runs a
        # protection check the rp2040 driver lacks ("Flash protection check is
        # not implemented"), so it fails after printing (E74, 2026-11-10).
        # 'flash banks' prints the same bank line, with the size just probed.
        info = self._mon('flash banks')
        m = re.search(r'size\s+(0x[0-9a-fA-F]+|\d+)', info)
        if _looks_failed(info) or not m or 'rp2040' not in info.lower():
            raise FlashGuardError(
                "OpenOCD's 'flash banks' did not report the RP2040 flash and its size. "
                f"It said: {info.strip()[:300] or 'nothing'}. Nothing has been read or written.")
        size = int(m.group(1), 0)
        if size != self.expected_size:
            raise FlashGuardError(
                f"The target's flash is {size} bytes (0x{size:X}), not the expected "
                f"{self.expected_size} bytes (0x{self.expected_size:X}). This procedure is only "
                "for a Pico or Pico W with its 2 MB flash; it stopped and nothing was written.")
        self.log(f"Flash identified: RP2040 flash at 0x10000000, {size // 1024} KiB.")
        return size

    def _read_bank(self, path, what):
        """The whole flash, read over SWD into `path`; returns the bytes."""
        self._halt()
        if os.path.exists(path):
            os.remove(path)            # a scratch file in the work folder, never a backup
        out = self._mon(f'flash read_bank 0 {tcl_path(path)}', T_READ)
        if _looks_failed(out, os.path.abspath(path)) or not os.path.exists(path):
            raise FlashGuardError(f"Reading the whole flash over SWD ({what}) failed. OpenOCD said:\n"
                                  f"{out.strip() or 'nothing'}")
        data = _read(path)
        if len(data) != self.expected_size:
            raise FlashGuardError(f"Reading the flash over SWD ({what}) gave {len(data)} bytes, "
                                  f"not {self.expected_size}.")
        return data

    # -- picotool ----------------------------------------------------------

    def _picotool(self, args, what):
        rc, out = self.run_picotool(args, T_PICOTOOL)
        if rc != 0:
            raise FlashGuardError(
                f"picotool failed while {what} (exit status {rc}). It said:\n{(out or '').strip()}\n"
                "Is the TARGET Pico in BOOTSEL mode (an RPI-RP2 drive showing)? " + WHICH_PICO)
        return out or ''

    def _picotool_save(self, path, what):
        if os.path.exists(path):
            os.remove(path)            # scratch file in the work folder
        self._picotool(['save', '--all', os.path.abspath(path), '-t', 'bin'], f"saving the flash ({what})")
        if not os.path.exists(path):
            raise FlashGuardError(f"picotool reported success but wrote no file ({what}).")
        data = _read(path)
        if len(data) != self.expected_size:
            raise FlashGuardError(f"picotool saved {len(data)} bytes ({what}), not the whole "
                                  f"{self.expected_size}-byte flash. Nothing has been written.")
        return data

    def unique_id_via_picotool(self, prompt=False):
        """The flash's 64-bit unique ID, read by picotool (board in BOOTSEL)."""
        if prompt:
            self.ask("Put the TARGET Pico in BOOTSEL mode.\n\n" + BOOTSEL_HOW + "\n\n" + WHICH_PICO
                     + "\n\nThen press \u201cDone: carry on\u201d.")
        out = self._picotool(['info', '-d'], "reading the flash's unique ID")
        uid = parse_flash_id(out)
        if not uid:
            raise FlashGuardError(
                "picotool did not report the flash's unique ID ('flash id'), so a backup can't "
                f"be tied to this board. It said:\n{out.strip()}\nNothing has been written.")
        return uid

    # -- backup ------------------------------------------------------------

    def backup(self, return_to_run_mode=True):
        """Steps 1-7: identify, back up twice, compare, store, record."""
        require_no_pending_restore(self.state_path)
        for d in (self.backup_dir, self.cache_dir, self.work_dir):
            os.makedirs(d, exist_ok=True)
        size = self.identify()
        a_path = os.path.join(self.work_dir, 'backup_a.bin')
        b_path = os.path.join(self.work_dir, 'backup_b.bin')

        self.log("Backup A: reading the whole flash over SWD ...")
        a = self._read_bank(a_path, "backup A")
        self.ask("Backup A (over the debug wires) is done. Backup B needs you now.\n\n"
                 + BOOTSEL_HOW + "\n\n" + WHICH_PICO + "\n\nThen press \u201cDone: carry on\u201d.")
        self.log("Backup B: reading the whole flash over USB with picotool ...")
        b = self._picotool_save(b_path, "backup B")
        sa, sb = sha256_bytes(a), sha256_bytes(b)
        self.log(f"  backup A (SWD) SHA-256: {sa}\n  backup B (USB) SHA-256: {sb}")
        uid = self.unique_id_via_picotool()     # while still in BOOTSEL
        if sa != sb:
            self.log("The two backups differ; reading both again once ...")
            b = self._picotool_save(b_path, "backup B, second read")
            self._reconnect()                   # after this the board may leave BOOTSEL
            a = self._read_bank(a_path, "backup A, second read")
            sa, sb = sha256_bytes(a), sha256_bytes(b)
            self.log(f"  backup A (SWD) SHA-256: {sa}\n  backup B (USB) SHA-256: {sb}")
            if sa != sb:
                raise FlashGuardError(
                    "The backup read over SWD and the backup read over USB are different, twice. "
                    "One of the two reads can't be trusted, so the procedure stopped: nothing "
                    "has been written to the board. Check the debug probe's wires and the USB "
                    "cable, then try again.")
        image = a

        backup = self._store(image, sa, uid, size)

        st = load_state(self.state_path) or {}
        older = list(st.get('older_backups', []))
        if st.get('backup'):
            older.append({'bin': st['backup'].get('bin'), 'sha256': st.get('sha256'),
                          'unique_id': st.get('unique_id'), 'backed_up_at': st.get('backed_up_at')})
        t = _iso(self.now())
        self._save_state({
            'format': 1, 'state': 'backed-up', 'unique_id': uid, 'size': size, 'sha256': sa,
            'backup': backup.paths(), 'backed_up_at': backup.created, 'written_at': None,
            'restored_at': None, 'readback_sha256': None, 'boot_check': None,
            'boot_check_detail': None, 'history': [{'state': 'backed-up', 'at': t}],
            'older_backups': older})
        self.log(f"Backup done and checked: {backup.bin_path}\n  copy: {backup.cache_bin_path}\n"
                 f"  drag-and-drop copy: {backup.uf2_path}\n  SHA-256: {sa}\n  flash unique ID: {uid}")
        if return_to_run_mode:
            self.ask("The backup is done and checked. Now unplug the TARGET Pico's USB cable and plug it "
                     "back in normally, without holding BOOTSEL. The RPI-RP2 drive disappears; macOS "
                     "may say \u201cDisk Not Ejected Properly\u201d: that is expected and harmless.\n\n"
                     "Then press \u201cDone: carry on\u201d.")
            self._reconnect()
        return backup

    def _store(self, image, sha, uid, size):
        """Step 6: two copies, each .bin + .sha256 + .uf2, all read back and checked."""
        stem = f"{self.now().strftime('%Y%m%d-%H%M%S')}-{uid}"
        k = 1
        while any(os.path.exists(os.path.join(d, s + ext))
                  for d in (self.backup_dir, self.cache_dir)
                  for s in [stem if k == 1 else f'{stem}-{k}']
                  for ext in ('.bin', '.sha256', '.uf2')):
            k += 1
        if k > 1:
            stem = f'{stem}-{k}'
        uf2 = make_uf2(image)
        paths = {}
        for tag, d in (('', self.backup_dir), ('cache_', self.cache_dir)):
            binp = os.path.join(d, stem + '.bin')
            shap = os.path.join(d, stem + '.sha256')
            uf2p = os.path.join(d, stem + '.uf2')
            try:
                _write_new(binp, image)
                _write_new(shap, f"{sha}  {os.path.basename(binp)}\n".encode())
                _write_new(uf2p, uf2)
            except OSError as e:
                raise FlashGuardError(f"Could not save the backup in {d} ({e}). Nothing has been "
                                      "written to the board.")
            self._check_copy(binp, shap, uf2p, sha, image)
            paths[tag] = (binp, shap, uf2p)
        return Backup(unique_id=uid, size=size, sha256=sha,
                      bin_path=paths[''][0], sha256_path=paths[''][1], uf2_path=paths[''][2],
                      cache_bin_path=paths['cache_'][0], cache_sha256_path=paths['cache_'][1],
                      cache_uf2_path=paths['cache_'][2], created=_iso(self.now()))

    def _check_copy(self, binp, shap, uf2p, sha, image):
        if sha256_file(binp) != sha:
            raise FlashGuardError(f"The saved backup {binp} does not read back the same. "
                                  "Nothing has been written to the board; check the disk.")
        if self._sha_from_file(shap) != sha:
            raise FlashGuardError(f"The checksum file {shap} does not read back the same.")
        check_uf2(uf2p, image)

    @staticmethod
    def _sha_from_file(path):
        try:
            with open(path, encoding='utf-8') as f:
                return f.read().split()[0].lower()
        except (OSError, IndexError):
            return None

    # -- state -------------------------------------------------------------

    def _save_state(self, st):
        _atomic_write_json(self.state_path, st)

    def _set_state(self, st, state, **fields):
        t = _iso(self.now())
        st['state'] = state
        st.update(fields)
        st.setdefault('history', []).append({'state': state, 'at': t})
        self._save_state(st)
        return st

    def status(self):
        """The state file in a few words, plus its contents."""
        st = load_state(self.state_path)
        if not st:
            return {'state': None, 'pending_restore': False,
                    'message': "No flash backup has been made; the flash has not been touched."}
        msg = {
            'backed-up': "The flash is backed up and has not been written.",
            'written': "The flash has been written and NOT yet restored. " + RESTORE_HINT,
            'restored': "The original flash has been restored and proven identical (SHA-256)."
                        + ("" if st.get('boot_check') in (None, True)
                           else " The boot check did not confirm the program started."),
        }[st['state']]
        return dict(st, pending_restore=(st['state'] == 'written'), message=msg)

    def mark_written(self, verify=True):
        """Call right before the campaign writes flash.  Checks over SWD that
        the flash still equals the backup (so a restore gives back exactly
        what is there now), then records 'written'."""
        st = load_state(self.state_path)
        if not st:
            raise FlashGuardError("There is no flash backup yet, so writing the flash is not "
                                  "allowed. Run the backup step first.")
        if st['state'] == 'written':
            return st
        backup = Backup.from_state(st)
        if verify:
            self.log("Checking that the flash still matches the backup before writing ...")
            data = self._read_bank(os.path.join(self.work_dir, 'prewrite.bin'), "check before writing")
            now_sha = sha256_bytes(data)
            if now_sha != backup.sha256:
                raise FlashGuardError(
                    "The flash no longer matches the backup (it was changed since the backup "
                    f"was made, or this is a different board). Backup SHA-256: {backup.sha256}; "
                    f"flash now: {now_sha}. Nothing was written. Make a new backup first.")
        return self._set_state(st, 'written', written_at=_iso(self.now()), restored_at=None,
                               readback_sha256=None, boot_check=None, boot_check_detail=None)

    # -- restore -----------------------------------------------------------

    def _good_image(self, backup):
        """The path of a backup copy whose SHA-256 is the recorded one."""
        for p, shap in ((backup.bin_path, backup.sha256_path),
                        (backup.cache_bin_path, backup.cache_sha256_path)):
            try:
                if os.path.getsize(p) == backup.size and sha256_file(p) == backup.sha256:
                    if self._sha_from_file(shap) not in (None, backup.sha256):
                        self.log(f"Note: {shap} disagrees with the state file; using the state file's checksum.")
                    return p
                self.log(f"Note: {p} does not match its recorded SHA-256; trying the other copy.")
            except OSError:
                self.log(f"Note: {p} can't be read; trying the other copy.")
        for u in (backup.uf2_path, backup.cache_uf2_path):
            try:
                data = decode_uf2(_read(u))
            except (OSError, FlashGuardError):
                continue
            if len(data) == backup.size and sha256_bytes(data) == backup.sha256:
                p = os.path.join(self.work_dir, 'from_uf2.bin')
                os.makedirs(self.work_dir, exist_ok=True)
                _write_scratch(p, data)
                if sha256_file(p) == backup.sha256:
                    self.log(f"Using the image decoded from {u}.")
                    return p
        raise FlashGuardError(
            "No copy of the backup matches its recorded SHA-256 (both .bin files and both .uf2 "
            "files were checked). Nothing has been written. Don't delete anything; ask for help.")

    def restore(self, backup=None, board_id=None):
        """Steps 8-11.  `board_id`: the board's flash unique ID, if the caller
        has it; otherwise read_unique_id() or picotool (asks for BOOTSEL)."""
        os.makedirs(self.work_dir, exist_ok=True)
        st = load_state(self.state_path)
        if backup is None:
            if not st:
                raise FlashGuardError("There is no flash backup recorded, so there is nothing to restore.")
            backup = Backup.from_state(st)
        if backup.size != self.expected_size:
            raise FlashGuardError(f"The backup is {backup.size} bytes, not {self.expected_size}; "
                                  "it can't be a whole-chip backup of this board.")
        image = self._good_image(backup)

        # Step 8: the right board
        if board_id is None:
            if self.read_unique_id:
                board_id = self.read_unique_id()
            else:
                board_id = self.unique_id_via_picotool(prompt=True)
                self._reconnect()
        bid = normalize_id(board_id)
        if not bid or bid != normalize_id(backup.unique_id):
            raise FlashGuardError(
                f"This board's flash ID is {bid or board_id!r}, but the backup belongs to the board "
                f"with flash ID {backup.unique_id}. Restoring it here would put another board's "
                "program on this one, so nothing was written. Connect the board the backup came from.")

        size = self.identify()
        if size != backup.size:
            raise FlashGuardError(f"The board's flash is {size} bytes but the backup is {backup.size}.")

        # Step 9: write and verify
        self.log(f"Writing the backup to the whole flash ({image}) ...")
        self._halt()
        p = os.path.abspath(image)
        out = self._mon(f'flash write_image erase {tcl_path(image)} 0x{XIP_BASE:08x} bin', T_WRITE)
        if _looks_failed(out, p):
            raise FlashGuardError("Writing the backup to the flash failed. OpenOCD said:\n"
                                  f"{out.strip()}\nThe flash may be half-written; that is not "
                                  "permanent. Run the restore again (or drag the .uf2 onto the "
                                  "RPI-RP2 drive in BOOTSEL mode).")
        out = self._mon(f'verify_image {tcl_path(image)} 0x{XIP_BASE:08x} bin', T_READ)
        m = re.search(r'verified\s+(\d+)\s+bytes', out)
        if _looks_failed(out, p) or not m or int(m.group(1)) != backup.size:
            raise FlashGuardError("OpenOCD's verify_image did not confirm the flash matches the "
                                  f"backup. It said:\n{out.strip() or 'nothing'}\nRun the restore again.")

        # Step 10: prove identical
        rb_path = os.path.join(self.work_dir, 'readback.bin')
        rb = self._read_bank(rb_path, "read-back after restore")
        rb_sha = sha256_bytes(rb)
        self.log(f"  backup    SHA-256: {backup.sha256}\n  read-back SHA-256: {rb_sha}")
        if rb_sha != backup.sha256:
            raise FlashGuardError("The flash read back after the restore does not match the backup "
                                  f"(backup {backup.sha256}, read-back {rb_sha}). The restore is NOT "
                                  "finished. Run the restore again.")
        if st and normalize_id(st.get('unique_id')) == normalize_id(backup.unique_id) \
                and st.get('sha256') == backup.sha256:
            st = self._set_state(st, 'restored', restored_at=_iso(self.now()), readback_sha256=rb_sha)
        self.log("The flash is identical to the backup, bit for bit.")

        # Step 11: boot check
        before = getattr(self.boot_check, 'before_reset', None)
        if before:
            before()
        out = self._mon('reset run')
        try:
            ok, detail = self.boot_check()
        except Exception as e:
            ok, detail = False, f"the boot check itself failed ({e})"
        if _looks_failed(out):
            ok, detail = False, f"'reset run' reported a problem: {out.strip()}. " + detail
        if st and st['state'] == 'restored':
            st['boot_check'] = bool(ok)
            st['boot_check_detail'] = detail
            self._save_state(st)
        if ok:
            self.log(f"Boot check passed: {detail}")
        else:
            self.log(f"Boot check did NOT confirm the program started: {detail}\n"
                     "The flash is still proven identical to the backup (SHA-256 above).")
        return RestoreResult(backup_sha256=backup.sha256, readback_sha256=rb_sha,
                             boot_ok=bool(ok), boot_detail=detail, image_used=image)


def check_uf2(uf2_path, image):
    """Decode a .uf2 and check it equals the image; FlashGuardError if not."""
    try:
        blob = _read(uf2_path)
    except OSError as e:
        raise FlashGuardError(f"The .uf2 backup {uf2_path} can't be read ({e}).")
    data = decode_uf2(blob)
    if data != image:
        raise FlashGuardError(f"The .uf2 backup {uf2_path} does not decode to the same image as "
                              "the .bin. Nothing has been written to the board.")
