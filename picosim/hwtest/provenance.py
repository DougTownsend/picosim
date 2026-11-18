"""
Where a Testing result came from, stamped on the result when it is made.

Every summary that lockstep.py and bench.py produce gets a 'provenance'
dict at run time: what kind of evidence it is, the target it ran on, when,
the calibration it used, the exact source and image, the picosim build, the
model's assumptions and what was compared.  The CLI, the Markdown and JSON
reports and the Testing window all label results from this dict, never from
whatever happens to be connected later, so a result keeps its own target
after a disconnect or a reconnect to another board.

Evidence kinds:
  standin    the stand-in emulator (standin.py) on this computer.  NOT a
             measurement: its SysTick counts picosim's own Table 81 model.
  physical   a real RP2040, through OpenOCD and a debug probe (or a GDB
             server the person started, which picosim cannot check).
  replay     a recorded hardware capture replayed offline (Level 5's
             `discover check`; listed here so the names agree).
  unknown    a result saved before results carried provenance.

Reports name a source file by its path inside the picosim checkout
(hwtests/b03_branch_timing.s) or, outside it, by its file name alone; its
SHA-256 identifies it exactly.  The full local path is left out unless the
person asks for it (`--local-paths`).
"""

import datetime
import functools
import hashlib
import os
import subprocess
import uuid

from .. import timing
from ..memmap import FLASH_BASE, USER_BASE, SRAM_BASE, STACK_TOP
from . import image as img

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVIDENCE = {
    'standin': "Stand-in: an emulator on this computer, not the real chip",
    'physical': "Physical RP2040: run on a real chip",
    'replay': "Recorded hardware replay: a saved capture from a real chip, replayed with no board",
    'unknown': "Unknown: this result was saved without a record of where it ran",
}
# the short name of the other machine in columns and lines ("picosim 64  stand-in 64")
SIDE = {'standin': 'stand-in', 'physical': 'RP2040', 'replay': 'recorded RP2040', 'unknown': 'target'}


def evidence_of(kind):
    """The evidence kind for a Target.kind."""
    return 'standin' if kind == 'standin' else ('physical' if kind in ('openocd', 'gdb') else 'unknown')


def side(prov):
    """Short label for the machine a result was compared with."""
    return SIDE.get((prov or {}).get('evidence', 'unknown'), 'target')


def now():
    """The time now, with its UTC offset (2026-11-18T09:15:02-05:00)."""
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def source_name(path):
    """A name for a source file that is safe to share: its path inside the
    picosim checkout, else its file name."""
    if not path:
        return ''
    a = os.path.abspath(path)
    try:
        rel = os.path.relpath(a, ROOT)
    except ValueError:                    # another drive (Windows)
        rel = None
    if rel and not rel.startswith(os.pardir) and not os.path.isabs(rel):
        return rel.replace(os.sep, '/')
    return os.path.basename(a)


def file_sha256(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


@functools.lru_cache(maxsize=1)
def picosim_build():
    """picosim's version, and the git commit of this checkout (and whether it
    had uncommitted changes); None where it cannot be told."""
    try:
        from importlib.metadata import version
        ver = version('picosim')
    except Exception:                     # noqa: BLE001 - not installed: say so
        ver = None
    commit = dirty = None
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                                text=True, timeout=5).stdout.strip() or None
        if commit:
            dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'],
                                        cwd=ROOT, capture_output=True, text=True,
                                        timeout=5).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {'version': ver, 'commit': commit, 'dirty': dirty}


def address_map():
    """Where picosim's addresses are on the chip under Testing (image.py)."""
    return {
        'code': {'picosim': [FLASH_BASE, FLASH_BASE + img.WINDOW],
                 'chip': [img.CODE_HW, img.CODE_HW + img.WINDOW],
                 'offset': img.CODE_OFF,
                 'program_start': {'picosim': USER_BASE, 'chip': img.to_hw(USER_BASE)}},
        'same_on_both': {'data': SRAM_BASE, 'stack_top': STACK_TOP},
        'text': address_text(),
    }


def address_text(side_name='chip'):
    return (f"Code moves: picosim's flash 0x{FLASH_BASE:08X}-0x{FLASH_BASE + img.WINDOW - 1:08X} is at "
            f"0x{img.CODE_HW:08X}-0x{img.CODE_HW + img.WINDOW - 1:08X} on the {side_name} "
            f"(+0x{img.CODE_OFF:08X}; the program starts at 0x{USER_BASE:08X} in picosim and "
            f"0x{img.to_hw(USER_BASE):08X} on the {side_name}). Data (from 0x{SRAM_BASE:08X}) and the "
            f"stack (top 0x{STACK_TOP:08X}) are at the same addresses on both.")


def target_info(target):
    """What a Target was, kept with the result."""
    kind = getattr(target, 'kind', '') or ''
    ev = evidence_of(kind)
    try:
        desc = target.description
    except Exception:                     # noqa: BLE001 - a closed connection
        desc = ''
    where = {
        'standin': "the stand-in emulator (Unicorn), on this computer",
        'openocd': "a physical RP2040 over SWD, through OpenOCD and a debug probe",
        'gdb': "a GDB server picosim did not start (taken to be a physical RP2040; picosim cannot check)",
    }.get(kind, "unknown")
    info = {'kind': kind or 'unknown', 'description': desc or 'unknown', 'executed_on': where,
            'chip_id': 'not recorded'}
    if kind == 'openocd':
        info['swd_khz'] = (getattr(target, 'args', None) or (None,) * 4)[3]
    return ev, info


def calibration_info(cal, ev):
    """The calibration a benchmark used, with what it is worth."""
    if not cal:
        return None
    out = dict(cal)
    if ev == 'standin':
        status = ("stand-in: its SysTick counts picosim's own Table 81 model, so the overhead "
                  "equals the prediction by construction (not a measurement)")
    elif ev == 'physical':
        status = ("measured on this connection" + (", steady" if cal.get('steady') else
                                                   ", NOT steady (the readings varied)"))
    else:
        status = "unknown"
    out['status'] = status
    return out


COMPARED = {
    'trace': ("After every instruction: R0-R15, the N Z C V flags, every byte the instruction "
              "wrote, and whether it faulted. When the run ends: the program's code, its data and "
              "the top 16 KiB of SRAM (the stack), what it printed, and the GPIO registers "
              "(GPIO_OUT, GPIO_OE, and each pin's CTRL and pad register the program wrote)."),
    'bench': ("Clock cycles per region: picosim's sum of each instruction's Table 81 cycles, next to "
              "the target's SysTick count for the same region minus the measuring overhead "
              "(the calibration)."),
}


def model_info(kind, ev):
    """picosim's model and its assumptions, as they apply to this result."""
    m = {'assumptions': [f"The program's code runs from SRAM on the target, at 0x{img.CODE_HW:08X} "
                         "(Testing never writes flash, so there is no XIP cache)."]}
    if kind == 'bench':
        m.update(timing=("RP2040 datasheet Table 81 cycles (zero wait states), with APB loads "
                         f"+{timing.APB_LOAD_EXTRA} and stores +{timing.APB_STORE_EXTRA} cycles as "
                         "measured on a real RP2040 (2026-10-29)"),
                 apb_load_extra=timing.APB_LOAD_EXTRA, apb_store_extra=timing.APB_STORE_EXTRA)
        m['assumptions'].append("SVC and BKPT have no fixed time in the model, so a region that "
                                "calls putchar/getchar has no meaningful time on the chip.")
    else:
        m['assumptions'].append("Calls to putchar/getchar are one step: picosim runs its OS, and on "
                                "the target the test runner does the same I/O, so no OS code runs there.")
    if ev == 'standin':
        m['assumptions'].append("The stand-in's peripherals are picosim's own model" +
                                (", and its SysTick counts the same Table 81 model as picosim, so "
                                 "its cycle counts agree with picosim by construction." if kind == 'bench'
                                 else "."))
    return m


def conditions(kind, ev='physical'):
    if kind != 'bench':
        clock = "not used"
    elif ev == 'standin':
        clock = "none: the stand-in counts cycles of its model, it has no clock"
    else:
        clock = ("not recorded (after reset halt the chip runs from its ring oscillator, about "
                 "6 MHz); cycle counts do not depend on the clock speed")
    return {'code_memory': f"SRAM at 0x{img.CODE_HW:08X} (no flash, no XIP cache)",
            'clock': clock,
            'samples': "one run per region" if kind == 'bench' else "one run"}


def stamp(summary, target, source, effective_source=None, calibration=None):
    """Add 'provenance' to a summary at the moment it is made.  source is
    the file the person chose; effective_source the one assembled (with any
    settings filled in)."""
    kind = summary.get('kind', 'trace')
    ev, tinfo = target_info(target)
    eff = effective_source or source
    src = {'name': source_name(source), 'sha256': file_sha256(source)}
    if eff and os.path.abspath(eff) != os.path.abspath(source):
        src['assembled_sha256'] = file_sha256(eff)
        src['settings_changed'] = True
    p = {'evidence': ev, 'evidence_label': EVIDENCE[ev], 'side': SIDE[ev],
         'target': tinfo, 'run_id': f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}",
         'run_at': now(), 'source': src, 'image': summary.get('image'),
         'picosim': dict(picosim_build()),
         'calibration': calibration_info(calibration, ev) if kind == 'bench' else None,
         'compared': COMPARED.get(kind, ''), 'model': model_info(kind, ev),
         'address_map': address_map(), 'conditions': conditions(kind, ev)}
    summary['provenance'] = p
    return p


def unknown():
    """The provenance of a result saved before results carried it."""
    return {'evidence': 'unknown', 'evidence_label': EVIDENCE['unknown'], 'side': SIDE['unknown'],
            'target': {'kind': 'unknown', 'description': 'unknown', 'executed_on': 'unknown'},
            'run_id': 'unknown', 'run_at': 'unknown', 'source': {'name': 'unknown', 'sha256': None},
            'image': None, 'picosim': {'version': None, 'commit': None, 'dirty': None},
            'calibration': None, 'compared': 'unknown', 'model': {}, 'address_map': None,
            'conditions': {}}


# ── timing labels: what a cycle count is and what it covers ────────────────

def timing_labels(prov, stretches=False, calibration=None):
    """{'picosim', 'target', 'scope', 'unit'}: always shown next to cycle counts."""
    ev = (prov or {}).get('evidence', 'unknown')
    cal = calibration or (prov or {}).get('calibration') or {}
    ov = cal.get('overhead') or {}
    ov_text = ' or '.join(str(v) for v in sorted(set(dict(ov).values()))) if ov else '?'
    target = {
        'standin': ("stand-in: the emulator's SysTick, which counts picosim's own Table 81 model "
                    f"(not a measurement), minus {ov_text} cycles of measuring overhead"),
        'physical': (f"RP2040: SysTick counted on the chip at full speed, minus {ov_text} cycles of "
                     "measuring overhead (calibrated on this connection)"),
    }.get(ev, "unknown source")
    scope = ("each stretch: from the start of main or the return from putchar/getchar, up to and "
             "including the next call or main's return" if stretches else
             "each region: from the return from bench_start up to, not including, the bl bench_stop")
    return {'picosim': ("picosim: RP2040 datasheet Table 81 cycles added up per instruction "
                        "(code in SRAM, zero wait states; APB wait cycles as measured)"),
            'target': target, 'scope': scope,
            'unit': "processor clock cycles (not time: no clock speed is involved)"}
