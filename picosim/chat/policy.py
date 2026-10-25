"""
The help policy: hints only, never a finished solution.

There is no setting that turns this off.  A student who needs more help
than three hints asks the teacher, or looks the topic up online, and every
hint says so.

solution_risk() scores how much a question asks for the answer itself:
code-writing verbs with an object ("write the code", "what line do I
add"), "just tell me", or words from the active challenge's task and its
Expect values.  A high score switches the reply to a hint.

Hints for a challenge come up a ladder, one step per request:
  1. the concept, with the guide page that teaches it
  2. where to look: the failed check (wanted vs got) and the line
  3. the shape of the change, in words
then "that's all my hints".  A teacher's own `@ Hint:` lines in the
challenge come first, in order.  Code is never shown, except lines the
guide itself already shows word for word.
"""

import re

from ..explain import OS_TOP
from .catalog import NO_HINTS_LEFT
from .reply import Reply
from .retrieve import quick, terms

_VERB_OBJ = re.compile(
    r"\b(write|give|show|tell|send|make|do|finish|solve|complete|code|type|paste|fix|generate|create|need)( me| us)? "
    r"(the |my |a |an |this |that |some |your )?(whole |full |complete |entire |working |correct |right |final |finished )?"
    r"(code|program|answer|answers|solution|solutions|lines? of code|instructions for|assembly|homework|assignment|lab)\b")
_FOR_ME = re.compile(r"\b(do|solve|finish|write|complete|code|make) (it|this|the challenge|my homework|my lab|the lab|"
                     r"the program|my program|this challenge) for me\b")
_WHAT_LINE = re.compile(r"\bwhat (line|lines|code|instruction|instructions|number|numbers|value|values|constant)s? "
                        r"(do|should|must|would|to|shall) (i )?(add|write|change|put|use|type|enter)\b|"
                        r"\bwhat (do|should|must) i (change|write|add|put)\b|\bwhich (line|number|numbers) (do|should) i change\b")
_JUST = re.compile(r"\bjust (tell|give|show) me\b|\b(give|tell|show|send|need|want|get|post|paste)( me| us)? "
                   r"(the |a |your |full |complete )?(answer|answers|solution|solutions)\b|\banswer key\b|\bcheat\b|"
                   r"\b(whats|what is|what are) the (answer|answers|solution|code)\b|"
                   r"\b(full|complete|whole|entire|final|finished|the) solution\b|"
                   r"\b(full|complete|whole|entire|final|finished) (answer|code|program)\b|\bjust do (it|what it says)\b|"
                   r"\bcopy (the|your) code\b|\bexample (that|which) (prints|does|shows|makes|counts|adds)\b")
_HOWTO = re.compile(r"\b(how (do|can|should|would) i|what (do|should) i do to|tell me how to|show me how to|how to)\b")

EXAMPLE_FOR = [
    (r"\bprint|letter|name|hello|word\b", "Say hello"),
    (r"\badd|sum|subtract|plus|minus\b", "Add two numbers"),
    (r"\bloop|count|repeat|times\b", "Count with a loop"),
    (r"\bmemory|list|array|load|store|ldr|str|word\b", "Add up a list in memory"),
    (r"\bled|blink|light|output pin\b", "Blink an LED"),
    (r"\binput|type|key|echo|getchar|read\b", "Echo what you type"),
    (r"\bfunction|call|bl\b|return|subroutine", "Call a function"),
    (r"\bbutton|switch|input pin\b", "Buttons and LEDs"),
]


def solution_risk(q, challenge=None):
    t = q.text
    score = 0.0
    if _VERB_OBJ.search(t) or _FOR_ME.search(t):
        score += 0.6
    if _WHAT_LINE.search(t) and not re.search(r"\b(console|key|keyboard|getchar)\b", t):
        score += 0.6
    if _JUST.search(t):
        score += 0.6
    if challenge:
        overlap = set(terms(challenge.get('task', ''))) & set(q.terms)
        overlap -= {'program', 'make', 'change', 'number', 'numbers'}
        literals = [w for text, (kind, _, want) in challenge.get('checks', [])
                    for w in ([want.strip()] if kind == 'output' and want.strip() else [str(want)])]
        lit = any(len(l) >= 1 and re.search(r'(?<![\w])' + re.escape(l.lower()) + r'(?![\w])', q.raw.lower())
                  for l in literals if l and not (l.isdigit() and len(l) == 1 and not re.search(r'\bprints? ' + l, q.text)))
        if overlap and _HOWTO.search(t):
            score += 0.35 + (0.15 if len(overlap) >= 2 else 0)
            nums = set(re.findall(r"\b\d+\b", challenge.get('task', '')))
            if nums & set(re.findall(r"\b\d+\b", t)):
                score += 0.25
        if lit:
            score += 0.4
    return min(score, 1.0)


def _code_lines(srv):
    """[(address, line, code)] of the student's own program, in order."""
    out = []
    for a in sorted(srv.prog.source):
        if a < OS_TOP:
            continue
        line, src = srv.prog.source[a]
        code = ' '.join(src.split('@')[0].split('/*')[0].split())
        if code and not code.startswith('.') or code.startswith('.word') or ': .word' in code:
            out.append((a, line, code))
    return out


def _source_lines(srv):
    """[(line, code)] of every line of the file (for data lines the listing leaves out)."""
    try:
        with open(srv.prog.path, encoding='utf-8', errors='replace') as f:
            return [(i + 1, ' '.join(l.split('@')[0].split())) for i, l in enumerate(f)]
    except OSError:
        return []


class Concept:
    def __init__(self, sentence, lookup, topic, topic_name, where, shape):
        self.sentence, self.lookup, self.topic, self.topic_name = sentence, lookup, topic, topic_name
        self.where, self.shape = where, shape


def concept(srv):
    """What a challenge is about, where in the program to look, and the shape of
    the change, worked out from its task, its checks and the program's code."""
    ch = srv.challenge
    task = ch['task'].lower()
    lines = _code_lines(srv)
    kinds = {k for _, (k, _, _) in ch['checks']}
    want_out = next((w for _, (k, _, w) in ch['checks'] if k == 'output'), '')

    def first(pat):
        return next(((ln, code) for _, ln, code in lines if re.match(pat, code)), None)

    def all_(pat):
        return [(ln, code) for _, ln, code in lines if re.match(pat, code)]

    def at(x):
        return f"line {x[0]} (`{x[1]}`)" if x else "your program"
    if re.search(r"\bloop|count", task):
        c = first(r"cmp\b")
        return Concept("A loop repeats while the `cmp` before its conditional branch finds the counter hasn't reached its "
                       "limit; the branch then decides whether to go round again.",
                       "ARM assembly loops with CMP and BNE", 'branches', "Flags and branches",
                       f"Look at {at(c)}: this is where the loop decides whether to go round again.",
                       "The loop stops when the `cmp` finds the counter equal to the number on that line. Think about "
                       "which number it should compare with so the loop ends right after the last thing the challenge "
                       "wants printed, and change just that number.")
    if re.search(r"\blist|memory|fifth|total|array", task):
        data = [(n, c) for n, c in _source_lines(srv) if '.word' in c]
        c = first(r"cmp\b")
        where = f"Look at line {data[0][0]} (`{data[0][1]}`), the list itself" if data else "Look at the `.word` list"
        return Concept("The numbers live in memory, in a `.word` list after `.data`. The loop reads one word each time "
                       "round with `ldr`, so the list and the number of rounds have to agree.",
                       "ARM assembly LDR and .word data", 'loadstore', "Loads and stores",
                       where + (f", and at {at(c)}, where the loop decides how many numbers to add." if c else '.'),
                       "Add the new number to the list, and make the loop run once more: its limit must match how "
                       "many numbers there are.")
    if re.search(r"\bfunction|twice|call", task):
        calls = [(n, c) for n, c in [(ln, code) for _, ln, code in lines]
                 if re.match(r"bl\s+(?!putchar|getchar)\w", c)]
        return Concept("A function gets its argument in R0 and leaves its result in R0; `bl` calls it and `bx lr` "
                       "(or `pop {…, pc}`) comes back to the line after the call.",
                       "ARM assembly functions with BL and BX LR", 'stack', "The stack and functions",
                       f"Look at {at(calls[0] if calls else None)} and at what R0 holds just before it.",
                       "Use the same pattern that already prints the first number: put the value in R0, call the "
                       "function, then print what comes back. You don't need a new function.")
    if 'output' in kinds and re.search(r"[a-z]", want_out.lower().replace('\\n', '')) and \
            not want_out.strip().isdigit():
        letters = all_(r"movs\s+r0,\s*#'")
        span = (f"lines {letters[0][0]}–{letters[-1][0]}" if len(letters) > 1 else at(letters[0])) if letters else \
            "the lines that print"
        return Concept("Each character is printed by putting it in R0 (`movs r0, #'H'`) and then calling putchar "
                       "(`bl putchar`).",
                       "ASCII characters and putchar in ARM assembly", 'instructions', "Instructions",
                       f"Look at {span}: each character printed is one `movs r0, #'…'` followed by one `bl putchar`.",
                       "One more character means one more pair of those two lines, in the right place, with the "
                       "right letter. Keep the new line character at the end.")
    regs = [t for _, (k, t, _) in ch['checks'] if k == 'reg']
    movs = all_(r"movs\s+r[0-7],\s*#\d")
    if 'output' in kinds:
        span = ' and '.join(f"line {n} (`{c}`)" for n, c in movs[:2]) or "the lines that set the numbers"
        return Concept("putchar prints the character whose code is in R0; a digit 0–9 is printed by adding '0' (48) "
                       "to the number first.",
                       "ASCII codes for digits and putchar", 'numbers', "Hex, binary and ASCII",
                       f"Look at {span}: these set the numbers the program works with.",
                       "Change only the constants on those lines. Work out on paper which numbers give what each check "
                       "wants" + (" (all of the checks at once)" if len(ch['checks']) > 1 else '') + ".")
    r = regs[0] if regs else 0
    name = {13: 'SP', 14: 'LR'}.get(r, f"R{r}")
    last = None
    for _, ln, code in lines:
        if re.match(r"\w+\s+" + name.lower() + r"\b", code.lower()):
            last = (ln, code)
    return Concept(f"A register's final value comes from the last instruction that writes it. Step through and watch "
                   f"{name} in the Registers panel.", f"ARM assembly registers and {name}", 'registers', "Registers",
                   f"Look at {at(last)}, the last line that writes {name}.",
                   f"Change what the lines before it compute so that {name} ends with the value the check wants.")


def _status(srv):
    """'The check output "9\\n" wants that, but it printed "7\\n".' or a nudge to run to the end."""
    c = srv.core
    if c is None:
        return ''
    if not c.halted:
        return "Run it to the end first (Run, Space), so the checks can tell you what's still wrong."
    if srv.fault:
        return "The program stopped with a CPU fault, so the checks didn't run: ask me \"why did it crash?\" first."
    bad = [r for r in (srv._checks() or []) if not r['ok']]
    if not bad:
        return "Every check passes already: you've done it!"
    return ' '.join(f"The check `{r['text']}` isn't met yet: {r['got']}." for r in bad[:2])


def hint(srv, conv):
    """The next rung of the hint ladder for the loaded program's challenge."""
    ch = srv.challenge
    key = (srv.path, ch['task'])
    n = conv.hint_count.get(key, 0)
    conv.hint_count[key] = n + 1
    k = concept(srv)
    teacher = ch.get('hints') or []
    end = f"\nIf you're still stuck, ask your teacher, or look up {k.lookup} online."
    status = _status(srv)
    if n < len(teacher):
        text = f"**Hint {n + 1}:** {teacher[n]}" + (f"\n{status}" if status and n == 0 else '')
    else:
        step = n - len(teacher)
        if step == 0:
            text = f"I won't write the answer for you, but here's a push. {k.sentence} Ask me for another hint if you need it."
        elif step == 1:
            text = (status + "\n" if status else '') + k.where
        elif step == 2:
            text = k.shape + " Try changing that, save, and press **Run**."
        else:
            text = NO_HINTS_LEFT.format(concept=k.lookup)
            end = ''
    r = Reply(text + end, 'hint', policy='hint').link(k.topic, k.topic_name)
    r.suggestions = ["Another hint", "Did I pass?", f"Explain {k.topic_name.lower()}"]
    return r


def general_hint(kb, q, ctx):
    """A push without code for "write me a program that…" when no challenge applies."""
    docs = kb.search(q.terms, ctx.level, 3, kinds=('summary',)) if kb and kb.loaded else []
    ex = next((name for pat, name in EXAMPLE_FOR if re.search(pat, q.text)), "Say hello")
    parts = ["I won't write the code for you, but here's a push."]
    topic = None
    if docs:
        d = docs[0][2]
        topic = (d.topic, d.topic_name)
        parts.append(quick(d.text, 260))
    parts.append(f"The **{ex}** example does something similar: open it from **Examples** and step through it, then "
                 "change one thing at a time.")
    look = topic[1].lower() if topic else "ARM Thumb assembly"
    parts.append(f"If you're still stuck, ask your teacher, or look up {look} online.")
    r = Reply('\n'.join(parts), 'hint', policy='hint', source='built-in')
    if topic:
        r.link(*topic)
    r.link('programs', "Writing programs")
    return r
