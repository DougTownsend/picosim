"""
picosim's memory map: the RP2040's, at the RP2040's addresses.

    0x00000000  ROM    16 KB   the Pico's boot ROM (empty in picosim)
    0x10000000  flash   2 MB   picosim's OS (os.s: _start, putchar, getchar, ...) in
                               its first 256 bytes, then the program's code and
                               constants (.text, .rodata) from 0x10000100
    0x20000000  SRAM  264 KB   the program's variables (.data, .bss) and the stack
    0x40000000 ...             I/O registers (gpio.py): RESETS, IO_BANK0, PADS_BANK0
    0xD0000000                 the SIO (GPIO in, out, output enable)

The stack starts at the top of SRAM (SP = 0x20042000) and grows down.
The program's code runs from flash as it would from the Pico's XIP cache,
with no wait states.  The OS is in flash, not ROM, because a BL reaches only
16 MB: from 0x10000100 it could not call putchar at 0x00000010.  On a Pico
the first 256 bytes of flash hold the boot code (boot2), and the SDK's
start-up code and putchar are in flash too.  Stores into ROM or flash stop the program (the core
explains why); the debugger and the loader can write anywhere.

The C++ core (picosim_core.cpp) holds the memories; the sizes here come from it.
"""

from ._picosim_core import (CPUCore, ROM_BASE, ROM_SIZE, FLASH_BASE, FLASH_SIZE,
                            SRAM_BASE, SRAM_SIZE, CODE_SLOTS)

ROM_END = ROM_BASE + ROM_SIZE
FLASH_END = FLASH_BASE + FLASH_SIZE
SRAM_END = SRAM_BASE + SRAM_SIZE            # 0x20042000

OS_BASE = FLASH_BASE                        # os.s, linked by os.ld
OS_END = FLASH_BASE + 0x100                 # the OS's part of flash ends here
MAIN_PTR = OS_END - 4                       # the loader writes main's address here; _start calls it
USER_BASE = OS_END                          # .text and .rodata (link.ld)
DATA_BASE = SRAM_BASE                       # .data, then .bss (link.ld)
STACK_TOP = SRAM_END                        # the initial SP
ERASED = 0xFF                               # flash the program does not fill

REGIONS = (                                 # (name, base, end, what is there)
    ('ROM', ROM_BASE, ROM_END, "the Pico's boot ROM (empty in picosim)"),
    ('Flash', FLASH_BASE, FLASH_END, "picosim's OS, then the program's code and constants"),
    ('SRAM', SRAM_BASE, SRAM_END, "the program's variables and the stack"),
)


def kind(addr):
    """'rom', 'flash', 'sram' or 'io' (any other address)."""
    return CPUCore.mem_kind(addr & 0xFFFFFFFF)


def is_memory(addr):
    return kind(addr) != 'io'


def in_os(addr):
    """The address is in the built-in OS (the first 256 bytes of flash)."""
    return OS_BASE <= addr < OS_END


def region_name(addr):
    for name, lo, hi, _ in REGIONS:
        if lo <= addr < hi:
            return name
    return 'I/O'


def span(addr, n):
    """(base, end) of the memory holding [addr, addr + n), or None if the
    range is not inside one of ROM, flash and SRAM."""
    for _, lo, hi, _ in REGIONS:
        if lo <= addr and addr + n <= hi:
            return lo, hi
    return None


def code_slot(addr):
    """The core's profile slot for a code address (None: not profiled)."""
    return CPUCore.code_slot(addr)


def slot_addr(slot):
    return CPUCore.slot_addr(slot)


class Image:
    """A program's memory as loaded: ROM, flash and SRAM contents at their
    RP2040 addresses.  Indexing and slicing take addresses, and slices stay
    inside one memory: image[a], image[a:b], image.find(0, a, b)."""

    def __init__(self):
        self.rom = bytearray(ROM_SIZE)
        self.flash = bytearray()               # grows as the program is loaded
        self.sram = bytearray(SRAM_SIZE)

    def _buf(self, addr, n, grow=False):
        """(bytearray, offset) holding [addr, addr + n)."""
        if ROM_BASE <= addr and addr + n <= ROM_END:
            return self.rom, addr - ROM_BASE
        if SRAM_BASE <= addr and addr + n <= SRAM_END:
            return self.sram, addr - SRAM_BASE
        if FLASH_BASE <= addr and addr + n <= FLASH_END:
            off = addr - FLASH_BASE
            if grow and off + n > len(self.flash):
                self.flash.extend(bytes([ERASED]) * (off + n - len(self.flash)))
            return self.flash, off
        raise ValueError(f"0x{addr:08X} + {n} bytes is not inside ROM, flash or SRAM")

    def write(self, addr, data):
        buf, off = self._buf(addr, len(data), grow=True)
        buf[off:off + len(data)] = data

    def read(self, addr, n):
        buf, off = self._buf(addr, n)
        out = bytes(buf[off:off + n])
        return out + bytes([ERASED]) * (n - len(out))   # flash past the program

    def __getitem__(self, key):
        if isinstance(key, slice):
            return self.read(key.start, max(0, key.stop - key.start))
        return self.read(key, 1)[0]

    def __setitem__(self, key, data):
        if isinstance(key, slice):
            self.write(key.start, data)
        else:
            self.write(key, bytes([data]))

    def find(self, sub, start, end):
        buf, off = self._buf(start, end - start)
        i = buf.find(sub, off, off + (end - start))
        return -1 if i < 0 else start + (i - off)

    def copy(self):
        new = Image()
        new.rom[:], new.flash[:], new.sram[:] = self.rom, self.flash, self.sram
        return new

    def load_into(self, core):
        """Put every memory into a C++ core."""
        core.load_memory(bytes(self.rom), ROM_BASE)
        core.load_memory(bytes(self.flash), FLASH_BASE)
        core.load_memory(bytes(self.sram), SRAM_BASE)


def fmt(addr):
    """An address as picosim shows it: 8 hex digits (0x10000124)."""
    return f"0x{addr & 0xFFFFFFFF:08X}"


SCRATCH = SRAM_BASE + 0x100                 # where scratch cores put the one instruction they clock


def scratch_core(hw1, hw2=0):
    """A core set up to clock one encoding at SCRATCH (in SRAM): R0-R7 hold
    an SRAM address, LR a Thumb return address, SP a word in SRAM.  The values
    are such that every load and store lands in SRAM; only the states matter."""
    c = CPUCore()
    c.strict = False          # e.g. BLX R3 to an even address: only the states matter here
    c.write16(SCRATCH, hw1)
    c.write16(SCRATCH + 2, hw2)
    for r in range(8):
        c.set_reg(r, SRAM_BASE + 0x400)
    c.set_reg(14, SRAM_BASE + 0x201)
    c.sp = SRAM_BASE + 0x800
    c.pc = SCRATCH
    return c
