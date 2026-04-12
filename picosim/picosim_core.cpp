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
#include <cstring>
#include <stdexcept>
#include <string>

namespace py = pybind11;

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

    // ── register helpers ─────────────────────────────────────────────────────

    uint32_t get_pc() const { return regs[15]; }
    void     set_pc(uint32_t v) { regs[15] = v & 0xFFFFFFFF; }
    uint32_t get_sp() const { return regs[13]; }
    void     set_sp(uint32_t v) { regs[13] = v; }
    uint32_t get_lr() const { return regs[14]; }
    void     set_lr(uint32_t v) { regs[14] = v; }

    // ARM pipeline-visible PC for the currently-executing instruction.
    // After fetch the PC has advanced by 2 or 4; _insn_addr holds the pre-fetch address.
    uint32_t reg_read(int n) const {
        if (n == 15) return (_insn_addr + 4) & ~3u;
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

    void write8 (uint32_t addr, uint32_t val) {
        if (is_peripheral(addr)) {
            if (peripheral_write) peripheral_write(addr, val & 0xFF, 1);
            return;
        }
        mem[addr & 0xFFFF] = (uint8_t)(val & 0xFF);
    }

    void write16(uint32_t addr, uint32_t val) {
        if (is_peripheral(addr)) {
            if (peripheral_write) peripheral_write(addr, val & 0xFFFF, 2);
            return;
        }
        uint16_t v = (uint16_t)(val & 0xFFFF);
        std::memcpy(&mem[addr & 0xFFFF], &v, 2);
    }

    void write32(uint32_t addr, uint32_t val) {
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

    void update_nzcv_add(uint32_t a, uint32_t b, uint64_t result) {
        uint32_t r32 = (uint32_t)(result & 0xFFFFFFFF);
        N = (r32 >> 31) & 1;
        Z = (r32 == 0) ? 1 : 0;
        C = (result > 0xFFFFFFFFull) ? 1 : 0;
        int32_t sa = s32(a), sb = s32(b), sr = s32(r32);
        V = ((sa > 0 && sb > 0 && sr < 0) || (sa < 0 && sb < 0 && sr > 0)) ? 1 : 0;
    }

    void update_nzcv_sub(uint32_t a, uint32_t b, uint32_t r) {
        N = (r >> 31) & 1;
        Z = (r == 0) ? 1 : 0;
        C = (a >= b) ? 1 : 0;
        int32_t sa = s32(a), sb = s32(b), sr = s32(r);
        V = ((sa > 0 && sb < 0 && sr < 0) || (sa < 0 && sb > 0 && sr > 0)) ? 1 : 0;
    }

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
        steps++;
        _insn_addr = regs[15];

        uint16_t hw1 = fetch16();
        if (is_32bit_thumb(hw1)) {
            uint16_t hw2 = fetch16();
            uint32_t word = ((uint32_t)hw1 << 16) | hw2;
            exec32(_insn_addr, word);
        } else {
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
                auto [r, c] = lsr(a, n ? n : 32);
                if (n) C = c;
                update_nz(r); reg_write(rdn, r); break;
            }
            case 0x4: {  // ASR reg
                int n = b & 0xFF;
                auto [r, c] = asr(a, n ? n : 32);
                if (n) C = c;
                update_nz(r); reg_write(rdn, r); break;
            }
            case 0x5: {  // ADC
                uint64_t r = (uint64_t)a + b + C;
                update_nzcv_add(a, b, r); reg_write(rdn, (uint32_t)r); break;
            }
            case 0x6: {  // SBC
                uint32_t r = a - b - (1 - C); update_nzcv_sub(a, b, r); reg_write(rdn, r); break;
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
                uint64_t r = (uint64_t)rn_val + imm + C;
                if (S) update_nzcv_add(rn_val, imm, r);
                reg_write(rd, (uint32_t)r); break;
            }
            case 0xB: {  // SBC
                uint32_t r = rn_val - imm - (1 - C);
                if (S) update_nzcv_sub(rn_val, imm, r);
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
            uint32_t rn_val = reg_read(rn);
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
        ;
}
