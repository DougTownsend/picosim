"""
Campaign 0: which chip, ROM and debug parts this is, plus the boot ROM.

Debugger reads only (nothing runs on the core):
  SYSINFO CHIP_ID / PLATFORM / GITREF, the ROM's magic and version byte,
  CPUID, MPU_TYPE, the CoreSight ROM table at 0xE00FF000 (which says whether
  an MTB exists), the breakpoint unit's and DWT's comparator counts, DEMCR,
  RESETS after `reset halt`, and the ROM itself (0x0-0x3FFF -> rom.bin).
It also times bulk reads and writes over SWD.
"""

import hashlib
import os
import time

NAME, TITLE, AGENT_BASED = 'identity', 'Identity: chip, ROM and debug parts', False

READS = [('chip_id', 0x40000000), ('platform', 0x40000004), ('gitref_rp2040', 0x40000040),
         ('cpuid', 0xE000ED00), ('mpu_type', 0xE000ED90), ('fp_ctrl', 0xE0002000),
         ('dwt_ctrl', 0xE0001000), ('demcr', 0xE000EDFC), ('dhcsr', 0xE000EDF0),
         ('resets_reset', 0x4000C000), ('resets_done', 0x4000C008), ('vtor', 0xE000ED08),
         ('aircr', 0xE000ED0C), ('ccr', 0xE000ED14), ('syst_calib', 0xE000E01C)]
ROM_TABLE = 0xE00FF000


def dry_cases(ctx):
    return []


def _read(t, addr):
    try:
        return t.rsp.read32(addr)
    except Exception:            # noqa: BLE001 - a DAP error is itself the answer
        return None


def rom_table(t):
    """The CoreSight ROM table's entries: (address, present, component id)."""
    out = []
    for i in range(32):
        e = _read(t, ROM_TABLE + 4 * i)
        if e is None or e == 0:
            break
        off = e & 0xFFFFF000
        if off & 0x80000000:
            off -= 1 << 32
        base = (ROM_TABLE + off) & 0xFFFFFFFF
        cid = [_read(t, base + 0xFF0 + 4 * k) for k in range(4)]
        pid = [_read(t, base + 0xFE0 + 4 * k) for k in range(4)]
        out.append({'entry': f"0x{e:08X}", 'base': f"0x{base:08X}", 'present': bool(e & 1),
                    'cid': [None if v is None else f"0x{v:02X}" for v in cid],
                    'pid': [None if v is None else f"0x{v:02X}" for v in pid],
                    'part': _part(pid)})
    return out


def _part(pid):
    if None in pid:
        return None
    part = (pid[0] & 0xFF) | ((pid[1] & 0xF) << 8)
    return {0x008: 'SCS (M0+)', 0x00A: 'DWT', 0x00B: 'BPU', 0x932: 'MTB-M0+'}.get(part, f"part 0x{part:03X}")


def run(ctx):
    from .. import facts as FX
    t = ctx.t
    t.reset_halt()
    t.vector_catch('none')
    regs = {name: _read(t, a) for name, a in READS}
    fs = []
    for name, a in READS:
        v = regs[name]
        fs.append(FX.fact(f"identity.{name}", None if v is None else f"0x{v:08X}", address=f"0x{a:08X}",
                          **({'note': 'the debugger could not read it'} if v is None else {})))
    cpuid = regs['cpuid']
    fs.append(FX.fact('identity.cpuid.matches_sdk', cpuid == 0x410CC601,
                      prediction={'value': '0x410CC601', 'from': 'Pico SDK (Cortex-M0+ r0p1)'}))
    if regs['chip_id'] is not None:
        fs.append(FX.fact('identity.chip_revision', regs['chip_id'] >> 28, unit='CHIP_ID[31:28]'))
    if regs['mpu_type'] is not None:
        fs.append(FX.fact('identity.mpu_regions', (regs['mpu_type'] >> 8) & 0xFF,
                          prediction={'value': 8, 'from': 'Pico SDK'}))
    if regs['fp_ctrl'] is not None:
        fs.append(FX.fact('identity.breakpoints', (regs['fp_ctrl'] >> 4) & 0xF))
    if regs['dwt_ctrl'] is not None:
        fs.append(FX.fact('identity.watchpoints', regs['dwt_ctrl'] >> 28))
    table = rom_table(t)
    fs.append(FX.fact('identity.has_mtb', any(e['part'] == 'MTB-M0+' and e['present'] for e in table),
                      note='ARMv6-M has no ETM and no DWT cycle counter; an MTB would trace branches'))
    # the boot ROM
    t0 = time.monotonic()
    try:
        rom = t.read_mem_fast(0, 0x4000)
    except Exception as e:                    # noqa: BLE001
        rom = None
        ctx.log(f"the ROM could not be read: {e}")
    rom_info = {}
    if rom:
        magic = rom[0x10:0x13]
        rom_info = {'sha256': hashlib.sha256(rom).hexdigest(), 'magic': magic.hex(), 'version': rom[0x13],
                    'seconds': round(time.monotonic() - t0, 3)}
        path = os.path.join(FX.root(ctx.kind), 'rom.bin' if ctx.label == 'chip1' else f'chips/{ctx.label}/rom.bin')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(rom)
        fs.append(FX.fact('identity.rom.version', rom[0x13], prediction={'value': 3, 'from': 'RP2040-B2 boot ROM'}))
        fs.append(FX.fact('identity.rom.magic', magic.hex(), prediction={'value': '4d7501', 'from': "boot ROM ('Mu', 1)"}))
        fs.append(FX.fact('identity.rom.sha256', rom_info['sha256']))
    # SWD throughput, both ways, through a quiet part of SRAM
    t.write_mem_fast(0x20020000, bytes(range(256)) * 256)
    back = t.read_mem_fast(0x20020000, 0x10000)
    ok = back == bytes(range(256)) * 256
    thr = dict(getattr(t, 'throughput', {}))
    fs.append(FX.fact('identity.swd.read_bytes_per_s', thr.get('read'), unit='B/s', source='measured'))
    fs.append(FX.fact('identity.swd.write_bytes_per_s', thr.get('write'), unit='B/s', source='measured'))
    fs.append(FX.fact('identity.swd.sram_round_trip_ok', ok))
    prov = ctx.provenance(None, campaign='0')
    path = ctx.write_facts('identity.json', 'identity', prov, fs, rom_table=table, rom=rom_info,
                           packet_bytes=t.rsp.packet_max)
    ctx.status(NAME, 'done', facts=path, differences=0 if cpuid == 0x410CC601 else 1)
    return path
