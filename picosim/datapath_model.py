"""
The multi-cycle datapath assembled from components.py and driven only by
the FSM's control signals.

    model = Datapath(state_before_the_cycle, memory_read)
    predicted = model.cycle(signals)       # bus value + every change at the edge

`signals` is the list the cycle panel shows ("GatePC", "LD.MAR",
"ADDR1MUX=SP", "ALUK=ADD", …).  The model does not look at what the C++
simulator decided; tests/test_datapath_model.py checks that what it
predicts is exactly what the simulator did, on every cycle.

The FSM holds the selects it sets in DECODE (destination register, access
size) for the rest of the instruction, like a real control unit holds its
state outputs; `held` keeps them.
"""

from .components import (MASK, Register, RegisterFile, Flags, ALU, Memory, ImmExtract,
                         mux, adder, tri_state, bus, pc_read, align4, clr_thumb, set_thumb,
                         load_ext, store_align, cond_eval, VECTOR_SVCALL)

REG = {n: i for i, n in enumerate(RegisterFile.NAMES)}
REG['PC'] = 15


class Datapath:
    def __init__(self, regs, flags, dp, read, held=None):
        """regs: R0-R15; flags: {N,Z,C,V}; dp: {MAR, MDR, IR, IR2, ALU_A, ALU_B};
        read(addr, nbytes): memory; held: selects latched by DECODE."""
        self.pc = Register('PC', regs[15])
        self.rf = RegisterFile(regs)
        self.flags = Flags(**flags)
        self.mar = Register('MAR', dp['MAR'])
        self.mdr = Register('MDR', dp['MDR'])
        self.ir = Register('IR', dp['IR'], 16)
        self.ir2 = Register('IR2', dp['IR2'], 16)
        self.alu_a = Register('ALU_A', dp['ALU_A'])
        self.alu_b = Register('ALU_B', dp['ALU_B'])
        self.mem = Memory(read)
        self.held = dict(held or {})

    @classmethod
    def from_core(cls, core, held=None):
        """The datapath as the C++ core holds it right now."""
        rd = {1: core.read8, 2: core.read16, 4: core.read32}
        return cls(list(core.regs), {'n': core.N, 'z': core.Z, 'c': core.C, 'v': core.V},
                   core.datapath, lambda a, n: rd[n](a), held)

    @staticmethod
    def _sel(signals, prefix):
        for s in signals:
            if s.startswith(prefix):
                return s[len(prefix):]
        return None

    def cycle(self, state, signals):
        """Run one cycle; returns {'bus', 'changes', 'writes', 'taken'}."""
        sig = set(signals)
        sel = lambda p: self._sel(signals, p)
        ir, ir2 = self.ir.value, self.ir2.value
        f = self.flags.f
        if state == 'FETCH_ADDR':
            self.held = {}            # a new instruction: nothing latched yet
        if state == 'DECODE':
            # the FSM latches its selects for the rest of the instruction
            for p in ('DR=', 'SIZE='):
                if sel(p):
                    self.held[p] = sel(p)
            return {'bus': None, 'changes': [], 'writes': [], 'taken': None, 'held': self.held}

        pcr = pc_read(self.pc.value, ir)                         # PC / Align
        port = lambda name: self.rf.read(REG[name], pcr)         # register-file read port
        sr1 = sel('SR1=') or sel('SR=')
        sr2 = sel('SR2=')

        # address adder: ADDR1MUX + ADDR2MUX
        a1s, a2s = sel('ADDR1MUX='), sel('ADDR2MUX=')
        addr_sum = None
        if a1s and a2s:
            base = mux(a1s, {'PC': pcr, 'Align(PC,4)': align4(pcr),
                             'SR1': port(sr1) if sr1 else 0, 'SP': self.rf.r[13]})
            if a2s in ('SR2', '0', '-4n', '+4n'):
                off = {'SR2': port(sr2) if sr2 else 0, '0': 0,
                       '-4n': -ImmExtract.list_bytes(ir) & MASK,
                       '+4n': ImmExtract.list_bytes(ir)}[a2s]
            else:
                off = ImmExtract.offset(a2s, ir, ir2)
            addr_sum = adder(base, off)

        # ALU: A is the SR1 read port when SR1 is selected this cycle (PASS of
        # store data), otherwise the A latch; B is the B latch.
        op = sel('ALUK=')
        alu_out, alu_flags = None, {}
        if op:
            a = port(sr1) if sr1 and op == 'PASS' else self.alu_a.value
            alu_out, alu_flags = ALU.compute(op, a, self.alu_b.value, f['C'])

        # memory read (MEM.EN, R/W=READ) — fetches read a halfword
        size = 'halfword' if state.startswith('FETCH') and state != 'FETCH_OPERANDS' \
            else self.held.get('SIZE=', 'word')
        mem_data = self.mem.read(self.mar.value, size) if 'MEM.EN' in sig and 'R/W=READ' in sig else None

        # the bus: exactly one tri-state buffer may drive it
        lx = sel('LOAD EXT=')
        mdr_out = load_ext(self.mdr.value, lx[1:], lx[0] == 'S') if lx else self.mdr.value
        b = bus(tri_state('GatePC' in sig, self.pc.value),
                tri_state('GateADDR' in sig, addr_sum),
                tri_state('GateALU' in sig, alu_out),
                tri_state('GateMDR' in sig, mdr_out),
                tri_state('GateVEC' in sig, VECTOR_SVCALL))

        # COND EVAL for B<cond>
        taken = cond_eval(ir >> 8, f) if (ir >> 12) == 0b1101 and state == 'EXECUTE_PC' else None

        # next values of every storage element (captured at the clock edge)
        writes = []
        if 'MEM.EN' in sig and 'R/W=WRITE' in sig:
            writes.append((self.mar.value, Memory.SIZE[self.held.get('SIZE=', 'word')], self.mdr.value))
        new_pc = None
        if 'LD.PC' in sig:
            new_pc = mux(sel('PCMUX='), {'PC+2': adder(self.pc.value, 2), 'ADDER': addr_sum,
                                         'BUS': clr_thumb(b) if b is not None else None})
        new_mar = b if 'LD.MAR' in sig else (adder(self.mar.value, 4) if 'MAR+4' in sig else None)
        new_mdr = None
        if 'LD.MDR' in sig:
            new_mdr = mem_data if mem_data is not None else store_align(b, self.held.get('SIZE=', 'word'))
        dr = sel('DR=') or self.held.get('DR=')
        reg_writes = {}
        if 'LD.REG' in sig:
            reg_writes[REG[dr]] = b
        if 'LD.SP' in sig:
            reg_writes[13] = b
        if 'LD.LR' in sig:
            reg_writes[14] = set_thumb(b) if 'SET THUMB BIT' in sig else b
        sr2mux = mux('IMM' if 'SR2MUX=IMM' in sig else 'REG',
                     {'IMM': ImmExtract.alu_immediate(ir) if 'SR2MUX=IMM' in sig else None,
                      'REG': port(sr2) if sr2 else None})

        # the clock edge
        before = self._snapshot()
        self.pc.clock(new_pc is not None, new_pc if new_pc is not None else 0)
        self.mar.clock(new_mar is not None, new_mar if new_mar is not None else 0)
        self.mdr.clock(new_mdr is not None, new_mdr if new_mdr is not None else 0)
        self.ir.clock('LD.IR' in sig, b if b is not None else 0)
        self.ir2.clock('LD.IR2' in sig, b if b is not None else 0)
        self.alu_a.clock('LD.ALUA' in sig, port(sr1) if sr1 else 0)
        self.alu_b.clock('LD.ALUB' in sig, sr2mux if sr2mux is not None else 0)
        for n, v in reg_writes.items():
            self.rf.clock(True, n, v)
        self.flags.clock('LD.CC' in sig, alu_flags)
        after = self._snapshot()
        changes = [(k, before[k], after[k]) for k in before if before[k] != after[k]]
        return {'bus': b, 'changes': changes, 'writes': writes, 'taken': taken, 'held': self.held}

    def _snapshot(self):
        s = {RegisterFile.NAMES[i]: v for i, v in enumerate(self.rf.r)}
        s['PC'] = self.pc.value
        s.update(self.flags.f)
        for r in (self.mar, self.mdr, self.ir, self.ir2, self.alu_a, self.alu_b):
            s[r.name] = r.value
        return s
