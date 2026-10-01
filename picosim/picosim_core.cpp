/**
 * picosim_core.cpp — ARMv6-M (Cortex-M0+) CPU core in C++
 *
 * Exposes a CPUCore class to Python via pybind11.
 * Flat 64 KB RAM lives in C++; peripheral addresses (>= 0x10000) are
 * dispatched to Python callbacks for GPIO etc.
 * SVC syscalls are also dispatched to a Python callback.
 */

#include <pybind11/pybind11.h>
#include <pybind11/functional.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace py = pybind11;

// ── cycle-level trace records ────────────────────────────────────────────────

enum ChangeKind { CH_REG = 0, CH_FLAG = 1, CH_DP = 2, CH_MEM = 3, CH_IO = 4 };

struct Change {
    int         kind;
    std::string name;
    uint32_t    old_v, new_v;
    uint32_t    addr;
    int         width;
};

struct CycleInfo {
    std::string state, phase, desc;
    std::vector<std::string> signals;
    bool        has_bus = false;
    uint32_t    bus = 0;
    int         branch = -1;       // -1 n/a, 0 not taken, 1 taken
    uint32_t    insn_addr = 0;
    int         index = 0;         // cycle number within the instruction
    int         total = -1;        // cycles in this instruction (-1 until decoded)
    bool        last = false;      // final cycle of the instruction
    std::vector<Change> changes;
};

struct MemWrite { uint32_t addr; int width; uint32_t old_v; bool io; uint32_t val; };

static std::string hex32(uint32_t v) {
    char b[16]; std::snprintf(b, sizeof b, "0x%08X", v); return b;
}
static std::string hex16(uint32_t v) {
    char b[16]; std::snprintf(b, sizeof b, "0x%04X", v & 0xFFFF); return b;
}
static const char* REGN[16] = {"R0","R1","R2","R3","R4","R5","R6","R7",
                               "R8","R9","R10","R11","R12","SP","LR","PC"};

// ── helpers ──────────────────────────────────────────────────────────────────

static inline uint32_t u32(int64_t v) { return (uint32_t)(v & 0xFFFFFFFF); }

static inline int32_t s32(uint32_t v) { return (int32_t)v; }

static inline int32_t sign_extend(uint32_t v, int bits) {
    uint32_t sign = 1u << (bits - 1);
    return (v & sign) ? (int32_t)(v | (~0u << bits)) : (int32_t)v;
}

// ── CPUCore ──────────────────────────────────────────────────────────────────

class CPUCore {
public:
    // ── state ────────────────────────────────────────────────────────────────
    uint8_t  mem[0x10000];   // 64 KB flat RAM
    uint32_t regs[16];       // R0–R15 (R13=SP, R14=LR, R15=PC)
    int N, Z, C, V;          // APSR flags
    bool     halted;
    uint64_t steps;
    bool     trace;
    uint32_t _insn_addr;     // address of executing instruction (for PC reads)

    // Python callbacks
    // peripheral_read(addr, nbytes) -> int
    std::function<uint32_t(uint32_t, int)> peripheral_read;
    // peripheral_write(addr, val, nbytes) -> None
    std::function<void(uint32_t, uint32_t, int)> peripheral_write;
    // svc_handler(num, regs[0..15]) -> None  (modifies regs in-place via array)
    std::function<void(int)> svc_handler;

    CPUCore() {
        std::memset(mem, 0, sizeof(mem));
        std::memset(regs, 0, sizeof(regs));
        N = Z = C = V = 0;
        halted = false;
        steps = 0;
        trace = false;
        _insn_addr = 0;
    }

    // ── datapath state (cycle-level model, Chapter 7 teaching datapath) ──────
    uint32_t MAR = 0, MDR = 0, IR = 0, IR2 = 0, ALU_A = 0, ALU_B = 0, TMP = 0;
    uint64_t cycles = 0;
    std::vector<MemWrite>* mem_log = nullptr;

    // ── register helpers ─────────────────────────────────────────────────────

    uint32_t get_pc() const { return regs[15]; }
    void     set_pc(uint32_t v) { regs[15] = v & 0xFFFFFFFF; }
    uint32_t get_sp() const { return regs[13]; }
    void     set_sp(uint32_t v) { regs[13] = v; }
    uint32_t get_lr() const { return regs[14]; }
    void     set_lr(uint32_t v) { regs[14] = v; }

    // Reading R15 as an operand gives the instruction's address + 4 (ARMv6-M
    // PC read value).  After fetch the PC register has advanced by 2 or 4;
    // _insn_addr holds the instruction's own address.  LDR (literal), ADR and
    // ADD Rd, PC, #imm round this down to a word boundary themselves (Align).
    uint32_t reg_read(int n) const {
        if (n == 15) return _insn_addr + 4;
        return regs[n];
    }

    void reg_write(int n, uint32_t v) {
        if (n == 15)
            regs[15] = v & 0xFFFFFFFEu;
        else
            regs[n] = v;
    }

    // ── memory access ────────────────────────────────────────────────────────

    bool is_peripheral(uint32_t addr) const { return addr >= 0x10000u; }

    uint8_t  read8 (uint32_t addr) {
        if (is_peripheral(addr)) {
            if (peripheral_read) return (uint8_t)peripheral_read(addr, 1);
            return 0;
        }
        return mem[addr & 0xFFFF];
    }

    uint16_t read16(uint32_t addr) {
        if (is_peripheral(addr)) {
            if (peripheral_read) return (uint16_t)peripheral_read(addr, 2);
            return 0;
        }
        uint16_t v;
        std::memcpy(&v, &mem[addr & 0xFFFF], 2);
        return v;
    }

    uint32_t read32(uint32_t addr) {
        if (is_peripheral(addr)) {
            if (peripheral_read) return peripheral_read(addr, 4);
            return 0;
        }
        uint32_t v;
        std::memcpy(&v, &mem[addr & 0xFFFF], 4);
        return v;
    }

    void log_write(uint32_t addr, int width, uint32_t val) {
        if (!mem_log) return;
        if (is_peripheral(addr)) { mem_log->push_back({addr, width, 0, true, val}); return; }
        uint32_t old = 0;
        std::memcpy(&old, &mem[addr & 0xFFFF], width);
        mem_log->push_back({addr & 0xFFFF, width, old, false, val});
    }

    void write8 (uint32_t addr, uint32_t val) {
        log_write(addr, 1, val & 0xFF);
        if (is_peripheral(addr)) {
            if (peripheral_write) peripheral_write(addr, val & 0xFF, 1);
            return;
        }
        mem[addr & 0xFFFF] = (uint8_t)(val & 0xFF);
    }

    void write16(uint32_t addr, uint32_t val) {
        log_write(addr, 2, val & 0xFFFF);
        if (is_peripheral(addr)) {
            if (peripheral_write) peripheral_write(addr, val & 0xFFFF, 2);
            return;
        }
        uint16_t v = (uint16_t)(val & 0xFFFF);
        std::memcpy(&mem[addr & 0xFFFF], &v, 2);
    }

    void write32(uint32_t addr, uint32_t val) {
        log_write(addr, 4, val);
        if (is_peripheral(addr)) {
            if (peripheral_write) peripheral_write(addr, val, 4);
            return;
        }
        std::memcpy(&mem[addr & 0xFFFF], &val, 4);
    }

    // ── flags ────────────────────────────────────────────────────────────────

    void update_nz(uint32_t r) {
        N = (r >> 31) & 1;
        Z = (r == 0) ? 1 : 0;
    }

    // ARM AddWithCarry(): every add, subtract and compare sets its flags from
    // this.  Subtraction is a + ~b + 1 (SUB, CMP) or a + ~b + C (SBC), so C is
    // the carry out of that sum ("no borrow").  V is set when both operands
    // have the same sign and the result's sign differs.
    uint32_t add_with_carry(uint32_t a, uint32_t b, int carry_in) {
        uint64_t sum = (uint64_t)a + b + (uint32_t)carry_in;
        uint32_t r = (uint32_t)sum;
        N = (r >> 31) & 1;
        Z = (r == 0) ? 1 : 0;
        C = (sum >> 32) & 1;
        V = (((a ^ r) & (b ^ r)) >> 31) & 1;
        return r;
    }

    void update_nzcv_add(uint32_t a, uint32_t b, uint64_t) { add_with_carry(a, b, 0); }
    void update_nzcv_sub(uint32_t a, uint32_t b, uint32_t)  { add_with_carry(a, ~b, 1); }

    // ── condition codes ──────────────────────────────────────────────────────

    bool check_cond(int cond) const {
        switch (cond & 0xF) {
        case 0x0: return Z;
        case 0x1: return !Z;
        case 0x2: return C;
        case 0x3: return !C;
        case 0x4: return N;
        case 0x5: return !N;
        case 0x6: return V;
        case 0x7: return !V;
        case 0x8: return C && !Z;
        case 0x9: return !C || Z;
        case 0xA: return N == V;
        case 0xB: return N != V;
        case 0xC: return !Z && (N == V);
        case 0xD: return Z || (N != V);
        default:  return true;  // AL
        }
    }

    // ── barrel shifter ───────────────────────────────────────────────────────

    struct ShiftResult { uint32_t val; int carry; };

    ShiftResult lsl(uint32_t val, int n) const {
        if (n == 0) return {val, C};
        if (n >= 32) { int c = (n == 32) ? (int)((val >> (32 - n)) & 1) : 0; return {0, c}; }
        int c = (val >> (32 - n)) & 1;
        return {val << n, c};
    }

    ShiftResult lsr(uint32_t val, int n) const {
        if (n == 0) return {val, C};
        if (n >= 32) { int c = (n == 32) ? (int)((val >> 31) & 1) : 0; return {0, c}; }
        int c = (val >> (n - 1)) & 1;
        return {val >> n, c};
    }

    ShiftResult asr(uint32_t val, int n) const {
        if (n == 0) return {val, C};
        if (n >= 32) { int c = (val >> 31) & 1; return {c ? 0xFFFFFFFF : 0u, c}; }
        int c = (val >> (n - 1)) & 1;
        return {u32(s32(val) >> n), c};
    }

    ShiftResult ror(uint32_t val, int n) const {
        n &= 31;
        if (n == 0) return {val, (int)((val >> 31) & 1)};
        uint32_t result = (val >> n) | (val << (32 - n));
        return {result, (int)((result >> 31) & 1)};
    }

    // ── Thumb modified immediate ─────────────────────────────────────────────

    struct ImmResult { uint32_t imm; int carry; };

    ImmResult thumb_expand_imm_c(uint32_t imm12) const {
        if (((imm12 >> 10) & 3) == 0) {
            int op = (imm12 >> 8) & 3;
            uint32_t val = imm12 & 0xFF;
            switch (op) {
            case 0: return {val, C};
            case 1: return {(val << 16) | val, C};
            case 2: return {(val << 24) | (val << 8), C};
            case 3: return {(val << 24) | (val << 16) | (val << 8) | val, C};
            }
        }
        uint32_t unrot = 0x80u | (imm12 & 0x7F);
        int n = (imm12 >> 7) & 0x1F;
        auto [result, c] = ror(unrot, n);
        return {result, c};
    }

    // ── fetch ────────────────────────────────────────────────────────────────

    uint16_t fetch16() {
        uint16_t hw = read16(regs[15]);
        regs[15] += 2;
        return hw;
    }

    static bool is_32bit_thumb(uint16_t hw) {
        return (hw >> 11) == 0x1D || (hw >> 11) == 0x1E || (hw >> 11) == 0x1F;
    }

    // ── step ─────────────────────────────────────────────────────────────────

    void step() {
        if (halted) return;
        if (in_insn) {   // finish a partially clocked instruction
            while (in_insn && !halted) step_cycle();
            return;
        }
        steps++;
        _insn_addr = regs[15];

        uint16_t hw1 = fetch16();
        IR = hw1;
        if (is_32bit_thumb(hw1)) {
            uint16_t hw2 = fetch16();
            IR2 = hw2;
            cycles += insn_cycle_count(hw1, hw2);
            uint32_t word = ((uint32_t)hw1 << 16) | hw2;
            exec32(_insn_addr, word);
        } else {
            cycles += insn_cycle_count(hw1, 0);
            exec16(_insn_addr, hw1);
        }
    }

    void check_halt() {
        if (regs[15] == 0xFFFFFFFEu) halted = true;
    }

    // ── run loop (releases GIL) ───────────────────────────────────────────────

    uint64_t run(int64_t max_steps = -1) {
        uint64_t count = 0;
        py::gil_scoped_release release;
        while (!halted) {
            step();
            check_halt();
            count++;
            if (max_steps >= 0 && (int64_t)count >= max_steps) break;
        }
        return count;
    }

    // ═════════════════════════════════════════════════════════════════════════
    //  16-bit Thumb instruction execution
    // ═════════════════════════════════════════════════════════════════════════

    void exec16(uint32_t addr, uint16_t hw) {
        int top5 = (hw >> 11) & 0x1F;
        int top4 = (hw >> 12) & 0xF;
        int top6 = (hw >> 10) & 0x3F;
        int top7 = (hw >>  9) & 0x7F;
        int top8 = (hw >>  8) & 0xFF;

        // ── Shift by immediate ─────────────────────────────────────────────
        if (top5 <= 2) {
            int op  = (hw >> 11) & 3;
            int imm = (hw >>  6) & 0x1F;
            int rm  = (hw >>  3) & 7;
            int rd  =  hw        & 7;
            if (op == 0) {       // LSL
                auto [r, c] = lsl(regs[rm], imm);
                if (imm) C = c;
                reg_write(rd, r); update_nz(r);
            } else if (op == 1) { // LSR
                int n = imm ? imm : 32;
                auto [r, c] = lsr(regs[rm], n);
                C = c; reg_write(rd, r); update_nz(r);
            } else {              // ASR
                int n = imm ? imm : 32;
                auto [r, c] = asr(regs[rm], n);
                C = c; reg_write(rd, r); update_nz(r);
            }
            return;
        }

        // ── ADD/SUB register/imm3 ──────────────────────────────────────────
        if (top5 == 3) {
            int op        = (hw >> 9) & 3;
            int rn_or_imm = (hw >> 6) & 7;
            int rn = (hw >> 3) & 7;
            int rd =  hw       & 7;
            uint32_t a = regs[rn];
            if (op == 0) {       // ADD Rd, Rn, Rm
                uint64_t r = (uint64_t)a + regs[rn_or_imm];
                update_nzcv_add(a, regs[rn_or_imm], r); reg_write(rd, (uint32_t)r);
            } else if (op == 1) { // SUB Rd, Rn, Rm
                uint32_t b = regs[rn_or_imm];
                uint32_t r = a - b; update_nzcv_sub(a, b, r); reg_write(rd, r);
            } else if (op == 2) { // ADD Rd, Rn, #imm3
                uint32_t b = (uint32_t)rn_or_imm;
                uint64_t r = (uint64_t)a + b;
                update_nzcv_add(a, b, r); reg_write(rd, (uint32_t)r);
            } else {              // SUB Rd, Rn, #imm3
                uint32_t b = (uint32_t)rn_or_imm;
                uint32_t r = a - b; update_nzcv_sub(a, b, r); reg_write(rd, r);
            }
            return;
        }

        // ── MOV/CMP/ADD/SUB Rd, #imm8 ────────────────────────────────────
        if (top5 >= 4 && top5 <= 7) {
            int op  = (hw >> 11) & 3;
            int rdn = (hw >>  8) & 7;
            uint32_t imm = hw & 0xFF;
            if (op == 0) {       // MOV
                reg_write(rdn, imm); update_nz(imm);
            } else if (op == 1) { // CMP
                uint32_t a = regs[rdn]; update_nzcv_sub(a, imm, a - imm);
            } else if (op == 2) { // ADD
                uint32_t a = regs[rdn];
                uint64_t r = (uint64_t)a + imm;
                update_nzcv_add(a, imm, r); reg_write(rdn, (uint32_t)r);
            } else {              // SUB
                uint32_t a = regs[rdn];
                uint32_t r = a - imm; update_nzcv_sub(a, imm, r); reg_write(rdn, r);
            }
            return;
        }

        // ── Data-processing ───────────────────────────────────────────────
        if (top6 == 0b010000) {
            int op  = (hw >> 6) & 0xF;
            int rm  = (hw >> 3) & 7;
            int rdn =  hw       & 7;
            uint32_t a = regs[rdn], b = regs[rm];
            switch (op) {
            case 0x0: { uint32_t r = a & b; update_nz(r); reg_write(rdn, r); break; }  // AND
            case 0x1: { uint32_t r = a ^ b; update_nz(r); reg_write(rdn, r); break; }  // EOR
            case 0x2: {  // LSL reg
                int n = b & 0xFF;
                auto [r, c] = lsl(a, n);
                if (n) C = c;
                update_nz(r); reg_write(rdn, r); break;
            }
            case 0x3: {  // LSR reg
                int n = b & 0xFF;
                auto [r, c] = lsr(a, n);
                if (n) C = c;
                update_nz(r); reg_write(rdn, r); break;
            }
            case 0x4: {  // ASR reg
                int n = b & 0xFF;
                auto [r, c] = asr(a, n);
                if (n) C = c;
                update_nz(r); reg_write(rdn, r); break;
            }
            case 0x5: {  // ADC
                uint32_t r = add_with_carry(a, b, C); reg_write(rdn, r); break;
            }
            case 0x6: {  // SBC
                uint32_t r = add_with_carry(a, ~b, C); reg_write(rdn, r); break;
            }
            case 0x7: {  // ROR
                int n = b & 0xFF;
                auto [r, c] = ror(a, n);
                if (n) C = c;
                update_nz(r); reg_write(rdn, r); break;
            }
            case 0x8: { uint32_t r = a & b; update_nz(r); break; }  // TST
            case 0x9: { uint32_t r = 0u - b; update_nzcv_sub(0, b, r); reg_write(rdn, r); break; } // NEG
            case 0xA: { uint32_t r = a - b; update_nzcv_sub(a, b, r); break; }  // CMP
            case 0xB: { uint64_t r = (uint64_t)a + b; update_nzcv_add(a, b, r); break; }  // CMN
            case 0xC: { uint32_t r = a | b; update_nz(r); reg_write(rdn, r); break; }  // ORR
            case 0xD: { uint32_t r = u32((uint64_t)a * b); update_nz(r); reg_write(rdn, r); break; } // MUL
            case 0xE: { uint32_t r = a & ~b; update_nz(r); reg_write(rdn, r); break; }  // BIC
            case 0xF: { uint32_t r = ~b; update_nz(r); reg_write(rdn, r); break; }  // MVN
            }
            return;
        }

        // ── Special data / BX ──────────────────────────────────────────────
        if (top6 == 0b010001) {
            int op  = (hw >> 8) & 3;
            int dn  = (hw >> 7) & 1;
            int rm  = (hw >> 3) & 0xF;
            int rdn = (dn << 3) | (hw & 7);
            if (op == 0) {       // ADD high reg
                reg_write(rdn, reg_read(rdn) + reg_read(rm));
            } else if (op == 1) { // CMP high reg
                uint32_t a = reg_read(rdn), b = reg_read(rm);
                update_nzcv_sub(a, b, a - b);
            } else if (op == 2) { // MOV high reg
                reg_write(rdn, reg_read(rm));
            } else {              // BX / BLX
                uint32_t target = reg_read(rm) & 0xFFFFFFFEu;
                if (dn) set_lr(regs[15] | 1);  // BLX: pc already advanced
                set_pc(target);
            }
            return;
        }

        // ── LDR literal ────────────────────────────────────────────────────
        if (top5 == 0b01001) {
            int rt  = (hw >> 8) & 7;
            uint32_t imm = (uint32_t)(hw & 0xFF) << 2;
            uint32_t base = reg_read(15) & ~3u;
            reg_write(rt, read32(base + imm));
            return;
        }

        // ── Load/store register offset ─────────────────────────────────────
        if (top4 == 0b0101) {
            int opA = (hw >> 9) & 7;
            int rm  = (hw >> 6) & 7;
            int rn  = (hw >> 3) & 7;
            int rt  =  hw       & 7;
            uint32_t addr2 = regs[rn] + regs[rm];
            switch (opA) {
            case 0: write32(addr2, regs[rt]); break;     // STR
            case 1: write16(addr2, regs[rt]); break;     // STRH
            case 2: write8 (addr2, regs[rt]); break;     // STRB
            case 3: reg_write(rt, u32((int32_t)(int8_t)read8(addr2))); break;  // LDRSB
            case 4: reg_write(rt, read32(addr2)); break; // LDR
            case 5: reg_write(rt, read16(addr2)); break; // LDRH
            case 6: reg_write(rt, read8 (addr2)); break; // LDRB
            case 7: { // LDRSH
                uint16_t v = read16(addr2);
                reg_write(rt, u32((int32_t)(int16_t)v));
                break;
            }
            }
            return;
        }

        // ── Load/store immediate offset ────────────────────────────────────
        if (top4 == 0b0110 || top4 == 0b0111 || top4 == 0b1000) {
            int op  = (hw >> 11) & 3;
            int imm = (hw >>  6) & 0x1F;
            int rn  = (hw >>  3) & 7;
            int rt  =  hw        & 7;
            if (top4 == 0b0110) {      // STR/LDR word
                uint32_t addr2 = regs[rn] + ((uint32_t)imm << 2);
                if (op & 1) reg_write(rt, read32(addr2));
                else        write32(addr2, regs[rt]);
            } else if (top4 == 0b0111) { // STRB/LDRB
                uint32_t addr2 = regs[rn] + (uint32_t)imm;
                if (op & 1) reg_write(rt, read8(addr2));
                else        write8(addr2, regs[rt]);
            } else {                    // STRH/LDRH
                uint32_t addr2 = regs[rn] + ((uint32_t)imm << 1);
                if (op & 1) reg_write(rt, read16(addr2));
                else        write16(addr2, regs[rt]);
            }
            return;
        }

        // ── SP-relative load/store ─────────────────────────────────────────
        if (top4 == 0b1001) {
            int l   = (hw >> 11) & 1;
            int rt  = (hw >>  8) & 7;
            uint32_t imm = (uint32_t)(hw & 0xFF) << 2;
            uint32_t addr2 = regs[13] + imm;
            if (l) reg_write(rt, read32(addr2));
            else   write32(addr2, regs[rt]);
            return;
        }

        // ── ADD PC/SP ──────────────────────────────────────────────────────
        if (top5 == 0b10100) {   // ADD Rd, PC, #imm8*4
            int rd  = (hw >> 8) & 7;
            uint32_t imm = (uint32_t)(hw & 0xFF) << 2;
            reg_write(rd, (reg_read(15) & ~3u) + imm);
            return;
        }
        if (top5 == 0b10101) {   // ADD Rd, SP, #imm8*4
            int rd  = (hw >> 8) & 7;
            uint32_t imm = (uint32_t)(hw & 0xFF) << 2;
            reg_write(rd, regs[13] + imm);
            return;
        }

        // ── Miscellaneous ──────────────────────────────────────────────────
        if (top8 == 0b10110000 || top8 == 0b10111000) {  // ADD/SUB SP, #imm7
            int sign = (hw >> 7) & 1;
            uint32_t imm = (uint32_t)(hw & 0x7F) << 2;
            if (sign) regs[13] -= imm;
            else      regs[13] += imm;
            return;
        }

        if (top8 == 0b10110010) {  // SXTH/SXTB/UXTH/UXTB
            int op = (hw >> 6) & 3;
            int rm = (hw >> 3) & 7;
            int rd =  hw       & 7;
            uint32_t v = regs[rm];
            switch (op) {
            case 0: reg_write(rd, u32((int32_t)(int16_t)(v & 0xFFFF))); break; // SXTH
            case 1: reg_write(rd, u32((int32_t)(int8_t) (v & 0xFF)));   break; // SXTB
            case 2: reg_write(rd, v & 0xFFFF); break;  // UXTH
            case 3: reg_write(rd, v & 0xFF);   break;  // UXTB
            }
            return;
        }

        if (top8 == 0b10111010) {  // REV/REV16/REVSH
            int op = (hw >> 6) & 3;
            int rm = (hw >> 3) & 7;
            int rd =  hw       & 7;
            uint32_t v = regs[rm];
            if (op == 0) {        // REV
                reg_write(rd, ((v & 0xFF) << 24) | (((v >> 8) & 0xFF) << 16) |
                              (((v >> 16) & 0xFF) << 8) | ((v >> 24) & 0xFF));
            } else if (op == 1) { // REV16
                reg_write(rd, (((v >> 8) & 0xFF) | ((v & 0xFF) << 8)) |
                              ((((v >> 24) & 0xFF) << 16) | (((v >> 16) & 0xFF) << 24)));
            } else if (op == 3) { // REVSH
                uint32_t r = (((v >> 8) & 0xFF) | ((v & 0xFF) << 8));
                reg_write(rd, u32((int32_t)(int16_t)(r & 0xFFFF)));
            }
            return;
        }

        // ── PUSH ──────────────────────────────────────────────────────────
        if (top7 == 0b1011010) {
            int lr_bit = (hw >> 8) & 1;
            int rlist  =  hw       & 0xFF;
            // push highest register first
            if (lr_bit) { regs[13] -= 4; write32(regs[13], regs[14]); }
            for (int i = 7; i >= 0; --i) {
                if (rlist & (1 << i)) { regs[13] -= 4; write32(regs[13], regs[i]); }
            }
            return;
        }

        // ── POP ───────────────────────────────────────────────────────────
        if (top8 == 0b10111101) {  // POP {rlist, PC}
            int pc_bit = (hw >> 8) & 1;
            int rlist  =  hw       & 0xFF;
            for (int i = 0; i < 8; ++i) {
                if (rlist & (1 << i)) { reg_write(i, read32(regs[13])); regs[13] += 4; }
            }
            if (pc_bit) {
                uint32_t target = read32(regs[13]) & 0xFFFFFFFEu;
                regs[13] += 4;
                set_pc(target);
            }
            return;
        }
        if (top8 == 0b10111100) {  // POP {rlist} (no PC)
            int rlist = hw & 0xFF;
            for (int i = 0; i < 8; ++i) {
                if (rlist & (1 << i)) { reg_write(i, read32(regs[13])); regs[13] += 4; }
            }
            return;
        }

        // ── BKPT ──────────────────────────────────────────────────────────
        if (top8 == 0b10111110) {
            halted = true;
            return;
        }

        // ── STM / LDM ─────────────────────────────────────────────────────
        if (top5 == 0b11000) {   // STMIA
            int rn    = (hw >> 8) & 7;
            int rlist =  hw       & 0xFF;
            uint32_t addr2 = regs[rn];
            for (int i = 0; i < 8; ++i) {
                if (rlist & (1 << i)) { write32(addr2, regs[i]); addr2 += 4; }
            }
            regs[rn] = addr2;
            return;
        }
        if (top5 == 0b11001) {   // LDMIA
            int rn    = (hw >> 8) & 7;
            int rlist =  hw       & 0xFF;
            uint32_t addr2 = regs[rn];
            for (int i = 0; i < 8; ++i) {
                if (rlist & (1 << i)) { reg_write(i, read32(addr2)); addr2 += 4; }
            }
            if (!(rlist & (1 << rn))) regs[rn] = addr2;  // writeback if Rn not in list
            return;
        }

        // ── Conditional branch / SVC ──────────────────────────────────────
        if (top4 == 0b1101) {
            int cond = (hw >> 8) & 0xF;
            if (cond == 0xF) { exec_svc(hw & 0xFF); return; }
            if (cond == 0xE) {
                throw std::runtime_error(
                    "UDF at 0x" + std::to_string(addr));
            }
            int32_t offset = sign_extend(hw & 0xFF, 8) * 2;
            if (check_cond(cond))
                set_pc(u32((int32_t)(regs[15] + 2) + offset));
            return;
        }

        // ── Unconditional branch ──────────────────────────────────────────
        if (top5 == 0b11100) {
            int32_t offset = sign_extend(hw & 0x7FF, 11) * 2;
            set_pc(u32((int32_t)(regs[15] + 2) + offset));
            return;
        }

        throw std::runtime_error(
            "Unimplemented 16-bit opcode 0x" +
            std::to_string(hw) + " at 0x" + std::to_string(addr));
    }

    // ═════════════════════════════════════════════════════════════════════════
    //  32-bit Thumb-2 instruction execution
    // ═════════════════════════════════════════════════════════════════════════

    void exec32(uint32_t addr, uint32_t word) {
        uint16_t hw1 = (word >> 16) & 0xFFFF;
        uint16_t hw2 =  word        & 0xFFFF;

        // ── BL (T1) ──────────────────────────────────────────────────────
        if ((hw1 & 0xF800) == 0xF000 && (hw2 & 0xD000) == 0xD000) {
            uint32_t S  = (hw1 >> 10) & 1;
            uint32_t imm10 = hw1 & 0x3FF;
            uint32_t J1 = (hw2 >> 13) & 1;
            uint32_t J2 = (hw2 >> 11) & 1;
            uint32_t imm11 = hw2 & 0x7FF;
            uint32_t I1 = (~(J1 ^ S)) & 1;
            uint32_t I2 = (~(J2 ^ S)) & 1;
            int32_t offset = sign_extend(
                (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1), 25);
            set_lr(regs[15] | 1);
            set_pc(u32((int32_t)regs[15] + offset));
            return;
        }

        // ── BLX (T2) ─────────────────────────────────────────────────────
        if ((hw1 & 0xF800) == 0xF000 && (hw2 & 0xD000) == 0xC000) {
            uint32_t S  = (hw1 >> 10) & 1;
            uint32_t imm10H = hw1 & 0x3FF;
            uint32_t J1 = (hw2 >> 13) & 1;
            uint32_t J2 = (hw2 >> 11) & 1;
            uint32_t imm10L = (hw2 >> 1) & 0x3FF;
            uint32_t I1 = (~(J1 ^ S)) & 1;
            uint32_t I2 = (~(J2 ^ S)) & 1;
            int32_t offset = sign_extend(
                (S << 24) | (I1 << 23) | (I2 << 22) | (imm10H << 12) | (imm10L << 2), 25);
            set_lr(regs[15] | 1);
            set_pc((u32((int32_t)regs[15] + offset)) & ~3u);
            return;
        }

        // ── Load/store multiple (32-bit) ──────────────────────────────────
        if ((hw1 & 0xFE50) == 0xE810) {
            int l     = (hw1 >> 4) & 1;
            int w     = (hw1 >> 5) & 1;
            int rn    = hw1 & 0xF;
            int rlist = hw2;
            uint32_t addr2 = regs[rn];
            if (!l) {   // STM
                for (int i = 0; i < 16; ++i) {
                    if (rlist & (1 << i)) { write32(addr2, regs[i]); addr2 += 4; }
                }
                if (w) regs[rn] = addr2;
            } else {    // LDM
                for (int i = 0; i < 16; ++i) {
                    if (rlist & (1 << i)) { reg_write(i, read32(addr2)); addr2 += 4; }
                }
                if (w && !(rlist & (1 << rn))) regs[rn] = addr2;
            }
            return;
        }

        // ── Data processing (modified immediate) ──────────────────────────
        if ((hw1 & 0xFA00) == 0xF000 && !(hw2 & 0x8000)) {
            int op4 = (hw1 >> 5) & 0xF;
            int S   = (hw1 >> 4) & 1;
            int rn  = hw1 & 0xF;
            int rd  = (hw2 >> 8) & 0xF;
            uint32_t imm3_8 = (((uint32_t)(hw2 >> 12) & 7) << 8) | (hw2 & 0xFF);
            uint32_t imm12  = (((uint32_t)(hw1 >> 10) & 1) << 11) | imm3_8;
            auto [imm, c]   = thumb_expand_imm_c(imm12);
            uint32_t rn_val = (rn != 15) ? regs[rn] : 0;

            switch (op4) {
            case 0x0: {  // AND / TST
                uint32_t r = rn_val & imm;
                if (S) { update_nz(r); C = c; }
                if (rd != 15) reg_write(rd, r);
                break;
            }
            case 0x1: {  // BIC
                uint32_t r = rn_val & ~imm;
                if (S) { update_nz(r); C = c; }
                reg_write(rd, r); break;
            }
            case 0x2: {  // ORR / MOV
                uint32_t r = (rn != 15) ? (rn_val | imm) : imm;
                if (S) { update_nz(r); C = c; }
                reg_write(rd, r); break;
            }
            case 0x3: {  // ORN / MVN
                uint32_t r = (rn != 15) ? (rn_val | ~imm) : ~imm;
                if (S) { update_nz(r); C = c; }
                reg_write(rd, r); break;
            }
            case 0x4: {  // EOR / TEQ
                uint32_t r = rn_val ^ imm;
                if (S) { update_nz(r); C = c; }
                if (rd != 15) reg_write(rd, r);
                break;
            }
            case 0x8: {  // ADD / CMN
                uint64_t r = (uint64_t)rn_val + imm;
                if (S) update_nzcv_add(rn_val, imm, r);
                if (rd != 15) reg_write(rd, (uint32_t)r);
                break;
            }
            case 0xA: {  // ADC
                int cin = C;
                uint32_t r = (uint32_t)((uint64_t)rn_val + imm + cin);
                if (S) add_with_carry(rn_val, imm, cin);
                reg_write(rd, r); break;
            }
            case 0xB: {  // SBC
                int cin = C;
                uint32_t r = (uint32_t)((uint64_t)rn_val + ~imm + cin);
                if (S) add_with_carry(rn_val, ~imm, cin);
                reg_write(rd, r); break;
            }
            case 0xD: {  // SUB / CMP
                uint32_t r = rn_val - imm;
                if (S) update_nzcv_sub(rn_val, imm, r);
                if (rd != 15) reg_write(rd, r);
                break;
            }
            case 0xE: {  // RSB
                uint32_t r = imm - rn_val;
                if (S) update_nzcv_sub(imm, rn_val, r);
                reg_write(rd, r); break;
            }
            }
            return;
        }

        // ── Data processing (plain binary immediate) ───────────────────────
        if ((hw1 & 0xFB50) == 0xF200) {
            int op4  = (hw1 >> 5) & 0xF;
            int rn   = hw1 & 0xF;
            int rd   = (hw2 >> 8) & 0xF;
            uint32_t i    = (hw1 >> 10) & 1;
            uint32_t imm3 = (hw2 >> 12) & 7;
            uint32_t imm8 = hw2 & 0xFF;
            uint32_t imm  = (i << 11) | (imm3 << 8) | imm8;
            uint32_t rn_val = rn == 15 ? (reg_read(15) & ~3u) : reg_read(rn);
            switch (op4) {
            case 0x0:  // ADD #imm12 / ADR
                reg_write(rd, rn_val + imm); break;
            case 0x4: {  // MOVW
                uint32_t imm16 = ((uint32_t)(hw1 & 0xF) << 12) |
                                 (((uint32_t)(hw1 >> 10) & 1) << 11) |
                                 (((uint32_t)(hw2 >> 12) & 7) << 8) |
                                 (hw2 & 0xFF);
                reg_write(rd, imm16); break;
            }
            case 0x6:  // SUB #imm12
                reg_write(rd, rn_val - imm); break;
            case 0xA:  // ADR (SUB from PC)
                reg_write(rd, (regs[15] & ~3u) - imm); break;
            case 0xC: {  // MOVT
                uint32_t imm16 = ((uint32_t)(hw1 & 0xF) << 12) |
                                 (((uint32_t)(hw1 >> 10) & 1) << 11) |
                                 (((uint32_t)(hw2 >> 12) & 7) << 8) |
                                 (hw2 & 0xFF);
                reg_write(rd, (regs[rd] & 0xFFFF) | (imm16 << 16)); break;
            }
            }
            return;
        }

        // ── Load/store (32-bit encodings) ──────────────────────────────────
        if ((hw1 & 0xFE00) == 0xF800) {
            int size  = (hw1 >> 5) & 3;
            int l     = (hw1 >> 4) & 1;
            int rn    = hw1 & 0xF;
            int rt    = (hw2 >> 12) & 0xF;
            uint32_t imm12 = hw2 & 0xFFF;
            uint32_t base  = (rn != 15) ? regs[rn] : (regs[15] & ~3u);
            uint32_t addr2 = base + imm12;
            if (l) {
                if (size == 2) reg_write(rt, read32(addr2));
                else if (size == 1) reg_write(rt, read16(addr2));
                else reg_write(rt, read8(addr2));
            } else {
                if (size == 2) write32(addr2, regs[rt]);
                else if (size == 1) write16(addr2, regs[rt]);
                else write8(addr2, regs[rt]);
            }
            return;
        }

        throw std::runtime_error(
            "Unimplemented 32-bit opcode 0x" +
            std::to_string(word) + " at 0x" + std::to_string(addr));
    }

    // ═════════════════════════════════════════════════════════════════════════
    //  Cycle-level execution (the multi-cycle teaching machine)
    //
    //  Every instruction is a sequence of clocked FSM states.  All instructions
    //  share FETCH_ADDR → FETCH_MEMORY → FETCH_IR (plus FETCH2_* for 32-bit
    //  encodings), then DECODE appends the instruction-specific states.  The
    //  sequences follow Chapter 7 of the course text; see docs/cycles.md.
    // ═════════════════════════════════════════════════════════════════════════

    enum Kind { K_ALU, K_ADR, K_LOAD, K_STORE, K_B, K_BCOND, K_BX, K_BLX,
                K_BL, K_PUSH, K_POP, K_LDM, K_STM, K_SVC, K_BKPT, K_UDF, K_UNDEF };

    struct Plan {
        Kind kind = K_UNDEF;
        int  nregs = 0;          // multi-register transfers
        bool writeback = false;  // multi-register base writeback
        bool pc_in_list = false;
    };

    struct Micro {
        std::string state, phase;
        std::function<void(CycleInfo&)> fn;
    };

    std::vector<Micro> uops;
    size_t   upc = 0;
    bool     in_insn = false;
    int      insn_total = -1;

    static int popcount(uint32_t v) { int n = 0; while (v) { n += v & 1; v >>= 1; } return n; }

    static Plan classify16(uint16_t hw) {
        Plan p;
        int top5 = (hw >> 11) & 0x1F, top4 = (hw >> 12) & 0xF;
        int top6 = (hw >> 10) & 0x3F, top7 = (hw >> 9) & 0x7F, top8 = (hw >> 8) & 0xFF;
        if (top5 <= 7 || top6 == 0b010000)                   { p.kind = K_ALU; return p; }
        if (top6 == 0b010001) { p.kind = (((hw >> 8) & 3) == 3) ? (((hw >> 7) & 1) ? K_BLX : K_BX) : K_ALU; return p; }
        if (top5 == 0b01001)                                 { p.kind = K_LOAD; return p; }
        if (top4 == 0b0101) { p.kind = (((hw >> 9) & 7) <= 2) ? K_STORE : K_LOAD; return p; }
        if (top4 == 0b0110 || top4 == 0b0111 || top4 == 0b1000 || top4 == 0b1001) {
            p.kind = ((hw >> 11) & 1) ? K_LOAD : K_STORE; return p;
        }
        if (top5 == 0b10100)                                 { p.kind = K_ADR; return p; }
        if (top5 == 0b10101 || top8 == 0b10110000 || top8 == 0b10111000 ||
            top8 == 0b10110010 || top8 == 0b10111010)        { p.kind = K_ALU; return p; }
        if (top7 == 0b1011010) {
            p.kind = K_PUSH; p.nregs = popcount(hw & 0x1FF); p.writeback = true; return p;
        }
        if (top8 == 0b10111101 || top8 == 0b10111100) {
            p.kind = K_POP; p.nregs = popcount(hw & 0x1FF); p.writeback = true;
            p.pc_in_list = top8 == 0b10111101; return p;
        }
        if (top8 == 0b10111110)                              { p.kind = K_BKPT; return p; }
        if (top5 == 0b11000) {
            p.kind = K_STM; p.nregs = popcount(hw & 0xFF); p.writeback = true; return p;
        }
        if (top5 == 0b11001) {
            int rn = (hw >> 8) & 7;
            p.kind = K_LDM; p.nregs = popcount(hw & 0xFF); p.writeback = !((hw >> rn) & 1); return p;
        }
        if (top4 == 0b1101) {
            int cond = (hw >> 8) & 0xF;
            p.kind = cond == 0xF ? K_SVC : cond == 0xE ? K_UDF : K_BCOND; return p;
        }
        if (top5 == 0b11100)                                 { p.kind = K_B; return p; }
        return p;
    }

    static Plan classify32(uint32_t word) {
        Plan p;
        uint16_t hw1 = (word >> 16) & 0xFFFF, hw2 = word & 0xFFFF;
        if ((hw1 & 0xF800) == 0xF000 && ((hw2 & 0xD000) == 0xD000 || (hw2 & 0xD000) == 0xC000)) {
            p.kind = K_BL; return p;
        }
        if ((hw1 & 0xFE50) == 0xE810) {
            int l = (hw1 >> 4) & 1, w = (hw1 >> 5) & 1, rn = hw1 & 0xF;
            p.kind = l ? K_LDM : K_STM; p.nregs = popcount(hw2);
            p.writeback = w && (!l || !((hw2 >> rn) & 1));
            p.pc_in_list = l && (hw2 & 0x8000);
            return p;
        }
        if ((hw1 & 0xFA00) == 0xF000 && !(hw2 & 0x8000)) { p.kind = K_ALU; return p; }
        if ((hw1 & 0xFB50) == 0xF200)                    { p.kind = K_ALU; return p; }
        if ((hw1 & 0xFE00) == 0xF800) { p.kind = ((hw1 >> 4) & 1) ? K_LOAD : K_STORE; return p; }
        return p;
    }

    // Number of states after fetch (DECODE onward) for a plan.
    static int exec_cycles(const Plan& p) {
        switch (p.kind) {
        case K_ALU: case K_ADR: case K_BX: case K_BL:  return 3;
        case K_LOAD: case K_STORE: case K_BLX:         return 4;
        case K_B: case K_BCOND: case K_SVC: case K_BKPT: return 2;
        case K_PUSH: case K_POP: case K_LDM: case K_STM:
            return 2 + 2 * p.nregs + (p.writeback ? 1 : 0);
        default: return 1;
        }
    }

    // Total clock cycles for the instruction whose halfwords are hw1/hw2.
    // 16-bit counts depend only on the opcode, so they come from a table.
    static int insn_cycle_count(uint16_t hw1, uint16_t hw2) {
        static const std::vector<uint8_t> table16 = [] {
            std::vector<uint8_t> t(0x10000);
            for (uint32_t hw = 0; hw < 0x10000; ++hw)
                t[hw] = (uint8_t)(3 + exec_cycles(classify16((uint16_t)hw)));
            return t;
        }();
        if (is_32bit_thumb(hw1)) return 6 + exec_cycles(classify32(((uint32_t)hw1 << 16) | hw2));
        return table16[hw1];
    }

    // ── operand description for operate instructions ────────────────────────

    struct AluInfo {
        std::string op;
        int rd = -1, ra = -1, rb = -1;   // -1 = unused
        bool has_imm = false; uint32_t imm = 0;
        bool writes = true, flags = true;
    };

    AluInfo alu_info16(uint16_t hw) const {
        AluInfo a;
        int top5 = (hw >> 11) & 0x1F, top6 = (hw >> 10) & 0x3F, top8 = (hw >> 8) & 0xFF;
        if (top5 <= 2) {
            static const char* n[] = {"LSL", "LSR", "ASR"};
            int op = (hw >> 11) & 3, imm = (hw >> 6) & 0x1F;
            a.op = n[op]; a.rd = hw & 7; a.ra = (hw >> 3) & 7;
            a.has_imm = true; a.imm = (op && !imm) ? 32 : imm;
        } else if (top5 == 3) {
            int op = (hw >> 9) & 3;
            a.op = (op & 1) ? "SUB" : "ADD"; a.rd = hw & 7; a.ra = (hw >> 3) & 7;
            if (op < 2) a.rb = (hw >> 6) & 7; else { a.has_imm = true; a.imm = (hw >> 6) & 7; }
        } else if (top5 <= 7) {
            static const char* n[] = {"MOV", "CMP", "ADD", "SUB"};
            int op = (hw >> 11) & 3, rdn = (hw >> 8) & 7;
            a.op = n[op]; a.rd = rdn; a.ra = op ? rdn : -1;
            a.has_imm = true; a.imm = hw & 0xFF; a.writes = op != 1;
        } else if (top6 == 0b010000) {
            static const char* n[] = {"AND","EOR","LSL","LSR","ASR","ADC","SBC","ROR",
                                      "TST","NEG","CMP","CMN","ORR","MUL","BIC","MVN"};
            int op = (hw >> 6) & 0xF, rm = (hw >> 3) & 7, rdn = hw & 7;
            a.op = n[op]; a.rd = rdn; a.ra = rdn; a.rb = rm;
            if (op == 0x9 || op == 0xF) a.ra = -1;
            if (op == 0x8 || op == 0xA || op == 0xB) a.writes = false;
        } else if (top6 == 0b010001) {
            int op = (hw >> 8) & 3, rm = (hw >> 3) & 0xF, rdn = (((hw >> 7) & 1) << 3) | (hw & 7);
            a.op = op == 0 ? "ADD" : op == 1 ? "CMP" : "MOV";
            a.rd = rdn; a.ra = op == 2 ? -1 : rdn; a.rb = rm;
            a.writes = op != 1; a.flags = op == 1;
        } else if (top5 == 0b10101) {
            a.op = "ADD"; a.rd = (hw >> 8) & 7; a.ra = 13; a.has_imm = true;
            a.imm = (uint32_t)(hw & 0xFF) << 2; a.flags = false;
        } else if (top8 == 0b10110000 || top8 == 0b10111000) {
            a.op = ((hw >> 7) & 1) ? "SUB" : "ADD"; a.rd = 13; a.ra = 13; a.has_imm = true;
            a.imm = (uint32_t)(hw & 0x7F) << 2; a.flags = false;
        } else if (top8 == 0b10110010) {
            static const char* n[] = {"SXTH", "SXTB", "UXTH", "UXTB"};
            a.op = n[(hw >> 6) & 3]; a.rd = hw & 7; a.rb = (hw >> 3) & 7; a.flags = false;
        } else if (top8 == 0b10111010) {
            static const char* n[] = {"REV", "REV16", "REV?", "REVSH"};
            a.op = n[(hw >> 6) & 3]; a.rd = hw & 7; a.rb = (hw >> 3) & 7; a.flags = false;
        } else if (top5 == 0b10100) {
            a.op = "ADD"; a.rd = (hw >> 8) & 7; a.ra = 15; a.has_imm = true;
            a.imm = (uint32_t)(hw & 0xFF) << 2; a.flags = false;
        }
        return a;
    }

    AluInfo alu_info32(uint32_t word) const {
        AluInfo a;
        uint16_t hw1 = (word >> 16) & 0xFFFF, hw2 = word & 0xFFFF;
        int rn = hw1 & 0xF, rd = (hw2 >> 8) & 0xF, op4 = (hw1 >> 5) & 0xF;
        a.rd = rd; a.has_imm = true; a.flags = (hw1 >> 4) & 1;
        if ((hw1 & 0xFA00) == 0xF000) {   // modified immediate
            static const char* n[] = {"AND","BIC","ORR","ORN","EOR","?","?","?",
                                      "ADD","?","ADC","SBC","?","SUB","RSB","?"};
            uint32_t imm12 = (((uint32_t)(hw1 >> 10) & 1) << 11) |
                             (((uint32_t)(hw2 >> 12) & 7) << 8) | (hw2 & 0xFF);
            a.imm = thumb_expand_imm_c(imm12).imm; a.op = n[op4];
            if ((op4 == 2 || op4 == 3) && rn == 15) { a.op = op4 == 2 ? "MOV" : "MVN"; }
            else a.ra = rn;
            if ((op4 == 0 || op4 == 4 || op4 == 8 || op4 == 0xD) && rd == 15) a.writes = false;
        } else {                           // plain binary immediate
            a.flags = false; a.ra = rn;
            uint32_t imm16 = ((uint32_t)(hw1 & 0xF) << 12) | (((uint32_t)(hw1 >> 10) & 1) << 11) |
                             (((uint32_t)(hw2 >> 12) & 7) << 8) | (hw2 & 0xFF);
            a.imm = (((hw1 >> 10) & 1) << 11) | (((hw2 >> 12) & 7) << 8) | (hw2 & 0xFF);
            if (op4 == 0x4)      { a.op = "MOVW"; a.ra = -1; a.imm = imm16; }
            else if (op4 == 0xC) { a.op = "MOVT"; a.ra = rd; a.imm = imm16; }
            else a.op = (op4 == 0x6 || op4 == 0xA) ? "SUB" : "ADD";
        }
        return a;
    }

    // ── memory-access decode ─────────────────────────────────────────────────

    struct MemInfo {
        bool load = true; int width = 4; bool sign = false;
        int rt = 0, rn = -1, rm = -1; uint32_t imm = 0; bool lit = false;
        std::string off_src;   // what ADDR2MUX selects
    };

    static MemInfo mem_info16(uint16_t hw) {
        MemInfo m;
        int top5 = (hw >> 11) & 0x1F, top4 = (hw >> 12) & 0xF;
        if (top5 == 0b01001) {
            m.rt = (hw >> 8) & 7; m.lit = true; m.imm = (uint32_t)(hw & 0xFF) << 2;
            m.off_src = "ZEXT(IR[7:0])x4";
        } else if (top4 == 0b0101) {
            static const int  w[] = {4, 2, 1, 1, 4, 2, 1, 2};
            int opA = (hw >> 9) & 7;
            m.load = opA >= 3; m.width = w[opA]; m.sign = opA == 3 || opA == 7;
            m.rm = (hw >> 6) & 7; m.rn = (hw >> 3) & 7; m.rt = hw & 7; m.off_src = "SR2";
        } else if (top4 == 0b1001) {
            m.load = (hw >> 11) & 1; m.rt = (hw >> 8) & 7; m.rn = 13;
            m.imm = (uint32_t)(hw & 0xFF) << 2; m.off_src = "ZEXT(IR[7:0])x4";
        } else {
            int imm = (hw >> 6) & 0x1F;
            m.load = (hw >> 11) & 1; m.rn = (hw >> 3) & 7; m.rt = hw & 7;
            m.width = top4 == 0b0110 ? 4 : top4 == 0b0111 ? 1 : 2;
            m.imm = (uint32_t)imm * m.width;
            m.off_src = m.width == 4 ? "ZEXT(IR[10:6])x4" : m.width == 2 ? "ZEXT(IR[10:6])x2" : "ZEXT(IR[10:6])";
        }
        return m;
    }

    static MemInfo mem_info32(uint32_t word) {
        MemInfo m;
        uint16_t hw1 = (word >> 16) & 0xFFFF, hw2 = word & 0xFFFF;
        int size = (hw1 >> 5) & 3;
        m.load = (hw1 >> 4) & 1; m.width = size == 2 ? 4 : size == 1 ? 2 : 1;
        m.rn = hw1 & 0xF; m.rt = (hw2 >> 12) & 0xF; m.imm = hw2 & 0xFFF;
        if (m.rn == 15) { m.rn = -1; m.lit = true; }
        m.off_src = "ZEXT(IR2[11:0])";
        return m;
    }

    uint32_t mem_read_w(uint32_t addr, int width) {
        return width == 4 ? read32(addr) : width == 2 ? read16(addr) : read8(addr);
    }
    void mem_write_w(uint32_t addr, uint32_t v, int width) {
        if (width == 4) write32(addr, v); else if (width == 2) write16(addr, v); else write8(addr, v);
    }

    // ── micro-op sequence builders ───────────────────────────────────────────

    void add(const char* state, const char* phase, std::function<void(CycleInfo&)> fn) {
        uops.push_back({state, phase, std::move(fn)});
    }

    void begin_fetch() {
        uops.clear(); upc = 0; in_insn = true; insn_total = -1;
        add("FETCH_ADDR", "FETCH", [this](CycleInfo& ci) {
            _insn_addr = regs[15];
            steps++;
            MAR = regs[15];
            regs[15] += 2;
            ci.signals = {"GatePC", "LD.MAR", "PCINC=+2", "PCMUX=PC+2", "LD.PC"};
            ci.has_bus = true; ci.bus = MAR;
            ci.desc = "MAR <- PC (" + hex32(MAR) + "); PC <- PC + 2 = " + hex32(regs[15]);
        });
        add("FETCH_MEMORY", "FETCH", [this](CycleInfo& ci) {
            MDR = read16(MAR);
            ci.signals = {"MEM.EN", "R/W=READ", "LD.MDR"};
            ci.desc = "MDR <- M[MAR] = " + hex16(MDR) + " (instruction halfword)";
        });
        add("FETCH_IR", "FETCH", [this](CycleInfo& ci) {
            IR = MDR & 0xFFFF;
            ci.signals = {"GateMDR", "LD.IR"};
            ci.has_bus = true; ci.bus = MDR;
            ci.desc = "IR <- MDR = " + hex16(IR);
            if (is_32bit_thumb((uint16_t)IR)) {
                ci.desc += "; 32-bit encoding, fetch second halfword";
                add_fetch2();
            } else {
                insn_total = insn_cycle_count((uint16_t)IR, 0);
                add("DECODE", "DECODE", [this](CycleInfo& c) { decode(c); });
            }
        });
    }

    void add_fetch2() {
        add("FETCH2_ADDR", "FETCH", [this](CycleInfo& ci) {
            MAR = regs[15];
            regs[15] += 2;
            ci.signals = {"GatePC", "LD.MAR", "PCINC=+2", "PCMUX=PC+2", "LD.PC"};
            ci.has_bus = true; ci.bus = MAR;
            ci.desc = "MAR <- PC (" + hex32(MAR) + "); PC <- PC + 2 = " + hex32(regs[15]);
        });
        add("FETCH2_MEMORY", "FETCH", [this](CycleInfo& ci) {
            MDR = read16(MAR);
            ci.signals = {"MEM.EN", "R/W=READ", "LD.MDR"};
            ci.desc = "MDR <- M[MAR] = " + hex16(MDR) + " (second halfword)";
        });
        add("FETCH2_IR", "FETCH", [this](CycleInfo& ci) {
            IR2 = MDR & 0xFFFF;
            ci.signals = {"GateMDR", "LD.IR2"};
            ci.has_bus = true; ci.bus = MDR;
            ci.desc = "IR2 <- MDR = " + hex16(IR2);
            insn_total = insn_cycle_count((uint16_t)IR, (uint16_t)IR2);
            add("DECODE", "DECODE", [this](CycleInfo& c) { decode(c); });
        });
    }

    static std::string imm_s(uint32_t v) { return "#" + std::to_string(v); }

    void decode(CycleInfo& ci) {
        bool wide = is_32bit_thumb((uint16_t)IR);
        uint32_t word = (IR << 16) | IR2;
        Plan p = wide ? classify32(word) : classify16((uint16_t)IR);
        switch (p.kind) {
        case K_ALU:   build_alu(ci, wide, word); break;
        case K_ADR:   build_adr(ci); break;
        case K_LOAD:
        case K_STORE: build_mem(ci, wide ? mem_info32(word) : mem_info16((uint16_t)IR)); break;
        case K_B:
        case K_BCOND: build_branch(ci, p.kind == K_BCOND); break;
        case K_BX:
        case K_BLX:   build_bx(ci, p.kind == K_BLX); break;
        case K_BL:    build_bl(ci, word); break;
        case K_PUSH: case K_POP: case K_LDM: case K_STM:
                      build_multi(ci, p, wide); break;
        case K_SVC: {
            int num = IR & 0xFF;
            ci.desc = "Decode: SVC #" + std::to_string(num) + " -> exception entry";
            add("SVC_CALL", "EXECUTE", [this, num](CycleInfo& c) {
                c.signals = {"GateVEC", "EXCEPTION"};
                c.has_bus = true; c.bus = 0x2C;   // SVCall is exception 11: vector at 11 x 4
                c.desc = "Exception entry (simplified): GateVEC drives the SVCall vector address 0x0000002C; "
                         "picosim then performs supervisor call #" + std::to_string(num) + " directly";
                exec_svc(num);
            });
            break;
        }
        case K_BKPT:
            ci.desc = "Decode: BKPT -> halt";
            add("HALT", "EXECUTE", [this](CycleInfo& c) {
                c.signals = {"HALT"};
                c.desc = "Breakpoint: the processor halts";
                halted = true;
            });
            break;
        case K_UDF:
            throw std::runtime_error("UDF at 0x" + std::to_string(_insn_addr));
        default:
            if (wide)
                throw std::runtime_error("Unimplemented 32-bit opcode 0x" +
                    std::to_string(word) + " at 0x" + std::to_string(_insn_addr));
            throw std::runtime_error("Unimplemented 16-bit opcode 0x" +
                std::to_string(IR) + " at 0x" + std::to_string(_insn_addr));
        }
        if ((int)uops.size() != insn_total)
            throw std::logic_error("cycle plan mismatch at 0x" + std::to_string(_insn_addr));
    }

    std::string src_desc(int r) const {
        return std::string(REGN[r]) + " = " + hex32(reg_read(r));
    }

    void build_alu(CycleInfo& ci, bool wide, uint32_t word) {
        AluInfo a = wide ? alu_info32(word) : alu_info16((uint16_t)IR);
        std::vector<std::string> sel;
        if (a.writes) sel.push_back(std::string("DR=") + REGN[a.rd]);
        if (a.ra >= 0) sel.push_back(std::string("SR1=") + REGN[a.ra]);
        if (a.rb >= 0) sel.push_back(std::string("SR2=") + REGN[a.rb]);
        if (a.has_imm) sel.push_back("SR2MUX=IMM");
        sel.push_back("ALUK=" + a.op);
        ci.signals = sel;
        ci.desc = "Decode: operate " + a.op + (a.flags ? "S" : "") + "; select operands and ALU function";
        add("FETCH_OPERANDS", "FETCH OPERANDS", [this, a](CycleInfo& c) {
            // ALU A is only loaded when the operation has a first operand
            // (MOV, MVN, NEG, SXTB… use B alone).
            if (a.ra >= 0) ALU_A = reg_read(a.ra);
            ALU_B = a.rb >= 0 ? reg_read(a.rb) : a.imm;
            if (a.ra >= 0) c.signals.push_back(std::string("SR1=") + REGN[a.ra]);
            if (a.rb >= 0) c.signals.push_back(std::string("SR2=") + REGN[a.rb]);
            if (a.has_imm) c.signals.push_back("SR2MUX=IMM");
            if (a.ra >= 0) c.signals.push_back("LD.ALUA");
            c.signals.push_back("LD.ALUB");
            std::string d = "ALU inputs: ";
            if (a.ra >= 0) d += "A <- " + src_desc(a.ra);
            else d += "A unused";
            d += ", B <- ";
            d += a.rb >= 0 ? src_desc(a.rb) : imm_s(a.imm);
            c.desc = d;
        });
        add("EXECUTE_COMMIT", "EXECUTE", [this, a, wide, word](CycleInfo& c) {
            uint32_t pc_before = regs[15];
            if (wide) exec32(_insn_addr, word); else exec16(_insn_addr, (uint16_t)IR);
            c.signals = {"ALUK=" + a.op};
            std::string d = "ALU computes " + a.op;
            if (a.writes) {
                c.signals.push_back("GateALU");
                c.signals.push_back(std::string("DR=") + REGN[a.rd]);
                if (a.rd == 15) {
                    c.signals.push_back("CLR THUMB BIT");
                    c.signals.push_back("PCMUX=BUS");
                    c.signals.push_back("LD.PC");
                } else {
                    c.signals.push_back("LD.REG");
                }
                c.has_bus = true; c.bus = regs[a.rd];
                d += "; " + std::string(REGN[a.rd]) + " <- " + hex32(regs[a.rd]);
                if (a.rd == 15 && regs[15] != pc_before) c.branch = 1;
            }
            if (a.flags) {
                c.signals.push_back("LD.CC");
                d += "; flags NZCV <- " + std::to_string(N) + std::to_string(Z) +
                     std::to_string(C) + std::to_string(V);
            }
            c.desc = d;
        });
    }

    void build_adr(CycleInfo& ci) {
        int rd = (IR >> 8) & 7;
        uint32_t imm = (IR & 0xFF) << 2;
        ci.signals = {std::string("DR=") + REGN[rd], "ADDR1MUX=Align(PC,4)", "ADDR2MUX=ZEXT(IR[7:0])x4"};
        ci.desc = "Decode: ADR; select aligned PC and IR[7:0] x 4";
        add("EVALUATE_ADDRESS", "EVALUATE ADDRESS", [this, imm](CycleInfo& c) {
            uint32_t base = (_insn_addr + 4) & ~3u;
            TMP = base + imm;
            c.signals = {"ADDR1MUX=Align(PC,4)", "ADDR2MUX=ZEXT(IR[7:0])x4"};
            c.desc = "Address adder: Align(PC,4) " + hex32(base) + " + " + imm_s(imm) + " = " + hex32(TMP);
        });
        add("STORE_RESULT", "STORE RESULT", [this, rd](CycleInfo& c) {
            exec16(_insn_addr, (uint16_t)IR);
            c.signals = {"ADDR1MUX=Align(PC,4)", "ADDR2MUX=ZEXT(IR[7:0])x4", "GateADDR",
                         std::string("DR=") + REGN[rd], "LD.REG"};
            c.has_bus = true; c.bus = regs[rd];
            c.desc = std::string(REGN[rd]) + " <- " + hex32(regs[rd]);
        });
    }

    void build_mem(CycleInfo& ci, MemInfo m) {
        std::string sz = m.width == 4 ? "word" : m.width == 2 ? "halfword" : "byte";
        std::string a1 = m.lit ? "Align(PC,4)" : m.rn == 13 ? "SP" : "SR1";
        ci.signals = {std::string(m.load ? "DR=" : "SR=") + REGN[m.rt], "ADDR1MUX=" + a1,
                      "ADDR2MUX=" + m.off_src, "SIZE=" + sz};
        ci.desc = std::string("Decode: ") + (m.load ? "load " : "store ") + sz +
                  "; select address inputs";
        add("EVALUATE_ADDRESS", "EVALUATE ADDRESS", [this, m, a1](CycleInfo& c) {
            uint32_t base = m.lit ? ((_insn_addr + 4) & ~3u) : regs[m.rn];
            uint32_t off  = m.rm >= 0 ? regs[m.rm] : m.imm;
            MAR = base + off;
            c.signals = {"ADDR1MUX=" + a1, "ADDR2MUX=" + m.off_src, "GateADDR", "LD.MAR"};
            if (!m.lit) c.signals.push_back(std::string("SR1=") + REGN[m.rn]);
            if (m.rm >= 0) c.signals.push_back(std::string("SR2=") + REGN[m.rm]);
            c.has_bus = true; c.bus = MAR;
            std::string bs = m.lit ? "Align(PC,4) " + hex32(base) : src_desc(m.rn);
            std::string os = m.rm >= 0 ? src_desc(m.rm) : imm_s(off);
            c.desc = "MAR <- " + bs + " + " + os + " = " + hex32(MAR);
        });
        if (m.load) {
            add("FETCH_OPERANDS", "FETCH OPERANDS", [this, m, sz](CycleInfo& c) {
                MDR = mem_read_w(MAR, m.width);
                c.signals = {"MEM.EN", "R/W=READ", "LD.MDR"};
                if (is_peripheral(MAR)) c.signals.push_back("IOSEL=IO");
                c.desc = "MDR <- M[" + hex32(MAR) + "] (" + sz + ") = " + hex32(MDR);
            });
            add("STORE_RESULT", "STORE RESULT", [this, m, sz](CycleInfo& c) {
                uint32_t v = MDR;
                if (m.sign) v = m.width == 1 ? u32((int32_t)(int8_t)v) : u32((int32_t)(int16_t)v);
                reg_write(m.rt, v);
                c.signals = {std::string("LOAD EXT=") + (m.sign ? "S" : "Z") + sz, "GateMDR"};
                if (m.rt == 15) {
                    c.signals.insert(c.signals.end(), {"CLR THUMB BIT", "PCMUX=BUS", "LD.PC"});
                    c.branch = 1;
                } else {
                    c.signals.insert(c.signals.end(), {std::string("DR=") + REGN[m.rt], "LD.REG"});
                }
                c.has_bus = true; c.bus = v;
                c.desc = std::string(REGN[m.rt]) + " <- " + (m.sign ? "sign" : "zero") +
                         "-extend(MDR) = " + hex32(regs[m.rt]);
            });
        } else {
            add("FETCH_OPERANDS", "FETCH OPERANDS", [this, m](CycleInfo& c) {
                MDR = m.width == 4 ? regs[m.rt] : m.width == 2 ? (regs[m.rt] & 0xFFFF) : (regs[m.rt] & 0xFF);
                c.signals = {std::string("SR1=") + REGN[m.rt], "ALUK=PASS", "GateALU",
                             "STORE ALIGN", "LD.MDR"};
                c.has_bus = true; c.bus = regs[m.rt];
                c.desc = "MDR <- " + src_desc(m.rt) + " (through ALU pass-through)";
            });
            add("STORE_RESULT", "STORE RESULT", [this, m, sz](CycleInfo& c) {
                mem_write_w(MAR, MDR, m.width);
                c.signals = {"MEM.EN", "R/W=WRITE"};
                if (is_peripheral(MAR)) c.signals.push_back("IOSEL=IO");
                c.desc = "M[" + hex32(MAR) + "] (" + sz + ") <- MDR = " + hex32(MDR);
            });
        }
    }

    void build_branch(CycleInfo& ci, bool cond) {
        int c4 = (IR >> 8) & 0xF;
        static const char* cn[] = {"EQ","NE","CS","CC","MI","PL","VS","VC",
                                   "HI","LS","GE","LT","GT","LE","AL","??"};
        ci.signals = {"ADDR1MUX=PC", "ADDR2MUX=SEXT(offset)x2"};
        if (cond) ci.signals.push_back(std::string("COND=") + cn[c4]);
        ci.desc = cond ? std::string("Decode: conditional branch B") + cn[c4] + "; read flags"
                       : "Decode: branch; select PC-relative target";
        add("EXECUTE_PC", "EXECUTE", [this, cond, c4](CycleInfo& c) {
            int32_t off = cond ? sign_extend(IR & 0xFF, 8) * 2 : sign_extend(IR & 0x7FF, 11) * 2;
            uint32_t target = u32((int32_t)(_insn_addr + 4) + off);
            bool taken = !cond || check_cond(c4);
            exec16(_insn_addr, (uint16_t)IR);
            c.signals = {"ADDR1MUX=PC", "ADDR2MUX=SEXT(offset)x2"};
            if (cond) c.signals.push_back(std::string("BranchTaken=") + (taken ? "1" : "0"));
            c.has_bus = false;
            c.branch = taken ? 1 : 0;
            if (taken) {
                c.signals.push_back("PCMUX=ADDER");
                c.signals.push_back("LD.PC");
                c.desc = "Target = PC(" + hex32(_insn_addr + 4) + ") + " + std::to_string(off) +
                         " = " + hex32(target) + "; branch taken, PC <- target";
            } else {
                c.desc = std::string("Condition ") + cn[c4] + " false (NZCV=" + std::to_string(N) +
                         std::to_string(Z) + std::to_string(C) + std::to_string(V) +
                         "); branch not taken, PC stays " + hex32(regs[15]);
            }
        });
    }

    void build_bx(CycleInfo& ci, bool link) {
        int rm = (IR >> 3) & 0xF;
        ci.signals = {std::string("SR1=") + REGN[rm]};
        ci.desc = std::string("Decode: ") + (link ? "BLX" : "BX") + " " + REGN[rm] + "; indirect branch";
        add("FETCH_OPERANDS", "FETCH OPERANDS", [this, rm](CycleInfo& c) {
            TMP = reg_read(rm);
            ALU_A = TMP;
            c.signals = {std::string("SR1=") + REGN[rm], "LD.ALUA"};
            c.desc = "ALU A <- target " + src_desc(rm);
        });
        if (link) {
            add("LINK", "STORE RESULT", [this](CycleInfo& c) {
                set_lr(regs[15] | 1);
                c.signals = {"GatePC", "SET THUMB BIT", "LD.LR"};
                c.has_bus = true; c.bus = regs[15];
                c.desc = "LR <- return address " + hex32(regs[15]) + " | 1 = " + hex32(regs[14]);
            });
        }
        add("EXECUTE_PC", "EXECUTE", [this](CycleInfo& c) {
            set_pc(TMP & 0xFFFFFFFEu);
            c.signals = {"ALUK=PASS", "GateALU", "CLR THUMB BIT", "PCMUX=BUS", "LD.PC"};
            c.has_bus = true; c.bus = TMP;
            c.branch = 1;
            c.desc = "PC <- " + hex32(TMP) + " & ~1 = " + hex32(regs[15]);
        });
    }

    void build_bl(CycleInfo& ci, uint32_t word) {
        uint16_t hw1 = (word >> 16) & 0xFFFF, hw2 = word & 0xFFFF;
        bool blx = (hw2 & 0xD000) == 0xC000;
        uint32_t S = (hw1 >> 10) & 1, J1 = (hw2 >> 13) & 1, J2 = (hw2 >> 11) & 1;
        uint32_t I1 = (~(J1 ^ S)) & 1, I2 = (~(J2 ^ S)) & 1;
        int32_t off = blx
            ? sign_extend((S << 24) | (I1 << 23) | (I2 << 22) | ((hw1 & 0x3FFu) << 12) | (((hw2 >> 1) & 0x3FFu) << 2), 25)
            : sign_extend((S << 24) | (I1 << 23) | (I2 << 22) | ((hw1 & 0x3FFu) << 12) | ((hw2 & 0x7FFu) << 1), 25);
        ci.signals = {"DR=LR", "ADDR1MUX=PC", "ADDR2MUX=SEXT(offset)x2"};
        ci.desc = "Decode: BL; combine IR and IR2 offset fields = " + std::to_string(off);
        add("LINK", "STORE RESULT", [this](CycleInfo& c) {
            set_lr(regs[15] | 1);
            c.signals = {"GatePC", "SET THUMB BIT", "LD.LR"};
            c.has_bus = true; c.bus = regs[15];
            c.desc = "LR <- return address " + hex32(regs[15]) + " | 1 = " + hex32(regs[14]);
        });
        add("EXECUTE_PC", "EXECUTE", [this, off, blx](CycleInfo& c) {
            uint32_t base = regs[15];
            uint32_t t = u32((int32_t)base + off);
            if (blx) t &= ~3u;
            set_pc(t);
            c.signals = {"ADDR1MUX=PC", "ADDR2MUX=SEXT(offset)x2", "PCMUX=ADDER", "LD.PC"};
            c.branch = 1;
            c.desc = "PC <- " + hex32(base) + " + " + std::to_string(off) + " = " + hex32(regs[15]);
        });
    }

    void build_multi(CycleInfo& ci, Plan p, bool wide) {
        uint32_t list; int rn; bool load;
        if (wide) { list = IR2 & 0xFFFF; rn = IR & 0xF; load = (IR >> 4) & 1; }
        else if (p.kind == K_PUSH) { list = (IR & 0xFF) | ((IR & 0x100) ? 0x4000 : 0); rn = 13; load = false; }
        else if (p.kind == K_POP)  { list = (IR & 0xFF) | ((IR & 0x100) ? 0x8000 : 0); rn = 13; load = true; }
        else { list = IR & 0xFF; rn = (IR >> 8) & 7; load = p.kind == K_LDM; }
        bool down = p.kind == K_PUSH;
        uint32_t total = 4u * (uint32_t)p.nregs;
        const char* nm = p.kind == K_PUSH ? "PUSH" : p.kind == K_POP ? "POP" : load ? "LDM" : "STM";
        ci.signals = {std::string("SR1=") + REGN[rn]};
        ci.desc = std::string("Decode: ") + nm + " of " + std::to_string(p.nregs) + " register(s)";
        add("EVALUATE_ADDRESS", "EVALUATE ADDRESS", [this, rn, down, total](CycleInfo& c) {
            MAR = down ? regs[rn] - total : regs[rn];
            c.signals = {std::string("ADDR1MUX=") + (rn == 13 ? "SP" : "SR1"),
                         down ? "ADDR2MUX=-4n" : "ADDR2MUX=0", "GateADDR", "LD.MAR",
                         std::string("SR1=") + REGN[rn]};
            c.has_bus = true; c.bus = MAR;
            c.desc = "MAR <- " + src_desc(rn) + (down ? " - " + std::to_string(total) : "") +
                     " = " + hex32(MAR);
        });
        for (int i = 0; i < 16; ++i) {
            if (!(list & (1u << i))) continue;
            if (load) {
                add("FETCH_OPERANDS", "FETCH OPERANDS", [this](CycleInfo& c) {
                    MDR = read32(MAR);
                    c.signals = {"MEM.EN", "R/W=READ", "LD.MDR"};
                    c.desc = "MDR <- M[" + hex32(MAR) + "] = " + hex32(MDR);
                });
                if (i == 15) {
                    add("EXECUTE_PC", "EXECUTE", [this](CycleInfo& c) {
                        set_pc(MDR & 0xFFFFFFFEu);
                        MAR += 4;
                        c.signals = {"GateMDR", "CLR THUMB BIT", "PCMUX=BUS", "LD.PC", "MAR+4"};
                        c.has_bus = true; c.bus = MDR; c.branch = 1;
                        c.desc = "PC <- MDR & ~1 = " + hex32(regs[15]) + "; MAR <- MAR + 4";
                    });
                } else {
                    add("STORE_RESULT", "STORE RESULT", [this, i](CycleInfo& c) {
                        reg_write(i, MDR);
                        MAR += 4;
                        c.signals = {"GateMDR", std::string("DR=") + REGN[i], "LD.REG", "MAR+4"};
                        c.has_bus = true; c.bus = MDR;
                        c.desc = std::string(REGN[i]) + " <- MDR = " + hex32(MDR) + "; MAR <- MAR + 4";
                    });
                }
            } else {
                add("FETCH_OPERANDS", "FETCH OPERANDS", [this, i](CycleInfo& c) {
                    MDR = regs[i];
                    c.signals = {std::string("SR1=") + REGN[i], "ALUK=PASS", "GateALU", "LD.MDR"};
                    c.has_bus = true; c.bus = MDR;
                    c.desc = "MDR <- " + std::string(REGN[i]) + " = " + hex32(MDR);
                });
                add("STORE_RESULT", "STORE RESULT", [this](CycleInfo& c) {
                    write32(MAR, MDR);
                    c.signals = {"MEM.EN", "R/W=WRITE", "MAR+4"};
                    c.desc = "M[" + hex32(MAR) + "] <- MDR = " + hex32(MDR) + "; MAR <- MAR + 4";
                    MAR += 4;
                });
            }
        }
        if (p.writeback) {
            add("WRITEBACK", "STORE RESULT", [this, rn, down, total](CycleInfo& c) {
                uint32_t v = down ? regs[rn] - total : regs[rn] + total;
                regs[rn] = v;
                c.signals = {std::string("ADDR1MUX=") + (rn == 13 ? "SP" : "SR1"),
                             down ? "ADDR2MUX=-4n" : "ADDR2MUX=+4n", "GateADDR",
                             std::string("SR1=") + REGN[rn], std::string("DR=") + REGN[rn],
                             rn == 13 ? "LD.SP" : "LD.REG"};
                c.has_bus = true; c.bus = v;
                c.desc = std::string(REGN[rn]) + " <- " + REGN[rn] + (down ? " - " : " + ") +
                         std::to_string(total) + " = " + hex32(v);
            });
        }
    }

    // ── one clock cycle ──────────────────────────────────────────────────────

    CycleInfo step_cycle() {
        CycleInfo ci;
        if (halted) { ci.state = "HALTED"; ci.phase = "HALTED"; ci.desc = "Processor is halted"; return ci; }
        if (!in_insn) begin_fetch();

        uint32_t r0[16]; std::memcpy(r0, regs, sizeof r0);
        int f0[4] = {N, Z, C, V};
        uint32_t d0[6] = {MAR, MDR, IR, IR2, ALU_A, ALU_B};
        std::vector<MemWrite> mlog;

        std::string state = uops[upc].state, phase = uops[upc].phase;
        auto fn = uops[upc].fn;   // copy: DECODE may grow uops
        ci.state = state; ci.phase = phase; ci.index = (int)upc;
        mem_log = &mlog;
        try {
            fn(ci);
        } catch (...) {
            mem_log = nullptr; in_insn = false; uops.clear();
            throw;
        }
        mem_log = nullptr;
        ci.insn_addr = _insn_addr;
        upc++; cycles++;
        ci.total = insn_total;

        static const char* FN[4] = {"N", "Z", "C", "V"};
        static const char* DN[6] = {"MAR", "MDR", "IR", "IR2", "ALU_A", "ALU_B"};
        for (int i = 0; i < 16; ++i)
            if (regs[i] != r0[i]) ci.changes.push_back({CH_REG, REGN[i], r0[i], regs[i], 0, 4});
        int f1[4] = {N, Z, C, V};
        for (int i = 0; i < 4; ++i)
            if (f1[i] != f0[i]) ci.changes.push_back({CH_FLAG, FN[i], (uint32_t)f0[i], (uint32_t)f1[i], 0, 1});
        uint32_t d1[6] = {MAR, MDR, IR, IR2, ALU_A, ALU_B};
        for (int i = 0; i < 6; ++i)
            if (d1[i] != d0[i]) ci.changes.push_back({CH_DP, DN[i], d0[i], d1[i], 0, 4});
        for (auto& w : mlog) {
            char b[32]; std::snprintf(b, sizeof b, w.io ? "IO[0x%08X]" : "M[0x%04X]", w.addr);
            ci.changes.push_back({w.io ? CH_IO : CH_MEM, b, w.old_v, w.val, w.addr, w.width});
        }

        if (upc >= uops.size()) {
            in_insn = false;
            ci.last = true;
            check_halt();
        }
        return ci;
    }

    // Name of the state the next step_cycle() will execute.
    std::string next_state() const {
        if (halted) return "HALTED";
        if (!in_insn) return "FETCH_ADDR";
        return uops[upc].state;
    }

    // ── SVC ──────────────────────────────────────────────────────────────────

    void exec_svc(int num) {
        if (svc_handler) {
            py::gil_scoped_acquire acquire;
            svc_handler(num);
        }
    }
};


// ═════════════════════════════════════════════════════════════════════════════
//  pybind11 module
// ═════════════════════════════════════════════════════════════════════════════

PYBIND11_MODULE(_picosim_core, m) {
    m.doc() = "ARMv6-M CPU core (C++)";

    py::class_<CPUCore>(m, "CPUCore")
        .def(py::init<>())

        // ── raw memory access (for ELF loading from Python) ───────────────
        .def("load_memory", [](CPUCore& self, py::bytes data, uint32_t offset) {
            auto buf = data.cast<std::string_view>();
            if (offset + buf.size() > 0x10000)
                throw std::runtime_error("load_memory: data overflows 64 KB");
            std::memcpy(self.mem + offset, buf.data(), buf.size());
        })
        .def("get_memory", [](CPUCore& self) {
            return py::bytes(reinterpret_cast<const char*>(self.mem), 0x10000);
        })
        .def("get_mem_slice", [](CPUCore& self, uint32_t offset, uint32_t length) {
            if (offset + length > 0x10000)
                throw std::runtime_error("get_mem_slice: out of bounds");
            return py::bytes(reinterpret_cast<const char*>(self.mem + offset), length);
        })
        .def("set_mem_byte", [](CPUCore& self, uint32_t addr, uint8_t val) {
            self.mem[addr & 0xFFFF] = val;
        })
        .def("read8",  [](CPUCore& self, uint32_t a) { return self.read8(a); })
        .def("read16", [](CPUCore& self, uint32_t a) { return self.read16(a); })
        .def("read32", [](CPUCore& self, uint32_t a) { return self.read32(a); })
        .def("write8",  [](CPUCore& self, uint32_t a, uint32_t v) { self.write8(a, v); })
        .def("write16", [](CPUCore& self, uint32_t a, uint32_t v) { self.write16(a, v); })
        .def("write32", [](CPUCore& self, uint32_t a, uint32_t v) { self.write32(a, v); })

        // ── registers ─────────────────────────────────────────────────────
        .def_property("pc",
            [](const CPUCore& self) { return self.get_pc(); },
            [](CPUCore& self, uint32_t v) { self.set_pc(v); })
        .def_property("sp",
            [](const CPUCore& self) { return self.get_sp(); },
            [](CPUCore& self, uint32_t v) { self.set_sp(v); })
        .def_property("lr",
            [](const CPUCore& self) { return self.get_lr(); },
            [](CPUCore& self, uint32_t v) { self.set_lr(v); })
        .def("get_reg", [](const CPUCore& self, int n) { return self.regs[n & 15]; })
        .def("set_reg", [](CPUCore& self, int n, uint32_t v) { self.reg_write(n & 15, v); })
        .def_property("regs",
            [](const CPUCore& self) {
                return std::vector<uint32_t>(self.regs, self.regs + 16);
            },
            [](CPUCore& self, const std::vector<uint32_t>& r) {
                for (int i = 0; i < 16 && i < (int)r.size(); ++i) self.regs[i] = r[i];
            })

        // ── flags ─────────────────────────────────────────────────────────
        .def_readwrite("N", &CPUCore::N)
        .def_readwrite("Z", &CPUCore::Z)
        .def_readwrite("C", &CPUCore::C)
        .def_readwrite("V", &CPUCore::V)

        // ── state ─────────────────────────────────────────────────────────
        .def_readwrite("halted", &CPUCore::halted)
        .def_readwrite("steps",  &CPUCore::steps)
        .def_readwrite("trace",  &CPUCore::trace)

        // ── callbacks ─────────────────────────────────────────────────────
        .def_readwrite("peripheral_read",  &CPUCore::peripheral_read)
        .def_readwrite("peripheral_write", &CPUCore::peripheral_write)
        .def_readwrite("svc_handler",      &CPUCore::svc_handler)

        // ── execution ─────────────────────────────────────────────────────
        .def("step",       &CPUCore::step)
        .def("check_halt", &CPUCore::check_halt)
        .def("run", &CPUCore::run,
             py::arg("max_steps") = -1,
             "Run until halted (or max_steps if >= 0). Releases the GIL.")
        .def("is_32bit_thumb", [](CPUCore&, uint16_t hw) {
            return CPUCore::is_32bit_thumb(hw);
        })

        // ── cycle-level execution ─────────────────────────────────────────
        .def("step_cycle", [](CPUCore& self) {
            CycleInfo ci = self.step_cycle();
            py::list changes;
            static const char* KN[] = {"reg", "flag", "datapath", "mem", "io"};
            for (auto& c : ci.changes) {
                py::dict d;
                d["kind"] = KN[c.kind]; d["name"] = c.name;
                d["old"] = c.old_v; d["new"] = c.new_v;
                if (c.kind >= CH_MEM) { d["addr"] = c.addr; d["width"] = c.width; }
                changes.append(d);
            }
            py::dict d;
            d["state"] = ci.state; d["phase"] = ci.phase; d["desc"] = ci.desc;
            d["signals"] = ci.signals;
            d["bus"] = ci.has_bus ? py::object(py::int_(ci.bus)) : py::object(py::none());
            d["branch"] = ci.branch < 0 ? py::object(py::none()) : py::object(py::bool_(ci.branch == 1));
            d["insn_addr"] = ci.insn_addr; d["index"] = ci.index; d["total"] = ci.total;
            d["last"] = ci.last; d["changes"] = changes;
            return d;
        }, "Advance exactly one clock cycle; returns a description of the cycle.")
        .def("next_state", &CPUCore::next_state)
        .def_property_readonly("in_insn", [](const CPUCore& s) { return s.in_insn; })
        .def_property_readonly("insn_addr", [](const CPUCore& s) { return s._insn_addr; })
        .def_property_readonly("cycle_index", [](const CPUCore& s) { return s.in_insn ? (int)s.upc : 0; })
        .def_property_readonly("insn_total", [](const CPUCore& s) { return s.insn_total; })
        .def_readwrite("cycles", &CPUCore::cycles)
        .def_property_readonly("datapath", [](const CPUCore& s) {
            py::dict d;
            d["MAR"] = s.MAR; d["MDR"] = s.MDR; d["IR"] = s.IR; d["IR2"] = s.IR2;
            d["ALU_A"] = s.ALU_A; d["ALU_B"] = s.ALU_B;
            return d;
        })
        .def_static("insn_cycle_count", [](uint16_t hw1, uint16_t hw2) {
            return CPUCore::insn_cycle_count(hw1, hw2);
        })
        ;
}
