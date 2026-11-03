"""
The program as a real RP2040 runs it under Testing: one 64 KiB SRAM image
that mirrors picosim's memory.

picosim runs a program in a flat 64 KiB space: the built-in OS at 0x0000 and
the program at 0x3000.  The RP2040 cannot run code at those addresses (its
ROM is there), so Testing links the same program for SRAM instead:

    picosim   0x0000  OS (os.s)          0x3000  program     0x10000  stack top
    RP2040    0x20000000  OS (os_hw.s)   0x20003000  program  0x20010000

Every address on the chip is then picosim's address + OFFSET, so a register
or a memory word holding an address differs by exactly OFFSET and everything
else is identical.  build() checks that: the same symbols at the same
offsets, and the same bytes except address literals.
"""

import hashlib
import os
import struct

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from ..cpu import MEM_SIZE, STACK_TOP

OFFSET = 0x20000000          # RP2040 address = picosim address + OFFSET
SRAM_END = 0x20042000        # end of the RP2040's 264 KiB of SRAM
USER_BASE = 0x3000
MAIN_PTR = 0x2FFC
BENCH_TABLE = 0x2000         # picosim address of os_hw.s's BENCH_TABLE
BENCH_MAX = 250
OS_CALLS = ('putchar', 'getchar', 'bench_start', 'bench_stop')


class ImageError(Exception):
    pass


def to_hw(addr):
    return addr + OFFSET


def to_sim(v):
    """A value read from the chip, as picosim would hold it: an SRAM address
    inside the mirrored 64 KiB (or just past it: the stack's top) becomes
    picosim's address."""
    return v - OFFSET if OFFSET <= v <= OFFSET + MEM_SIZE else v


def same(sim_v, hw_v):
    """True if the chip's value is picosim's value (an address may differ by OFFSET)."""
    return sim_v == hw_v or (0 <= sim_v <= MEM_SIZE and hw_v == sim_v + OFFSET)


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
                if not s.name or s.name.startswith('$') or s['st_shndx'] == 'SHN_ABS':
                    continue
                v = s['st_value']
                if s['st_info']['type'] == 'STT_FUNC':
                    v &= ~1
                out[s.name] = v
    return out


def _load(elf_path, base, image):
    """Copy the ELF's loadable segments into `image` (which starts at `base`)."""
    with open(elf_path, 'rb') as f:
        for seg in ELFFile(f).iter_segments():
            if seg.header.p_type != 'PT_LOAD' or not seg.header.p_memsz:
                continue
            a = seg.header.p_vaddr - base
            data = seg.data()
            if a < 0 or a + seg.header.p_memsz > len(image):
                raise ImageError(f"segment at 0x{seg.header.p_vaddr:08X} is outside the 64 KiB image")
            image[a:a + len(data)] = data


def build_os():
    """Assemble os_hw.s (cached) and check it lines up with os.s.  Returns its ELF path."""
    from ..loader import build_os_elf
    from ..sim import assemble
    src = os.path.join(_here(), 'os_hw.s')
    ld = os.path.join(_here(), 'os_hw.ld')
    out = os.path.join(_cache_dir(), 'os_hw.elf')
    if not os.path.exists(out) or max(os.path.getmtime(src), os.path.getmtime(ld)) > os.path.getmtime(out):
        ok, msg = assemble(src, out, ld)
        if not ok:
            raise ImageError(f"building the RP2040 OS failed:\n{msg}")
    sim_syms = _symbols(build_os_elf())
    hw_syms = _symbols(out)
    for name in ('_start',) + OS_CALLS:
        if name not in hw_syms or hw_syms[name] - OFFSET != sim_syms.get(name):
            raise ImageError(f"os_hw.s's {name} is not at the same offset as os.s's "
                             f"(0x{hw_syms.get(name, 0):08X} vs 0x{sim_syms.get(name, 0):04X})")
    return out


class HwImage:
    """A program built for the RP2040's SRAM.

    image      64 KiB, the chip's 0x20000000-0x2000FFFF (OS, program, zeros)
    symbols    name -> picosim address (the same names on both)
    calls      OS routine name -> picosim address
    halt_addr  picosim address of _start's BKPT, where main returns to
    """

    def __init__(self, image, symbols, calls, halt_addr, elf):
        self.image = image
        self.symbols = symbols
        self.calls = calls
        self.halt_addr = halt_addr
        self.elf = elf

    @property
    def digest(self):
        return hashlib.sha1(self.image).hexdigest()[:12]


def build(source, prog):
    """Build `source` (the .s file picosim assembled into `prog`) for SRAM.

    Raises ImageError when the build fails or its layout is not picosim's."""
    from ..sim import assemble
    os_elf = build_os()
    pkg = os.path.dirname(_here())
    with open(os.path.join(pkg, 'link.ld')) as f:
        ld_text = f.read()
    # the program's own linker script, moved into SRAM
    hw_ld_text = ld_text.replace('ORIGIN = 0x3000', f'ORIGIN = 0x{to_hw(USER_BASE):08X}') \
                        .replace('. = 0x3000;', f'. = 0x{to_hw(USER_BASE):08X};')
    if hw_ld_text == ld_text:
        raise ImageError("link.ld no longer places the program at 0x3000")
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

    image = bytearray(MEM_SIZE)
    _load(os_elf, OFFSET, image)
    _load(elf, OFFSET, image)
    struct.pack_into('<I', image, MAIN_PTR, to_hw(prog.main_addr) | 1)

    hw_syms = {n: v - OFFSET for n, v in _symbols(elf).items() if OFFSET <= v < OFFSET + MEM_SIZE}
    sim_syms = {name: addr for addr, name in prog.sym_map.items() if addr >= USER_BASE}
    for name, addr in sim_syms.items():
        if name in hw_syms and hw_syms[name] != addr:
            raise ImageError(f"label {name} is at 0x{addr:04X} in picosim but 0x{to_hw(hw_syms[name]):08X} "
                             "on the RP2040, so the two builds are laid out differently")
    _check_bytes(prog, image)

    os_syms = _symbols(os_elf)
    calls = {n: os_syms[n] - OFFSET for n in OS_CALLS}
    halt = os_syms['_start'] - OFFSET + 6
    return HwImage(bytes(image), {**sim_syms, **calls}, calls, halt, elf)


def _check_bytes(prog, image):
    """The program's bytes must be picosim's, except words that are address
    literals (picosim's address + OFFSET)."""
    sim = prog.image
    end = max([e for _, e in prog.sections.values()] + [USER_BASE])
    a = USER_BASE
    while a < end:
        if sim[a:a + 4] == image[a:a + 4]:
            a += 4
            continue
        s, h = struct.unpack_from('<I', sim, a)[0], struct.unpack_from('<I', image, a)[0]
        if a % 4 == 0 and same(s, h):
            a += 4
            continue
        raise ImageError(f"the RP2040 build differs from picosim's at 0x{a:04X} "
                         f"(0x{s:08X} vs 0x{h:08X}) in a way that is not an address")
