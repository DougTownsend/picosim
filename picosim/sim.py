#!/usr/bin/env python3
"""
ARMv6-M (Cortex-M0+) Simulator
Flat 16-bit address space, similar in spirit to the LC-3 simulator.
Loads ELF files compiled with arm-none-eabi-gcc -mthumb -mcpu=cortex-m0plus.
"""

import os
import sys
import struct
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
import capstone

# ── Constants ──────────────────────────────────────────────────────────────────

MEM_SIZE    = 0x10000   # 64 KiB (16-bit address space)
STACK_TOP   = 0x10000   # initial SP  (past end of address space — wraps fine)

# ARMv6-M condition codes
COND_EQ = 0x0; COND_NE = 0x1; COND_CS = 0x2; COND_CC = 0x3
COND_MI = 0x4; COND_PL = 0x5; COND_VS = 0x6; COND_VC = 0x7
COND_HI = 0x8; COND_LS = 0x9; COND_GE = 0xA; COND_LT = 0xB
COND_GT = 0xC; COND_LE = 0xD; COND_AL = 0xE

# ── Helpers ────────────────────────────────────────────────────────────────────

def s32(v): return v if v < 0x80000000 else v - 0x100000000
def u32(v): return v & 0xFFFFFFFF
def s8(v):  return v if v < 0x80 else v - 0x100
def sign_extend(v, bits): return v if not (v >> (bits-1)) else v - (1 << bits)


class SimulatorError(Exception):
    pass


# ── ELF loader ─────────────────────────────────────────────────────────────────

def load_elf(path):
    """
    Parse an ELF file and return:
      - memory bytes (bytearray, MEM_SIZE long)
      - entry point address
      - dict mapping address -> disassembly string
      - dict mapping address -> symbol name
    """
    memory = bytearray(MEM_SIZE)
    asm_map = {}      # addr -> "mnemonic  op_str"
    sym_map = {}      # addr -> name

    with open(path, 'rb') as f:
        elf = ELFFile(f)

        if elf.header.e_machine != 'EM_ARM':
            raise SimulatorError(f"Not an ARM ELF file: {path}")

        entry = elf.header.e_entry & 0xFFFFFFFE   # clear Thumb bit

        # ── collect symbols ──────────────────────────────────────────────────
        for section in elf.iter_sections():
            if isinstance(section, SymbolTableSection):
                for sym in section.iter_symbols():
                    if sym.name and sym['st_value']:
                        addr = sym['st_value'] & 0xFFFFFFFE
                        sym_map[addr] = sym.name

        # ── load LOAD segments into flat memory ──────────────────────────────
        for seg in elf.iter_segments():
            if seg.header.p_type != 'PT_LOAD':
                continue
            vaddr  = seg.header.p_vaddr
            filesz = seg.header.p_filesz
            memsz  = seg.header.p_memsz
            data   = seg.data()

            if vaddr + memsz > MEM_SIZE:
                raise SimulatorError(
                    f"Segment at 0x{vaddr:04X} size {memsz} overflows "
                    f"16-bit address space (max 0x{MEM_SIZE:X})"
                )
            memory[vaddr:vaddr + filesz] = data
            # bss (memsz > filesz) is already zero from bytearray

        # ── disassemble all executable sections ──────────────────────────────
        cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
        cs.detail = False

        for section in elf.iter_sections():
            if not (section['sh_flags'] & 0x4):   # SHF_EXECINSTR
                continue
            sh_addr = section['sh_addr']
            sh_size = section['sh_size']
            if sh_size == 0:
                continue
            code = bytes(memory[sh_addr:sh_addr + sh_size])
            for insn in cs.disasm(code, sh_addr):
                asm_map[insn.address] = f"{insn.mnemonic:<8} {insn.op_str}"

    return memory, entry, asm_map, sym_map


# ── CPU ────────────────────────────────────────────────────────────────────────

class CPU:
    def __init__(self, memory, entry, asm_map, sym_map, trace=False):
        self.mem     = memory          # bytearray, 64 KiB
        self.asm_map = asm_map
        self.sym_map = sym_map
        self.trace   = trace

        self.regs = [0] * 16          # R0-R15; R13=SP R14=LR R15=PC
        self.regs[13] = STACK_TOP     # SP
        self.regs[14] = 0xFFFFFFFF    # LR  (sentinel: return from main)
        self.regs[15] = entry         # PC

        # APSR flags
        self.N = 0
        self.Z = 0
        self.C = 0
        self.V = 0

        self.halted   = False
        self.steps    = 0
        self._itstate = 0   # IT block state (simple support)

    # ── register accessors ────────────────────────────────────────────────────

    @property
    def pc(self): return self.regs[15]
    @pc.setter
    def pc(self, v): self.regs[15] = u32(v)

    @property
    def sp(self): return self.regs[13]
    @sp.setter
    def sp(self, v): self.regs[13] = u32(v)

    @property
    def lr(self): return self.regs[14]
    @lr.setter
    def lr(self, v): self.regs[14] = u32(v)

    def reg(self, n):
        if n == 15: return (self.pc + 4) & ~3   # PC reads as PC+4 aligned
        return self.regs[n]

    def set_reg(self, n, v):
        if n == 15:
            self.pc = v & 0xFFFFFFFE
        else:
            self.regs[n] = u32(v)

    # ── memory access ─────────────────────────────────────────────────────────

    def mem_read8(self, addr):
        addr &= 0xFFFF
        return self.mem[addr]

    def mem_read16(self, addr):
        addr &= 0xFFFF
        return struct.unpack_from('<H', self.mem, addr)[0]

    def mem_read32(self, addr):
        addr &= 0xFFFF
        return struct.unpack_from('<I', self.mem, addr)[0]

    def mem_write8(self, addr, val):
        addr &= 0xFFFF
        self.mem[addr] = val & 0xFF

    def mem_write16(self, addr, val):
        addr &= 0xFFFF
        struct.pack_into('<H', self.mem, addr, val & 0xFFFF)

    def mem_write32(self, addr, val):
        addr &= 0xFFFF
        struct.pack_into('<I', self.mem, addr, u32(val))

    # ── flags ─────────────────────────────────────────────────────────────────

    def update_nz(self, result32):
        self.N = 1 if (result32 & 0x80000000) else 0
        self.Z = 1 if (result32 & 0xFFFFFFFF) == 0 else 0

    def update_nzcv_add(self, a, b, result):
        r32 = result & 0xFFFFFFFF
        self.N = 1 if (r32 & 0x80000000) else 0
        self.Z = 1 if r32 == 0 else 0
        self.C = 1 if result > 0xFFFFFFFF else 0
        sa = s32(a); sb = s32(b); sr = s32(r32)
        self.V = 1 if (sa > 0 and sb > 0 and sr < 0) or \
                     (sa < 0 and sb < 0 and sr > 0) else 0

    def update_nzcv_sub(self, a, b, result):
        r32 = result & 0xFFFFFFFF
        self.N = 1 if (r32 & 0x80000000) else 0
        self.Z = 1 if r32 == 0 else 0
        self.C = 1 if a >= b else 0   # borrow = NOT C
        sa = s32(a); sb = s32(b); sr = s32(r32)
        self.V = 1 if (sa > 0 and sb < 0 and sr < 0) or \
                     (sa < 0 and sb > 0 and sr > 0) else 0

    # ── condition check ───────────────────────────────────────────────────────

    def check_cond(self, cond):
        c = cond & 0xF
        if c == COND_EQ: return self.Z
        if c == COND_NE: return not self.Z
        if c == COND_CS: return self.C
        if c == COND_CC: return not self.C
        if c == COND_MI: return self.N
        if c == COND_PL: return not self.N
        if c == COND_VS: return self.V
        if c == COND_VC: return not self.V
        if c == COND_HI: return self.C and not self.Z
        if c == COND_LS: return not self.C or self.Z
        if c == COND_GE: return self.N == self.V
        if c == COND_LT: return self.N != self.V
        if c == COND_GT: return not self.Z and (self.N == self.V)
        if c == COND_LE: return self.Z or (self.N != self.V)
        return True  # AL

    # ── barrel shifter ────────────────────────────────────────────────────────

    def lsl(self, val, n, update_c=False):
        val = u32(val)
        if n == 0: return val, self.C
        if n >= 32: c = (val >> (32 - n)) & 1 if n == 32 else 0; return 0, c
        c = (val >> (32 - n)) & 1
        return u32(val << n), c

    def lsr(self, val, n, update_c=False):
        val = u32(val)
        if n == 0: return val, self.C
        if n >= 32: c = (val >> 31) & 1 if n == 32 else 0; return 0, c
        c = (val >> (n - 1)) & 1
        return val >> n, c

    def asr(self, val, n, update_c=False):
        val = u32(val)
        if n == 0: return val, self.C
        if n >= 32:
            c = (val >> 31) & 1
            return (0xFFFFFFFF if c else 0), c
        c = (val >> (n - 1)) & 1
        result = s32(val) >> n
        return u32(result), c

    def ror(self, val, n, update_c=False):
        val = u32(val)
        n = n & 31
        if n == 0: return val, (val >> 31) & 1
        result = u32((val >> n) | (val << (32 - n)))
        c = (result >> 31) & 1
        return result, c

    # ── fetch & decode ────────────────────────────────────────────────────────

    def fetch16(self):
        hw = self.mem_read16(self.pc)
        self.pc += 2
        return hw

    def is_32bit_thumb(self, hw):
        return (hw >> 11) in (0b11101, 0b11110, 0b11111)

    def fetch_instruction(self):
        hw1 = self.fetch16()
        if self.is_32bit_thumb(hw1):
            hw2 = self.fetch16()
            return (hw1 << 16) | hw2, True
        return hw1, False

    # ── step ──────────────────────────────────────────────────────────────────

    def step(self):
        if self.halted:
            return
        self.steps += 1
        insn_addr = self.pc
        if self.trace:
            sym = self.sym_map.get(insn_addr, "")
            sym_str = f" <{sym}>" if sym else ""
            asm = self.asm_map.get(insn_addr, "???")
            print(f"  0x{insn_addr:04X}{sym_str}: {asm}")
        raw, is32 = self.fetch_instruction()

        if is32:
            self._exec32(insn_addr, raw)
        else:
            self._exec16(insn_addr, raw & 0xFFFF)

    # ═════════════════════════════════════════════════════════════════════════
    #  16-bit Thumb instruction execution
    # ═════════════════════════════════════════════════════════════════════════

    def _exec16(self, addr, hw):
        top5 = (hw >> 11) & 0x1F
        top4 = (hw >> 12) & 0xF
        top6 = (hw >> 10) & 0x3F
        top7 = (hw >>  9) & 0x7F
        top8 = (hw >>  8) & 0xFF

        # ── Shift by immediate: LSL/LSR/ASR (top5 = 0,1,2) ─────────────────────
        # top5=0: LSL imm, top5=1: LSR imm, top5=2: ASR imm
        if top5 <= 0b00010:
            op  = (hw >> 11) & 0x3
            imm = (hw >>  6) & 0x1F
            rm  = (hw >>  3) & 0x7
            rd  =  hw        & 0x7
            if op == 0:    # LSL Rd, Rm, #imm5
                result, c = self.lsl(self.regs[rm], imm)
                if imm: self.C = c
                self.set_reg(rd, result); self.update_nz(result)
            elif op == 1:  # LSR Rd, Rm, #imm5
                n = imm if imm else 32
                result, c = self.lsr(self.regs[rm], n)
                self.C = c; self.set_reg(rd, result); self.update_nz(result)
            else:          # ASR Rd, Rm, #imm5
                n = imm if imm else 32
                result, c = self.asr(self.regs[rm], n)
                self.C = c; self.set_reg(rd, result); self.update_nz(result)
            return

        # ── ADD/SUB register and 3-bit immediate (top5 = 3 = 0b00011) ────────
        if top5 == 0b00011:
            op  = (hw >> 9) & 0x3
            rn_or_imm = (hw >> 6) & 0x7
            rn  = (hw >> 3) & 0x7
            rd  =  hw       & 0x7
            if op == 0:    # ADD Rd, Rn, Rm
                a = self.regs[rn]; b = self.regs[rn_or_imm]
                r = a + b; self.update_nzcv_add(a, b, r)
                self.set_reg(rd, r)
            elif op == 1:  # SUB Rd, Rn, Rm
                a = self.regs[rn]; b = self.regs[rn_or_imm]
                r = a - b; self.update_nzcv_sub(a, b, r)
                self.set_reg(rd, r)
            elif op == 2:  # ADD Rd, Rn, #imm3
                a = self.regs[rn]; b = rn_or_imm
                r = a + b; self.update_nzcv_add(a, b, r)
                self.set_reg(rd, r)
            else:          # SUB Rd, Rn, #imm3
                a = self.regs[rn]; b = rn_or_imm
                r = a - b; self.update_nzcv_sub(a, b, r)
                self.set_reg(rd, r)
            return

        # ── MOV/CMP/ADD/SUB Rd, #imm8 (top5 = 4,5,6,7 = 0b00100..0b00111) ──
        if top5 in (0b00100, 0b00101, 0b00110, 0b00111):
            op  = (hw >> 11) & 0x3
            rdn = (hw >>  8) & 0x7
            imm =  hw        & 0xFF
            if op == 0:    # MOV
                self.set_reg(rdn, imm); self.update_nz(imm)
            elif op == 1:  # CMP
                a = self.regs[rdn]; r = a - imm
                self.update_nzcv_sub(a, imm, r)
            elif op == 2:  # ADD
                a = self.regs[rdn]; r = a + imm
                self.update_nzcv_add(a, imm, r); self.set_reg(rdn, r)
            else:          # SUB
                a = self.regs[rdn]; r = a - imm
                self.update_nzcv_sub(a, imm, r); self.set_reg(rdn, r)
            return

        # ── Data-processing (T1) ─────────────────────────────────────────────
        if top6 == 0b010000:
            op  = (hw >> 6) & 0xF
            rm  = (hw >> 3) & 0x7
            rdn =  hw       & 0x7
            a = self.regs[rdn]; b = self.regs[rm]
            if op == 0x0:  # AND
                r = a & b; self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0x1:  # EOR
                r = a ^ b; self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0x2:  # LSL
                n = b & 0xFF
                r, c = self.lsl(a, n)
                if n: self.C = c
                self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0x3:  # LSR
                n = b & 0xFF
                r, c = self.lsr(a, n if n else 32)
                if n: self.C = c
                self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0x4:  # ASR
                n = b & 0xFF
                r, c = self.asr(a, n if n else 32)
                if n: self.C = c
                self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0x5:  # ADC
                r = a + b + self.C; self.update_nzcv_add(a, b, r); self.set_reg(rdn, r)
            elif op == 0x6:  # SBC
                r = a - b - (1 - self.C); self.update_nzcv_sub(a, b, r); self.set_reg(rdn, r)
            elif op == 0x7:  # ROR
                n = b & 0xFF
                r, c = self.ror(a, n)
                if n: self.C = c
                self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0x8:  # TST
                r = a & b; self.update_nz(r)
            elif op == 0x9:  # NEG/RSB #0
                r = 0 - a; self.update_nzcv_sub(0, a, r); self.set_reg(rdn, r)
            elif op == 0xA:  # CMP
                r = a - b; self.update_nzcv_sub(a, b, r)
            elif op == 0xB:  # CMN
                r = a + b; self.update_nzcv_add(a, b, r)
            elif op == 0xC:  # ORR
                r = a | b; self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0xD:  # MUL
                r = u32(a * b); self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0xE:  # BIC
                r = a & ~b; self.update_nz(r); self.set_reg(rdn, r)
            elif op == 0xF:  # MVN
                r = ~b; self.update_nz(r); self.set_reg(rdn, r)
            return

        # ── Special data instructions & BX (T1) ──────────────────────────────
        if top6 == 0b010001:
            op  = (hw >> 8) & 0x3
            dn  = (hw >> 7) & 0x1
            rm  = (hw >> 3) & 0xF
            rdn = ((dn << 3) | (hw & 0x7))
            if op == 0:    # ADD (high register)
                r = self.reg(rdn) + self.reg(rm)
                self.set_reg(rdn, r)
            elif op == 1:  # CMP (high register)
                a = self.reg(rdn); b = self.reg(rm)
                self.update_nzcv_sub(a, b, a - b)
            elif op == 2:  # MOV (high register)
                self.set_reg(rdn, self.reg(rm))
            elif op == 3:  # BX / BLX
                target = self.reg(rm) & 0xFFFFFFFE
                if dn:  # BLX
                    self.lr = self.pc | 1
                self.pc = target
            return

        # ── LDR (literal) ────────────────────────────────────────────────────
        if top5 == 0b01001:
            rt  = (hw >> 8) & 0x7
            imm = (hw & 0xFF) << 2
            base = (self.reg(15)) & ~3   # PC is already advanced by 4; reg(15) adds 4
            addr = base + imm
            self.set_reg(rt, self.mem_read32(addr))
            return

        # ── Load/store (register offset) ─────────────────────────────────────
        if top4 == 0b0101:
            opA = (hw >> 9) & 0x7
            rm  = (hw >> 6) & 0x7
            rn  = (hw >> 3) & 0x7
            rt  =  hw       & 0x7
            addr = u32(self.regs[rn] + self.regs[rm])
            if   opA == 0b000: self.mem_write32(addr, self.regs[rt])    # STR
            elif opA == 0b001: self.mem_write16(addr, self.regs[rt])    # STRH
            elif opA == 0b010: self.mem_write8 (addr, self.regs[rt])    # STRB
            elif opA == 0b011: self.set_reg(rt, s8(self.mem_read8(addr)) & 0xFFFFFFFF)  # LDRSB
            elif opA == 0b100: self.set_reg(rt, self.mem_read32(addr))  # LDR
            elif opA == 0b101: self.set_reg(rt, self.mem_read16(addr))  # LDRH
            elif opA == 0b110: self.set_reg(rt, self.mem_read8 (addr))  # LDRB
            elif opA == 0b111:  # LDRSH
                v = self.mem_read16(addr)
                self.set_reg(rt, v if not (v & 0x8000) else v - 0x10000)
            return

        # ── Load/store (immediate offset) ────────────────────────────────────
        if top4 in (0b0110, 0b0111, 0b1000):
            op  = (hw >> 11) & 0x3
            imm = (hw >>  6) & 0x1F
            rn  = (hw >>  3) & 0x7
            rt  =  hw        & 0x7
            if top4 == 0b0110:    # STR/LDR word
                addr = u32(self.regs[rn] + (imm << 2))
                if op & 1: self.set_reg(rt, self.mem_read32(addr))
                else:      self.mem_write32(addr, self.regs[rt])
            elif top4 == 0b0111:  # STRB/LDRB
                addr = u32(self.regs[rn] + imm)
                if op & 1: self.set_reg(rt, self.mem_read8(addr))
                else:      self.mem_write8(addr, self.regs[rt])
            else:                 # STRH/LDRH
                addr = u32(self.regs[rn] + (imm << 1))
                if op & 1: self.set_reg(rt, self.mem_read16(addr))
                else:      self.mem_write16(addr, self.regs[rt])
            return

        # ── SP-relative load/store ───────────────────────────────────────────
        if top4 in (0b1001,):
            l   = (hw >> 11) & 0x1
            rt  = (hw >>  8) & 0x7
            imm = (hw & 0xFF) << 2
            addr = u32(self.sp + imm)
            if l: self.set_reg(rt, self.mem_read32(addr))
            else: self.mem_write32(addr, self.regs[rt])
            return

        # ── ADD PC/SP ─────────────────────────────────────────────────────────
        if top5 == 0b10100:   # ADD Rd, PC, #imm8*4
            rd  = (hw >> 8) & 0x7
            imm = (hw & 0xFF) << 2
            self.set_reg(rd, (self.reg(15) & ~3) + imm)
            return
        if top5 == 0b10101:   # ADD Rd, SP, #imm8*4
            rd  = (hw >> 8) & 0x7
            imm = (hw & 0xFF) << 2
            self.set_reg(rd, u32(self.sp + imm))
            return

        # ── Miscellaneous 16-bit ──────────────────────────────────────────────
        if top8 in (0b10110000, 0b10111000):   # ADD/SUB SP, #imm7
            sign = (hw >> 7) & 0x1
            imm  = (hw & 0x7F) << 2
            if sign: self.sp = u32(self.sp - imm)
            else:    self.sp = u32(self.sp + imm)
            return

        if top8 == 0b10110010:   # SXTH / SXTB / UXTH / UXTB
            op  = (hw >> 6) & 0x3
            rm  = (hw >> 3) & 0x7
            rd  =  hw       & 0x7
            v = self.regs[rm]
            if op == 0:  # SXTH
                r = v & 0xFFFF; self.set_reg(rd, r if not (r & 0x8000) else r - 0x10000)
            elif op == 1:  # SXTB
                r = v & 0xFF;   self.set_reg(rd, r if not (r & 0x80)   else r - 0x100)
            elif op == 2:  # UXTH
                self.set_reg(rd, v & 0xFFFF)
            elif op == 3:  # UXTB
                self.set_reg(rd, v & 0xFF)
            return

        if top8 == 0b10111010:   # REV / REV16 / REVSH
            op  = (hw >> 6) & 0x3
            rm  = (hw >> 3) & 0x7
            rd  =  hw       & 0x7
            v = self.regs[rm]
            if op == 0:   # REV
                r = ((v & 0xFF) << 24) | (((v >> 8) & 0xFF) << 16) | \
                    (((v >> 16) & 0xFF) << 8) | ((v >> 24) & 0xFF)
                self.set_reg(rd, r)
            elif op == 1:  # REV16
                r = (((v >> 8)  & 0xFF) | ((v & 0xFF) << 8) |
                     (((v >> 24) & 0xFF) << 16) | (((v >> 16) & 0xFF) << 24))
                self.set_reg(rd, r)
            elif op == 3:  # REVSH
                r = (((v >> 8) & 0xFF) | ((v & 0xFF) << 8))
                self.set_reg(rd, r if not (r & 0x8000) else r - 0x10000)
            return

        # ── PUSH / POP ───────────────────────────────────────────────────────
        # PUSH T1: bits[15:9] = 1011010, bit[8] = R (include LR)
        # Encoding: 0xB4xx (R=0) or 0xB5xx (R=1)
        if top7 == 0b1011010:    # PUSH {rlist[, LR]}
            lr_bit = (hw >> 8) & 0x1
            rlist  =  hw       & 0xFF
            regs_to_push = []
            if lr_bit: regs_to_push.append(14)
            for i in range(7, -1, -1):
                if rlist & (1 << i): regs_to_push.append(i)
            # push highest register first (decreasing address order)
            regs_to_push = sorted(regs_to_push, reverse=True)
            for rn in regs_to_push:
                self.sp = u32(self.sp - 4)
                self.mem_write32(self.sp, self.regs[rn])
            return
        if top8 == 0b10111101:   # POP {rlist, PC}
            pc_bit = (hw >> 8) & 0x1
            rlist  =  hw       & 0xFF
            regs_to_pop = sorted([i for i in range(8) if rlist & (1 << i)])
            for rn in regs_to_pop:
                self.set_reg(rn, self.mem_read32(self.sp))
                self.sp = u32(self.sp + 4)
            if pc_bit:
                target = self.mem_read32(self.sp) & 0xFFFFFFFE
                self.sp = u32(self.sp + 4)
                self.pc = target
            return

        # ── POP (T1) without PC ──────────────────────────────────────────────
        if top8 == 0b10111100:   # POP {rlist}
            rlist = hw & 0xFF
            for i in range(8):
                if rlist & (1 << i):
                    self.set_reg(i, self.mem_read32(self.sp))
                    self.sp = u32(self.sp + 4)
            return

        # ── BKPT ─────────────────────────────────────────────────────────────
        if top8 == 0b10111110:   # BKPT #imm8 — treat as simulator halt
            self.halted = True
            return

        # ── STM / LDM ────────────────────────────────────────────────────────
        if top5 == 0b11000:   # STMIA
            rn    = (hw >> 8) & 0x7
            rlist =  hw       & 0xFF
            addr  = self.regs[rn]
            for i in range(8):
                if rlist & (1 << i):
                    self.mem_write32(addr, self.regs[i])
                    addr += 4
            self.regs[rn] = u32(addr)  # writeback
            return
        if top5 == 0b11001:   # LDMIA
            rn    = (hw >> 8) & 0x7
            rlist =  hw       & 0xFF
            addr  = self.regs[rn]
            for i in range(8):
                if rlist & (1 << i):
                    self.set_reg(i, self.mem_read32(addr))
                    addr += 4
            if not (rlist & (1 << rn)):  # writeback if Rn not in list
                self.regs[rn] = u32(addr)
            return

        # ── Conditional branch (T1) ──────────────────────────────────────────
        if top4 == 0b1101:
            cond   = (hw >> 8) & 0xF
            if cond == 0xF:   # SVC
                self._svc(hw & 0xFF)
                return
            if cond == 0xE:   # UDF (undefined)
                raise SimulatorError(f"UDF at 0x{addr:04X}")
            # ARM Thumb PC = instruction_addr + 4; after fetch16 PC = addr+2
            # so target = (addr+4) + offset = (self.pc+2) + offset
            offset = sign_extend(hw & 0xFF, 8) << 1
            if self.check_cond(cond):
                self.pc = u32(self.pc + 2 + offset)
            return

        # ── Unconditional branch (T2) ────────────────────────────────────────
        if top5 == 0b11100:
            offset = sign_extend(hw & 0x7FF, 11) << 1
            self.pc = u32(self.pc + 2 + offset)
            return

        # ── BL prefix handled as 32-bit — should not reach here ──────────────
        raise SimulatorError(f"Unimplemented 16-bit opcode 0x{hw:04X} at 0x{addr:04X}")

    # ═════════════════════════════════════════════════════════════════════════
    #  32-bit Thumb-2 instruction execution (subset used by cortex-m0+)
    # ═════════════════════════════════════════════════════════════════════════

    def _exec32(self, addr, word):
        hw1 = (word >> 16) & 0xFFFF
        hw2 =  word        & 0xFFFF

        op1 = (hw1 >> 11) & 0x3

        # ── BL (T1) ──────────────────────────────────────────────────────────
        if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
            S   = (hw1 >> 10) & 0x1
            imm10 = hw1 & 0x3FF
            J1  = (hw2 >> 13) & 0x1
            J2  = (hw2 >> 11) & 0x1
            imm11 = hw2 & 0x7FF
            I1 = (~(J1 ^ S)) & 1
            I2 = (~(J2 ^ S)) & 1
            offset = sign_extend(
                (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1),
                25
            )
            self.lr = self.pc | 1   # return address (Thumb bit set)
            self.pc = u32(self.pc + offset)
            return

        # ── BLX (T2) ─────────────────────────────────────────────────────────
        if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xC000:
            S     = (hw1 >> 10) & 0x1
            imm10H = hw1 & 0x3FF
            J1    = (hw2 >> 13) & 0x1
            J2    = (hw2 >> 11) & 0x1
            imm10L = (hw2 >> 1) & 0x3FF
            I1 = (~(J1 ^ S)) & 1
            I2 = (~(J2 ^ S)) & 1
            offset = sign_extend(
                (S << 24) | (I1 << 23) | (I2 << 22) | (imm10H << 12) | (imm10L << 2),
                25
            )
            self.lr = self.pc | 1
            self.pc = u32((self.pc + offset) & ~3)
            return

        # ── Load/store multiple (32-bit) ─────────────────────────────────────
        if (hw1 & 0xFE50) == 0xE810:   # LDM/LDMDB
            l  = (hw1 >> 4) & 0x1
            w  = (hw1 >> 5) & 0x1
            rn = hw1 & 0xF
            rlist = hw2
            addr_r = self.regs[rn]
            if not l:  # STM
                for i in range(16):
                    if rlist & (1 << i):
                        self.mem_write32(addr_r, self.regs[i]); addr_r += 4
                if w: self.regs[rn] = u32(addr_r)
            else:       # LDM
                for i in range(16):
                    if rlist & (1 << i):
                        self.set_reg(i, self.mem_read32(addr_r)); addr_r += 4
                if w and not (rlist & (1 << rn)): self.regs[rn] = u32(addr_r)
            return

        # ── Data processing (modified immediate, 32-bit) ─────────────────────
        if (hw1 & 0xFA00) == 0xF000 and not (hw2 & 0x8000):
            # covers AND/ORR/EOR/BIC/ORN/TST/TEQ/MOV/MVN with imm12
            op4 = (hw1 >> 5) & 0xF
            S   = (hw1 >> 4) & 0x1
            rn  = hw1 & 0xF
            rd  = (hw2 >> 8) & 0xF
            # Decode Thumb modified immediate
            imm3_8 = ((hw2 >> 12) & 0x7) << 8 | (hw2 & 0xFF)
            imm12  = ((hw1 >> 10) & 0x1) << 11 | imm3_8
            imm, c = self._thumb_expand_imm_c(imm12, self.C)
            rn_val = self.regs[rn] if rn != 15 else 0
            if op4 == 0x0:   # AND / TST
                r = rn_val & imm
                if S: self.update_nz(r); self.C = c
                if rd != 15: self.set_reg(rd, r)
            elif op4 == 0x1:  # BIC
                r = rn_val & ~imm
                if S: self.update_nz(r); self.C = c
                self.set_reg(rd, r)
            elif op4 == 0x2:  # ORR / MOV
                r = (rn_val | imm) if rn != 15 else imm
                if S: self.update_nz(r); self.C = c
                self.set_reg(rd, r)
            elif op4 == 0x3:  # ORN / MVN
                r = (rn_val | ~imm) if rn != 15 else ~imm
                if S: self.update_nz(r); self.C = c
                self.set_reg(rd, u32(r))
            elif op4 == 0x4:  # EOR / TEQ
                r = rn_val ^ imm
                if S: self.update_nz(r); self.C = c
                if rd != 15: self.set_reg(rd, r)
            elif op4 == 0x8:  # ADD / CMN
                r = rn_val + imm
                if S: self.update_nzcv_add(rn_val, imm, r)
                if rd != 15: self.set_reg(rd, r)
            elif op4 == 0xA:  # ADC
                r = rn_val + imm + self.C
                if S: self.update_nzcv_add(rn_val, imm, r)
                self.set_reg(rd, r)
            elif op4 == 0xB:  # SBC
                r = rn_val - imm - (1 - self.C)
                if S: self.update_nzcv_sub(rn_val, imm, r)
                self.set_reg(rd, r)
            elif op4 == 0xD:  # SUB / CMP
                r = rn_val - imm
                if S: self.update_nzcv_sub(rn_val, imm, r)
                if rd != 15: self.set_reg(rd, r)
            elif op4 == 0xE:  # RSB
                r = imm - rn_val
                if S: self.update_nzcv_sub(imm, rn_val, r)
                self.set_reg(rd, r)
            return

        # ── Data processing (plain binary immediate) ──────────────────────────
        if (hw1 & 0xFB50) == 0xF200:
            op4  = (hw1 >> 5) & 0xF
            rn   = hw1 & 0xF
            rd   = (hw2 >> 8) & 0xF
            i    = (hw1 >> 10) & 0x1
            imm3 = (hw2 >> 12) & 0x7
            imm8 =  hw2        & 0xFF
            imm  = (i << 11) | (imm3 << 8) | imm8
            rn_val = self.regs[rn] if rn != 15 else self.pc
            if op4 == 0x0:    # ADD #imm12 / ADR
                self.set_reg(rd, u32(rn_val + imm))
            elif op4 == 0x4:  # MOV #imm16 (MOVW)
                imm16 = ((hw1 & 0xF) << 12) | (((hw1 >> 10) & 0x1) << 11) | \
                        (((hw2 >> 12) & 0x7) << 8) | (hw2 & 0xFF)
                self.set_reg(rd, imm16)
            elif op4 == 0x6:  # SUB #imm12
                self.set_reg(rd, u32(rn_val - imm))
            elif op4 == 0xA:  # ADR (SUB from PC)
                self.set_reg(rd, u32((self.pc & ~3) - imm))
            elif op4 == 0xC:  # MOVT
                imm16 = ((hw1 & 0xF) << 12) | (((hw1 >> 10) & 0x1) << 11) | \
                        (((hw2 >> 12) & 0x7) << 8) | (hw2 & 0xFF)
                self.set_reg(rd, (self.regs[rd] & 0xFFFF) | (imm16 << 16))
            return

        # ── Load/store (32-bit encodings) ─────────────────────────────────────
        if (hw1 & 0xFE00) == 0xF800:
            size = (hw1 >> 5) & 0x3
            l    = (hw1 >> 4) & 0x1
            rn   = hw1 & 0xF
            rt   = (hw2 >> 12) & 0xF
            imm12 = hw2 & 0xFFF
            addr_r = (self.regs[rn] if rn != 15 else (self.pc & ~3)) + imm12
            if l:
                if size == 2: self.set_reg(rt, self.mem_read32(addr_r))
                elif size == 1: self.set_reg(rt, self.mem_read16(addr_r))
                else: self.set_reg(rt, self.mem_read8(addr_r))
            else:
                if size == 2: self.mem_write32(addr_r, self.regs[rt])
                elif size == 1: self.mem_write16(addr_r, self.regs[rt])
                else: self.mem_write8(addr_r, self.regs[rt])
            return

        raise SimulatorError(
            f"Unimplemented 32-bit opcode 0x{word:08X} at 0x{addr:04X}"
        )

    def _thumb_expand_imm_c(self, imm12, carry_in):
        """ARMv6-M Thumb modified immediate encoding."""
        if (imm12 >> 10) & 0x3 == 0:
            op = (imm12 >> 8) & 0x3
            val = imm12 & 0xFF
            if op == 0: return val, carry_in
            if op == 1: return (val << 16) | val, carry_in
            if op == 2: return (val << 24) | (val << 8), carry_in
            if op == 3: return (val << 24) | (val << 16) | (val << 8) | val, carry_in
        else:
            unrot = 0x80 | (imm12 & 0x7F)
            n = (imm12 >> 7) & 0x1F
            result, c = self.ror(unrot, n)
            return result, c

    # ── SVC (syscall emulation) ───────────────────────────────────────────────

    def _svc(self, num):
        """SVC dispatch table."""
        if num == 0:
            # write(fd, buf_addr, len)
            fd   = self.regs[0]
            buf  = self.regs[1] & 0xFFFF
            n    = self.regs[2]
            data = bytes(self.mem[buf:buf + n])
            os.write(fd, data)
        elif num == 1:
            # exit(code)
            raise SystemExit(self.regs[0])
        elif num == 2:
            # putchar(c) — print r0 as ASCII; return r0 unchanged
            sys.stdout.write(chr(self.regs[0] & 0xFF))
            sys.stdout.flush()
        elif num == 3:
            # getchar() — read one ASCII char without requiring Enter
            try:
                import tty, termios          # Unix / macOS
                fd = sys.stdin.fileno()
                try:
                    old = termios.tcgetattr(fd)
                    tty.setraw(fd)
                    ch = sys.stdin.read(1)
                except (termios.error, AttributeError):
                    # stdin is not a tty (e.g. piped) — normal read
                    ch = sys.stdin.read(1)
                else:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except ImportError:
                try:
                    import msvcrt             # Windows
                    ch = msvcrt.getwch()
                except ImportError:
                    ch = sys.stdin.read(1)   # last resort
            self.regs[0] = ord(ch) if ch else 0xFFFFFFFF
        else:
            raise SimulatorError(f"Unknown SVC #{num}")

    # ── sentinel halt (return from main via LR=0xFFFFFFFF) ────────────────────

    def check_halt(self):
        if self.pc == 0xFFFFFFFE:
            self.halted = True


# ── Display ────────────────────────────────────────────────────────────────────

REG_NAMES = ['r0','r1','r2','r3','r4','r5','r6','r7',
             'r8','r9','r10','r11','r12','sp','lr','pc']

def print_state(cpu, asm_map, sym_map):
    # registers
    for i in range(0, 16, 4):
        parts = []
        for j in range(i, min(i+4, 16)):
            parts.append(f"{REG_NAMES[j]:>3}: {cpu.regs[j]:08X}")
        print("  " + "   ".join(parts))
    # flags
    print(f"  NZCV: {cpu.N}{cpu.Z}{cpu.C}{cpu.V}")
    # current instruction
    pc = cpu.pc
    sym = sym_map.get(pc, "")
    sym_str = f" <{sym}>" if sym else ""
    asm = asm_map.get(pc, "???")
    print(f"  PC 0x{pc:04X}{sym_str}: {asm}")


# ── Interactive shell ──────────────────────────────────────────────────────────

def run_interactive(cpu, asm_map, sym_map):
    print("ARMv6-M Simulator  (type 'h' for help)")
    print_state(cpu, asm_map, sym_map)

    last_cmd = ""
    while True:
        try:
            prompt = "\nsim(halted)> " if cpu.halted else "\nsim> "
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        cmd = raw if raw else last_cmd
        last_cmd = cmd
        parts = cmd.split()
        if not parts:
            continue
        op = parts[0].lower()

        if op in ('q', 'quit', 'exit'):
            break

        elif op in ('h', 'help', '?'):
            print(
                "  s / step [n]    step n instructions (default 1)\n"
                "  r / run         run until halt or breakpoint\n"
                "  p / print       print registers\n"
                "  x <addr> [n]    examine memory: n words at hex addr\n"
                "  b <addr>        set breakpoint at hex addr\n"
                "  db <addr>       delete breakpoint\n"
                "  lb              list breakpoints\n"
                "  d [addr] [n]    disassemble n instructions at addr\n"
                "  reg <rN> <val>  set register (e.g.  reg r0 42)\n"
                "  q / quit        quit\n"
                "  <enter>         repeat last command"
            )

        elif op in ('s', 'step'):
            n = int(parts[1]) if len(parts) > 1 else 1
            for _ in range(n):
                if cpu.halted: break
                try:
                    cpu.step()
                except SimulatorError as e:
                    print(f"  ERROR: {e}")
                    cpu.halted = True
                    break
                except SystemExit as e:
                    print(f"  Program exited with code {e.code}")
                    cpu.halted = True
                    break
                cpu.check_halt()
                if cpu.halted: print("  Program halted.")
            print_state(cpu, asm_map, sym_map)

        elif op in ('r', 'run'):
            if cpu.halted:
                print("  Program is halted.  Use 'q' to quit.")
            else:
                breakpoints = getattr(run_interactive, '_bp', set())
                try:
                    while not cpu.halted:
                        cpu.step()
                        cpu.check_halt()
                        if cpu.pc in breakpoints:
                            print(f"  Breakpoint hit at 0x{cpu.pc:04X}")
                            break
                except SimulatorError as e:
                    print(f"  ERROR: {e}")
                    cpu.halted = True
                except SystemExit as e:
                    print(f"  Program exited with code {e.code}")
                    cpu.halted = True
                print_state(cpu, asm_map, sym_map)

        elif op in ('p', 'print'):
            print_state(cpu, asm_map, sym_map)

        elif op in ('x', 'mem'):
            if len(parts) < 2:
                print("  Usage: x <addr_hex> [count]"); continue
            base = int(parts[1], 16)
            count = int(parts[2]) if len(parts) > 2 else 8
            for i in range(count):
                a = (base + i * 4) & 0xFFFF
                v = cpu.mem_read32(a)
                sym = sym_map.get(a, "")
                sym_str = f" <{sym}>" if sym else ""
                print(f"  0x{a:04X}{sym_str}: 0x{v:08X}  ({v})")

        elif op == 'b':
            if len(parts) < 2:
                print("  Usage: b <addr_hex>"); continue
            if not hasattr(run_interactive, '_bp'):
                run_interactive._bp = set()
            addr_b = int(parts[1], 16)
            run_interactive._bp.add(addr_b)
            print(f"  Breakpoint set at 0x{addr_b:04X}")

        elif op == 'db':
            bp = getattr(run_interactive, '_bp', set())
            if len(parts) < 2:
                print("  Usage: db <addr_hex>"); continue
            addr_b = int(parts[1], 16)
            bp.discard(addr_b)
            print(f"  Breakpoint removed at 0x{addr_b:04X}")

        elif op == 'lb':
            bp = getattr(run_interactive, '_bp', set())
            if not bp: print("  No breakpoints.")
            for a in sorted(bp): print(f"  0x{a:04X}")

        elif op in ('d', 'dis', 'disasm'):
            base = int(parts[1], 16) if len(parts) > 1 else cpu.pc
            count = int(parts[2]) if len(parts) > 2 else 10
            shown = 0
            a = base
            while shown < count and a < MEM_SIZE:
                sym = sym_map.get(a, "")
                if sym: print(f"  <{sym}>:")
                asm = asm_map.get(a)
                if asm:
                    marker = "=>" if a == cpu.pc else "  "
                    print(f"  {marker} 0x{a:04X}: {asm}")
                    shown += 1
                    # advance: peek at instruction size
                    hw = cpu.mem_read16(a)
                    a += 4 if cpu.is_32bit_thumb(hw) else 2
                else:
                    a += 2

        elif op == 'reg':
            if len(parts) < 3:
                print("  Usage: reg <rN> <value>"); continue
            rname = parts[1].lower()
            val   = int(parts[2], 0)
            if rname in REG_NAMES:
                cpu.set_reg(REG_NAMES.index(rname), val)
                print(f"  {rname} = 0x{val:08X}")
            else:
                print(f"  Unknown register: {rname}")

        else:
            print(f"  Unknown command '{op}'.  Type 'h' for help.")


# ── Assembler / linker helpers ─────────────────────────────────────────────────

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def compile_asm(s_file, elf_file, ld_file, extra_ld_flags=None):
    """Assemble and link a .s file.  Returns True on success, False on error."""
    import subprocess, shutil
    if shutil.which('arm-none-eabi-gcc') is None:
        print(
            "Error: arm-none-eabi-gcc not found on PATH.\n"
            "See DEPENDENCIES.md for installation instructions.",
            file=sys.stderr,
        )
        return False
    cmd = [
        'arm-none-eabi-gcc',
        '-mcpu=cortex-m0plus', '-mthumb',
        '-nostdlib',
        '-T', ld_file,
        '-o', elf_file,
        s_file,
    ]
    if extra_ld_flags:
        cmd.extend(extra_ld_flags)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("Assembler/linker error:", file=sys.stderr)
        combined = (result.stderr + result.stdout).strip()
        if combined:
            print(combined, file=sys.stderr)
        return False
    return True


def ensure_os_elf():
    """Compile os.s → os.elf if missing or stale.  Returns path to os.elf."""
    sd     = _script_dir()
    os_s   = os.path.join(sd, 'os.s')
    os_ld  = os.path.join(sd, 'os.ld')
    os_elf = os.path.join(sd, 'os.elf')

    if not os.path.exists(os_s):
        print(f"Error: OS source '{os_s}' not found.", file=sys.stderr)
        sys.exit(1)

    need_build = (
        not os.path.exists(os_elf) or
        os.path.getmtime(os_s) > os.path.getmtime(os_elf) or
        (os.path.exists(os_ld) and os.path.getmtime(os_ld) > os.path.getmtime(os_elf))
    )
    if need_build:
        print("Compiling OS...")
        if not compile_asm(os_s, os_elf, os_ld):
            sys.exit(1)
    return os_elf


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="ARMv6-M (Cortex-M0+) Simulator — flat 16-bit address space"
    )
    parser.add_argument("input", help=".s assembly file or .elf binary to load")
    parser.add_argument("--run", "-r", action="store_true",
                        help="run to completion without interactive prompt")
    parser.add_argument("--steps", "-n", type=int, default=0,
                        help="execute exactly N steps then stop")
    parser.add_argument("--trace", "-t", action="store_true",
                        help="print instruction trace during execution")
    args = parser.parse_args()

    # ── Compile OS first (needed to resolve putchar/getchar in user code) ───────
    os_elf_path = ensure_os_elf()

    # ── Assemble user .s file if needed ──────────────────────────────────────
    input_file = args.input
    if input_file.endswith('.s'):
        base    = input_file[:-2]
        elf_out = base + '.elf'
        ld_file = os.path.join(_script_dir(), 'link.ld')
        # --just-symbols lets the linker resolve putchar/getchar from the OS
        just_syms = f'-Wl,--just-symbols={os_elf_path}'
        print(f"Assembling '{input_file}'...")
        if not compile_asm(input_file, elf_out, ld_file,
                           extra_ld_flags=[just_syms]):
            sys.exit(1)
        input_file = elf_out

    # ── Load OS ELF ───────────────────────────────────────────────────────────
    try:
        os_memory, os_entry, os_asm, os_syms = load_elf(os_elf_path)
    except (SimulatorError, FileNotFoundError) as e:
        print(f"Error loading OS ELF: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Load user ELF ─────────────────────────────────────────────────────────
    try:
        user_memory, user_entry, user_asm, user_syms = load_elf(input_file)
    except (SimulatorError, FileNotFoundError) as e:
        print(f"Error loading '{input_file}': {e}", file=sys.stderr)
        sys.exit(1)

    # ── Merge memories: OS owns 0x0000-0x2FFF, user owns 0x3000-0xFFFF ───────
    memory = bytearray(MEM_SIZE)
    memory[0x0000:0x3000] = os_memory[0x0000:0x3000]
    memory[0x3000:      ] = user_memory[0x3000:      ]

    # Write user main()'s Thumb address into the OS pointer slot at 0x2FFC
    main_thumb_addr = user_entry | 1   # ensure Thumb bit is set for BLX
    struct.pack_into('<I', memory, 0x2FFC, main_thumb_addr)

    asm_map = {**os_asm,  **user_asm}
    sym_map = {**os_syms, **user_syms}

    print(f"Loaded '{input_file}'  main=0x{user_entry:04X}  "
          f"{len(asm_map)} instructions disassembled")

    cpu = CPU(memory, os_entry, asm_map, sym_map, trace=args.trace)

    if args.run:
        try:
            while not cpu.halted:
                cpu.step()
                cpu.check_halt()
        except SimulatorError as e:
            print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
        except SystemExit as e:
            sys.exit(e.code)
    elif args.steps:
        for _ in range(args.steps):
            if cpu.halted: break
            try:
                cpu.step()
            except SimulatorError as e:
                print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
            except SystemExit as e:
                sys.exit(e.code)
            cpu.check_halt()
        print_state(cpu, asm_map, sym_map)
    else:
        run_interactive(cpu, asm_map, sym_map)


if __name__ == "__main__":
    main()
