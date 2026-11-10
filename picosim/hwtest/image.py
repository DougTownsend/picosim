"""
The program as a real RP2040 runs it under Testing.

picosim runs a program on the RP2040's memory map (memmap.py): its OS in the
first 256 bytes of flash, its code after that, its variables in SRAM at
0x20000000 and its stack at the top of SRAM.  Testing cannot write the
chip's flash (it never flashes a test), so it puts a copy of picosim's flash
in SRAM instead and links the program there:

    picosim   0x10000000  OS (os.s)   0x10000100  program code   (flash)
    RP2040    0x20020000  OS (os_hw.s) 0x20020100  program code   (SRAM)
    both      0x20000000  .data, .bss              0x20042000  stack top

Code addresses on the chip are picosim's + CODE_OFF; data and stack
addresses are the same on both.  A register or a memory word holding a code
address differs by exactly CODE_OFF and everything else is identical.
build() checks that: the same symbols at the same places, and the same
bytes except code-address literals.

os_hw.s's chip-only routines (the SysTick reads, the timed OS) sit just
below the copy, at HW_EXTRA, and its table of SysTick readings at
BENCH_TABLE: SRAM the program does not use (its data must end below DATA_LIMIT).
"""

import hashlib
import os
import struct

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from ..memmap import (FLASH_BASE, OS_END, USER_BASE, MAIN_PTR, SRAM_BASE, SRAM_END,
                      STACK_TOP, fmt)

CODE_HW = 0x20020000          # where the chip holds picosim's flash (0x10000000)
CODE_OFF = CODE_HW - FLASH_BASE
WINDOW = 0xC000               # 48 KiB of flash copied: the OS and the program's code
HW_EXTRA = CODE_HW - 0x400    # os_hw.s's .text.hw (os_hw.ld): within a B's reach of putchar
BENCH_TABLE = 0x2001E000      # chip address of os_hw.s's SysTick table
BENCH_MAX = 250
DATA_LIMIT = BENCH_TABLE      # the program's .data and .bss must end below this
STACK_KEEP = 0x4000           # the top 16 KiB of SRAM (the stack) is cleared and compared
OS_CALLS = ('putchar', 'getchar', 'bench_start', 'bench_stop')
OFFSET = CODE_OFF             # (older name)


class ImageError(Exception):
    pass


def _in_flash_window(a):
    return FLASH_BASE <= a < FLASH_BASE + WINDOW


def to_hw(addr):
    """picosim's address as the chip has it: code moves, data does not."""
    return addr + CODE_OFF if _in_flash_window(addr) else addr


def to_sim(v):
    """A value read from the chip, as picosim would hold it: an address in
    the chip's copy of the flash becomes picosim's flash address."""
    return v - CODE_OFF if CODE_HW <= v < CODE_HW + WINDOW else v


def same(sim_v, hw_v):
    """True if the chip's value is picosim's value (a code address may differ by CODE_OFF)."""
    return sim_v == hw_v or (_in_flash_window(sim_v) and hw_v == sim_v + CODE_OFF)


def _here():
    return os.path.dirname(os.path.abspath(__file__))


def _cache_dir():
    try:
        from platformdirs import user_cache_dir
        d = os.path.join(user_cache_dir("picosim"), "hwtest")
    except ImportError:
        d = os.path.expanduser("~/.cache/picosim/hwtest")
    os.makedirs(d, exist_ok=True)
    return d


def _symbols(elf_path):
    out = {}
    with open(elf_path, 'rb') as f:
        for sec in ELFFile(f).iter_sections():
            if not isinstance(sec, SymbolTableSection):
                continue
            for s in sec.iter_symbols():
                if not s.name or s.name.startswith('$') or s['st_shndx'] in ('SHN_ABS', 'SHN_UNDEF'):
                    continue
                v = s['st_value']
                if s['st_info']['type'] == 'STT_FUNC':
                    v &= ~1
                out[s.name] = v
    return out


def _load(elf_path, image):
    """Copy the ELF's loaded sections (code and initialised data) into
    `image` ({chip address: bytearray} chunks; a section must fall inside
    one).  Sections, not segments: a segment can start with padding."""
    with open(elf_path, 'rb') as f:
        for sec in ELFFile(f).iter_sections():
            if not sec['sh_flags'] & 0x2 or sec['sh_type'] != 'SHT_PROGBITS' or not sec['sh_size']:
                continue                         # not loaded, or .bss (cleared anyway)
            va, data = sec['sh_addr'], sec.data()
            for base, buf in image.items():
                if base <= va and va + len(data) <= base + len(buf):
                    buf[va - base:va - base + len(data)] = data
                    break
            else:
                raise ImageError(f"the program has {len(data)} bytes at {fmt(va)}, outside "
                                 "the SRAM Testing uses for it")


def build_os(timed=False):
    """Assemble os_hw.s (cached) and check it lines up with os.s.  Returns its
    ELF path.  timed: the variant that times every stretch between OS calls."""
    from ..loader import build_os_elf
    from ..sim import assemble
    src = os.path.join(_here(), 'os_hw.s')
    ld = os.path.join(_here(), 'os_hw.ld')
    out = os.path.join(_cache_dir(), 'os_hw_timed.elf' if timed else 'os_hw.elf')
    if not os.path.exists(out) or max(os.path.getmtime(src), os.path.getmtime(ld)) > os.path.getmtime(out):
        ok, msg = assemble(src, out, ld, extra_ld_flags=['-Wa,--defsym,TIMED=1'] if timed else None)
        if not ok:
            raise ImageError(f"building the RP2040 OS failed:\n{msg}")
    sim_syms = _symbols(build_os_elf())
    hw_syms = _symbols(out)
    for name in ('_start',) + OS_CALLS:
        if name not in hw_syms or to_sim(hw_syms[name]) != sim_syms.get(name):
            raise ImageError(f"os_hw.s's {name} is not at the same offset as os.s's "
                             f"({fmt(hw_syms.get(name, 0))} vs {fmt(sim_syms.get(name, 0))})")
    return out


class HwImage:
    """A program built for the RP2040's SRAM.

    chunks     [(chip address, bytes)] to write: the copy of the flash (OS and
               program), os_hw.s's chip-only routines, the program's data
               (.data as loaded, .bss and the rest up to a 4 KiB boundary
               cleared), the top of SRAM (the stack) and BENCH_TABLE cleared
    compare    [(start, end)] picosim addresses compared when a test ends: the
               program's code, its data and the stack's part of SRAM
    symbols    name -> picosim address (the same names on both)
    calls      OS routine name -> picosim address
    halt_addr  picosim address of _start's BKPT, where main returns to
    timed      built with the timed OS: {'go', 'putchar', 'getchar', 'end'}
               -> chip address of hw_go and of the three BKPTs; else None
    """

    def __init__(self, chunks, compare, symbols, calls, halt_addr, elf, timed=None):
        self.chunks = chunks
        self.compare = compare
        self.symbols = symbols
        self.calls = calls
        self.halt_addr = halt_addr
        self.elf = elf
        self.timed = timed

    @property
    def digest(self):
        h = hashlib.sha1()
        for a, data in self.chunks:
            h.update(a.to_bytes(4, 'little') + data)
        return h.hexdigest()[:12]


def build(source, prog, timed=False):
    """Build `source` (the .s file picosim assembled into `prog`) for SRAM.
    timed: with the OS that times every stretch between OS calls (bench.py).

    Raises ImageError when the build fails or its layout is not picosim's."""
    from ..sim import assemble
    os_elf = build_os(timed)
    pkg = os.path.dirname(_here())
    with open(os.path.join(pkg, 'link.ld')) as f:
        ld_text = f.read()
    # the program's own linker script, its flash moved into SRAM
    flash = f'ORIGIN = 0x{USER_BASE:08X}, LENGTH = 2M - 0x{USER_BASE - FLASH_BASE:X}'
    hw_ld_text = ld_text.replace(flash, f'ORIGIN = 0x{to_hw(USER_BASE):08X}, '
                                        f'LENGTH = 0x{WINDOW - (USER_BASE - FLASH_BASE):X}')
    if hw_ld_text == ld_text:
        raise ImageError(f"link.ld no longer places the program at 0x{USER_BASE:08X}")
    key = hashlib.sha1((os.path.abspath(source) + hw_ld_text).encode()).hexdigest()[:10]
    work = _cache_dir()
    hw_ld = os.path.join(work, 'link_hw.ld')
    old = None
    if os.path.exists(hw_ld):
        with open(hw_ld) as f:
            old = f.read()
    if old != hw_ld_text:
        tmp = f"{hw_ld}.{os.getpid()}.tmp"      # replaced in one step: other processes use it too
        with open(tmp, 'w') as f:
            f.write(hw_ld_text)
        os.replace(tmp, hw_ld)
    elf = os.path.join(work, f"{os.path.splitext(os.path.basename(source))[0]}-{key}.elf")
    ok, msg = assemble(source, elf, hw_ld,
                       extra_s_files=[os.path.join(pkg, 'peripherals.s')],
                       extra_ld_flags=[f'-Wl,--just-symbols={os_elf}'])
    if not ok:
        raise ImageError(f"building the program for the RP2040 failed:\n{msg}")

    code_end = max([e for k, (_, e) in prog.sections.items() if k == 'text'] + [USER_BASE])
    data_end = max([e for k, (_, e) in prog.sections.items() if k != 'text'] + [SRAM_BASE])
    if code_end > FLASH_BASE + WINDOW:
        raise ImageError(f"the program's code ends at {fmt(code_end)}: Testing copies only the "
                         f"first {WINDOW // 1024} KiB of flash into the chip's SRAM")
    if data_end > DATA_LIMIT:
        raise ImageError(f"the program's variables end at {fmt(data_end)}: Testing needs SRAM from "
                         f"{fmt(DATA_LIMIT)} on for itself")
    window = bytearray(b'\xff' * (code_end - FLASH_BASE + 3 & ~3))
    extra = bytearray(CODE_HW - HW_EXTRA)
    data = bytearray(min(DATA_LIMIT, (data_end + 0xFFF) & ~0xFFF) - SRAM_BASE)
    image = {CODE_HW: window, HW_EXTRA: extra, SRAM_BASE: data}
    _load(os_elf, image)
    _load(elf, image)
    struct.pack_into('<I', window, MAIN_PTR - FLASH_BASE, to_hw(prog.main_addr) | 1)

    hw_syms = {n: to_sim(v) for n, v in _symbols(elf).items()}
    sim_syms = {name: addr for addr, name in prog.sym_map.items() if addr >= USER_BASE}
    for name, addr in sim_syms.items():
        if name in hw_syms and hw_syms[name] != addr:
            raise ImageError(f"label {name} is at {fmt(addr)} in picosim but {fmt(to_hw(hw_syms[name]))} "
                             "on the RP2040, so the two builds are laid out differently")
    _check_bytes(prog, window, data, code_end, data_end)

    os_syms = _symbols(os_elf)
    calls = {n: to_sim(os_syms[n]) for n in OS_CALLS}
    halt = to_sim(os_syms['_start']) + 6
    stops = None
    if timed:
        stops = {k: os_syms[n] for k, n in (('go', 'hw_go'), ('putchar', 'hw_putchar_bkpt'),
                                            ('getchar', 'hw_getchar_bkpt'), ('end', 'hw_end_bkpt'))}
    stack = (STACK_TOP - STACK_KEEP, STACK_TOP)
    chunks = [(CODE_HW, bytes(window)), (HW_EXTRA, bytes(extra)), (SRAM_BASE, bytes(data)),
              (stack[0], bytes(STACK_KEEP)), (BENCH_TABLE, bytes(8 + 8 * BENCH_MAX))]
    compare = [(USER_BASE, code_end), (SRAM_BASE, SRAM_BASE + len(data)), stack]
    return HwImage(chunks, compare, {**sim_syms, **calls}, calls, halt, elf, stops)


def _check_bytes(prog, window, data, code_end, data_end):
    """The program's bytes must be picosim's, except words that are code
    address literals (picosim's address + CODE_OFF)."""
    for lo, hi, buf, base in ((USER_BASE, code_end, window, FLASH_BASE),
                              (SRAM_BASE, data_end, data, SRAM_BASE)):
        a = lo
        while a < hi:
            n = min(4, hi - a)
            sim, hw = prog.image[a:a + n], bytes(buf[a - base:a - base + n])
            if sim == hw:
                a += 4
                continue
            s, h = int.from_bytes(sim, 'little'), int.from_bytes(hw, 'little')
            if a % 4 == 0 and n == 4 and same(s, h):
                a += 4
                continue
            raise ImageError(f"the RP2040 build differs from picosim's at {fmt(a)} "
                             f"(0x{s:08X} vs 0x{h:08X}) in a way that is not an address")
