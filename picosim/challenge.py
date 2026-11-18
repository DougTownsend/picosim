"""Auto-checked challenges declared in a program's header comment.

    @ Challenge: Make the program print 9, and keep R3 = 1.
    @ Expect: output "9\\n"
    @ Expect: R3 = 1
    @ Expect: mem32[total] = 150
    @ Hint: Which value does CMP see last, without printing it?

When the program halts, every Expect line is checked: `output "…"` against
everything the program printed (a Python-style string, so \\n is a new line),
`Rn = value` (also SP, LR; the value in decimal, 0x hex, a negative
number or a character like 'H') against the register's final value, and
`mem32[label] = value` against the word stored in SRAM at that label (or
`mem32[label + 4]`, or an address such as `mem32[0x20000010]`).  The word
is read without running anything, so checking never changes the machine.
Hint lines are shown after a try that did not pass.
"""

import ast
import re

_LINE = re.compile(r'^\s*@\s*(Challenge|Expect|Hint)\s*:\s*(.*?)\s*$', re.I)
_REG = re.compile(r'^(r1[0-2]|r[0-9]|sp|lr)\s*=\s*(.+)$', re.I)
_MEM = re.compile(r'^mem32\s*\[\s*([A-Za-z_.$][\w.$]*|0x[0-9a-f]+|\d+)\s*'
                  r'(?:\+\s*(0x[0-9a-f]+|\d+)\s*)?\]\s*=\s*(.+)$', re.I)
_NAMES = {'sp': 13, 'lr': 14}
SRAM = (0x20000000, 0x20042000)       # memmap.SRAM_BASE, SRAM_END


class ChallengeError(ValueError):
    pass


def _value(text):
    text = text.strip()
    if len(text) == 3 and text[0] == text[2] == "'":
        return ord(text[1])
    try:
        return int(text, 0) & 0xFFFFFFFF
    except ValueError:
        raise ChallengeError(f"not a number: {text}") from None


def _check(text):
    """One Expect line as a check: (kind, target, wanted)."""
    if text.lower().startswith('output'):
        rest = text[6:].strip()
        try:
            want = ast.literal_eval(rest)
        except (ValueError, SyntaxError):
            want = None
        if not isinstance(want, str):
            raise ChallengeError(f'write the output in quotes, like output "7\\n": {text}')
        return ('output', None, want)
    m = _REG.match(text)
    if m:
        name = m.group(1).lower()
        n = _NAMES.get(name, None)
        if n is None:
            n = int(name[1:])
        return ('reg', n, _value(m.group(2)))
    if text.lower().startswith('mem'):
        m = _MEM.match(text)
        if not m:
            raise ChallengeError(f'write a memory check like mem32[total] = 150: {text}')
        where = m.group(1)
        if where[0].isdigit():
            where = int(where, 0)
        return ('mem32', (where, int(m.group(2) or '0', 0)), _value(m.group(3)))
    raise ChallengeError(f'an Expect line checks output "…", a register (R2 = 7) '
                         f'or a word in memory (mem32[total] = 150): {text}')


def parse(source):
    """The challenge in a program's source text, or None.  Only the comment
    lines before the first instruction are read."""
    task = []
    checks = []
    hints = []
    for line in source.splitlines():
        s = line.strip()
        if s and not s.startswith('@'):
            break
        m = _LINE.match(line)
        if not m:
            continue
        kind = m.group(1).lower()
        if kind == 'challenge':
            task.append(m.group(2))
        elif kind == 'hint':
            hints.append(m.group(2))
        else:
            checks.append((m.group(2), _check(m.group(2))))
    if not checks:
        return None
    return {'task': ' '.join(task), 'checks': checks, 'hint': ' '.join(hints)}


def labels(challenge):
    """The labels the challenge's memory checks name."""
    return [k[1][0] for _, k in challenge['checks'] if k[0] == 'mem32' and isinstance(k[1][0], str)]


def _show(s):
    return repr(s).replace("'", '"') if '"' not in s else repr(s)


def _reg_name(n):
    return {13: 'SP', 14: 'LR'}.get(n, f'R{n}')


def _number(v):
    signed = v - (1 << 32) if v & 0x80000000 else v
    return f'{signed}' + (f' (0x{v:X})' if v > 9 else '')


def _mem_check(target, symbols, peek):
    """(ok-to-compare, value or the reason it cannot be read) for a
    mem32[...] check.  `symbols` maps label names to addresses; `peek(addr)`
    reads an SRAM word without side effects."""
    where, plus = target
    if isinstance(where, str):
        base = (symbols or {}).get(where)
        if base is None:
            near = [n for n in (symbols or {}) if n.lower() == where.lower()]
            return False, (f'your program has no label called {where}' +
                           (f' (there is {near[0]}: capitals matter)' if near else
                            ': check its spelling, and that its line ends with a colon'))
        name = where + (f' + {plus}' if plus else '')
    else:
        base, name = where, None
    addr = (base + plus) & 0xFFFFFFFF
    at = f'{name} (0x{addr:08X})' if name else f'0x{addr:08X}'
    if addr % 4:
        return False, f'{at} is not a multiple of 4, so it is not the start of a word'
    if not SRAM[0] <= addr <= SRAM[1] - 4:
        return False, f'{at} is not in SRAM, where a program stores its data (put the label after .data)'
    if peek is None:
        return False, 'memory could not be read'
    return True, (at, peek(addr) & 0xFFFFFFFF)


def run(challenge, regs, output, symbols=None, peek=None):
    """[{'text', 'ok', 'got'}] for each check, given the final registers,
    everything printed (\\r\\n counts as \\n) and, for mem32 checks, the
    program's labels {name: address} and `peek(addr)`, which reads the SRAM
    word at addr without changing anything."""
    out = output.replace('\r\n', '\n').replace('\r', '\n')
    results = []
    for text, (kind, target, want) in challenge['checks']:
        if kind == 'output':
            ok = out == want
            got = f'it printed {_show(out)}' if out else 'it printed nothing'
        elif kind == 'mem32':
            readable, v = _mem_check(target, symbols, peek)
            if readable:
                at, v = v
                ok = v == want
                got = f'{at} holds {_number(v)}'
            else:
                ok, got = False, v
        else:
            v = regs[target] & 0xFFFFFFFF
            ok = v == want
            got = f'{_reg_name(target)} = {_number(v)}'
        results.append({'text': text, 'ok': ok, 'got': got})
    return results
