"""
The RP2040's registers as the Pico SDK describes them: RP2040.svd plus the
headers' per-field _ACCESS tags.  This is the *prediction* campaign C checks
every register against; it is never the truth.

    registers()     [{'periph', 'name', 'addr', 'reset', 'fields': [...],
                      'access', 'read_action', 'rf', ...}]  (second instances
                    of I2C/PIO/PLL/SPI/UART included)
    peripherals()   {name: {'base', 'size', 'registers': [...]}}
    PPB_REGISTERS   the core's own registers (SCB, NVIC, SysTick, MPU), which
                    the SVD leaves out, with Arm's reset values
"""

import glob
import os
import re
import xml.etree.ElementTree as ET
from functools import lru_cache


def sdk_dir():
    for d in (os.environ.get('PICO_SDK_PATH'), os.path.expanduser('~/pico-sdk'),
              os.path.expanduser('~/.pico-sdk/sdk/2.1.1'), os.path.expanduser('~/.pico-sdk/sdk/2.2.0')):
        if d and os.path.exists(os.path.join(d, 'src', 'rp2040', 'hardware_regs', 'RP2040.svd')):
            return d
    return None


def _int(text, default=0):
    if text is None:
        return default
    text = text.strip().lower()
    return int(text, 16) if text.startswith('0x') else int(text)


@lru_cache(maxsize=1)
def _header_access():
    """{(PERIPH, REG, FIELD): 'RO'|'RW'|'RF'|...} from the SDK headers."""
    out = {}
    d = sdk_dir()
    if not d:
        return out
    for path in glob.glob(os.path.join(d, 'src', 'rp2040', 'hardware_regs', 'include', 'hardware', 'regs', '*.h')):
        with open(path) as f:
            for m in re.finditer(r'#define (\w+)_ACCESS "(\w+)"', f.read()):
                out[m.group(1)] = m.group(2)
    return out


@lru_cache(maxsize=1)
def peripherals():
    d = sdk_dir()
    if not d:
        raise FileNotFoundError("the Pico SDK (with RP2040.svd) was not found; set PICO_SDK_PATH")
    root = ET.parse(os.path.join(d, 'src', 'rp2040', 'hardware_regs', 'RP2040.svd')).getroot()
    raw = {}
    for p in root.iter('peripheral'):
        raw[p.findtext('name')] = p
    acc = _header_access()
    out = {}
    for name, p in raw.items():
        src = raw.get(p.get('derivedFrom')) if p.get('derivedFrom') else p
        base = _int(p.findtext('baseAddress'))
        if name == 'VIRTUAL':
            continue                     # not on the bus: the SVD's placeholder
        blk = src.find('addressBlock')
        size = _int(blk.findtext('size')) if blk is not None else 0x1000
        regs = []
        sname = src.findtext('name')
        prefixes = [sname, name, sname.rstrip('0123456789'), name.rstrip('0123456789')]
        for r in src.iter('register'):
            rname = r.findtext('name')
            dim = r.findtext('dim')
            offs = [_int(r.findtext('addressOffset'))]
            names = [rname]
            if dim:
                inc = _int(r.findtext('dimIncrement'))
                offs = [offs[0] + i * inc for i in range(_int(dim))]
                names = [rname.replace('%s', str(i)) for i in range(_int(dim))]
            fields = []
            for fl in r.iter('field'):
                br = fl.findtext('bitRange')
                if br:
                    hi, lo = (int(x) for x in br.strip('[]').split(':'))
                else:
                    lo, hi = _int(fl.findtext('bitOffset')), _int(fl.findtext('bitOffset')) + _int(fl.findtext('bitWidth')) - 1
                fname = fl.findtext('name')
                tag = next((acc[k] for k in (f"{P}_{rname}_{fname}".upper() for P in prefixes) if k in acc), None)
                fields.append({'name': fname, 'lo': lo, 'hi': hi, 'access': fl.findtext('access'),
                               'read_action': fl.findtext('readAction'),
                               'modified_write': fl.findtext('modifiedWriteValues'), 'tag': tag})
            for off, nm in zip(offs, names):
                ra = r.findtext('readAction') or next((f['read_action'] for f in fields if f['read_action']), None)
                regs.append({'periph': name, 'name': nm, 'offset': off, 'addr': base + off,
                             'reset': _int(r.findtext('resetValue')), 'size': _int(r.findtext('size'), 32),
                             'access': r.findtext('access') or _reg_access(fields),
                             'read_action': ra,
                             'rf': any(f['tag'] in ('RF', 'RWF') for f in fields)
                             or any(acc.get(f"{P}_{rname}".upper()) in ('RF', 'RWF') for P in prefixes),
                             'fields': fields})
        out[name] = {'base': base, 'size': size, 'registers': regs}
    return out


def _reg_access(fields):
    kinds = {f['access'] for f in fields}
    if kinds == {'read-only'}:
        return 'read-only'
    if kinds <= {'write-only'}:
        return 'write-only'
    return 'read-write'


def registers():
    return [r for p in peripherals().values() for r in p['registers']]


def predicted_mask(reg):
    """The writable bits the SVD predicts: fields not read-only."""
    m = 0
    for f in reg['fields']:
        if f['access'] in ('read-write', 'write-only', 'writeOnce', 'read-writeOnce'):
            m |= ((1 << (f['hi'] - f['lo'] + 1)) - 1) << f['lo']
    return m


NOT_STORED_TAGS = ('SC', 'WC', 'RF', 'RWF', 'WF')
# PPB registers whose writes set, clear or reset rather than store (ARMv6-M ARM B3.4, B3.3)
PPB_NOT_STORED = {0xE000E100, 0xE000E180, 0xE000E200, 0xE000E280,   # NVIC_ISER/ICER/ISPR/ICPR: write 1 to set/clear
                  0xE000E018}                                         # SYST_CVR: any write clears it


def not_stored_mask(reg):
    """Documented bits a write does not simply store, so reading back says
    nothing about whether they can be written: write-only fields, write-1-
    to-clear and self-clearing ones (the SVD's oneToClear, the headers' SC,
    WC, RF, RWF, WF), and the PPB's set/clear registers."""
    if reg['addr'] in PPB_NOT_STORED:
        return 0xFFFFFFFF
    m = 0
    for f in reg['fields']:
        if f['access'] == 'write-only' or f['modified_write'] or f['tag'] in NOT_STORED_TAGS:
            m |= ((1 << (f['hi'] - f['lo'] + 1)) - 1) << f['lo']
    if reg['access'] == 'write-only' and not reg['fields']:
        m = 0xFFFFFFFF
    return m


def defined_mask(reg):
    m = 0
    for f in reg['fields']:
        m |= ((1 << (f['hi'] - f['lo'] + 1)) - 1) << f['lo']
    return m


# Arm's documented resets (ARMv6-M ARM, Cortex-M0+ TRM) and the SDK's m0plus.h
PPB_REGISTERS = [
    ('SYST_CSR', 0xE000E010, 0x00000000), ('SYST_RVR', 0xE000E014, None), ('SYST_CVR', 0xE000E018, None),
    ('SYST_CALIB', 0xE000E01C, None),
    ('NVIC_ISER', 0xE000E100, 0), ('NVIC_ICER', 0xE000E180, 0), ('NVIC_ISPR', 0xE000E200, 0),
    ('NVIC_ICPR', 0xE000E280, 0)] + [(f'NVIC_IPR{i}', 0xE000E400 + 4 * i, 0) for i in range(8)] + [
    ('CPUID', 0xE000ED00, 0x410CC601), ('ICSR', 0xE000ED04, 0), ('VTOR', 0xE000ED08, 0),
    ('AIRCR', 0xE000ED0C, 0xFA050000), ('SCR', 0xE000ED10, 0), ('CCR', 0xE000ED14, 0x00000204),
    ('SHPR2', 0xE000ED1C, 0), ('SHPR3', 0xE000ED20, 0), ('SHCSR', 0xE000ED24, 0),
    ('MPU_TYPE', 0xE000ED90, 0x00000800), ('MPU_CTRL', 0xE000ED94, 0), ('MPU_RNR', 0xE000ED98, 0),
    ('MPU_RBAR', 0xE000ED9C, None), ('MPU_RASR', 0xE000EDA0, None),
]
PPB_NO_WRITE = {0xE000ED00, 0xE000ED0C, 0xE000EDF0, 0xE000EDF4, 0xE000EDF8, 0xE000EDFC}   # CPUID, AIRCR, debug
