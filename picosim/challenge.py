"""Auto-checked challenges declared in a program's header comment.

    @ Challenge: Make the program print 9, and keep R3 = 1.
    @ Expect: output "9\\n"
    @ Expect: R3 = 1
    @ Hint: The two numbers are on lines 23 and 24.

When the program halts, every Expect line is checked: `output "…"` against
everything the program printed (a Python-style string, so \\n is a new line),
and `Rn = value` (also SP, LR; the value in decimal, 0x hex, a negative
number or a character like 'H') against the register's final value.

Hint lines are the teacher's own hints for Ask picosim, given one at a time,
in order, when a student asks for help with the challenge.
"""

import ast
import re

_LINE = re.compile(r'^\s*@\s*(Challenge|Expect|Hint)\s*:\s*(.*?)\s*$', re.I)
_REG = re.compile(r'^(r1[0-2]|r[0-9]|sp|lr)\s*=\s*(.+)$', re.I)
_NAMES = {'sp': 13, 'lr': 14}


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
    raise ChallengeError(f'an Expect line checks output "…" or a register (R2 = 7): {text}')


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
        if m.group(1).lower() == 'challenge':
            task.append(m.group(2))
        elif m.group(1).lower() == 'hint':
            hints.append(m.group(2))
        else:
            checks.append((m.group(2), _check(m.group(2))))
    if not checks:
        return None
    return {'task': ' '.join(task), 'checks': checks, 'hints': hints}


def _show(s):
    return repr(s).replace("'", '"') if '"' not in s else repr(s)


def _reg_name(n):
    return {13: 'SP', 14: 'LR'}.get(n, f'R{n}')


def run(challenge, regs, output):
    """[{'text', 'ok', 'got'}] for each check, given the final registers and
    everything printed (\\r\\n counts as \\n)."""
    out = output.replace('\r\n', '\n').replace('\r', '\n')
    results = []
    for text, (kind, target, want) in challenge['checks']:
        if kind == 'output':
            ok = out == want
            got = f'it printed {_show(out)}' if out else 'it printed nothing'
        else:
            v = regs[target] & 0xFFFFFFFF
            ok = v == want
            signed = v - (1 << 32) if v & 0x80000000 else v
            got = f'{_reg_name(target)} = {signed}' + (f' (0x{v:X})' if v > 9 else '')
        results.append({'text': text, 'ok': ok, 'got': got})
    return results
