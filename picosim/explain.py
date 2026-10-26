"""
Plain-language explanations of single steps, for the GUI's "Explain this
step" panel.

After each Step (instruction, cycle, over or back) the server asks
Explainer.explain() for two short sections:

  now   what the step just did, in one or two plain sentences, with a list
        of details underneath (exactly what changed, how the instruction
        works, its clock cycles) for students who want the full picture
  next  what the next instruction (or clock cycle) will do, where the
        program goes after it, and why: the comment on that line of the
        program, plus what the code pattern is for (saving registers,
        deciding a loop, calling a function, …)

The forecast is not guessed: the next instruction really runs, on a scratch
copy of the machine (memory, registers, flags and pins copied over), so
what "next" says is exactly what the next Step will do.  The scratch copy
is thrown away; the student's machine is never touched.

Every detail line carries the lowest Level it is meant for, so the GUI can
leave out what a Level 1 student has not met yet (clock cycles, encodings).
"""

import re

from . import timing
from .cpu import SimulatorError, svc_write_data
from .walkthrough import (REG_NAMES, COND, Walkthrough, h32, h16, _operands, _reg, _imm,
                          _join, _cap)

OS_TOP = 0x3000          # the built-in OS lives below this address

SIO = {0x04: "GPIO_IN", 0x10: "GPIO_OUT", 0x14: "GPIO_OUT_SET", 0x18: "GPIO_OUT_CLR",
       0x1C: "GPIO_OUT_XOR", 0x20: "GPIO_OE", 0x24: "GPIO_OE_SET", 0x28: "GPIO_OE_CLR",
       0x2C: "GPIO_OE_XOR"}

# What each FSM state is for, said as the start of "the next cycle will …"
STATE_NEXT = {
    'FETCH_ADDR': ("FETCH", "start fetching the next instruction: the PC's address goes into MAR, "
                            "and the PC moves on by 2"),
    'FETCH_MEMORY': ("FETCH", "read the instruction from memory into MDR"),
    'FETCH_IR': ("FETCH", "copy the instruction from MDR into IR, where the control unit can read it"),
    'FETCH2_ADDR': ("FETCH", "start fetching the second half of this 32-bit instruction"),
    'FETCH2_MEMORY': ("FETCH", "read the second halfword from memory into MDR"),
    'FETCH2_IR': ("FETCH", "copy the second halfword into IR2"),
    'DECODE': ("DECODE", "decode the instruction: work out which registers and operation it uses "
                         "and which states come next"),
    'EVALUATE_ADDRESS': ("EVALUATE ADDRESS", "work out the memory address with the address adder"),
    'FETCH_OPERANDS': ("FETCH OPERANDS", "get the values to work on: registers into the ALU, or "
                                         "memory into MDR"),
    'EXECUTE_COMMIT': ("EXECUTE", "let the ALU compute the result and write it to the register and "
                                  "the flags"),
    'EXECUTE_PC': ("EXECUTE", "change the PC (a jump), or leave it alone if a branch is not taken"),
    'STORE_RESULT': ("STORE RESULT", "put the result where it belongs: a register, or memory for a "
                                     "store"),
    'LINK': ("STORE RESULT", "save the return address in LR"),
    'WRITEBACK': ("STORE RESULT", "move SP past the words that were pushed or popped"),
    'SVC_CALL': ("EXECUTE", "call the built-in OS"),
    'HALT': ("EXECUTE", "stop the processor"),
}

# One sentence about each instruction, for "How this instruction works".
ABOUT = {
    'movs': "MOVS (move) copies a number or a register into a register. The S means it also "
            "updates the N and Z flags.",
    'mov': "MOV copies one register into another without changing the flags; unlike MOVS it can "
           "also use R8–R12.",
    'adds': "ADDS adds two numbers and puts the sum in the first register named. The S means it "
            "updates all four flags, N Z C V.",
    'add': "ADD adds without changing the flags.",
    'adcs': "ADCS adds two numbers plus the C flag (used to add numbers bigger than 32 bits).",
    'subs': "SUBS subtracts the last number from the one before it and puts the difference in "
            "the first register named. It updates N Z C V.",
    'sub': "SUB subtracts without changing the flags.",
    'sbcs': "SBCS subtracts with a borrow from the C flag.",
    'rsbs': "RSBS (reverse subtract, written NEGS too) works out 0 − the register: it makes a "
            "number negative.",
    'cmp': "CMP compares two numbers by subtracting the second from the first. The answer is "
           "thrown away and only the flags change, ready for a conditional branch.",
    'cmn': "CMN compares by adding the two numbers; only the flags change.",
    'tst': "TST ANDs two numbers only to set the flags: Z = 1 means they have no 1 bits in "
           "common. Programs use it to test one bit, such as a button's pin.",
    'ands': "ANDS keeps only the bits that are 1 in both numbers (a mask keeps the bits you want).",
    'orrs': "ORRS sets every bit that is 1 in either number.",
    'eors': "EORS (exclusive OR) flips the bits of the first number wherever the second has a 1.",
    'bics': "BICS (bit clear) clears the bits of the first number wherever the second has a 1.",
    'mvns': "MVNS (move NOT) copies a register with every bit flipped.",
    'muls': "MULS multiplies two registers; the register that gets the result must be one of them. "
            "It updates N and Z.",
    'lsls': "LSLS shifts the bits left, filling with 0s; each place doubles the number. The last "
            "bit shifted out goes into C.",
    'lsrs': "LSRS shifts the bits right, filling with 0s; each place halves an unsigned number.",
    'asrs': "ASRS shifts right but keeps the sign bit, so it halves signed numbers too.",
    'rors': "RORS rotates the bits right: bits shifted out at the bottom come back in at the top.",
    'b': "B (branch) always jumps to the label.",
    'bl': "BL (branch with link) calls a function: it puts the return address in LR, then jumps. "
          "The function comes back with BX LR or POP {…, PC}.",
    'blx': "BLX calls the function whose address is in a register.",
    'bx': "BX jumps to the address in a register. BX LR is how a function returns.",
    'push': "PUSH saves registers on the stack, in memory: SP goes down 4 bytes for each "
            "register. POP puts them back later.",
    'pop': "POP loads registers back from the stack and moves SP up 4 bytes for each. Popping "
           "into PC is a jump: it returns from a function.",
    'ldr': "LDR (load register) copies a 32-bit word from memory into a register. The address is "
           "in the square brackets.",
    'ldrb': "LDRB loads one byte from memory and fills the other 24 bits of the register with 0.",
    'ldrh': "LDRH loads a halfword (2 bytes) and fills the top 16 bits with 0.",
    'ldrsb': "LDRSB loads one byte and copies its sign bit into the top 24 bits.",
    'ldrsh': "LDRSH loads a halfword and copies its sign bit into the top 16 bits.",
    'str': "STR (store register) copies a register into 4 bytes of memory at the address in the "
           "square brackets.",
    'strb': "STRB stores the lowest byte of a register.",
    'strh': "STRH stores the lowest halfword (2 bytes) of a register.",
    'svc': "SVC (supervisor call) asks the built-in OS for a service: #2 prints the character in "
           "R0 (putchar) and #3 reads a key into R0 (getchar).",
    'bkpt': "BKPT (breakpoint) stops the processor. picosim's built-in OS uses it when main has "
            "returned. (On a real Pico with no debugger attached, BKPT causes a HardFault "
            "instead.)",
    'nop': "NOP does nothing for one instruction.",
    'adr': "ADR puts the address of a label into a register.",
}
LITERAL_ABOUT = ("LDR Rd, =value loads any 32-bit number, such as an address too big for MOVS "
                 "(which only makes 0 to 255). The assembler stores the number just after your "
                 "code, in a literal pool, and this LDR reads it from there using the PC.")

FLAG_MEANING = {
    'N': ("N = 1: the result is negative (bit 31 is 1)", "N = 0: the result is not negative"),
    'Z': ("Z = 1: the result is zero (after CMP: the two were equal)",
          "Z = 0: the result is not zero (after CMP: the two were different)"),
    'C': ("C = 1: a carry out of bit 31 (after CMP or SUBS: no borrow, so the first was ≥ the "
          "second as unsigned numbers)",
          "C = 0: no carry (after CMP or SUBS: a borrow, so the first was < the second as unsigned "
          "numbers)"),
    'V': ("V = 1: signed overflow: the true answer does not fit in a signed 32-bit number, so "
          "the sign bit came out wrong",
          "V = 0: no signed overflow"),
}
COND_FLAGS = {'eq': 'Z', 'ne': 'Z', 'cs': 'C', 'hs': 'C', 'cc': 'C', 'lo': 'C', 'mi': 'N', 'pl': 'N',
              'vs': 'V', 'vc': 'V', 'hi': 'CZ', 'ls': 'CZ', 'ge': 'NV', 'lt': 'NV', 'gt': 'ZNV',
              'le': 'ZNV'}
ALU_EXPR = {'add': '+', 'adc': '+', 'sub': '−', 'sbc': '−', 'and': 'AND', 'orr': 'OR',
            'eor': 'XOR', 'bic': 'AND NOT', 'mul': '×'}
BITWISE = ('and', 'orr', 'eor', 'bic', 'mvn', 'lsl', 'lsr', 'asr', 'ror', 'tst')


def snap(core):
    return {'regs': list(core.regs),
            'flags': {'N': core.N, 'Z': core.Z, 'C': core.C, 'V': core.V},
            'datapath': dict(core.datapath)}


def signed(v):
    return v - 0x100000000 if v & 0x80000000 else v


def num(v, hexy=False, char=False):
    """A value for a student: decimal (with its hex when that helps), its
    signed reading when bit 31 is set, and with `char` the character it codes."""
    v &= 0xFFFFFFFF
    if hexy:
        return f"0x{v:X}"
    if v >= 0xFFFF0000:                    # a small negative number
        return f"{signed(v)} (0x{v:08X})".replace('-', '−')
    if v >= 0x1000:                        # an address or a bit mask
        return f"0x{v:X}"
    t = str(v) if v <= 9 else f"{v} (0x{v:X})"
    if char and 32 <= v < 127:
        t += f", the character '{chr(v)}'"
    elif char and v == 10:
        t += ", the new-line character"
    return t


def reg_list(tok):
    """'{r4, r7, lr}' → ['R4', 'R7', 'LR']."""
    return [_reg(r) for r in tok.strip('{} ').split(',') if r.strip()]


def io_name(addr):
    if 0xD0000000 <= addr < 0xD0000040:
        return "SIO " + SIO.get(addr - 0xD0000000, f"+0x{addr - 0xD0000000:X}")
    if 0x40014000 <= addr < 0x40014800:
        off = addr - 0x40014000
        return f"IO_BANK0 GPIO{off >> 3}_{'CTRL' if off & 7 == 4 else 'STATUS'}"
    if 0x4001C000 <= addr < 0x4001C100:
        return "PADS_BANK0 " + ("VOLTAGE_SELECT" if addr == 0x4001C000 else f"GPIO{(addr - 0x4001C004) >> 2}")
    if 0x4000C000 <= addr < 0x4000C100:
        return f"RESETS +0x{addr - 0x4000C000:X}"
    return h32(addr)


def _mnemonic(text):
    m = text.split()[0].lower() if text.strip() else ''
    return re.sub(r'\.(w|n)$', '', m)


def _cond(base):
    m = re.match(r'^b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)$', base)
    return m.group(1) if m else None


class Run:
    """One instruction (or a whole call) as it ran: the machine before and
    after, its cycles, what it printed and which pins it changed."""

    def __init__(self, text, addr, pre, post, cycles=(), out='', pins=(), whole_call=None,
                 fault=None, halted=False, mem_addr=None, waits=False):
        self.text, self.addr, self.pre, self.post = text, addr, pre, post
        self.cycles = list(cycles)
        self.out = out
        self.pins = list(pins)            # [(pin, level before, level after)]
        self.whole_call = whole_call      # name of the function a Step over ran
        self.fault = fault
        self.halted = halted
        self.mem_addr = mem_addr
        self.waits = waits                # the call stops in getchar, waiting for a key
        if mem_addr is None:
            for cy in self.cycles:
                if cy['info']['state'] == 'EVALUATE_ADDRESS':
                    self.mem_addr = cy['post']['datapath']['MAR']
        self.changes = [ch for cy in self.cycles for ch in cy['info']['changes']]


class Explainer:
    def __init__(self, prog):
        self.prog = prog
        self.words = Walkthrough.describer(prog)
        self._scratch = None
        self._scratch_of = None

    # ── names for addresses and lines ────────────────────────────────────────

    def label(self, addr):
        return self.prog.sym_map.get(addr, '')

    def line_of(self, addr):
        """(line number, code, comment) of the source line at addr."""
        line, src = self.prog.source.get(addr, (0, ''))
        code, comment = src, ''
        for mark in ('@', '/*'):
            i = src.find(mark)
            if i >= 0:
                code, comment = src[:i], src[i + len(mark):]
                break
        comment = comment.replace('*/', '').strip()
        return line, ' '.join(code.split()), comment

    def where(self, addr):
        """'loop (at 3004, line 21)' / 'putchar (at 0010, in the built-in OS)'."""
        lab = self.label(addr)
        line, _, _ = self.line_of(addr)
        place = "in the built-in OS" if addr < OS_TOP else (f"line {line}" if line else '')
        if lab:
            return f"{lab} (at {addr:04X}" + (f", {place})" if place else ")")
        return f"{addr:04X}" + (f" ({place})" if place else '')

    def inline(self, addr, text):
        """An instruction named inside a sentence: "line 22 (movs r0, #'H')"."""
        line, code, _ = self.line_of(addr)
        shown = code or ' '.join(text.split())
        if addr < OS_TOP:
            return f"{shown} at {addr:04X} in the built-in OS"
        return f"line {line} ({shown})" if line else f"{shown} at {addr:04X}"

    def head(self, addr, text):
        """How an instruction is named at the top of a section."""
        line, code, _ = self.line_of(addr)
        shown = code or ' '.join(text.split())
        if addr < OS_TOP:
            return f"{addr:04X}  {shown}   (built-in OS)"
        return f"Line {line}:  {shown}" if line else f"{addr:04X}  {shown}"

    def charish(self, run, reg):
        """True when a value in `reg` is a character here: the line writes a
        character constant ('H'), or R0 is about to be printed."""
        _, code, _ = self.line_of(run.addr)
        if "'" in code:
            return True
        if reg != 'R0':
            return False
        for a in (run.addr + 2, run.addr + 4):
            t = self.prog.asm_map.get(a, '')
            if _mnemonic(t) == 'bl':
                tgt = _imm(_operands(t)[0]) if _operands(t) else None
                return tgt is not None and self.label(tgt) == 'putchar'
        return False

    # ── the scratch machine for forecasts ───────────────────────────────────

    def _scratch_core(self):
        if self._scratch_of is not self.prog:
            self._scratch = self.prog.clone()
            self._scratch_of = self.prog
        return self._scratch

    def forecast(self, core, gpio, keys, whole_os_calls):
        """Run the next instruction on a copy of the machine.  With
        `whole_os_calls` (Level 1) a BL into the OS runs the whole call, and
        a main that returns runs the OS's last instructions, as Step does."""
        sp = self._scratch_core()
        c = sp.cpu._core
        c.load_memory(core.get_memory(), 0)
        c.regs = list(core.regs)
        c.N, c.Z, c.C, c.V = core.N, core.Z, core.C, core.V
        c.PRIMASK, c.CONTROL, c.PSP = core.PRIMASK, core.CONTROL, core.PSP
        c.halted = False
        g = sp.cpu.gpio
        if g is not None and gpio is not None:
            for k in ('_oe', '_out', '_pue', '_pde', '_resets', '_resets_done'):
                setattr(g, k, getattr(gpio, k))
            g._ext = list(gpio._ext)
            g._ctrl = list(gpio._ctrl)
        out = []
        keys = list(keys)

        def svc(n):
            if n == 0:
                out.append(svc_write_data(c).decode('latin-1'))
            elif n == 1:
                c.halted = True
            elif n == 2:
                out.append(chr(c.get_reg(0) & 0xFF))
            elif n == 3:
                c.set_reg(0, keys.pop(0) if keys else 0xFFFFFFFF)
        c.svc_handler = svc
        before_pins = g.pin_states() if g else []
        addr = c.pc
        text = self.prog.asm_map.get(addr, '???')
        pre = snap(c)
        cycles = []
        fault = None
        whole = None
        waits = False
        try:
            while True:
                info = c.step_cycle()
                cycles.append({'info': info, 'post': snap(c)})
                if info['last']:
                    c.check_halt()
                    break
            call = self.words_call_target(addr, text, pre)
            if whole_os_calls and call is not None and call[1] < OS_TOP and not c.halted:
                whole = self.label(call[1]) or f"{call[1]:04X}"
                for _ in range(10000):
                    if c.halted or (c.pc == call[0] and c.sp >= pre['regs'][13]):
                        break
                    if c.read16(c.pc) == 0xDF03 and not keys:
                        waits = True             # getchar: nothing typed yet
                        break
                    c.step()
                    c.check_halt()
            if whole_os_calls and not c.halted and c.pc < OS_TOP and addr >= OS_TOP:
                for _ in range(16):          # main returned: the OS halts at once
                    if c.halted or c.pc >= OS_TOP:
                        break
                    c.step()
                    c.check_halt()
        except (RuntimeError, SimulatorError) as e:
            fault = str(e)
        after_pins = g.pin_states() if g else []
        pins = [(a['pin'], a['level'], b['level'], a['dir'], b['dir']) for a, b in zip(before_pins, after_pins)
                if (a['level'], a['dir']) != (b['level'], b['dir'])]
        return Run(text, addr, pre, snap(c), cycles, ''.join(out), pins, whole, fault, c.halted, waits=waits)

    @staticmethod
    def words_call_target(addr, text, pre):
        """(return address, target) when `text` is a call, else None."""
        base = _mnemonic(text)
        ops = _operands(text)
        if base == 'bl':
            t = _imm(ops[0]) if ops else None
            return (addr + 4, t) if t is not None else None
        if base == 'blx' and ops:
            r = _reg(ops[0])
            if r in REG_NAMES:
                return (addr + 2, pre['regs'][REG_NAMES.index(r)] & ~1)
        return None

    # ── one instruction in plain words ───────────────────────────────────────

    def say(self, run, future=False):
        """One or two sentences: what the instruction does with these values.
        `future` words it as what the next step will do."""
        T = (lambda past, fut: fut) if future else (lambda past, fut: past)
        text = run.text
        base = _mnemonic(text)
        ops = _operands(text)
        r0, r1 = run.pre['regs'], run.post['regs']
        f1 = run.post['flags']
        pc1 = r1[15]
        cond = _cond(base)

        def rv(r, regs):
            return regs[REG_NAMES.index(r)] if r in REG_NAMES else 0

        def place(a, via=''):
            """'memory address 0x3024 (the label numbers, the address in R1)'"""
            if a is None:
                return 'memory'
            if a >= 0x10000:
                return f"the I/O register {io_name(a)}" + (f" ({via})" if via else '')
            lab = self.label(a) or next((n for n, x in self.prog.label_map.items() if x == a), '')
            notes = ([f"the label {lab}"] if lab else []) + ([via] if via else [])
            return f"memory address 0x{a:X}" + (f" ({', '.join(notes)})" if notes else '')

        if run.whole_call and run.waits:
            return (f"will call {run.whole_call}, which waits for you to type a key in the serial console "
                    "and then returns its character code in R0.")
        if run.whole_call:
            s = T(f"Called {run.whole_call} and ran the whole function, then came back to the next line.",
                  f"will call {run.whole_call} and run the whole function, then come back to the next line.")
            if run.out:
                s += T(f" It printed {self._printed(run.out)} in the serial console.",
                       f" It will print {self._printed(run.out)} in the serial console.")
            return s
        if base == 'push' and ops:
            names = reg_list(ops[0])
            n = 4 * len(names)
            it = 'it' if len(names) == 1 else 'they'
            return T(f"Saved {_join(names)} on the stack so {it} can be put back later. "
                     f"SP went down by {n} bytes, from {h32(r0[13])} to {h32(r1[13])}.",
                     f"will save {_join(names)} on the stack so {it} can be put back later. "
                     f"SP will go down by {n} bytes, to {h32(r1[13])}.")
        if base == 'pop' and ops:
            names = reg_list(ops[0])
            s = T(f"Took {_join(names)} back off the stack (SP went up from {h32(r0[13])} to {h32(r1[13])}).",
                  f"will take {_join(names)} back off the stack (SP goes up to {h32(r1[13])}).")
            if 'PC' in names and (pc1 & ~1) < OS_TOP <= run.addr:
                s += T(" Loading PC is a jump: main returned to the built-in OS, which had called it.",
                       " Loading PC is a jump: main returns to the built-in OS, which called it.")
            elif 'PC' in names:
                s += T(f" Loading PC is a jump: the function returned to {self.where(pc1 & ~1)}.",
                       f" Loading PC is a jump: the function returns to {self.where(pc1 & ~1)}.")
            return s
        if base.startswith('ldr') and len(ops) >= 2 and ('pc' in ops[1].lower() or '=' in ops[1]):
            rd = _reg(ops[0])
            v = rv(rd, r1)
            lab = self.label(v) or self.label(v & ~1) or \
                next((n for n, x in self.prog.label_map.items() if x == v), '')
            what = f" (the address of {lab})" if lab else (f" (the address of {io_name(v)})" if v >= 0x10000 and
                                                           io_name(v) != h32(v) else '')
            return T(f"Loaded {h32(v)}{what} into {rd}. The assembler stored this number just after "
                     "the code, because MOVS cannot make numbers this big.",
                     f"will load {h32(v)}{what} into {rd}, from the literal pool just after the code.") \
                if v > 255 else \
                T(f"Loaded the number {num(v)} into {rd}.", f"will load the number {num(v)} into {rd}.")
        if base.startswith('ldr') and ops:
            rd = _reg(ops[0])
            size = {'ldrb': 'a byte', 'ldrh': 'a halfword', 'ldrsb': 'a signed byte',
                    'ldrsh': 'a signed halfword'}.get(base, 'a word')
            via = ''
            m = re.match(r'\[(\w+)', ops[1]) if len(ops) > 1 else None
            if m and _reg(m.group(1)) in REG_NAMES:
                via = f"the address in {_reg(m.group(1))}" + (" plus an offset" if ',' in ops[1] else '')
            v = rv(rd, r1)
            io = run.mem_addr is not None and run.mem_addr >= 0x10000
            shown = num(v, io, self.charish(run, rd) or base == 'ldrb')
            return T(f"Loaded {size} from {place(run.mem_addr, via)} into {rd}: {rd} = {shown}.",
                     f"will load {size} from {place(run.mem_addr, via)} into {rd}, so {rd} becomes {shown}.")
        if base.startswith('str') and ops:
            rs = _reg(ops[0])
            part = {'strb': 'the low byte of ', 'strh': 'the low halfword of '}.get(base, '')
            v = rv(rs, r0)
            io = run.mem_addr is not None and run.mem_addr >= 0x10000
            s = T(f"Stored {part}{rs} ({num(v, io)}) into {place(run.mem_addr)}.",
                  f"will store {part}{rs} ({num(v, io)}) into {place(run.mem_addr)}.")
            if io:
                s += ' ' + self._pin_effect(run, future)
            return s
        if base == 'b':
            return T(f"Jumped to {self.where(pc1)}.", f"will jump to {self.where(pc1)}.")
        if cond:
            words, rule = COND[cond]
            seq = run.addr + 2
            taken = (pc1 & ~1) != seq
            fl = ', '.join(f"{k} = {f1[k]}" for k in COND_FLAGS[cond])
            s = T(f"Checked the condition “{words}” ({rule}). The flags say {fl}, so ",
                  f"will check the condition “{words}” ({rule}). The flags say {fl}, so ")
            if taken:
                s += T(f"it jumped to {self.where(pc1)}.", f"it will jump to {self.where(pc1)}.")
            else:
                s += T("it did not jump: the program carries on with the next line.",
                       "it will not jump: the program carries on with the next line.")
            return s
        if base in ('bl', 'blx'):
            tgt = pc1 & ~1
            return T(f"Called the function {self.where(tgt)}. LR = {h32(r1[14])}: the address of the "
                     "next line, plus 1 (a marker meaning \"Thumb instructions\", the only kind the Pico runs), so the "
                     "function knows where to come back to.",
                     f"will call the function {self.where(tgt)}, saving the address of the next line "
                     f"(plus 1) in LR as {h32(r1[14])} so the function knows where to come back to.")
        if base == 'bx':
            r = _reg(ops[0]) if ops else 'LR'
            if r == 'LR':
                return T(f"Returned from the function to {self.where(pc1 & ~1)}, the address in LR.",
                         f"will return from the function to {self.where(pc1 & ~1)}, the address in LR.")
            return T(f"Jumped to the address in {r}, {self.where(pc1 & ~1)}.",
                     f"will jump to the address in {r}, {self.where(pc1 & ~1)}.")
        if base == 'svc':
            n = _imm(ops[0]) if ops else None
            ch = r0[0] & 0xFF
            if n == 2:
                return T(f"Asked picosim to print the character in R0: {self._printed(chr(ch))} appeared in the "
                         "serial console. (This is the inside of putchar.)",
                         f"will ask picosim to print the character in R0, {self._printed(chr(ch))}, in the "
                         "serial console. (This is the inside of putchar.)")
            if n == 3:
                v = r1[0]
                return T(f"Read a key you typed: R0 = {num(v, char=True)}.", f"will read the key you typed into R0: {num(v, char=True)}.")
            return T(f"Asked the built-in OS for service #{n}.", f"will ask the built-in OS for service #{n}.")
        if base == 'bkpt':
            return T("Stopped the processor: the program is finished.",
                     "will stop the processor: the program will be finished.")
        if base in ('cmp', 'cmn', 'tst') and len(ops) >= 2:
            return self._compare(base, ops, r0, f1, future, "'" in self.line_of(run.addr)[1])
        s = self._data(base, ops, r0, r1, run, future)
        if s:
            return s
        line = self.words.summary(text, run.pre, run.post, run.cycles) if run.cycles else f"Ran {text}."
        return line if not future else "will do this: " + line[:1].lower() + line[1:]

    def _printed(self, out):
        shown = out.replace('\n', '\\n')
        if len(shown) > 40:
            shown = shown[:40] + '…'
        if out == '\n':
            return "a new line"
        return f"“{shown}”"

    def _pin_effect(self, run, future):
        if not run.pins:
            if io_name(run.mem_addr).endswith('_CTRL'):
                v = next((ch['new'] for ch in run.changes if ch['kind'] == 'io'), None)
                if v is not None and v & 0x1F == 5:
                    return "Function 5 (SIO) connects the pin to the SIO registers, so the program can drive it."
                return "This chooses which part of the chip controls the pin."
            return "No pin changes level." if not future else "No pin will change level."
        parts = []
        for pin, a, b, da, db in run.pins:
            lv = 'high (1)' if b else 'low (0)' if b == 0 else 'floating'
            if da != db and db == 'out':
                parts.append(f"GP{pin} {'becomes' if not future else 'will become'} an output, driven {lv}")
            elif da != db:
                parts.append(f"GP{pin} {'becomes' if not future else 'will become'} an input ({lv})")
            else:
                parts.append(f"GP{pin} {'turns' if not future else 'will turn'} "
                             f"{'on: high (1)' if b else 'off: low (0)' if b == 0 else 'floating'}")
        return _cap(_join(parts)) + '.'

    def _compare(self, base, ops, r0, f1, future, quoted=False):
        T = (lambda past, fut: fut) if future else (lambda past, fut: past)

        def val(tok):
            v = _imm(tok)
            if v is not None and tok.strip().startswith('#'):
                v &= 0xFFFFFFFF
                return v, (f"{v} ('{chr(v)}')" if quoted and 32 <= v < 127 else str(v))
            r = _reg(tok)
            x = r0[REG_NAMES.index(r)] if r in REG_NAMES else 0
            if base == 'tst' or 0x1000 <= x < 0xFFFF0000:
                return x, f"{r} (0x{x:X})"
            if quoted and 32 <= x < 127:
                return x, f"{r} ({x}, '{chr(x)}')"
            return x, f"{r} ({str(signed(x)).replace('-', '−')})"
        a, an = val(ops[0])
        b, bn = val(ops[1])
        if base == 'tst':
            res = a & b
            s = T(f"Tested {an} against {bn}: their AND is 0x{res:X}, ", f"will test {an} against {bn}: their AND is 0x{res:X}, ")
            s += "so Z = 1 (no bit in common)." if res == 0 else "so Z = 0 (some bit is 1 in both)."
            return s + T(" Nothing else changed.", " Nothing else changes.")
        if base == 'cmn':
            return T(f"Compared {an} with −{bn} by adding them. Only the flags changed: N={f1['N']} Z={f1['Z']} C={f1['C']} V={f1['V']}.",
                     f"will compare {an} with −{bn} by adding them. Only the flags change: N={f1['N']} Z={f1['Z']} C={f1['C']} V={f1['V']}.")
        sa, sb = signed(a), signed(b)
        diff = str(signed((a - b) & 0xFFFFFFFF)).replace('-', '−')
        if a == b:
            rel = "they are equal"
        elif (sa < sb) == (a < b):
            rel = f"they are not equal: {ops[0].upper() if _reg(ops[0]) in REG_NAMES else an} is {'smaller' if a < b else 'bigger'}"
        else:
            rel = ("they are not equal; as signed numbers the first is " + ('smaller' if sa < sb else 'bigger') +
                   ", as unsigned numbers it is " + ('smaller' if a < b else 'bigger'))
        s = T(f"Compared {an} with {bn} by working out {sa} − {sb} = {diff}. Nothing is stored; only the flags "
              f"changed (N={f1['N']} Z={f1['Z']} C={f1['C']} V={f1['V']}): {rel}. The branch after it uses them.",
              f"will compare {an} with {bn} by working out {sa} − {sb} = {diff}. Only the flags change "
              f"(they will be N={f1['N']} Z={f1['Z']} C={f1['C']} V={f1['V']}): {rel}.")
        return s

    def _data(self, base, ops, r0, r1, run, future):
        """Arithmetic, logic, shifts and moves: 'R2 = R0 + R1 = 3 + 4 = 7.'"""
        sets = base.endswith('s') and base[:-1] in ('mov', 'add', 'sub', 'adc', 'sbc', 'rsb', 'neg',
                                                    'and', 'orr', 'eor', 'bic', 'mvn', 'mul', 'lsl',
                                                    'lsr', 'asr', 'ror')
        op = base[:-1] if sets else base
        if not ops or _reg(ops[0]) not in REG_NAMES:
            return ''
        rd = _reg(ops[0])
        res = r1[REG_NAMES.index(rd)]
        hexy = op in BITWISE
        ch = self.charish(run, rd)
        T = (lambda past, fut: fut) if future else (lambda past, fut: past)

        def term(tok):
            v = _imm(tok)
            if v is not None and tok.strip().startswith('#'):
                return str(v), (f"0x{v:X}" if hexy else str(v))
            r = _reg(tok)
            if r in REG_NAMES:
                x = r0[REG_NAMES.index(r)]
                if hexy or 0x1000 <= x < 0x80000000:
                    return r, f"0x{x:X}"
                return r, str(signed(x)).replace('-', '−')
            return tok, tok
        if len(ops) == 2:
            a, b = ops[0], ops[1]
        else:
            a, b = ops[1], ops[2]
        (an, av), (bn, bv) = term(a), term(b)
        if op in ('mov',):
            if _reg(b) in REG_NAMES:
                s = T(f"Copied {bn} into {rd}: {rd} = {num(res, char=ch)}.",
                      f"will copy {bn} into {rd}, so {rd} = {num(res, char=ch)}.")
            else:
                s = T(f"Set {rd} to {num(res, char=ch)}.", f"will set {rd} to {num(res, char=ch)}.")
        elif op in ALU_EXPR:
            e = f"{an} {ALU_EXPR[op]} {bn}"
            ev = f"{av} {ALU_EXPR[op]} {bv}"
            if op in ('adc', 'sbc'):
                e += " with the carry"
            if e == ev:
                body = f"{rd} = {e} = {num(res, hexy, ch)}"
            else:
                body = f"{rd} = {e} = {ev} = {num(res, hexy, ch)}"
            s = T(f"Worked out {body}.", f"will work out {body}.")
        elif op in ('rsb', 'neg'):
            s = T(f"Made {bn} negative: {rd} = 0 − {bv} = {num(res)}.", f"will make {bn} negative: {rd} = 0 − {bv} = {num(res)}.")
        elif op == 'mvn':
            s = T(f"Flipped every bit of {bn}: {rd} = NOT {bv} = 0x{res:X}.", f"will flip every bit of {bn}: {rd} = 0x{res:X}.")
        elif op in ('lsl', 'lsr', 'asr', 'ror'):
            if len(ops) == 2:
                src, sh = ops[0], ops[1]
            else:
                src, sh = ops[1], ops[2]
            (sn, sv), (_, hv) = term(src), term(sh)
            word = {'lsl': 'left', 'lsr': 'right', 'asr': 'right (keeping the sign)', 'ror': 'right (rotating)'}[op]
            s = T(f"Shifted {sn} ({sv}) {word} by {hv}: {rd} = 0x{res:X}.",
                  f"will shift {sn} ({sv}) {word} by {hv}, so {rd} = 0x{res:X}.")
        else:
            return ''
        if sets:
            f0, f1 = run.pre['flags'], run.post['flags']
            if f1['Z'] and not f0['Z']:
                s += T(" The result is zero, so the Z flag turned on.", " The result will be zero, so the Z flag turns on.")
            elif f0['Z'] and not f1['Z']:
                s += T(" The result is not zero, so the Z flag turned off.", " The result will not be zero, so Z turns off.")
            if f1['N'] and not f0['N']:
                s += T(" It is negative, so N turned on.", " It will be negative, so N turns on.")
        return s

    # ── the detail lists ─────────────────────────────────────────────────────

    def changed_lines(self, run, future=False):
        """[[level, text]]: every register, flag, byte, pin and output the run changed."""
        out = []
        r0, r1 = run.pre['regs'], run.post['regs']
        verb = "will become" if future else "→"
        for i in range(15):
            if r0[i] != r1[i]:
                ch = self.charish(run, REG_NAMES[i])
                out.append([1, f"{REG_NAMES[i]}: {num(r0[i], i >= 13)} {verb} {num(r1[i], i >= 13, ch)}"])
        f0, f1 = run.pre['flags'], run.post['flags']
        for k in 'NZCV':
            if f0[k] != f1[k]:
                out.append([1, f"Flag {k}: {f0[k]} {verb} {f1[k]}. {FLAG_MEANING[k][0 if f1[k] else 1]}."])
        for ch in run.changes:
            if ch['kind'] == 'mem':
                w = ch['width']
                at = f"Memory at 0x{ch['addr']:X} ({w} byte{'s' if w > 1 else ''})"
                if ch['old'] == ch['new']:
                    out.append([2, f"{at}: {'will be written' if future else 'written'} with "
                                   f"0x{ch['new']:0{2 * w}X}, the value it already held"])
                else:
                    out.append([2, f"{at}: 0x{ch['old']:0{2 * w}X} {verb} 0x{ch['new']:0{2 * w}X}"])
            elif ch['kind'] == 'io':
                out.append([2, f"I/O register {io_name(int(ch['addr']))} ← {h32(ch['new'])}"])
        for pin, a, b, da, db in run.pins:
            show = lambda v, d: ('floating' if v is None else str(int(v))) + f" ({d}put)"
            out.append([2, f"Pin GP{pin}: {show(a, da)} {verb} {show(b, db)}"])
        if run.out:
            out.append([1, ("It will print " if future else "Printed ") + self._printed(run.out)])
        if not out:
            out.append([1, "No register, flag or memory value " + ("will change" if future else "changed") +
                        " apart from the PC."])
        return out

    def about(self, run):
        base = _mnemonic(run.text)
        ops = _operands(run.text)
        if base.startswith('ldr') and len(ops) >= 2 and 'pc' in ops[1].lower():
            return LITERAL_ABOUT
        if _cond(base):
            words, rule = COND[_cond(base)]
            return (f"B{_cond(base).upper()} (branch if {words}) jumps only when the flags say {rule}; "
                    "otherwise the next line runs. It never changes the flags itself.")
        return ABOUT.get(base, '')

    def pc_line(self, run, future=False):
        a, b = run.addr, run.post['regs'][15] & ~1
        size = 4 if any(cy['info']['state'] == 'FETCH2_IR' for cy in run.cycles) else 2
        if run.halted:
            return [1, "The processor stops here: the program is finished."]
        if b == a + size:
            return [1, f"PC: {a:04X} {'will move' if future else 'moved'} on to {b:04X}, the next instruction "
                       f"(this one is {size} bytes long)."]
        return [1, f"PC: {a:04X} {'will jump' if future else 'jumped'} to {self.where(b)}."]

    def cycle_line(self, run, future=False):
        if not run.cycles:
            return None
        names = [cy['info']['state'] for cy in run.cycles]
        return [3, f"On the teaching datapath this {'will take' if future else 'took'} {len(names)} clock "
                   f"cycles: {' → '.join(names)}."]

    def pipeline_line(self, run, core, future=False):
        """[4, …]: the instruction's cycles on the RP2040's own 2-stage pipeline."""
        try:
            hw1 = core.read16(run.addr)
            hw2 = core.read16(run.addr + 2) if timing.is_wide(hw1) else 0
        except (RuntimeError, ValueError):
            return None
        _, row, rule, n, _ = timing.pico_row(hw1, hw2)
        if rule is None:
            return [4, f"On the RP2040's pipeline: the datasheet (Table 81, “{row}”) gives no fixed cycle count."]
        if rule == 'ldst':
            sio = run.mem_addr is not None and 0xD0000000 <= run.mem_addr < 0xE0000000
            n, why = (1, " (1, not 2: the SIO is on the single-cycle I/O port)") if sio else (2, '')
        elif rule == 'mul':
            n, why = 1, " (the RP2040 has the fast multiplier)"
        elif rule == 'bcond':
            taken = (run.post['regs'][15] & ~1) != run.addr + 2
            n, why = (2, " (taken: 1 more to refill the pipeline)") if taken else (1, " (not taken)")
        else:
            why = ''
        return [4, f"On the RP2040's 2-stage pipeline it {'will take' if future else 'took'} {n} "
                   f"clock cycle{'s' if n != 1 else ''}{why}: datasheet Table 81, “{row}”."]

    def encoding_line(self, run, core):
        try:
            hw = core.read16(run.addr)
            if core.is_32bit_thumb(hw):
                return [2, f"In memory this instruction is 32 bits, 0x{hw:04X} 0x{core.read16(run.addr + 2):04X}, "
                           f"at address {run.addr:04X}."]
            return [2, f"In memory this instruction is the 16-bit number 0x{hw:04X}, at address {run.addr:04X}."]
        except (RuntimeError, ValueError):
            return None

    # ── why: what the next instruction is for ───────────────────────────────

    def purpose(self, run, core):
        """A sentence on why the program does this, from the line's comment
        and the shape of the code (None when nothing useful can be said)."""
        base = _mnemonic(run.text)
        ops = _operands(run.text)
        addr = run.addr
        after = self.prog.asm_map.get(addr + 2, '') if base not in ('bl',) else ''
        nxt = _mnemonic(after) if after else ''
        cond = _cond(base)
        tgt = run.post['regs'][15] & ~1
        why = ''
        if addr <= 4 and base in ('ldr', 'blx'):          # _start: the first three instructions
            why = "picosim's built-in OS is starting your program: it finds the address of main and calls it."
        elif base == 'bkpt' and addr < OS_TOP:
            why = "main has returned to the built-in OS, which stops the processor here."
        elif base == 'svc':
            why = "This is how putchar and getchar ask picosim to print a character or read a key."
        elif base == 'push' and self.label(addr):
            why = ("A function starts by saving LR and any of R4–R7 it will change, so it can put them "
                   "back and return safely at the end.")
        elif base == 'push':
            why = "It saves registers on the stack so their values can be put back later."
        elif base == 'pop' and ops and 'PC' in reg_list(ops[0]):
            why = "This ends the function: it puts back the registers saved at the start and returns to the caller."
        elif base in ('cmp', 'cmn', 'tst') and _cond(nxt):
            why = "It sets the flags so the branch on the next line can decide where to go."
        elif base in ('cmp', 'cmn', 'tst'):
            why = "It sets the flags for a later conditional branch."
        elif cond:
            target = _imm(ops[0]) if ops else None
            if target is not None and target <= addr:
                why = ("It decides whether to go round the loop again: it jumps back when the condition holds "
                       "and carries on to the next line when it does not.")
            else:
                why = "It decides whether to skip ahead (when the condition holds) or carry straight on."
        elif base == 'b':
            why = ("It goes back to the start of a loop, to repeat it." if tgt <= addr else
                   "It jumps over code that should not run here.")
        elif base in ('bl', 'blx'):
            why = "It calls a function: the function runs, then the program comes back to the line after this one."
        elif base == 'bx':
            why = "It returns from the function to the line after the call."
        elif base.startswith('ldr') and len(ops) >= 2 and 'pc' in ops[1].lower():
            v = run.post['regs'][REG_NAMES.index(_reg(ops[0]))] if _reg(ops[0]) in REG_NAMES else 0
            why = ("The program needs a number (often an address) too big for MOVS, so it loads it from a literal pool."
                   if v > 255 else "LDR Rd, = loads a named constant whatever its size, so the line works with any value.")
        elif base.startswith('str') and run.mem_addr is not None and run.mem_addr >= 0x10000:
            why = "Writing a hardware register is how a program controls the Pico's pins."
        elif base.startswith('ldr') and run.mem_addr is not None and run.mem_addr >= 0x10000:
            why = "Reading a hardware register is how a program finds out the level of the Pico's pins."
        elif base.startswith(('ldr', 'str')):
            why = "Values live in memory; the ALU only works on registers, so they are loaded and stored."
        elif ops and _reg(ops[0]) == 'R0' and nxt == 'bl':
            t = _imm(_operands(after)[0]) if _operands(after) else None
            fn = self.label(t) if t is not None else ''
            why = (f"R0 is where {fn} looks for the character to print." if fn == 'putchar' else
                   f"R0 is where a function looks for its first argument, and {fn} is called next." if fn else
                   "R0 is where a function looks for its first argument.")
        _, _, comment = self.line_of(addr)
        if comment and not comment.startswith('<-'):
            why = (why + " " if why else '') + f"The comment on this line says: “{comment}”."
        return why or None

    # ── the sections ─────────────────────────────────────────────────────────

    def instruction_now(self, run, core):
        detail = [[1, a] for a in [self.about(run)] if a]
        detail += [[1, "What changed:"]] + [[lv, "  • " + t] for lv, t in self.changed_lines(run)]
        detail.append(self.pc_line(run))
        for extra in (self.encoding_line(run, core), self.cycle_line(run), self.pipeline_line(run, core)):
            if extra:
                detail.append(extra)
        return {'head': self.head(run.addr, run.text), 'simple': self.say(run), 'detail': detail}

    def instruction_next(self, run, core):
        """The forecast as a section: what it will do, where the program
        goes after it, and why."""
        if run.fault:
            return {'head': self.head(run.addr, run.text),
                    'simple': f"This instruction will stop the program with a CPU fault: {run.fault}",
                    'why': "A real Pico would crash here too. Read the message, fix the program and Reload.",
                    'detail': []}
        simple = _cap(self.say(run, future=True))
        b = run.post['regs'][15] & ~1
        size = 4 if any(cy['info']['state'] == 'FETCH2_IR' for cy in run.cycles) else 2
        if run.waits:
            goes = "The program will stop there until you type a key; then it carries on with the next line."
        elif run.halted:
            goes = "After it the processor stops: the program is finished."
        elif run.whole_call or b == run.addr + size:
            nt = self.prog.asm_map.get(b, '')
            goes = f"After it the program carries on with the next instruction, {self.inline(b, nt)}." if nt else \
                "After it the program carries on with the next instruction."
        elif b < OS_TOP <= run.addr and _mnemonic(run.text) in ('bl', 'blx'):
            goes = f"After it the program is inside {self.where(b)}; the function comes back to the next line when it is done."
        elif b < OS_TOP <= run.addr:
            goes = "After it the program is back in the built-in OS."
        elif _mnemonic(run.text) in ('bx', 'pop'):
            goes = f"After it the program is back in the code that called the function, at {self.inline(b, self.prog.asm_map.get(b, ''))}."
        elif b < run.addr and b >= OS_TOP and run.addr >= OS_TOP:
            goes = f"After it the program goes back up to {self.inline(b, self.prog.asm_map.get(b, ''))}."
        else:
            goes = f"After it the program continues at {self.inline(b, self.prog.asm_map.get(b, ''))}."
        detail = [[1, a] for a in [self.about(run)] if a]
        if not run.waits:
            detail += [[1, "What will change:"]] + [[lv, "  • " + t] for lv, t in self.changed_lines(run, True)]
        for extra in (self.encoding_line(run, core), self.cycle_line(run, True),
                      self.pipeline_line(run, core, True)):
            if extra:
                detail.append(extra)
        return {'head': self.head(run.addr, run.text), 'simple': simple, 'goes': goes,
                'why': self.purpose(run, core), 'detail': detail}

    def cycle_now(self, info, pre, post, text, core):
        e = self.words.explain(info, pre, post, text)
        k, n = info['index'] + 1, info['total']
        of = f" of {n}" if n > 0 else ''     # the count is known once IR holds the instruction
        phase = info['phase'].capitalize()
        simple = f"Clock cycle {k}{of} of this instruction ({info['state']}, {phase} phase). {e['what']}"
        detail = [[3, s] for s in e['how']] + [[3, s if s.startswith('At the clock edge') else
                                                 "At the clock edge: " + s] for s in e['edge']]
        sig = ' '.join(info['signals'])
        if sig:
            detail.append([3, "Control signals on: " + sig])
        return {'head': self.head(info['insn_addr'], text) + f"   · cycle {k}{of}",
                'simple': simple, 'detail': detail}

    def cycle_next(self, core):
        st = core.next_state()
        phase, does = STATE_NEXT.get(st, ('', 'run the next state'))
        k = core.cycle_index + 1
        n = core.insn_total
        of = f" of {n}" if n > 0 else ''
        addr = core.insn_addr
        text = self.prog.asm_map.get(addr, '???')
        simple = f"The next clock cycle ({k}{of}) is {st}, in the {phase.capitalize()} phase: it will {does}."
        remain = ''
        if n > 0 and k == n:
            remain = "That is the last cycle of this instruction; after it the next instruction's fetch begins."
        elif n > 0:
            remain = f"After it, {n - k} more cycle{'s' if n - k != 1 else ''} finish{'es' if n - k == 1 else ''} this instruction."
        return {'head': self.head(addr, text) + f"   · cycle {k}{of}", 'simple': simple,
                'goes': remain, 'why': None, 'detail': []}


# ── a whole explanation for the server's last action ─────────────────────────

def _changed_values(srv, before):
    """[[level, text]] for every register and flag that differs from `before`."""
    c = srv.core
    r0, r1 = before['regs'], list(c.regs)
    out = [[1, f"{REG_NAMES[i]}: {num(r0[i], i >= 13)} → {num(r1[i], i >= 13)}"]
           for i in range(16) if r0[i] != r1[i]]
    for k in 'NZCV':
        v = getattr(c, k)
        if before['flags'][k] != v:
            out.append([1, f"Flag {k}: {before['flags'][k]} → {v}"])
    return out


def _history_run(srv, ex, before):
    """The instruction the last step ran, from the server's cycle history."""
    c = srv.core
    addr = before['pc']
    text = srv.prog.asm_map.get(addr, '???')
    post = snap(c)
    hist = [h for h in srv.history if h['insn_addr'] == addr]
    cycles = [{'info': h, 'post': post} for h in hist]
    mem = None
    for h in hist:
        if h['state'] == 'EVALUATE_ADDRESS':
            mem = next((ch['new'] for ch in h['changes'] if ch['name'] == 'MAR'), None)
    pins = _pin_changes(srv)
    return Run(text, addr, before['snap'], post, cycles, srv.out_text[before['out']:], pins,
               halted=c.halted, mem_addr=mem)


def _pin_changes(srv):
    before = {p['pin']: p for p in srv.gpio_before}
    return [(p['pin'], before[p['pin']]['level'], p['level'], before[p['pin']]['dir'], p['dir'])
            for p in srv.gpio_pins()
            if p['pin'] in before and (p['level'], p['dir']) != (before[p['pin']]['level'], before[p['pin']]['dir'])]


def _stop_reason(srv):
    c = srv.core
    if srv.fault:
        return "because of a CPU fault"
    if c.halted:
        return "because the program finished"
    if srv.waiting_input:
        return "because getchar is waiting for you to type a key"
    if not c.in_insn and c.pc in srv.breakpoints:
        return "at a breakpoint, just before this line runs"
    if not c.in_insn and c.pc == srv.prog.main_addr:
        return "at the first line of main"
    return "because you pressed Pause"


def build(srv):
    """The explanation of the server's last user action: {kind, now, next}.
    Each section is {head, simple, detail: [[level, text]], …}; see the
    module docstring."""
    c = srv.core
    ex = srv.explainer
    before = srv.before
    kind = srv.step_kind
    pipe = srv.machine == 'm0plus'
    now = None
    if kind == 'load':
        if srv.skip_boot:
            simple = ("Nothing of your program has run yet. picosim's built-in OS has already started it, "
                      "so the next instruction is the first line of main.")
        else:
            simple = ("Nothing has run yet. The CPU starts at address 0000, in picosim's built-in OS, "
                      "which sets things up and then calls your main.")
        now = {'head': "The program is loaded", 'simple': simple,
               'detail': [[1, "SP = 0x00010000: the stack starts at the top of RAM and grows down."],
                          [1, "Press Step instruction (S) to run the next instruction and see what it did."],
                          [3, "Press Step cycle (C) to run just one clock cycle of it instead."]]}
    elif kind == 'run':
        n = c.steps - before['steps']
        where = ex.head(c.insn_addr if c.in_insn else c.pc, srv.prog.asm_map.get(c.pc, ''))
        now = {'head': "Run stopped" if not c.halted else "Run finished",
               'simple': f"Run ran {n} instruction{'s' if n != 1 else ''} by itself and stopped {_stop_reason(srv)}. "
                         "Step from here to see each instruction explained.",
               'detail': [[3, f"That took {c.cycles - before['cycles']} clock cycles."]] +
                         ([[1, "Registers that changed during the run:"]] +
                          [[lv, "  • " + t] for lv, t in _changed_values(srv, before['snap'])]
                          if _changed_values(srv, before['snap']) else []) +
                         ([[1, f"Stopped at {where}."]] if not c.halted else [])}
    elif kind == 'back':
        now = {'head': "Stepped back",
               'simple': "Everything is back as it was before the last step: registers, flags, memory, pins "
                         "and the console. The instruction below runs again when you step.",
               'detail': [[1, "Values that went back:"]] + [[lv, "  • " + t] for lv, t in _changed_values(srv, before['snap'])]}
    elif kind == 'goto':
        n = c.steps - (1 if c.in_insn else 0) - (srv.boot_steps if srv.skip_boot else 0)
        cyc = f", {c.cycle_index} clock cycle{'s' if c.cycle_index != 1 else ''} into it" if c.in_insn else ''
        now = {'head': f"Jumped to step {n + 1}{cyc}",
               'simple': "The program has run up to here exactly as if you had stepped it: registers, flags, memory "
                         "and the console are what they would be. Step on from here (or Step back) to carry on.",
               'detail': [[1, "Values that changed:"]] + [[lv, "  • " + t] for lv, t in _changed_values(srv, before['snap'])]}
    elif kind == 'edit':
        what = sorted(srv.changed)
        vals = []
        for name in what:
            if name in REG_NAMES:
                vals.append(f"{name} to {num(c.regs[REG_NAMES.index(name)], REG_NAMES.index(name) >= 13)}")
            elif name in 'NZCV':
                vals.append(f"flag {name} to {getattr(c, name)}")
        pins = [f"pin GP{p} to {'floating' if b is None else int(b)}" for p, _, b, _, _ in _pin_changes(srv)]
        items = vals + pins + (["some bytes of memory"] if srv.mem_changed else [])
        now = {'head': "You changed a value",
               'simple': (f"You set {_join(items)}. " if items else "You changed a value. ") +
                         "The program has not moved: the next instruction works with the new value.",
               'detail': [[1, "Step back undoes this too, and Reload puts everything back as the file says."]]}
    elif kind in ('insn', 'step', 'cycle') and before is not None and \
            (srv.waiting_input or c.cycles == before['cycles']):
        now = {'head': ex.head(before['pc'], srv.prog.asm_map.get(before['pc'], '')),
               'simple': ("This is getchar's SVC #3: it cannot finish until you type a key, so the step "
                          "stopped here and waits." if srv.waiting_input else "Nothing ran."), 'detail': []}
    elif kind == 'cycle' and srv.history:
        info = srv.history[-1]
        text = srv.prog.asm_map.get(info['insn_addr'], '???')
        now = ex.cycle_now(info, before['snap'], snap(c), text, c)
        if info['last'] and srv.insn_before is not None:
            run = _history_run(srv, ex, srv.insn_before)
            now['simple'] += " That was the last cycle: the instruction is finished. " + ex.say(run)
    elif kind == 'call':
        call = ex.words_call_target(before['pc'], srv.prog.asm_map.get(before['pc'], ''), before['snap'])
        name = (ex.label(call[1]) or f"{call[1]:04X}") if call else "the function"
        run = Run(srv.prog.asm_map.get(before['pc'], '???'), before['pc'], before['snap'], snap(c),
                  out=srv.out_text[before['out']:], pins=_pin_changes(srv), whole_call=name, halted=c.halted)
        now = {'head': ex.head(before['pc'], run.text), 'simple': ex.say(run),
               'detail': [[1, a] for a in [ex.about(Run('bl', 0, before['snap'], before['snap']))] if a] +
                         [[1, "What changed:"]] + [[lv, "  • " + t] for lv, t in ex.changed_lines(run)]}
        if srv.running or (call and not c.in_insn and c.pc != call[0] and not c.halted):
            now['simple'] = (f"Step over started the call to {name}, but it stopped inside it "
                             f"{_stop_reason(srv)}.")
    elif kind == 'pipe':
        n = c.steps - before['steps']
        now = {'head': f"Pipeline cycle {srv.pipe.view().get('cycle', '')}",
               'simple': "The pipeline moved on one clock cycle. "
                         + ("An instruction finished executing. " if n else "No instruction finished in this cycle. ")
                         + "Here is what each stage holds now:",
               'detail': [], 'pipe_stages': True}
        if n == 1 and before is not None:
            run = _history_run(srv, ex, before)
            now['simple'] += ''
            now['detail'] = [[4, "The instruction that finished: " + ex.say(run)]]
    elif before is not None:                      # insn, step
        run = _history_run(srv, ex, before)
        now = ex.instruction_now(run, c)
        if srv.step_over_os and before['pc'] >= OS_TOP and c.halted and srv.fault is None:
            now['simple'] += " The OS then stopped the processor: the program is finished."
    if srv.fault:
        now = now or {'head': '', 'simple': '', 'detail': []}
        now['fault'] = (f"The CPU stopped with a fault: {srv.fault.rstrip('.')}. A real Pico would crash here too. "
                        "The console says what went wrong; fix the program, save it and press Reload.")

    # what comes next
    if c.halted:
        nxt = {'head': "Nothing: the program has finished",
               'simple': "The processor has stopped, so there is no next step." if srv.fault is None else
                         "The processor stopped because of the fault.",
               'goes': "Press Reload to start the program again from the beginning.", 'why': None, 'detail': []}
    elif srv.waiting_input or srv.needs_input():
        at = c.insn_addr if c.in_insn else c.pc
        if srv.step_over_os and at < OS_TOP and srv.stop_at is not None:
            at = srv.stop_at[0] - 4                   # Level 1: the program's own bl getchar line
        nxt = {'head': ex.head(at, srv.prog.asm_map.get(at, '')),
               'simple': "getchar is waiting for a key. Click in the serial console and type one: getchar "
                         "returns its character code in R0.",
               'goes': ("As soon as you type it, the program carries on by itself." if srv.running else
                        "After you type it, Step again (or press Run) to carry on."),
               'why': None, 'detail': []}
    elif c.in_insn and not pipe:
        nxt = ex.cycle_next(c)
    else:
        run = ex.forecast(c, srv.prog.cpu.gpio, srv.inp, srv.step_over_os and not pipe)
        nxt = ex.instruction_next(run, c)
    return {'kind': kind, 'now': now, 'next': nxt}
