"""
Campaign F: what a program flashed with picosim's Flash button really runs in.

This is the one campaign that writes the target's flash, so it runs inside
flashguard's guarantee (proposal §7.1):

  1. a full backup of the flash, made twice by independent paths (OpenOCD
     over SWD, picotool over USB in BOOTSEL) and compared by SHA-256 —
     done once per board, and asked for before anything else;
  2. the probe firmware is built exactly as Flash builds a program
     (wrapper_main.c: USB stdio, core 1 running report_pins), with a main
     that stops at once on BKPT #0x42, and written with OpenOCD;
  3. measurements, with the firmware halted at its BKPT (S2 state);
  4. ALWAYS, even after an error: the backup written back, a full read-back
     whose SHA-256 must equal the backup's, and a boot check.  F is not
     finished until that verification has passed.

Measurements run as short snippets of code in SRAM the firmware does not use
(above its .bss), with the firmware's own vector table and interrupts left
alone, each ending on a BKPT:

  * clocks: clk_sys, ref, peri, usb, adc, rtc with the chip's frequency
    counter (CLOCKS FC0: the measuring-only exception to the CLOCKS ban);
  * the SSI/XIP setup, enabled NVIC interrupts and priorities, SysTick, VTOR;
  * core 1: what OpenOCD can say about it;
  * XIP cache: miss and hit costs, line size, set conflicts (2-way, 8 KB
    apart), the NOALLOC and NOCACHE aliases, FLUSH, the hit/access counters,
    and a loop run from flash, first pass against later ones;
  * interference: one SRAM loop timed 256 times (USB and core 1 at work);
  * real time: clk_sys cycles per TIMER microsecond;
  * campaign G's clock-dependent part (TIMER, UART loopback), if present;
  * the two old checks: 01_hello prints after Flash; a program that skips
    FUNCSEL leaves GP21 undriven (its pad's OUTTOPAD/OETOPAD stay 0).

On the stand-in there is no flash: the guard, the build and the writes are
skipped and the snippets run in the reset state (most measurements then
record errors), which checks the code paths.  Output: environment.json.
"""

import os
import shutil
import struct
import time

NAME, TITLE, AGENT_BASED = 'flashed', 'Flashed programs: clocks, XIP cache, interference (writes flash)', False
FC0 = {'REF_KHZ': 0x40008080, 'MIN_KHZ': 0x40008084, 'MAX_KHZ': 0x40008088, 'DELAY': 0x4000808C,
       'INTERVAL': 0x40008090, 'SRC': 0x40008094, 'STATUS': 0x40008098, 'RESULT': 0x4000809C}
FC_SOURCES = {'clk_ref': 0x08, 'clk_sys': 0x09, 'clk_peri': 0x0A, 'clk_usb': 0x0B, 'clk_adc': 0x0C,
              'clk_rtc': 0x0D, 'xosc': 0x05, 'rosc': 0x03, 'pll_sys': 0x01, 'pll_usb': 0x02}
XIP_CTRL, XIP_FLUSH, XIP_STAT, XIP_CTR_HIT, XIP_CTR_ACC = 0x14000000, 0x14000004, 0x14000008, 0x1400000C, 0x14000010
SYST_CSR, SYST_RVR, SYST_CVR = 0xE000E010, 0xE000E014, 0xE000E018
TIMELR, TIMERAWL = 0x4005400C, 0x40054028
SNIPPET_AT = 0x2003C000                # code for the snippets (checked to be above the firmware's RAM)
DATA_AT = 0x2003E000
SSI_READ = [('CTRLR0', 0x18000000), ('CTRLR1', 0x18000004), ('SSIENR', 0x18000008), ('BAUDR', 0x18000014),
            ('SR', 0x18000028), ('SPI_CTRLR0', 0x180000F4), ('RX_SAMPLE_DLY', 0x180000F0)]


def dry_cases(ctx):
    return []


def h(*hws):
    return b''.join(x.to_bytes(2, 'little') for x in hws)


class Snippets:
    """Code run on core 0 in the halted firmware: write it, set registers,
    run to its BKPT, read registers back."""

    def __init__(self, t, log):
        self.t, self.log = t, log

    def run(self, code, regs=None, timeout=10.0):
        from .. import safety
        r = self.t.rsp
        safety.check_write(SNIPPET_AT, 4, 0, why='snippet')
        r.write_mem(SNIPPET_AT, code + h(0xBE00))
        for n, v in (regs or {}).items():
            r.write_reg(n, v)
        r.write_reg(self.t.xpsr_n, 0x01000000)
        r.write_reg(15, SNIPPET_AT)
        how = self.t.run(timeout)
        if how != 'halted':
            raise RuntimeError(f"a snippet did not finish ({how})")
        regs = r.read_core_regs()
        if regs[15] != SNIPPET_AT + len(code):
            raise RuntimeError(f"a snippet stopped at 0x{regs[15]:08X}, not at its end (a fault?)")
        return regs

    def timed_loads(self, addr, n=16, width=4):
        """Cycles of n loads from addr, with SysTick: (raw, per load)."""
        ld = {4: 0x6808, 2: 0x8808, 1: 0x7808}[width]     # ldr r0,[r1]
        code = h(0x683E, *([ld] * n), 0x683D)               # ldr r6,[r7] ... ldr r5,[r7]
        regs = self.run(code, {1: addr, 7: SYST_CVR})
        return (regs[6] - regs[5]) % (1 << 24)


def _read(t, a):
    try:
        return t.rsp.read32(a)
    except Exception:                       # noqa: BLE001
        return None


def _w(t, a, v):
    from .. import safety
    safety.check_write(a, 4, v, why='campaign F')
    t.rsp.write32(a, v)


def measure_clocks(t):
    out = {}
    w = lambda a, v: _w(t, a, v)                 # noqa: E731
    for name, src in FC_SOURCES.items():
        try:
            w(FC0['REF_KHZ'], 12000)        # clk_ref runs from the 12 MHz crystal after the SDK's setup
            w(FC0['INTERVAL'], 10)
            w(FC0['MIN_KHZ'], 0)
            w(FC0['MAX_KHZ'], 0xFFFFFFFF)
            w(FC0['SRC'], src)
            for _ in range(100):
                if _read(t, FC0['STATUS']) & 0x10:
                    break
            res = _read(t, FC0['RESULT'])
            out[name] = None if res is None else round((res >> 5) + (res & 0x1F) / 32, 3)
        except Exception as e:              # noqa: BLE001
            out[name] = f"error: {e}"
    return out


def xip_cache(sn, t, log):
    """Costs and geometry of the XIP cache, from timed loads and its counters."""
    from .. import safety
    out = {}
    base = 0x10010000                       # inside the firmware image's area: real flash contents
    safety.check_write(XIP_FLUSH, 4, 1)
    try:
        _w(t, XIP_FLUSH, 1)
        _read(t, XIP_STAT)
        out['miss_16_loads_same_line'] = sn.timed_loads(base, 16)
        out['hit_16_loads'] = sn.timed_loads(base, 16)
        strides = {}
        for stride in (4, 8, 16, 32):
            _w(t, XIP_FLUSH, 1)
            # one load at base, one at base+stride: is the second a hit?
            code = h(0x683E, 0x6808, 0x6848 if stride == 4 else 0x6808, 0x683D)
            regs = sn.run(code, {1: base, 7: SYST_CVR}) if stride == 4 else None
            if stride != 4:
                c2 = h(0x683E, 0x6808, 0x6810, 0x683D)     # ldr r0,[r1]; ldr r0,[r2]
                regs = sn.run(c2, {1: base, 2: base + stride, 7: SYST_CVR})
            strides[stride] = (regs[6] - regs[5]) % (1 << 24)
        out['two_loads_by_stride'] = strides
        _w(t, XIP_CTR_HIT, 0)
        _w(t, XIP_CTR_ACC, 0)
        _w(t, XIP_FLUSH, 1)
        # three lines 8 KB apart (one set): a 2-way cache keeps two of them
        c3 = h(0x683E, 0x6808, 0x6810, 0x6818, 0x6808, 0x683D)
        regs = sn.run(c3, {1: base, 2: base + 0x2000, 3: base + 0x4000, 7: SYST_CVR})
        out['set_conflict_4_loads'] = (regs[6] - regs[5]) % (1 << 24)
        out['counters_after_conflict'] = {'hit': _read(t, XIP_CTR_HIT), 'acc': _read(t, XIP_CTR_ACC)}
        out['noalloc_16'] = sn.timed_loads(0x11010000, 16)
        out['nocache_16'] = sn.timed_loads(0x12010000, 16)
        out['nocache_noalloc_16'] = sn.timed_loads(0x13010000, 16)
    except Exception as e:                  # noqa: BLE001
        out['error'] = str(e)
        log(f"XIP cache measurements stopped: {e}")
    return out


def flash_loop(sn, t, addr, log):
    """flash_loop (code in flash) from a cold cache and again, timed."""
    out = {}
    if addr is None:
        return {'error': 'flash_loop not found in the firmware'}
    try:
        _w(t, XIP_FLUSH, 1)
        code = h(0x683E, 0x4790, 0x683D)                  # ldr r6,[r7]; blx r2; ldr r5,[r7]
        for k in ('cold', 'warm'):
            regs = sn.run(code, {0: 8, 2: addr | 1, 7: SYST_CVR})
            out[k] = (regs[6] - regs[5]) % (1 << 24)
        out['iterations'] = 8
        out['per_iteration_nops'] = 16
    except Exception as e:                  # noqa: BLE001
        out['error'] = str(e)
    return out


def interference(sn, t, n=256):
    """One SRAM loop (64 iterations of subs/bne) timed n times in a row; the
    spread shows interrupts (USB) landing in it."""
    # r4 = table, r3 = count; per run: ldr r6,[r7]; movs r0,#64; 1: subs r0,#1; bne 1b; ldr r5,[r7];
    # subs r6,r6,r5; str r6,[r4]; adds r4,#4; subs r3,#1; bne top
    code = h(0x683E, 0x2040, 0x3801, 0xD1FD, 0x683D, 0x1B76, 0x6026, 0x3404, 0x3B01, 0xD1F5)
    try:
        sn.run(code, {3: n, 4: DATA_AT, 7: SYST_CVR}, timeout=20.0)
        data = t.rsp.read_mem(DATA_AT, 4 * n)
        vals = [v & 0xFFFFFF for (v,) in struct.iter_unpack('<I', data)]
        hist = {}
        for v in vals:
            hist[v] = hist.get(v, 0) + 1
        return {'runs': n, 'histogram': {str(k): v for k, v in sorted(hist.items())},
                'min': min(vals), 'max': max(vals)}
    except Exception as e:                  # noqa: BLE001
        return {'error': str(e)}


def real_time(sn, t):
    """clk_sys cycles per TIMER microsecond (TIMERAWL before and after a timed loop)."""
    try:
        # ldr r2,[r1] (TIMERAWL); ldr r6,[r7]; r0 = 4096; 1: subs r0,#1; bne 1b; ldr r5,[r7]; ldr r3,[r1]
        code = h(0x680A, 0x683E, 0x2080, 0x0140, 0x3801, 0xD1FD, 0x683D, 0x680B)
        regs = sn.run(code, {1: TIMERAWL, 7: SYST_CVR})
        us = (regs[3] - regs[2]) & 0xFFFFFFFF
        cyc = (regs[6] - regs[5]) % (1 << 24)
        return {'us': us, 'cycles': cyc, 'cycles_per_us': round(cyc / us, 3) if us else None}
    except Exception as e:                  # noqa: BLE001
        return {'error': str(e)}


def build_firmware(log, source=None, name='probe'):
    """Picosim's real Flash build of `source` (default: fw/probe_main.s).
    Returns the firmware ELF's path (a copy, so later builds keep it)."""
    from ... import uf2
    from ..build import cache_dir
    src = source or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fw', 'probe_main.s')
    work = cache_dir('fw')
    s = os.path.join(work, f"{name}.s")
    shutil.copy2(src, s)
    if not uf2.build_uf2(s):
        raise RuntimeError("building the probe firmware failed (see the output above)")
    elf = os.path.join(uf2._cache_dir(), 'build', 'firmware.elf')
    out = os.path.join(work, f"{name}.elf")
    shutil.copy2(elf, out)
    return out


def _symbols(elf):
    from elftools.elf.elffile import ELFFile
    out = {}
    with open(elf, 'rb') as f:
        for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols():
            out[s.name] = s['st_value']
    return out


def wait_for_bkpt(t, timeout=20.0):
    """After `reset run`: wait until the core halts on the firmware's BKPT."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = t.dhcsr()
        if st['halted']:
            return True
        time.sleep(0.25)
    return False


def flash_program(t, elf, log):
    said = t.rsp.monitor(f"program {{{elf}}} verify", timeout=300.0)
    log(' '.join(said.split())[:200])
    if 'Verified OK' not in said and 'verified' not in said.lower():
        raise RuntimeError(f"OpenOCD did not confirm the write: {said.strip()[:300]}")
    t.rsp.monitor('reset run', timeout=15.0)


def old_checks(ctx, t, guard_ok):
    """01_hello prints after Flash; skipping FUNCSEL leaves the pin undriven."""
    out = {}
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    try:
        elf = build_firmware(ctx.log, os.path.join(root, 'examples', '01_hello.s'), 'hello')
        from .. import flashguard
        before = flashguard._serial_ports()
        flash_program(t, elf, ctx.log)
        port, text = None, ''
        end = time.monotonic() + 10
        while time.monotonic() < end and port is None:
            new = sorted(set(flashguard._serial_ports()) - set(before))
            port = new[0] if new else None
            time.sleep(0.25)
        if port:
            import serial                    # pyserial, if installed
            with serial.Serial(port, 115200, timeout=6) as s:
                text = s.read(256).decode('latin-1')
        out['01_hello'] = {'port': port, 'printed': text[:120], 'ok': bool(text.strip())}
    except Exception as e:                   # noqa: BLE001
        out['01_hello'] = {'error': str(e)}
    if 21 in ctx.pins:
        src = os.path.join(ctx.root, 'nofuncsel.s')
        with open(src, 'w') as f:
            f.write("        .syntax unified\n        .cpu cortex-m0plus\n        .thumb\n        .text\n"
                    "main:   ldr r0, =0xD0000024\n        ldr r1, =(1 << 21)\n        str r1, [r0]\n"
                    "        ldr r0, =0xD0000014\n        str r1, [r0]\n        bkpt #0x43\n        b main\n")
        try:
            elf = build_firmware(ctx.log, src, 'nofuncsel')
            flash_program(t, elf, ctx.log)
            wait_for_bkpt(t)
            st = _read(t, 0x40014000 + 8 * 21)          # GPIO21_STATUS
            out['funcsel_skipped'] = {'status': None if st is None else f"0x{st:08X}",
                                      'pad_driven': None if st is None else bool(st >> 13 & 1),
                                      'out_to_pad': None if st is None else bool(st >> 9 & 1)}
        except Exception as e:               # noqa: BLE001
            out['funcsel_skipped'] = {'error': str(e)}
    else:
        out['funcsel_skipped'] = {'not run': 'needs --pins 21 (the program sets GP21 as an SIO output)'}
    return out


def run(ctx):
    from .. import facts as FX
    from .. import flashguard
    t = ctx.t
    standin = ctx.kind == 'standin'
    env = {}
    guard = None
    restored = None
    try:
        if not standin:
            guard = flashguard.FlashGuard.from_target(t, ask=ctx.ask, log=ctx.log)
            st = flashguard.load_state()
            if not st:
                if not getattr(ctx.opts, 'yes', False) and ctx.ask:
                    ctx.ask("Campaign F writes the target Pico's flash. First the whole flash is backed up "
                            "twice (SWD and USB/BOOTSEL) and compared; at the end it is written back and "
                            "read back to prove it is identical.")
                guard.backup()
            ctx.status(NAME, 'partial', step='build')
            elf = build_firmware(ctx.log)
            syms = _symbols(elf)
            end_bss = syms.get('__bss_end__') or syms.get('end') or 0
            if end_bss >= SNIPPET_AT:
                raise RuntimeError(f"the firmware's RAM reaches 0x{end_bss:08X}: no room for the snippets")
            guard.mark_written()
            ctx.status(NAME, 'partial', step='write and boot')
            flash_program(t, elf, ctx.log)
            if not wait_for_bkpt(t):
                raise RuntimeError("the probe firmware did not reach its BKPT within 20 s")
        else:
            t.reset_halt()
            syms = {}
        sn = Snippets(t, ctx.log)
        ctx.status(NAME, 'partial', step='measure')
        env['pc_at_bkpt'] = f"0x{t.rsp.read_core_regs()[15]:08X}"
        env['clocks_khz'] = measure_clocks(t)
        env['ssi'] = {n: _hx(_read(t, a)) for n, a in SSI_READ}
        env['xip_ctrl'] = {'CTRL': _hx(_read(t, XIP_CTRL)), 'STAT': _hx(_read(t, XIP_STAT))}
        env['nvic'] = {'ISER': _hx(_read(t, 0xE000E100)),
                       'IPR': [_hx(_read(t, 0xE000E400 + 4 * i)) for i in range(8)]}
        env['systick'] = {'CSR': _hx(_read(t, SYST_CSR)), 'RVR': _hx(_read(t, SYST_RVR))}
        env['vtor'] = _hx(_read(t, 0xE000ED08))
        env['resets'] = {'RESET': _hx(_read(t, 0x4000C000)), 'DONE': _hx(_read(t, 0x4000C008))}
        try:
            env['core1'] = t.rsp.monitor('targets').strip()[:500]
        except Exception as e:                 # noqa: BLE001
            env['core1'] = f"not available: {e}"
        old_csr = _read(t, SYST_CSR)
        _w(t, SYST_RVR, 0xFFFFFF)
        _w(t, SYST_CSR, 5)
        env['xip_cache'] = xip_cache(sn, t, ctx.log)
        env['flash_loop'] = flash_loop(sn, t, syms.get('flash_loop'), ctx.log)
        env['interference'] = interference(sn, t)
        env['real_time'] = real_time(sn, t)
        if old_csr is not None:
            _w(t, SYST_CSR, old_csr)
        try:
            from . import peripherals
            if hasattr(peripherals, 'flashed_part'):
                ck = {k: v for k, v in (env.get('clocks_khz') or {}).items()
                      if k in ('clk_sys', 'clk_peri') and isinstance(v, (int, float)) and v > 0}
                env['peripherals'] = peripherals.flashed_part(ctx, t, ck if len(ck) == 2 else None)
        except Exception as e:                 # noqa: BLE001
            env['peripherals'] = {'error': str(e)}
        if not standin:
            env['old_checks'] = old_checks(ctx, t, True)
    finally:
        if guard is not None and flashguard.load_state() and flashguard.load_state().get('state') == 'written':
            ctx.status(NAME, 'partial', step='restore')
            res = guard.restore()
            restored = {'backup_sha256': res.backup_sha256, 'readback_sha256': res.readback_sha256,
                        'identical': res.backup_sha256 == res.readback_sha256, 'boot_ok': res.boot_ok,
                        'boot_detail': res.boot_detail}
            ctx.log(f"flash restored: backup {res.backup_sha256}\n                read-back {res.readback_sha256}")
    env['restore'] = restored if not standin else {'not needed': 'the stand-in has no flash'}
    fs = []
    for name, khz in env['clocks_khz'].items():
        if isinstance(khz, (int, float)):
            fs.append(FX.fact(f"environment.clock.{name}", khz, unit='kHz',
                              **({'prediction': {'value': 125000, 'from': 'Pico SDK default clk_sys'}}
                                 if name == 'clk_sys' else {})))
    rt = env.get('real_time', {})
    if rt.get('cycles_per_us'):
        fs.append(FX.fact('environment.cycles_per_us', rt['cycles_per_us'],
                          prediction={'value': 125, 'from': 'timing.PICO_CLOCK_MHZ'}))
    if restored:
        fs.append(FX.fact('environment.flash_restored_identical', restored['identical']))
    prov = ctx.provenance(None, campaign='F')
    path = ctx.write_facts('environment.json', 'environment', prov, fs, environment=env)
    done = standin or (restored and restored['identical'])
    ctx.status(NAME, 'done' if done else 'partial', facts=path, differences=None,
               **({} if done else {'step': 'restore not verified'}))
    return path


def _hx(v):
    return None if v is None else f"0x{v:08X}"
