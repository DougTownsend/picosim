"""
Program walkthrough for the user guide.

build() loads a fresh copy of a program (the GUI's machine is not touched),
clocks it cycle by cycle from reset and explains every instruction and every
cycle in plain words.  Each cycle also carries the machine state right after
its clock edge, so the GUI can draw the CPU diagram exactly as it looked
during that cycle.
"""

import re

from .loader import load_program, LoadError
from .cpu import SimulatorError

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
    'MOVW': 'pass the 16-bit immediate through', 'MOVT': 'replace the top half of A with the immediate',
}

GATE_NAMES = {'GatePC': ('gatePC', 'the PC'), 'GateADDR': ('gateAdder', "the address adder's sum"),
              'GateALU': ('gateALU', "the ALU's result"),
              'GateMDR': ('gateMDR', 'the memory data in MDR (through LOAD EXT)'),
              'GateVEC': ('gateVector', 'the exception-vector address')}


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


class Walkthrough:
    def __init__(self, path, limit=DEFAULT_LIMIT):
        self.prog = load_program(path)
        self.core = self.prog.cpu._core
        self.limit = limit
        self.output = ''
        self.core.svc_handler = self._svc

    def _svc(self, num):
        c = self.core
        if num == 2:
            self.output += chr(c.get_reg(0) & 0xFF)
        elif num == 0:
            self.output += c.get_mem_slice(c.get_reg(1) & 0xFFFF, c.get_reg(2)).decode('latin-1')
        elif num == 1:
            c.halted = True
        elif num == 3:
            c.set_reg(0, 0xFFFFFFFF)   # never reached: build() stops before getchar
        else:
            raise SimulatorError(f"Unknown SVC #{num}")

    def label(self, addr):
        return self.prog.sym_map.get(addr, '')

    def where(self, addr):
        lab = self.label(addr)
        return f"{lab} ({h32(addr)})" if lab else h32(addr)

    # ── run ──────────────────────────────────────────────────────────────────

    def build(self):
        c = self.core
        steps = []
        stopped = ''
        while True:
            if c.halted:
                stopped = "The processor has halted: the program is finished."
                break
            if len(steps) >= self.limit:
                stopped = (f"The walkthrough stops after the first {self.limit} instructions; "
                           "the program itself keeps going when you run it.")
                break
            pc = c.pc
            if c.read16(pc) == 0xDF03:
                stopped = ("Next the program calls getchar (SVC #3), which waits for you to type a key in the "
                           "serial console. The walkthrough stops here because what happens next depends on "
                           "that key; run the program to continue.")
                break
            pre = _snap(c)
            cycles = []
            try:
                while True:
                    info = c.step_cycle()
                    post = _snap(c)
                    cycles.append({'info': info, 'post': post})
                    if info['last'] or c.halted:
                        break
            except (RuntimeError, SimulatorError) as e:
                stopped = f"The CPU stopped with a fault: {e}"
                break
            text = self.prog.asm_map.get(pc, '???')
            step = {'addr': pc, 'text': text, 'label': self.label(pc),
                    'summary': self.summary(text, pre, post, cycles),
                    'cycles': []}
            prev = pre
            for cy in cycles:
                cy['text'] = self.explain(cy['info'], prev, cy['post'], text)
                prev = cy['post']
                step['cycles'].append(cy)
            steps.append(step)
        return {'type': 'walkthrough', 'path': self.prog.path, 'steps': steps,
                'stopped': stopped, 'output': self.output, 'limit': self.limit}

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
            return f"the I/O register at {h32(a)} (Pico hardware)" if a >= 0x10000 else f"memory address {h32(a)}"

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
            s = (f"Calls the function whose address is in {_reg(ops[0]) if ops else 'a register'} "
                 f"({self.where(pc1)}), saving the return address {h32(regs1[14])} in LR.")
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
        st = info['state']
        regs0 = pre['regs']
        dp0, dp1 = pre['datapath'], post['datapath']
        sig = info['signals']
        out = []
        intro = {
            'FETCH_ADDR': f"The fetch of the next instruction begins. The PC holds its address, {h32(regs0[15])}; "
                          "that address must go to MAR before memory can be read, and the PC is stepped on to the next "
                          "halfword in the same cycle.",
            'FETCH_MEMORY': f"Memory is read at the address in MAR ({h32(dp0['MAR'])}). The 16-bit instruction "
                            f"halfword stored there, {h16(dp1['MDR'])}, is captured in MDR.",
            'FETCH_IR': "The halfword in MDR is copied over the bus into the instruction register IR, where the control "
                        "unit (FSM) can read it.",
            'FETCH2_ADDR': "This is a 32-bit instruction, so a second fetch begins: the PC now points at its second "
                           "halfword, which goes to MAR, and the PC steps on again.",
            'FETCH2_MEMORY': f"Memory is read at MAR ({h32(dp0['MAR'])}): the second halfword, {h16(dp1['MDR'])}, "
                             "goes into MDR.",
            'FETCH2_IR': "The second halfword moves from MDR over the bus into IR2. Now IR and IR2 together hold the "
                         "whole 32-bit instruction.",
            'DECODE': "The FSM decodes the instruction bits in IR" + (" and IR2" if 'IR2' in str(info['desc']) else "") +
                      ": it works out which registers, immediate and operation this instruction uses, and which "
                      "states must follow. "
                      + (_cap(info['desc'].split(':', 1)[1].strip().rstrip('.')) + '. '
                         if info['desc'].startswith('Decode:') else '') +
                      "Nothing is stored in this cycle; the FSM only sets the register numbers and mux selects "
                      "that the next cycles use.",
            'EVALUATE_ADDRESS': "The address adder computes the memory address this instruction uses"
                                + (" (here the address itself is the result)." if not _has(info, 'LD.MAR') else '.'),
            'FETCH_OPERANDS': ("Memory is read at the address in MAR." if _has(info, 'R/W=READ') else
                               "The store data is prepared: the register's value travels through the ALU and the bus "
                               "into MDR." if _has(info, 'LD.MDR') else
                               "The source registers are read and loaded into the ALU's input latches A and B."),
            'EXECUTE_COMMIT': "The ALU computes the result from its latched inputs, and the result is committed "
                              "(written to its destination).",
            'STORE_RESULT': ("The memory write happens: MDR's value is stored at the address in MAR." if _has(info, 'R/W=WRITE')
                             else "The result is written to its destination register."),
            'EXECUTE_PC': "The PC is updated (or, for a branch that is not taken, left alone).",
            'LINK': "The return address is saved in LR so the called function can come back.",
            'WRITEBACK': "The base register (SP for PUSH/POP) is updated past the words that were transferred.",
            'SVC_CALL': "The supervisor call starts exception entry; picosim then performs the OS service directly.",
            'HALT': "The breakpoint stops the processor.",
        }.get(st, info['desc'])
        out.append(intro)

        # who drives the bus
        gate = next((g for g in GATE_NAMES if g in sig), None)
        if gate:
            name, what = GATE_NAMES[gate]
            v = info['bus']
            out.append(f"{gate} = 1 enables the {name} tri-state buffer, so {what}"
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
            kind = "an I/O register (Pico hardware)" if mar >= 0x10000 else "memory"
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
            out.append("Load enables: " + _join(loads) + ". They capture their inputs at the clock edge that ends "
                       "this cycle — during the cycle they still hold their old values.")

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
            out.append("At the clock edge: " + '; '.join(edge) + ".")
        elif st == 'DECODE':
            out.append("At the clock edge nothing is stored; the FSM simply moves to its next state.")
        else:
            out.append("At the clock edge no stored value changes"
                       + (" (MDR is loaded with the value it already held)." if _has(info, 'LD.MDR') else "."))
        return ' '.join(out)


def build(path, limit=DEFAULT_LIMIT):
    """Walk through `path` from reset; returns the message the GUI shows."""
    try:
        return Walkthrough(path, limit).build()
    except LoadError as e:
        return {'type': 'walkthrough', 'path': path, 'steps': [], 'stopped': f"Could not load: {e}",
                'output': '', 'limit': limit}
