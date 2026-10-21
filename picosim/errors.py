"""
Assembler and linker messages turned into something a beginner can act on.

explain() takes the raw text that arm-none-eabi-gcc printed and the .s file,
and returns one entry per problem: the line number, that line of the file,
the message without temporary paths, and a hint saying what usually causes
it.  The hints match the guide's Troubleshooting page.
"""

import os
import re

# (pattern in the message, hint)
HINTS = [
    (r"cannot honor width suffix",
     "A missing S (write adds, not add; movs, not mov with a #constant), or a register "
     "from R8 to R12. Most instructions only take R0-R7."),
    (r"invalid immediate: .* is out of range",
     "The constant is too big or negative: movs, cmp and two-register adds / subs take 0 to 255."),
    (r"immediate value out of range",
     "Three-register adds / subs (adds r0, r1, #9) take only 0 to 7. Use adds r0, #9 on one "
     "register, or put the constant in a register first."),
    (r"invalid offset, value too big",
     "A load or store offset that is too big, negative or not a multiple of the size; or, on an "
     "ldr rX, =value line, a literal pool too far away (put .ltorg after a b or pop)."),
    (r"bad arguments to instruction|syntax error|garbage following instruction",
     "Check the commas and the # before constants (movs r0, #1), and the register names."),
    (r"bad instruction",
     "Not an instruction the assembler knows: check the spelling. Labels need a colon (loop:)."),
    (r"undefined reference to",
     "A branch, bl or ldr =label names a label that does not exist. Check its spelling "
     "(capitals matter) and that the label's line ends with a colon."),
    (r"symbol .* is already defined",
     "The same label is used twice. Give each label its own name."),
    (r"branch out of range",
     "The branch target is too far away for this branch. Use b to a label nearer, or bl."),
    (r"selected processor does not support",
     "Not a Cortex-M0+ instruction (it exists on bigger ARM chips). The Instructions page "
     "lists what the M0+ has."),
    (r"lo register required",
     "This instruction only takes R0-R7."),
]

_LINE = re.compile(r"^(?P<file>[^:\n]+\.s):(?P<line>\d+): (?:Error|Warning): (?P<msg>.*)$")
_UNDEF = re.compile(r"undefined reference to [`'](?P<name>[^'`]+)'")
_NOISE = re.compile(r"Assembler messages:|cannot find entry symbol|collect2: error|"
                    r"ld returned \d+ exit status|in function `")


def hint_for(msg):
    for pat, hint in HINTS:
        if re.search(pat, msg):
            return hint
    return ""


def explain(raw, s_path):
    """[{line, source, message, hint}] for the problems in `raw` (line 0 when
    the message names no line).  Empty if nothing could be recognised."""
    try:
        with open(s_path, encoding="utf-8", errors="replace") as f:
            src = f.read().split("\n")
    except OSError:
        src = []

    def text(n):
        return src[n - 1].strip() if 0 < n <= len(src) else ""

    out = []
    seen = set()
    for row in raw.splitlines():
        row = row.strip()
        m = _LINE.match(row)
        if m:
            n = int(m.group("line"))
            msg = m.group("msg").strip()
            if (n, msg) not in seen:
                seen.add((n, msg))
                out.append({"line": n, "source": text(n), "message": msg, "hint": hint_for(msg)})
            continue
        u = _UNDEF.search(row)
        if u:
            name = u.group("name")
            # the linker gives no line: find the first line that uses the name
            n = next((i + 1 for i, l in enumerate(src)
                      if re.search(r"(^|[\s,=])" + re.escape(name) + r"\b", l.split("@")[0])
                      and not l.strip().startswith(name + ":")), 0)
            key = ("undef", name)
            if key not in seen:
                seen.add(key)
                out.append({"line": n, "source": text(n),
                            "message": f"there is no label called {name}", "hint": hint_for("undefined reference to")})
    out.sort(key=lambda e: (e["line"] == 0, e["line"]))
    return out


def clean(raw):
    """The raw message without temporary paths and linker noise (for the
    rare message explain() does not recognise)."""
    keep = []
    for row in raw.splitlines():
        if not row.strip() or _NOISE.search(row) or row.startswith("Assembler/linker error"):
            continue
        keep.append(re.sub(r"(/[^\s:]+/)+([^/\s:]+)", r"\2", row))
    return "\n".join(keep)
