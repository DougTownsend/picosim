"""
Answers from the program that is running right now: why it stopped, what
a step did, why a register holds a value, why the LED is off, what a load
error means, whether the challenge passed.

Everything here reads the server's state (never changes it) and reuses
what the rest of picosim already says: explain.py's explanation of the
last step, the server's record of which instruction last wrote each
register, errors.py's hints and challenge.py's checks.
"""

import os
import re

from .. import explain, cycles
from ..explain import OS_TOP, FLAG_MEANING, COND_FLAGS, ABOUT, io_name
from ..walkthrough import COND, REG_NAMES
from .. import settings as prog_settings
from .. import errors
from .reply import Reply

REG_DEF = {
    'SP': "SP (R13, the stack pointer) holds the address of the top of the stack. The stack starts at "
          "0x00010000, the top of RAM, and grows down: each PUSH moves SP down 4 bytes per register.",
    'LR': "LR (R14, the link register) holds the address a function returns to. BL puts the address of the "
          "line after it (plus 1, the Thumb bit) in LR, and BX LR jumps back there.",
    'PC': "PC (R15, the program counter) holds the address of the next instruction. It moves on by 2 after a "
          "16-bit instruction (4 after BL), and a branch loads a new address into it.",
}
LOW = ("R0–R7 are the general-purpose registers most instructions can use. R0–R3 also carry a function's "
       "arguments and result, and a function may change them; R4–R7 must be put back as they were.")
HIGH = ("R8–R12 are high registers: only a few instructions (MOV, ADD, CMP, BX) can use them, so programs "
        "rarely do.")


class Ctx:
    """What the student is looking at (sent by the GUI with each question)."""

    def __init__(self, d=None):
        d = d or {}
        try:
            self.level = max(1, min(4, int(d.get('level', 1))))
        except (TypeError, ValueError):
            self.level = 1
        self.visible = set(d.get('visible') or [])
        self.known = 'visible' in d
        self.mode = d.get('mode', 'sim')
        self.tab = d.get('tab', 'disassembly')
        self.diagram = bool(d.get('diagram'))
        self.machine = d.get('machine', 'multicycle')
        self.board = d.get('board', 'strip')

    def shows(self, cid):
        return cid in self.visible if self.known else True


def show_me(reply, ctx, cid, label="Show me"):
    """Add a "Show me" for control `cid`, only if the student can see it."""
    if ctx.shows(cid):
        reply.act('spotlight', cid, label)
    return reply


def hx(v):
    return f"0x{v & 0xFFFFFFFF:08X}"


def value_words(v, char=True):
    """'72 = 0x00000048 = 'H'' — a register value every way a student reads it."""
    v &= 0xFFFFFFFF
    s = f"{v}" if v < 0x80000000 else f"{v - (1 << 32)} (or {v} unsigned)"
    s += f" = {hx(v)}"
    if char and 32 <= v < 127:
        s += f" = the character '{chr(v)}'"
    elif char and v == 10:
        s += " = the new-line character"
    return s


def code_at(srv, addr):
    """`movs r0, #'H'` (line 18) for an instruction address."""
    ex = srv.explainer
    line, code, comment = ex.line_of(addr)
    text = code or ' '.join(srv.prog.asm_map.get(addr, '?').split())
    if addr < OS_TOP:
        lab = _func_of(srv, addr)
        return f"`{text}` in the built-in OS" + (f" ({lab})" if lab else '')
    return f"line {line} (`{text}`)" if line else f"`{text}` at {addr:04X}"


def _func_of(srv, addr):
    best = None
    for a, name in srv.prog.sym_map.items():
        if a <= addr and not name.startswith('.') and not name.startswith('_end') and (best is None or a > best[0]):
            best = (a, name)
    return best[1] if best else ''


def need_program(srv, ctx, sim=True, running_ok=False):
    """None when a live answer can be given, else the reply saying why not."""
    if srv.core is None:
        if getattr(srv, 'last_load_error', None):
            return load_error(srv, ctx)
        return show_me(Reply("No program is loaded yet, so there's nothing to look at. Click **Examples** and "
                             "pick **Say hello** to start, or **Open…** your own `.s` file.", 'no_program'),
                       ctx, 'examples', "Show me Examples")
    if sim and ctx.mode == 'pico':
        return show_me(Reply("You're in **Pico (USB serial)** mode, so the simulator isn't running your program "
                             "here. Switch the mode menu back to **Simulator** to step through it, then ask again.",
                             'pico_mode'), ctx, 'mode')
    if srv.running and not srv.waiting_input and not running_ok:
        return show_me(Reply("It's running. Press **Pause** (Space) and ask again, and I can explain where it is.",
                             'running'), ctx, 'run')
    return None


def _explanation(srv):
    try:
        return srv._explanation()
    except Exception:              # an explanation must never break the bot
        return None


def _section_text(sec, level, with_detail=True):
    if not sec:
        return '', ''
    head = sec.get('head', '')
    parts = [f"**{head}**" if head else '', sec.get('simple', '')]
    if sec.get('goes'):
        parts.append("Then: " + sec['goes'])
    detail = [t for lv, t in sec.get('detail', []) if lv <= level] if with_detail else []
    return '\n'.join(p for p in parts if p), '\n'.join(detail)


# ── D: why did it stop / what happened / what next ──────────────────────────

def what_happened(srv, ctx):
    r = need_program(srv, ctx)
    if r:
        return r
    e = _explanation(srv)
    if not e or not e.get('now'):
        return Reply("Nothing has happened yet: press **Step instruction** (S) and ask me again.", 'what_happened')
    text, detail = _section_text(e['now'], ctx.level)
    if e['now'].get('fault'):
        text += '\n' + e['now']['fault']
    nxt = e.get('next') or {}
    why = nxt.get('why') or ''
    r = Reply(text, 'what_happened', detail=detail, why=why)
    r.suggestions = ["What happens next?", "Tell me more"]
    return r.link('explain', "Explain this step")


def what_next(srv, ctx):
    r = need_program(srv, ctx)
    if r:
        return r
    e = _explanation(srv)
    nxt = (e or {}).get('next')
    if not nxt:
        return Reply("I can't tell what comes next right now. Press **Step instruction** (S) and ask again.", 'what_next')
    text, detail = _section_text(nxt, ctx.level)
    why = nxt.get('why') or ''
    if why:
        text += "\nWhy: " + why
    r = Reply(text, 'what_next', detail=detail)
    r.suggestions = ["What just happened?", "Why did it stop?"]
    return r.link('explain', "Explain this step")


def why_stop(srv, ctx):
    r = need_program(srv, ctx)
    if r:
        return r
    c = srv.core
    if srv.fault:
        return why_fault(srv, ctx)
    if c.halted:
        r = Reply("The program **finished**: `main` returned to picosim's built-in OS (with `pop {…, pc}` or "
                  "`bx lr`), and the OS stopped the processor. That's why Step and Run are greyed out. Press "
                  "**Reload** to run it again from the start.", 'why_stop')
        if srv.challenge:
            r.suggestions = ["Did I pass?"]
        return show_me(r, ctx, 'reload', "Show me Reload")
    if srv.waiting_input or srv.needs_input():
        return why_waiting(srv, ctx)
    pc = c.insn_addr if c.in_insn else c.pc
    if not c.in_insn and pc in srv.breakpoints:
        return show_me(Reply(f"It stopped at a **breakpoint**, just before {code_at(srv, pc)} runs. Press **Run** to "
                             "carry on, or click the red ● in the Disassembly to remove the breakpoint.", 'why_stop'),
                       ctx, 'disassembly', "Show me the breakpoint")
    if srv.action == 'run':
        return Reply(f"Run stopped {explain._stop_reason(srv)}. The next instruction is {code_at(srv, pc)}. Press "
                     "**Run** to carry on, or **Step instruction** (S) to go one line at a time.", 'why_stop')
    return show_me(Reply(f"It hasn't stopped because of a problem: **Step instruction** runs one instruction and then "
                         f"waits for you on purpose. The next one is {code_at(srv, pc)}. Press Step again, or **Run** "
                         "(Space) to let the program go by itself.", 'why_stop'), ctx, 'run', "Show me Run")


def why_waiting(srv, ctx):
    r = need_program(srv, ctx, running_ok=True)
    if r:
        return r
    if srv.waiting_input or srv.needs_input():
        r = Reply("It's in **getchar**, waiting for you to type a key. Click in the serial console (bottom right) "
                  "and type one: getchar puts the key's character code in R0 and the program carries on.",
                  'why_waiting')
        return show_me(r, ctx, 'console', "Show me the console").link('serial', "Serial console")
    if srv.running:
        return Reply("It isn't waiting: it's running. If it seems stuck, it may be in a loop that never ends; press "
                     "**Pause** (Space) and ask \"why won't it stop?\".", 'why_waiting')
    return why_stop(srv, ctx)


FAULT_HINTS = [
    (r"bit 0 clear", "A BX, BLX or POP {PC} jumped to an even address. Addresses you jump to must have bit 0 set "
                     "(the Thumb bit). If you load a label's address with `ldr r0, =label`, put `.thumb_func` on the "
                     "line before that label."),
    (r"Unaligned", "LDR and STR need an address that is a multiple of 4, and LDRH / STRH a multiple of 2. Use LDRB / "
                   "STRB for single bytes, and put `.align 2` before word data."),
    (r"Undefined instruction", "The CPU found something that is not a Cortex-M0+ instruction: code written for a "
                               "bigger ARM chip, or the program ran on into data."),
    (r"jumped to|fetch|0x00010000|0x10000", "The program ran off the end of memory. The usual cause: `main` has no "
                                           "`pop {…, pc}` at its end, so the CPU runs on through empty memory."),
    (r"Bus fault", "The address is not RAM (0x0000–0xFFFF) or a simulated I/O register. Common causes: `main` has "
                   "no `pop {…, pc}` at its end, a `pop` without a matching `push`, or a pointer that went wrong."),
    (r"Unknown SVC", "SVC #2 (putchar) and SVC #3 (getchar) are the only services picosim's OS offers; call them "
                     "with `bl putchar` / `bl getchar`."),
]


def why_fault(srv, ctx):
    r = need_program(srv, ctx, running_ok=True)
    if r:
        return r
    if not srv.fault:
        return Reply("Your program hasn't faulted: a **CPU fault** is when a program does something that would crash "
                     "a real Pico (an undefined instruction, a bad address, an unaligned load). picosim then stops "
                     "and the console says what went wrong.", 'why_fault').link('trouble', "Troubleshooting")
    hint = next((h for pat, h in FAULT_HINTS if re.search(pat, srv.fault)), '')
    c = srv.core
    at = c.insn_addr if c.in_insn else c.pc
    where = f" It happened at {code_at(srv, at)}." if at in srv.prog.asm_map else ''
    text = f"The CPU stopped with a **fault**: {srv.fault.rstrip('.')}.{where}"
    if hint:
        text += "\n" + hint
    text += "\nFix the program, save it, and press **Reload**."
    return Reply(text, 'why_fault').link('trouble', "Troubleshooting")


def wont_stop(srv, ctx):
    if srv.core is None:
        return need_program(srv, ctx)
    c = srv.core
    lines = ["Press **Pause** (Space) to stop it where it is, or **Reload** to start over."]
    pc = c.insn_addr if c.in_insn else c.pc
    text = srv.prog.asm_map.get(pc, '')
    if text.split()[:1] == ['bx'] and (c.regs[14] & ~1) == pc:
        lines.append(f"It's stuck on {code_at(srv, pc)}: LR holds the address of this same `bx lr`, so it jumps "
                     "back to itself forever. That happens when a function calls another function (even putchar) "
                     "without saving LR first: start it with `push {lr}` and end it with `pop {pc}`.")
    else:
        lines.append("A program that never stops is usually in a loop whose exit condition is never met: check "
                     "the `cmp` before the branch that goes back, and that the counter really changes each time "
                     "round. Some programs, like **Blink an LED**, loop forever on purpose.")
    lines.append("Step through one round of the loop and watch the registers in the compare.")
    r = Reply('\n'.join(lines), 'wont_stop').link('trouble', "Troubleshooting").link('branches', "Flags and branches")
    return show_me(r, ctx, 'run', "Show me Pause")


# ── E, G: registers and flags ───────────────────────────────────────────────

def _reg_index(name):
    name = name.upper()
    return {'SP': 13, 'LR': 14, 'PC': 15}.get(name, int(name[1:]) if name[1:].isdigit() else 0)


def reg_name(name):
    i = _reg_index(name)
    return REG_NAMES[i] if i < 16 else name.upper()


def register(srv, ctx, reg, why=False, define=False, claimed=None):
    name = reg_name(reg)
    idx = _reg_index(name)
    parts = []
    if claimed is not None and srv.core is not None and ctx.mode == 'sim' and \
            (srv.core.regs[idx] & 0xFFFFFFFF) != (claimed & 0xFFFFFFFF):
        parts.append(f"{name} isn't {claimed} right now.")
    if define:
        parts.append(REG_DEF.get(name) or (LOW if idx < 8 else HIGH))
    if srv.core is None:
        if not parts:
            parts.append(REG_DEF.get(name) or (LOW if idx < 8 else HIGH))
        return Reply(parts[0], 'reg_def', slots={'reg': name}).link('registers', "Registers")
    if ctx.mode == 'pico':
        r = Reply(' '.join(parts + ["In Pico mode picosim can't see the board's registers; switch to Simulator to "
                                    "watch them."]), 'reg_def', slots={'reg': name})
        return r
    c = srv.core
    v = c.regs[idx]
    run_note = " (it's running, so this keeps changing)" if srv.running and not srv.waiting_input else ''
    parts.append(f"{name} is {value_words(v)} now{run_note}.")
    if name == 'SP':
        depth = 0x10000 - (v & 0xFFFFFFFF)
        if 0 < depth <= 0x10000:
            parts.append(f"So {depth} bytes ({depth // 4} word{'s' if depth // 4 != 1 else ''}) are on the stack: "
                         "each PUSH moved SP down 4 bytes per register.")
        elif depth == 0:
            parts.append("That is the very top of RAM: nothing is on the stack.")
    elif name == 'PC':
        pc = c.insn_addr if c.in_insn else c.pc
        parts.append(f"The next instruction is {code_at(srv, pc)}.")
    elif name == 'LR' and (v & ~1) in srv.prog.asm_map:
        parts.append(f"A `bx lr` would return to {code_at(srv, v & ~1)}.")
    writer = getattr(srv, 'last_writer', {}).get(name)
    if name == 'SP' and why and not define:
        parts.insert(0, REG_DEF['SP'])
    if writer is None and idx < 13:
        guess = _static_writers(srv, name)
        if len(guess) == 1:
            writer = guess[0]
        elif guess:
            parts.append("The lines of your program that write it are " +
                         ', '.join(code_at(srv, a) for a in guess[:3]) + "; the last one to run set it.")
            writer = 'many'
    if name not in ('PC',) and writer != 'many':
        if writer == 'edit':
            parts.append("You typed this value in yourself. **Reload** puts back what the program sets.")
        elif writer is None:
            if v == 0 and idx < 13:
                parts.append(f"Nothing has written {name} yet, so it still holds its starting value, 0.")
            elif why or not define:
                parts.append("I can't tell which line set it (it changed during Run, or before a Step back); "
                             "step through with **Step instruction** to see.")
        elif writer < OS_TOP:
            what = _os_why(srv, name, writer, v)
            parts.append(what)
        else:
            about = ABOUT.get(explain._mnemonic(srv.prog.asm_map.get(writer, '')), '')
            _, _, comment = srv.explainer.line_of(writer)
            parts.append(f"It was set by {code_at(srv, writer)}." + (f" {about}" if why and about else '') +
                         (f" The comment there says: “{comment}”." if why and comment and not comment.startswith('<-') else ''))
    r = Reply(' '.join(parts), 'reg_why' if why else ('reg_def' if define else 'reg_value'), slots={'reg': name})
    r.suggestions = [f"What does {name} do?" if not define else f"Why is {name} {v if v < 1000 else hx(v)}?",
                     "What just happened?"]
    return show_me(r.link('registers', "Registers"), ctx, 'registers')


def _static_writers(srv, name):
    """Addresses of the program's own instructions whose first operand is
    register `name` and that write it (not stores, compares or pushes)."""
    out = []
    reg = name.lower()
    alias = {'sp': ('sp', 'r13'), 'lr': ('lr', 'r14')}.get(reg, (reg,))
    for a, t in sorted(srv.prog.asm_map.items()):
        if a < OS_TOP or t.startswith('.'):
            continue
        parts = t.split(None, 1)
        m = explain._mnemonic(t)
        if len(parts) < 2 or m.startswith(('str', 'cmp', 'cmn', 'tst', 'push', 'b', 'svc')) and m != 'bics':
            if not (m == 'pop' and len(parts) > 1 and any(x in parts[1].lower() for x in alias)):
                continue
        first = parts[1].split(',')[0].strip().lower()
        if first in alias or (m == 'pop' and any(x in parts[1].lower() for x in alias)):
            out.append(a)
    return out


def data_labels(srv):
    """The program's labels that name data (.word, .byte, …), in address order."""
    p = srv.prog
    named = dict(p.label_map)
    named.update({n: a for a, n in p.sym_map.items()})
    out = []
    for name, a in sorted(named.items(), key=lambda x: x[1]):
        if a >= OS_TOP and str(p.asm_map.get(a, '.')).startswith('.') and not name.startswith(('_', '.')) \
                and name not in out:
            out.append(name)
    return out


def last_branch(srv):
    """The mnemonic of the conditional branch that ran most recently, or None."""
    if srv.core is None:
        return None
    for r in reversed(srv.core.recent(64)):
        m = explain._mnemonic(srv.prog.asm_map.get(r['addr'], ''))
        if m.startswith('b') and m[1:] in COND_FLAGS:
            return m
    return None


def _os_why(srv, name, writer, v):
    fn = _func_of(srv, writer)
    if fn == 'getchar' and name == 'R0':
        return "getchar (in the built-in OS) put the code of the key you typed there."
    if fn == '_start' or writer < 0x10:
        if name == 'LR':
            return "The built-in OS set this before main: it is the address main returns to when it is finished."
        if name == 'R0':
            return ("The built-in OS set this before main: it loaded main's address (plus 1, the Thumb bit) into "
                    "R0 to call it. Your program doesn't need it.")
        return "The built-in OS set this before main."
    return f"The built-in OS set this (in {fn or 'its code'}), not a line of your program."


def value_why(srv, ctx, value):
    """'why 0x3001': the register that holds that value."""
    if srv.core is None:
        return None
    for i in range(16):
        if (srv.core.regs[i] & 0xFFFFFFFF) == (value & 0xFFFFFFFF):
            return register(srv, ctx, REG_NAMES[i], why=True)
    return None


def flag(srv, ctx, f):
    f = f.upper()
    words = {'N': "negative", 'Z': "zero", 'C': "carry", 'V': "overflow"}[f]
    parts = [f"**{f}** is the {words} flag. {FLAG_MEANING[f][0]}; {FLAG_MEANING[f][1].split(': ', 1)[-1]}."]
    if f == 'Z':
        parts.append("BEQ jumps when Z = 1 and BNE when Z = 0, so after `cmp` Z says whether the two were equal.")
    elif f == 'C':
        parts.append("BHS/BCS jump when C = 1 and BLO/BCC when C = 0 (unsigned comparisons).")
    elif f == 'N':
        parts.append("BMI jumps when N = 1 and BPL when N = 0; N and V together decide BLT, BGE, BGT and BLE.")
    else:
        parts.append("BVS jumps when V = 1 and BVC when V = 0; it matters for signed comparisons.")
    if srv.core is not None and ctx.mode == 'sim':
        v = getattr(srv.core, f)
        w = getattr(srv, 'last_writer', {}).get(f)
        parts.append(f"Right now {f} = {v}" + (f", set by {code_at(srv, w)}." if isinstance(w, int) else "."))
    r = Reply(' '.join(parts), 'flag', slots={'flag': f}).link('branches', "Flags and branches")
    return show_me(r, ctx, 'registers', "Show me the flags")


def branch_why(srv, ctx, mnem):
    cond = mnem[1:] if mnem.startswith('b') else ''
    if cond not in COND:
        return None
    words, rule = COND[cond]
    flags = COND_FLAGS[cond]
    head = f"**{mnem.upper()}** jumps only when {rule} (\"{words}\"); otherwise the next line runs."
    if srv.core is None or ctx.mode != 'sim':
        return Reply(head, 'branch_why').link('branches', "Flags and branches")
    c = srv.core
    lines = [a for a, t in sorted(srv.prog.asm_map.items()) if a >= OS_TOP and t.split()[:1] == [mnem]]
    recent = {r['addr']: r for r in c.recent(256)}
    ran = [a for a in lines if a in recent]
    parts = [head]
    now = ', '.join(f"{k} = {getattr(c, k)}" for k in flags)
    if ran:
        a = ran[-1]
        taken = recent[a]['cond_taken']
        parts.append(f"The last time {code_at(srv, a)} ran, it {'jumped' if taken else 'did not jump'}.")
    elif lines:
        parts.append(f"{code_at(srv, lines[0])} hasn't run yet.")
    setter = next((w for w in (getattr(srv, 'last_writer', {}).get(k) for k in flags) if isinstance(w, int)), None)
    parts.append(f"Right now {now}" + (f", from {code_at(srv, setter)}." if setter is not None else "."))
    parts.append("To change what it does, change the compare before it (or what it compares), not the branch.")
    return Reply(' '.join(parts), 'branch_why', slots={'mnem': mnem}).link('branches', "Flags and branches")


# ── H: memory and labels ────────────────────────────────────────────────────

def label_where(srv, ctx, label):
    if srv.core is None:
        return need_program(srv, ctx)
    p = srv.prog
    addr = p.label_map.get(label)
    if addr is None:
        addr = next((a for a, n in p.sym_map.items() if n == label), None)
    if addr is None:
        return None
    code = addr in p.asm_map and not p.asm_map[addr].startswith('.')
    parts = [f"**{label}** is at address {hx(addr)}."]
    if code:
        parts.append(f"It labels {code_at(srv, addr)}: a place in the code that branches and calls can jump to.")
    elif addr < 0x10000:
        word = int.from_bytes(srv.core.get_mem_slice(addr & ~3 if addr % 4 else addr, 4), 'little')
        parts.append(f"The word there now is {value_words(word, False)}. `ldr rX, ={label}` puts this address in a register, "
                     f"then `ldr rY, [rX]` reads the value.")
    r = Reply(' '.join(parts), 'label_where', slots={'label': label})
    if ctx.level >= 2:
        r.act('memory_tab', label, f"Show {label} in the Memory tab")
    else:
        r.text += " (The Memory tab, where you can see it, appears at Level 2.)"
    return r.link('memmap', "Memory and addresses")


def addr_what(srv, ctx, addr):
    if srv.core is None:
        return need_program(srv, ctx)
    p = srv.prog
    if addr >= 0x10000:
        name = io_name(addr)
        if name != explain.h32(addr):
            return Reply(f"{hx(addr)} is not RAM: it is the I/O register **{name}**. A load or store there talks to the "
                         "Pico's hardware (memory-mapped I/O).", 'addr_what').link('gpio', "GPIO: controlling pins")
        return Reply(f"{hx(addr)} is outside picosim's 64 KB of RAM (0x0000–0xFFFF) and is not one of the simulated "
                     "I/O registers, so a load or store there stops the program with a bus fault.",
                     'addr_what').link('memmap', "Memory and addresses")
    word = int.from_bytes(srv.core.get_mem_slice(addr & ~3, 4), 'little')
    sp = srv.core.sp
    sec = next((n for n, (a, b) in getattr(p, 'sections', {}).items() if a <= addr < b), None)
    if addr < OS_TOP:
        region = "picosim's built-in OS"
    elif addr >= sp:
        region = "the stack (between SP and the top of RAM)"
    elif sec:
        region = f"your program (its `{sec}` section)"
    elif addr in p.asm_map or addr < max(list(p.asm_map) + [0x3000]):
        region = "your program"
    else:
        region = "free RAM"
    lab = p.sym_map.get(addr) or next((n for n, a in p.label_map.items() if a == addr), '')
    parts = [f"Address {hx(addr)} is in {region}" + (f", at the label **{lab}**" if lab else '') + "."]
    if addr in p.asm_map and not p.asm_map[addr].startswith('.'):
        parts.append(f"It holds the instruction {code_at(srv, addr)}.")
    else:
        parts.append(f"The word at {hx(addr & ~3)} is {value_words(word, False)} (stored lowest byte first: "
                     f"{' '.join(f'{b:02X}' for b in word.to_bytes(4, 'little'))}).")
    r = Reply(' '.join(parts), 'addr_what')
    if ctx.level >= 2:
        r.act('memory_tab', f"0x{addr:X}", "Show it in the Memory tab")
    return r.link('memmap', "Memory and addresses")


# ── I: GPIO and the LED ─────────────────────────────────────────────────────

def _led_pin(srv, q):
    if q.pins:
        return q.pins[0]
    for s in prog_settings.describe(srv.prog.path) if srv.prog and srv.prog.path else []:
        if 'LED' in s['name'].upper():
            try:
                return int(s.get('value', s['default']), 0)
            except (ValueError, TypeError):
                pass
    for p in srv.gpio_pins():
        if p['dir'] == 'out' or p.get('sio_unrouted'):
            return p['pin']
    return None


def led(srv, ctx, q):
    if srv.core is None:
        return need_program(srv, ctx)
    pin = _led_pin(srv, q)
    guide = ('gpio', "GPIO: controlling pins")
    if re.search(r"\b(which|what) pin\b|\bled pin\b", q.text) and pin is not None and not q.pins:
        has = any('LED' in s['name'].upper() for s in prog_settings.describe(srv.prog.path)) if srv.prog.path else False
        r = Reply(f"Your program drives its LED on **GP{pin}**" + (" (its LED_PIN setting). To use another pin, click "
                  "**⚙ Settings**, pick the GPIO your LED is wired to and apply." if has else ".") +
                  " Not sure which pin your LED is on? The **Buttons and LEDs** example's FIND_PINS setting finds it.",
                  'led', slots={'pin': pin}).link(*guide)
        return show_me(r, ctx, 'settings', "Show me Settings") if has else r
    if pin is None:
        r = Reply("Your program hasn't made any pin an output yet, so no LED can light. To light an LED on GPn a "
                  "program does three things: writes 5 (SIO) to the pin's IO_BANK0 CTRL register, sets the pin's bit "
                  "in GPIO_OE_SET (0xD0000024) to make it an output, then sets the bit in GPIO_OUT_SET (0xD0000014). "
                  "The **Blink an LED** example does all three.", 'led')
        return r.link(*guide)
    p = srv.gpio_pins()[pin] if pin < len(srv.gpio_pins()) else None
    mask = 1 << pin
    ctrl = 0x40014004 + 8 * pin
    lines = [f"Checking **GP{pin}**" + (" (your program's LED_PIN setting)" if not q.pins else '') + ":"]
    if p is None:
        return Reply(f"GP{pin} is not a pin the Pico has (GP0–GP29).", 'led')
    live = srv.board_live()
    if not live and p['func'] != 5:
        lines.append(f"• It isn't connected to the SIO yet: write 5 to its IO_BANK0 CTRL register at "
                     f"`0x{ctrl:08X}` (0x40014004 + 8 × {pin}). The real RP2040 needs this too.")
    else:
        lines.append("• It is connected to the SIO (function 5). ✓")
    if p['dir'] != 'out':
        lines.append(f"• It is still an **input**: write its bit, `1 << {pin}` = `0x{mask:X}`, to GPIO_OE_SET "
                     "(`0xD0000024`) to make it an output.")
    else:
        lines.append("• It is an output. ✓")
    if p['dir'] == 'out' and p['level'] == 1:
        lines.append("• It is driven **high (1)**, so the LED is on now." +
                     (" If your real LED stays dark, check which way round it is and its resistor." if live else ''))
    elif p['dir'] == 'out':
        lines.append(f"• It is driven **low (0)**: the LED is off. Writing `0x{mask:X}` to GPIO_OUT_SET "
                     "(`0xD0000014`) turns it on, GPIO_OUT_CLR (`0xD0000018`) off, GPIO_OUT_XOR (`0xD000001C`) flips it.")
    if srv.core.halted is False and srv.core.steps < 40 and not p['dir'] == 'out':
        lines.append("Your program may simply not have got that far yet: Run it or keep stepping.")
    r = Reply('\n'.join(lines), 'led', slots={'pin': pin}).link(*guide)
    if ctx.level >= 2:
        show_me(r, ctx, 'pico', "Show me the board")
    return r


def button(srv, ctx, q):
    r = Reply("A button is an **input** pin: your program reads it from GPIO_IN (`0xD0000004`) and tests the pin's "
              "bit with `ands` or `tst`. In picosim, click an input pin on the Pico board drawing to drive it like a "
              "switch: floating → 1 → 0 → floating. A pin your program made an output can't be clicked. A button "
              "wired to GND needs the pin's pull-up on, so it reads 1 until pressed.", 'button')
    r.link('gpio', "GPIO: controlling pins").link('pico', "Raspberry Pi Pico board")
    return show_me(r, ctx, 'pico', "Show me the board")


# ── K: won't load ───────────────────────────────────────────────────────────

def load_error(srv, ctx, pasted=None):
    errs = None
    if pasted:
        found = []
        for row in pasted.splitlines():
            m = re.search(r"(?:\.s:(\d+):\s*)?(?:Error|Warning):\s*(.+)$", row.strip())
            if m:
                found.append({'line': int(m.group(1) or 0), 'source': '', 'message': m.group(2).strip(),
                              'hint': errors.hint_for(m.group(2))})
        if not found:
            h = errors.hint_for(pasted)
            if h:
                found.append({'line': 0, 'source': '', 'message': pasted.strip()[:120], 'hint': h})
        errs = found or None
    le = getattr(srv, 'last_load_error', None)
    if errs is None and le:
        errs = le.get('errors') or [{'line': 0, 'source': '', 'message': le.get('message', ''), 'hint': ''}]
    if not errs:
        if srv.core is not None:
            return Reply("Your program loaded without errors. If it does something you didn't expect, step through "
                         "it with **Step instruction** (S) and watch what changes.", 'load_error')
        return Reply("When a program won't load, the **Could not load program** box shows each wrong line and what "
                     "usually fixes it. Fix the line in your editor, save, and press **Reload**.",
                     'load_error').link('trouble', "Troubleshooting")
    lines = ["Here's what the assembler is complaining about:" if len(errs) > 1 else "Here's what the assembler says:"]
    for e in errs[:4]:
        where = f"**Line {e['line']}**" + (f": `{e['source']}`" if e.get('source') else '') if e['line'] else "**Message**"
        lines.append(f"• {where} — {e['message']}" + (f"\n  {e['hint']}" if e.get('hint') else ''))
    lines.append("Fix it in your editor and save: picosim reloads the file by itself.")
    return Reply('\n'.join(lines), 'load_error').link('trouble', "Troubleshooting")


# ── M: challenges ───────────────────────────────────────────────────────────

def challenge_status(srv, ctx):
    ch = srv.challenge
    if not ch:
        return Reply("This program has no challenge. Examples with one show it in a strip under the toolbar: "
                     "**Add two numbers**, **Count with a loop**, **Add up a list in memory** and **Call a function**.",
                     'did_i_pass').link('challenges', "Challenges")
    c = srv.core
    if c is None:
        return need_program(srv, ctx)
    if srv.fault:
        return Reply("The program stopped with a CPU fault, so the checks didn't run. Ask me \"why did it crash?\", "
                     "fix it, and run it to the end again.", 'did_i_pass')
    if not c.halted:
        return show_me(Reply("Not yet: the checks run when the program **finishes**. Press **Run** (Space) to let it "
                             "reach the end, then the strip under the toolbar turns green or says what's still "
                             "wrong.", 'did_i_pass'), ctx, 'run', "Show me Run")
    res = srv._checks() or []
    if res and all(r['ok'] for r in res):
        return Reply("**Yes, you passed!** Every check is green: " + '; '.join(f"`{r['text']}` ✓" for r in res) + ".",
                     'did_i_pass')
    lines = ["**Not yet.**"] + [f"• `{r['text']}` " + ("✓" if r['ok'] else f"✗ — {r['got']}") for r in res]
    r = Reply('\n'.join(lines), 'did_i_pass')
    r.suggestions = ["Give me a hint"]
    return r


def challenge_task(srv, ctx):
    ch = srv.challenge
    if not ch:
        return challenge_status(srv, ctx)
    checks = '\n'.join(f"• `{t}`" for t, _ in ch['checks'])
    r = Reply(f"**The challenge:** {ch['task']}\nWhen the program finishes, picosim checks:\n{checks}", 'challenge_task')
    r.suggestions = ["Give me a hint", "Did I pass?"]
    return r.link('challenges', "Challenges")


# ── P: cycle counts (Level 3) ───────────────────────────────────────────────

def cycle_count(mnem):
    rows = [(name, n) for name, n, _, _ in cycles.rows() if name.split()[0].lower() == mnem]
    if not rows:
        return None
    shown = '; '.join(f"`{name}` {n} cycles" for name, n in rows[:4])
    return Reply(f"On picosim's multi-cycle datapath: {shown}. Every instruction first spends 3 cycles fetching itself "
                 "(FETCH_ADDR, FETCH_MEMORY, FETCH_IR), then DECODE, then the states its kind needs.",
                 'cycles', slots={'mnem': mnem}).link('cycles', "Cycle by cycle").link('kinds', "Other instructions")


# ── N: the program itself ───────────────────────────────────────────────────

def program_about(srv, ctx):
    if srv.prog is None:
        return need_program(srv, ctx)
    head = {}
    try:
        with open(srv.prog.path, encoding='utf-8', errors='replace') as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith('@'):
                    break
                m = re.match(r'@\s*(Title|About|Try it)\s*:\s*(.*)', s)
                if m:
                    head[m.group(1).lower()] = m.group(2)
    except OSError:
        pass
    name = os.path.basename(srv.prog.path)
    if head.get('title'):
        t = f"This is **{head['title']}** ({name}). {head.get('about', '')}"
    else:
        t = f"This is **{name}**, your own program. Step through it with **Step instruction** (S); the Explain this " \
            "step panel says what each line does."
    if head.get('try it'):
        t += f"\nTry it: {head['try it']}"
    return Reply(t.strip(), 'program_about')
