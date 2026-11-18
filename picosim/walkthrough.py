"""
Program walkthrough, for the walkthrough panel beside the CPU diagram.

build() runs a fresh copy of a program (the GUI's machine is not touched),
clocks it cycle by cycle from reset and explains every instruction and every
cycle in plain words.  Each cycle also carries the machine state right after
its clock edge, so the GUI can draw the CPU diagram exactly as it looked
during that cycle.  The copy is made from the program already loaded when
there is one (Program.clone(): no assembling), so the walkthrough always
matches what is loaded, even after a save that does not build.

compact_steps() yields the panel's compact rows one at a time without the
snapshots, so the server can send the first page at once.
"""

import re

from .loader import load_program, LoadError
from .cpu import SimulatorError, svc_write_data
from .memmap import is_memory

REG_NAMES = ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10',
             'R11', 'R12', 'SP', 'LR', 'PC']
DEFAULT_LIMIT = 200

COND = {
    'eq': ('equal', 'Z = 1'), 'ne': ('not equal', 'Z = 0'),
    'cs': ('carry set / unsigned ≥', 'C = 1'), 'hs': ('unsigned ≥', 'C = 1'),
    'cc': ('carry clear / unsigned <', 'C = 0'), 'lo': ('unsigned <', 'C = 0'),
    'mi': ('negative', 'N = 1'), 'pl': ('positive or zero', 'N = 0'),
    'vs': ('overflow', 'V = 1'), 'vc': ('no overflow', 'V = 0'),
    'hi': ('unsigned >', 'C = 1 and Z = 0'), 'ls': ('unsigned ≤', 'C = 0 or Z = 1'),
    'ge': ('signed ≥', 'N = V'), 'lt': ('signed <', 'N ≠ V'),
    'gt': ('signed >', 'Z = 0 and N = V'), 'le': ('signed ≤', 'Z = 1 or N ≠ V'),
}

ALU_WORDS = {
    'ADD': 'add its inputs', 'SUB': 'subtract B from A', 'ADC': 'add A, B and the carry flag',
    'SBC': 'subtract B and the borrow from A', 'RSB': 'subtract A from B', 'NEG': 'negate B (0 − B)',
    'AND': 'AND the bits of A and B', 'ORR': 'OR the bits of A and B', 'EOR': 'exclusive-OR A and B',
    'BIC': 'clear in A the bits that are set in B', 'MVN': 'invert every bit of B', 'MOV': 'pass B through',
    'LSL': 'shift A left by B bits', 'LSR': 'shift A right by B bits (filling with 0)',
    'ASR': 'shift A right by B bits (copying the sign bit)', 'ROR': 'rotate A right by B bits',
    'MUL': 'multiply A by B', 'CMP': 'subtract B from A to compare them',
    'CMN': 'add A and B to compare them', 'TST': 'AND A and B to test bits',
    'PASS': 'pass its input straight through', 'SXTB': 'sign-extend the low byte of B',
    'SXTH': 'sign-extend the low halfword of B', 'UXTB': 'zero-extend the low byte of B',
    'UXTH': 'zero-extend the low halfword of B', 'REV': 'reverse the byte order of B',
    'REV16': 'reverse the bytes in each halfword of B', 'REVSH': 'reverse the low two bytes of B and sign-extend',
}

GATE_NAMES = {'GatePC': ("the PC's", 'the PC'), 'GateADDR': ("the address adder's", "the address adder's sum"),
              'GateALU': ("the ALU's", "the ALU's result"),
              'GateMDR': ("MDR's", 'the memory data in MDR (through LOAD EXT)'),
              'GateVEC': ("the vector block's", 'the exception-vector address')}


def h32(v):
    return f"0x{v & 0xFFFFFFFF:08X}"


def h16(v):
    return f"0x{v & 0xFFFF:04X}"


def _snap(core):
    return {'regs': list(core.regs),
            'flags': {'N': core.N, 'Z': core.Z, 'C': core.C, 'V': core.V},
            'datapath': dict(core.datapath)}


def _sig(info, prefix):
    """Value of the first signal starting with `prefix` (or None)."""
    for s in info['signals']:
        if s.startswith(prefix):
            return s[len(prefix):]
    return None


def _has(info, name):
    return name in info['signals']


def _join(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def _operands(text):
    """Split 'ldr r0, [r1, #4]' into ['r0', '[r1, #4]'] (brackets kept whole)."""
    parts = text.split(None, 1)
    if len(parts) < 2:
        return []
    out, depth, cur = [], 0, ''
    for ch in parts[1]:
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _cap(s):
    return s[:1].upper() + s[1:]


def _reg(tok):
    t = tok.strip().lower()
    return {'sp': 'SP', 'lr': 'LR', 'pc': 'PC', 'r13': 'SP', 'r14': 'LR', 'r15': 'PC',
            'sb': 'R9', 'sl': 'R10', 'fp': 'R11', 'ip': 'R12'}.get(t, t.upper())


def _imm(tok):
    t = tok.strip().lstrip('#')
    try:
        return int(t, 0)
    except ValueError:
        return None


def _opnd(tok, regs):
    """Describe an operand in words: 'R1 (0x00000005)' or 'the number 5'."""
    v = _imm(tok)
    if v is not None and tok.strip().startswith('#'):
        return f"the number {v}" + (f" ({h32(v)})" if v > 9 else '')
    r = _reg(tok)
    if r in REG_NAMES:
        return f"{r} ({h32(regs[REG_NAMES.index(r)])})"
    return tok


def _intro(info, regs0, dp0, dp1):
    """The first sentence of a cycle's explanation: what the cycle is for.
    Only the sentence for this cycle's state is built (the compact
    walkthrough asks for one per cycle)."""
    st = info['state']
    if st == 'FETCH_ADDR':
        return (f"The fetch of the next instruction begins. The PC holds its address, {h32(regs0[15])}; "
                "that address must go to MAR before memory can be read, and the PC is stepped on to the next "
                "halfword in the same cycle.")
    if st == 'FETCH_MEMORY':
        return (f"Memory is read at the address in MAR ({h32(dp0['MAR'])}). The 16-bit instruction "
                f"halfword stored there, {h16(dp1['MDR'])}, is captured in MDR.")
    if st == 'FETCH_IR':
        return ("The halfword in MDR is copied over the bus into the instruction register IR, where the control "
                "unit (FSM) can read it.")
    if st == 'FETCH2_ADDR':
        return ("This is a 32-bit instruction, so a second fetch begins: the PC now points at its second "
                "halfword, which goes to MAR, and the PC steps on again.")
    if st == 'FETCH2_MEMORY':
        return (f"Memory is read at MAR ({h32(dp0['MAR'])}): the second halfword, {h16(dp1['MDR'])}, "
                "goes into MDR.")
    if st == 'FETCH2_IR':
        return ("The second halfword moves from MDR over the bus into IR2. Now IR and IR2 together hold the "
                "whole 32-bit instruction.")
    if st == 'DECODE':
        return ("The FSM decodes the instruction bits in IR" + (" and IR2" if 'IR2' in str(info['desc']) else "") +
                ": it works out which registers, immediate and operation this instruction uses, and which "
                "states must follow. "
                + (_cap(info['desc'].split(':', 1)[1].strip().rstrip('.')) + '. '
                   if info['desc'].startswith('Decode:') else '') +
                "Nothing is stored in this cycle; the FSM only sets the register numbers and mux selects "
                "that the next cycles use.")
    if st == 'EVALUATE_ADDRESS':
        return ("The address adder computes the memory address this instruction uses"
                + (" (here the address itself is the result)." if not _has(info, 'LD.MAR') else '.'))
    if st == 'FETCH_OPERANDS':
        return ("Memory is read at the address in MAR." if _has(info, 'R/W=READ') else
                "The store data is prepared: the register's value travels through the ALU and the bus "
                "into MDR." if _has(info, 'LD.MDR') else
                "The source registers are read and loaded into the ALU's input latches A and B.")
    if st == 'STORE_RESULT':
        return ("The memory write happens: MDR's value is stored at the address in MAR." if _has(info, 'R/W=WRITE')
                else "The result is written to its destination register.")
    return _INTRO.get(st, info['desc'])


_INTRO = {
    'EXECUTE_COMMIT': "The ALU computes the result from its latched inputs, and the result is committed "
                      "(written to its destination).",
    'EXECUTE_PC': "The PC is updated (or, for a branch that is not taken, left alone).",
    'LINK': "The return address is saved in LR so the called function can come back.",
    'WRITEBACK': "The base register (SP for PUSH/POP) is updated past the words that were transferred.",
    'SVC_CALL': "The supervisor call starts exception entry; picosim then performs the OS service directly.",
    'HALT': "The breakpoint stops the processor.",
}


class Walkthrough:
    def __init__(self, path=None, limit=DEFAULT_LIMIT, keys=(), prog=None):
        # a copy of a program that is already loaded (no assembling), or the file
        self.prog = prog.clone() if prog is not None else load_program(path)
        self.core = self.prog.cpu._core
        self.limit = limit
        self.output = ''
        self.stopped = ''
        self.keys = list(keys)          # keys getchar returns, in order (the student's typing)
        self.core.svc_handler = self._svc

    @classmethod
    def describer(cls, prog):
        """Only the wording (summary, explain) for a program that is already
        loaded; its machine is left alone (explain.py uses this)."""
        w = cls.__new__(cls)
        w.prog = prog
        return w

    def _svc(self, num):
        c = self.core
        if num == 2:
            self.output += chr(c.get_reg(0) & 0xFF)
        elif num == 0:
            self.output += svc_write_data(c).decode('latin-1')
        elif num == 1:
            c.halted = True
        elif num == 3:
            c.set_reg(0, self.keys.pop(0) if self.keys else 0xFFFFFFFF)   # build() stops before a getchar with no key
        else:
            raise SimulatorError(f"Unknown SVC #{num}")

    def label(self, addr):
        return self.prog.sym_map.get(addr, '')

    def where(self, addr):
        lab = self.label(addr)
        return f"{lab} ({h32(addr)})" if lab else h32(addr)

    # ── run ──────────────────────────────────────────────────────────────────

    def _instructions(self, full):
        """Run the copy one instruction at a time, clocking every cycle, and
        yield (pc, pre, cycles) per instruction; self.stopped says why it
        ended.  With `full`, each cycle carries the whole machine after its
        clock edge; otherwise only the datapath (what the compact form's
        one sentence per cycle needs), and the last one the registers."""
        c = self.core
        n = 0
        while True:
            if c.halted:
                self.stopped = "The processor has halted: the program is finished."
                return
            if n >= self.limit:
                self.stopped = (f"The walkthrough stops after the first {self.limit} instructions; "
                                "the program itself keeps going when you run it.")
                return
            pc = c.pc
            if is_memory(pc) and c.read16(pc) == 0xDF03 and not self.keys:
                self.stopped = ("Next the program calls getchar (SVC #3), which waits for you to type a key in the "
                                "serial console. The walkthrough stops here because what happens next depends on "
                                "that key; run the program to continue.")
                return
            pre = _snap(c)
            cycles = []
            try:
                while True:
                    info = c.step_cycle()
                    cycles.append({'info': info, 'post': _snap(c) if full else {'datapath': c.datapath}})
                    if info['last'] or c.halted:
                        break
            except (RuntimeError, SimulatorError) as e:
                self.stopped = f"The CPU stopped with a fault: {e}"
                return
            if not full:
                cycles[-1]['post'] = _snap(c)
            n += 1
            yield pc, pre, cycles

    def steps(self, compact=False):
        """Each instruction as a walkthrough step (see build); compact steps
        are what compact() would make of them, built without the detail."""
        prev_dp = None
        for pc, pre, cycles in self._instructions(not compact):
            text = self.prog.asm_map.get(pc, '???')
            step = {'addr': pc, 'text': text, 'label': self.label(pc),
                    'summary': self.summary(text, pre, cycles[-1]['post'], cycles), 'cycles': []}
            if compact:
                regs0 = pre['regs']
                dp0 = pre['datapath']
                for cy in cycles:
                    info = cy['info']
                    dp1 = cy['post']['datapath']
                    step['cycles'].append({'state': info['state'], 'phase': info['phase'],
                                           'what': _intro(info, regs0, dp0, dp1)})
                    regs0, dp0 = (cy['post']['regs'] if 'regs' in cy['post'] else regs0), dp1
            else:
                prev = pre
                for cy in cycles:
                    cy.update(self.explain(cy['info'], prev, cy['post'], text))
                    prev = cy['post']
                    step['cycles'].append(cy)
            yield step

    def pipeline_steps(self, compact=False):
        """The same run on the RP2040's 2-stage pipeline (see build_pipeline);
        compact steps carry each Execute cycle's name, worked out from the
        pipeline's schedule rather than a picture of every cycle."""
        from .pipeline import Pipeline
        c = self.core
        pipe = Pipeline(c, self.prog.asm_map)
        regs = lambda: {'regs': list(c.regs), 'flags': {'N': c.N, 'Z': c.Z, 'C': c.C, 'V': c.V}}
        if compact:
            first = [{'state': 'first fetch', 'phase': 'FETCH', 'what': '', 'cycle': 0}]
        else:
            first = [{'view': pipe.view(-1), 'post': regs()}]
        for pc, pre, cycles in self._instructions(not compact):
            pipe.sync()
            row = [r for r in pipe.rows if not r['flushed']][-1]
            text = self.prog.asm_map.get(pc, '???')
            step = {'addr': pc, 'text': text, 'label': self.label(pc),
                    'summary': self.summary(text, pre, cycles[-1]['post'], cycles),
                    'row': row['row'], 'flushes': row['refill'] > 0}
            if compact:
                views = []
                for u in range(row['start'], row['start'] + row['cycles']):
                    k = u - row['start'] + 1
                    name, kind = f"Execute {k}/{row['cycles']}", 'EXECUTE'
                    if row['refill'] > 0 and k == row['exec']:
                        name, kind = name + ' · taken', 'TAKEN'
                    elif k > row['exec']:
                        name, kind = name + ' · refill', 'DECODE'
                    views.append({'state': name, 'phase': kind, 'what': '', 'cycle': u + 1})
            else:
                post = regs()
                views = [{'view': pipe.view(u), 'post': post}
                         for u in range(row['start'], row['start'] + row['cycles'])]
            step['cycles'] = first + views
            first = []
            yield step

    def build(self):
        steps = list(self.steps())
        return {'type': 'walkthrough', 'path': self.prog.path, 'steps': steps,
                'stopped': self.stopped, 'output': self.output, 'limit': self.limit}

    def build_pipeline(self):
        """The same run on the RP2040's 2-stage pipeline.  Each step is one
        instruction with a picture of the pipeline in every cycle it spends
        in Execute; the first step also starts with the very first fetch."""
        steps = list(self.pipeline_steps())
        return {'type': 'pipe_walkthrough', 'path': self.prog.path, 'steps': steps,
                'stopped': self.stopped, 'output': self.output, 'limit': self.limit}

    # ── one-line overview of an instruction ──────────────────────────────────

    def summary(self, text, pre, post, cycles):
        regs0, regs1 = pre['regs'], post['regs']
        ops = _operands(text)
        mn = text.split()[0].lower() if text.strip() else ''
        base = re.sub(r'\.(w|n)$', '', mn)
        wide = any(x['info']['state'] == 'FETCH2_IR' for x in cycles)
        seq = cycles[0]['info']['insn_addr'] + (4 if wide else 2)
        pc1 = regs1[15]
        mem_addr = None
        for cy in cycles:
            if cy['info']['state'] == 'EVALUATE_ADDRESS':
                mem_addr = cy['post']['datapath']['MAR']
        changes = [ch for cy in cycles for ch in cy['info']['changes']]

        def rset(r):
            return regs1[REG_NAMES.index(r)] if r in REG_NAMES else 0

        def place(a):
            if a is None:
                return 'memory'
            return f"the I/O register at {h32(a)} (Pico hardware)" if not is_memory(a) else f"memory address {h32(a)}"

        s = ''
        cond = None
        m = re.match(r'^b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)$', base)
        if m:
            cond = m.group(1)
        if base in ('push',):
            s = (f"Saves {ops[0] if ops else 'registers'} on the stack: SP moves down from "
                 f"{h32(regs0[13])} to {h32(regs1[13])} and the values are written there.")
        elif base in ('pop',):
            s = f"Restores {ops[0] if ops else 'registers'} from the stack (SP {h32(regs0[13])} → {h32(regs1[13])})."
            if 'pc' in text.lower():
                s += f" Loading PC returns from the function to {self.where(pc1)}."
        elif base.startswith('ldm') or base.startswith('stm'):
            s = (("Loads " if base.startswith('ldm') else "Stores ") + (ops[1] if len(ops) > 1 else 'registers') +
                 f" {'from' if base.startswith('ldm') else 'to'} consecutive words starting at {h32(regs0[REG_NAMES.index(_reg(ops[0].rstrip('!')))] if ops else 0)}.")
        elif base.startswith('ldr') and len(ops) >= 2 and ('pc' in ops[1].lower() or '=' in ops[1]):
            s = (f"Loads the constant {h32(rset(_reg(ops[0])))} from the literal pool at {h32(mem_addr or 0)} "
                 f"into {_reg(ops[0])}.")
        elif base.startswith('ldr'):
            size = {'ldrb': 'a byte', 'ldrh': 'a halfword', 'ldrsb': 'a signed byte',
                    'ldrsh': 'a signed halfword'}.get(base, 'a word')
            s = f"Loads {size} from {place(mem_addr)} into {_reg(ops[0])}: {_reg(ops[0])} = {h32(rset(_reg(ops[0])))}."
        elif base.startswith('str'):
            size = {'strb': 'the low byte of ', 'strh': 'the low halfword of '}.get(base, '')
            r = _reg(ops[0])
            s = f"Stores {size}{r} ({h32(regs0[REG_NAMES.index(r)])}) to {place(mem_addr)}."
        elif base == 'adr' or (base.startswith('add') and len(ops) >= 2 and ops[1].lower() == 'pc'):
            s = f"Puts the address {h32(rset(_reg(ops[0])))} into {_reg(ops[0])}."
        elif base in ('b',):
            s = f"Jumps to {self.where(pc1)}."
        elif cond:
            words, rule = COND[cond]
            taken = pc1 != seq
            f = post['flags']
            s = (f"Branches if {words} ({rule}). The flags are N={f['N']} Z={f['Z']} C={f['C']} V={f['V']}, so the "
                 f"branch is {'taken: execution continues at ' + self.where(pc1) if taken else 'not taken: execution continues with the next instruction'}.")
        elif base == 'bl':
            s = (f"Calls the function at {self.where(pc1)}: saves the return address "
                 f"{h32(regs1[14])} (bit 0 = Thumb) in LR, then jumps there.")
        elif base == 'blx':
            s = (f"Calls the function whose address is in {_reg(ops[0]) if ops else 'a register'}, "
                 f"{self.where(pc1)}, saving the return address {h32(regs1[14])} in LR.")
        elif base == 'bx':
            r = _reg(ops[0]) if ops else 'LR'
            s = (f"Returns from the function: jumps to the address in LR, {self.where(pc1)}." if r == 'LR'
                 else f"Jumps to the address in {r}, {self.where(pc1)}.")
        elif base == 'svc':
            n = _imm(ops[0]) if ops else None
            ch = regs0[0] & 0xFF
            s = {2: f"Asks the OS to print the character in R0 ({repr(chr(ch)) if 32 <= ch < 127 else h32(ch)}) — this is putchar.",
                 3: "Asks the OS for a typed character (getchar).",
                 1: "Asks the OS to end the program."}.get(n, f"Supervisor call #{n}.")
        elif base == 'bkpt':
            s = "Breakpoint: the processor halts here, so the program is finished."
        elif base.startswith(('cmp', 'cmn', 'tst')) and len(ops) >= 2:
            f = post['flags']
            verb = {'cmp': 'Compares', 'cmn': 'Compares (by adding)', 'tst': 'Tests the bits of'}[base[:3]]
            s = (f"{verb} {_opnd(ops[0], regs0)} with {_opnd(ops[1], regs0)}. Only the flags change: "
                 f"N={f['N']} Z={f['Z']} C={f['C']} V={f['V']}, for a following conditional branch to use.")
        else:
            s = self._data_summary(base, ops, regs0, regs1, pre, post) or f"Executes {text}."
        return s

    def _data_summary(self, base, ops, regs0, regs1, pre, post):
        setsflags = base.endswith('s') and base[:-1] in (
            'mov', 'add', 'sub', 'adc', 'sbc', 'rsb', 'neg', 'and', 'orr', 'eor', 'bic', 'mvn',
            'mul', 'lsl', 'lsr', 'asr', 'ror')
        op = base[:-1] if setsflags else base
        if not ops:
            return ''
        rd = _reg(ops[0])
        if rd not in REG_NAMES:
            return ''
        res = regs1[REG_NAMES.index(rd)]
        if len(ops) == 2:
            a, b = ops[0], ops[1]
        else:
            a, b = ops[1], ops[2] if len(ops) > 2 else ops[1]
        A, B = _opnd(a, regs0), _opnd(b, regs0)
        phrase = {
            'mov': f"Copies {B} into {rd}",
            'add': f"Adds {B} to {A}", 'sub': f"Subtracts {B} from {A}",
            'adc': f"Adds {B} and the carry to {A}", 'sbc': f"Subtracts {B} and the borrow from {A}",
            'rsb': f"Subtracts {A} from {B}", 'neg': f"Negates {B}",
            'and': f"ANDs the bits of {A} with {B}", 'orr': f"ORs the bits of {A} with {B}",
            'eor': f"Exclusive-ORs {A} with {B}", 'bic': f"Clears in {A} the bits set in {B}",
            'mvn': f"Inverts every bit of {B}", 'mul': f"Multiplies {A} by {B}",
            'lsl': f"Shifts {A} left by {B.replace('the number ', '')} bits",
            'lsr': f"Shifts {A} right by {B.replace('the number ', '')} bits",
            'asr': f"Shifts {A} right (keeping the sign) by {B.replace('the number ', '')} bits",
            'ror': f"Rotates {A} right by {B.replace('the number ', '')} bits",
            'sxtb': f"Sign-extends the low byte of {B}", 'sxth': f"Sign-extends the low halfword of {B}",
            'uxtb': f"Zero-extends the low byte of {B}", 'uxth': f"Zero-extends the low halfword of {B}",
            'rev': f"Reverses the byte order of {B}",
            'rev16': f"Reverses the two bytes in each halfword of {B}",
            'revsh': f"Swaps the low two bytes of {B} and sign-extends the result",
        }.get(op)
        if phrase is None:
            return ''
        if op != 'mov':
            phrase += f" and puts the result in {rd}"
        s = phrase + f": {rd} = {h32(res)}."
        if setsflags:
            f0, f1 = pre['flags'], post['flags']
            s += " Flags N Z C V = " + ' '.join(str(f1[k]) for k in 'NZCV')
            s += '.' if f0 != f1 else ' (unchanged).'
        return s

    # ── a cycle in words ─────────────────────────────────────────────────────

    def explain(self, info, pre, post, text):
        """One cycle in words: {'what': the cycle's purpose, 'how': one sentence
        per part of the datapath involved, 'edge': what the clock edge stores
        (old → new) or why nothing is, 'text': all of it as one paragraph}."""
        st = info['state']
        regs0 = pre['regs']
        dp0, dp1 = pre['datapath'], post['datapath']
        sig = info['signals']
        out = []
        out.append(_intro(info, regs0, dp0, dp1))

        # who drives the bus
        gate = next((g for g in GATE_NAMES if g in sig), None)
        if gate:
            name, what = GATE_NAMES[gate]
            v = info['bus']
            out.append(f"{gate} = 1 turns on {name} tri-state buffer, so {what}"
                       + (f", {h32(v)}," if v is not None else '') + " is driven onto the bus. "
                       "Every other buffer stays off (high impedance).")
        elif st != 'DECODE':
            out.append("No tri-state buffer is enabled, so nothing drives the bus this cycle.")

        # register-file reads
        reads = [f"{s.split('=')[0]} reads {s.split('=')[1]} ({h32(regs0[REG_NAMES.index(s.split('=')[1])])})"
                 for s in sig if (s.startswith('SR1=') or s.startswith('SR2=')) and st != 'DECODE'
                 and s.split('=')[1] in REG_NAMES]
        if reads:
            out.append("The register file's read port " + _join(reads) + ".")

        # address adder
        a1, a2 = _sig(info, 'ADDR1MUX='), _sig(info, 'ADDR2MUX=')
        if a1 and a2 and st != 'DECODE':
            b1 = {'PC': f"the PC read value (instruction address + 4 = {h32(info['insn_addr'] + 4)})",
                  'Align(PC,4)': f"Align(PC,4) = {h32((info['insn_addr'] + 4) & ~3)}",
                  'SR1': 'the SR1 register', 'SP': f"SP ({h32(regs0[13])})"}.get(a1, a1)
            b2 = {'SR2': 'the SR2 register', '0': 'zero', '-4n': '−4 × (number of registers)',
                  '+4n': '+4 × (number of registers)'}.get(a2, 'the extended immediate ' + a2)
            out.append(f"ADDR1MUX selects {b1} and ADDR2MUX selects {b2}; the address adder adds them.")

        # ALU
        k = _sig(info, 'ALUK=')
        if k and st != 'DECODE':
            src = ''
            if _has(info, 'SR2MUX=IMM') and st == 'FETCH_OPERANDS':
                src = ' SR2MUX selects the immediate for the B input.'
            out.append(f"ALUK = {k} tells the ALU to {ALU_WORDS.get(k, k.lower())}.{src}")
        elif st == 'FETCH_OPERANDS' and _has(info, 'SR2MUX=IMM'):
            out.append("SR2MUX selects the immediate from IMM / OFFSET for the B input.")

        # memory
        if _has(info, 'MEM.EN'):
            mar = dp0['MAR']
            kind = "an I/O register (Pico hardware)" if not is_memory(mar) else "memory"
            if _has(info, 'R/W=READ'):
                out.append(f"MEM.EN and R/W = READ make {kind} read at MAR = {h32(mar)}; the data arrives on its "
                           "way into MDR.")
            else:
                out.append(f"MEM.EN and R/W = WRITE make {kind} store MDR ({h32(dp0['MDR'])}) at MAR = {h32(mar)}.")

        # PC selection / branch decision
        pm = _sig(info, 'PCMUX=')
        bt = _sig(info, 'BranchTaken=')
        if bt is not None:
            out.append("COND EVAL checks the flags against the branch condition and sends BranchTaken = "
                       f"{bt} to the FSM" + (", so it selects the branch target." if bt == '1' else
                                             f", so LD.PC stays off and the PC keeps {h32(regs0[15])}."))
        if pm and _has(info, 'LD.PC'):
            src = {'PC+2': f"the +2 incrementer's output ({h32(regs0[15])} + 2)",
                   'ADDER': "the branch target from the address adder",
                   'BUS': "the bus value with bit 0 cleared by CLR THUMB"}.get(pm, pm)
            out.append(f"PCMUX selects {src}.")
        if _has(info, 'SET THUMB BIT'):
            out.append("SET THUMB sets bit 0 of the bus value (marking Thumb code) on its way to LR.")
        lx = _sig(info, 'LOAD EXT=')
        if lx:
            out.append(f"LOAD EXT {'sign' if lx.startswith('S') else 'zero'}-extends the {lx[1:]} from MDR to 32 bits.")
        if _has(info, 'STORE ALIGN') and st == 'FETCH_OPERANDS':
            out.append("STORE ALIGN places the data in the right byte lanes for the access size.")

        # load enables
        dr = _sig(info, 'DR=')
        loads = []
        names = {'LD.MAR': 'MAR (LD.MAR)', 'LD.MDR': 'MDR (LD.MDR)', 'LD.IR': 'IR (LD.IR)',
                 'LD.IR2': 'IR2 (LD.IR2)', 'LD.PC': 'the PC (LD.PC)', 'LD.CC': 'the N Z C V flags (LD.CC)',
                 'LD.LR': 'LR (LD.LR)', 'LD.SP': 'SP (LD.SP)', 'LD.ALUA': 'ALU input A (LD.ALUA)',
                 'LD.ALUB': 'ALU input B (LD.ALUB)', 'MAR+4': 'MAR from its +4 incrementer (MAR+4)',
                 'LD.REG': f"register {dr} (LD.REG, DR = {dr})"}
        if st != 'DECODE':
            for s in sig:
                if s in names:
                    loads.append(names[s])
        if loads:
            out.append("Load enables: " + _join(loads) + ". They take their new values at the clock edge.")

        # what the edge does
        edge = []
        for ch in info['changes']:
            kind, nm, o, n = ch['kind'], ch['name'], ch['old'], ch['new']
            if kind == 'flag':
                edge.append(f"flag {nm} {o} → {n}")
            elif kind == 'mem':
                w = ch['width']
                edge.append(f"{nm} ({w} byte{'s' if w > 1 else ''}) {o:0{2 * w}X} → {n:0{2 * w}X}")
            elif kind == 'io':
                edge.append(f"I/O register {nm[3:-1]} ← {h32(n)}")
            elif nm in ('IR', 'IR2'):
                edge.append(f"{nm} {h16(o)} → {h16(n)}")
            else:
                edge.append(f"{nm.replace('ALU_', 'ALU ')} {h32(o)} → {h32(n)}")
        if edge:
            last = "At the clock edge: " + '; '.join(edge) + "."
        elif st == 'DECODE':
            last = "At the clock edge nothing is stored; the FSM simply moves to its next state."
        else:
            last = ("At the clock edge no stored value changes"
                    + (" (MDR is loaded with the value it already held)." if _has(info, 'LD.MDR') else "."))
        return {'what': out[0], 'how': out[1:], 'edge': edge if edge else [last],
                'text': ' '.join(out + [last])}


def build(path, limit=DEFAULT_LIMIT, keys=(), prog=None):
    """Walk through `path` from reset; returns the message the GUI shows.
    `keys` are what getchar reads, in order (the student's typing).  With
    `prog` (a loaded Program) a copy of it is walked instead, without
    assembling the file again."""
    try:
        return Walkthrough(path, limit, keys, prog).build()
    except LoadError as e:
        return {'type': 'walkthrough', 'path': path, 'steps': [], 'stopped': f"Could not load: {e}",
                'output': '', 'limit': limit}


def build_pipeline(path, limit=DEFAULT_LIMIT, keys=(), prog=None):
    """Walk through `path` on the 2-stage pipeline; the GUI's message."""
    try:
        return Walkthrough(path, limit, keys, prog).build_pipeline()
    except LoadError as e:
        return {'type': 'pipe_walkthrough', 'path': path, 'steps': [], 'stopped': f"Could not load: {e}",
                'output': '', 'limit': limit}


def compact_steps(prog, pipe=False, limit=DEFAULT_LIMIT, keys=()):
    """(walkthrough, generator of compact steps) for a copy of the loaded
    program `prog`: the walkthrough panel's rows, one at a time, so the
    server can send the first ones at once and build the rest between
    commands.  When the generator ends, the walkthrough's .stopped and
    .output are set."""
    w = Walkthrough(limit=limit, keys=keys, prog=prog)
    return w, (w.pipeline_steps(compact=True) if pipe else w.steps(compact=True))


def compact(wt):
    """The walkthrough without its machine snapshots, for the walkthrough
    panel beside the CPU diagram: each step's instruction and explanation,
    and per clock cycle a name, its phase and one sentence (a few kB per
    step instead of tens).  compact_steps() makes the same rows without
    building the snapshots first."""
    pipe = wt.get('type') == 'pipe_walkthrough'
    steps = []
    for s in wt['steps']:
        cycles = []
        for cy in s['cycles']:
            if pipe:
                e = cy['view'].get('E')
                if e is None:
                    cycles.append({'state': 'first fetch', 'phase': 'FETCH', 'what': '', 'cycle': cy['view'].get('cycle')})
                    continue
                name = f"Execute {e['k']}/{e['cycles']}"
                kind = 'EXECUTE'
                if e.get('redirect'):
                    name, kind = name + ' · taken', 'TAKEN'
                elif e.get('refilling'):
                    name, kind = name + ' · refill', 'DECODE'
                cycles.append({'state': name, 'phase': kind, 'what': '', 'cycle': cy['view'].get('cycle')})
            else:
                cycles.append({'state': cy['info']['state'], 'phase': cy['info']['phase'], 'what': cy.get('what', '')})
        step = {'addr': s['addr'], 'text': s['text'], 'label': s['label'], 'summary': s['summary'], 'cycles': cycles}
        if pipe:
            step.update(row=s.get('row', ''), flushes=s.get('flushes', False))
        steps.append(step)
    return dict(wt, steps=steps, compact=True)
