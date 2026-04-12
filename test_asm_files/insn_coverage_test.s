.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * insn_coverage_test.s — Exercises all instruction categories not covered
 * by the other test files.  Returns 0 if every test passes, otherwise the
 * number of failures.
 *
 * Tested here (not elsewhere):
 *   Shifts (imm):  lsl, lsr, asr #imm  (positive and negative operands)
 *   Shifts (reg):  lsl, lsr, asr, ror  by register
 *   ALU:           and, orr, eor, bic, mvn, mul, tst, cmn, adc, sbc, neg
 *   Sign-extend:   sxth, sxtb
 *   Byte-reverse:  rev, rev16, revsh
 *   Load/store:    ldrsb, ldrsh (register offset)
 *   Block xfer:    stmia, ldmia
 *   Wide imm:      (movw/movt not available on ARMv6-M / Cortex-M0+)
 *   Branches:      bne, bge, blt, bgt, ble, bmi, bpl, bhi, bcs
 */

main:
    push    {r4, r5, r6, r7, lr}
    movs    r7, #0              @ r7 = failure counter

    /* ── Shifts by immediate ──────────────────────────────────────────── */

    @ LSL #imm: 5 << 3 = 40
    movs    r0, #5
    lsls    r1, r0, #3
    movs    r0, #40
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ LSR #imm: 80 >> 3 = 10
    movs    r0, #80
    lsrs    r1, r0, #3
    movs    r0, #10
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ ASR #imm (positive): 80 >> 2 = 20
    movs    r0, #80
    asrs    r1, r0, #2
    movs    r0, #20
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ ASR #imm (negative): 0x80000000 >> 1 = 0xC0000000
    movs    r0, #1
    lsls    r0, r0, #31         @ r0 = 0x80000000
    asrs    r1, r0, #1          @ r1 = 0xC0000000
    movs    r0, #3
    lsls    r0, r0, #30         @ r0 = 0xC0000000
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:

    /* ── Shifts by register ───────────────────────────────────────────── */

    @ LSL reg: 1 << 8 = 256
    movs    r0, #1
    movs    r1, #8
    lsls    r0, r1              @ r0 = r0 << r1 = 256
    movs    r1, #1
    lsls    r1, r1, #8
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ LSR reg: 256 >> 4 = 16
    movs    r0, #1
    lsls    r0, r0, #8          @ r0 = 256
    movs    r1, #4
    lsrs    r0, r1              @ r0 = 256 >> 4 = 16
    movs    r1, #16
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ ASR reg: 64 >> 2 = 16
    movs    r0, #64
    movs    r1, #2
    asrs    r0, r1              @ r0 = 64 >> 2 = 16
    movs    r1, #16
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ ROR reg: 0x01 ror 4 = 0x10000000
    movs    r0, #1
    movs    r1, #4
    rors    r0, r1
    movs    r1, #1
    lsls    r1, r1, #28         @ r1 = 0x10000000
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:

    /* ── ALU operations ───────────────────────────────────────────────── */

    @ AND: 0xFF & 0x0F = 0x0F
    movs    r0, #0xFF
    movs    r1, #0x0F
    ands    r0, r1
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ ORR: 0x0F | 0xF0 = 0xFF
    movs    r0, #0x0F
    movs    r1, #0xF0
    orrs    r0, r1
    movs    r1, #0xFF
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ EOR: 0xFF ^ 0x0F = 0xF0
    movs    r0, #0xFF
    movs    r1, #0x0F
    eors    r0, r1
    movs    r1, #0xF0
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ BIC: 0xFF & ~0x0F = 0xF0
    movs    r0, #0xFF
    movs    r1, #0x0F
    bics    r0, r1
    movs    r1, #0xF0
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ MVN: ~0 + 1 = 0  (wraps)
    movs    r0, #0
    mvns    r1, r0              @ r1 = 0xFFFFFFFF
    adds    r1, r1, #1          @ r1 = 0 (wraps)
    cmp     r1, #0
    beq     1f
    adds    r7, r7, #1
1:
    @ MUL: 6 * 7 = 42
    movs    r0, #6
    movs    r1, #7
    muls    r0, r1, r0          @ r0 = r0 * r1
    movs    r1, #42
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ TST: 0xF0 & 0x0F = 0 → Z=1
    movs    r0, #0xF0
    movs    r1, #0x0F
    tst     r0, r1
    beq     1f                  @ Z=1 expected
    adds    r7, r7, #1
1:
    @ CMN: 0 + 0 = 0 → Z=1
    movs    r0, #0
    cmn     r0, r0
    beq     1f                  @ Z=1 expected
    adds    r7, r7, #1
1:

    @ ADC: carry from 0x80000000+0x80000000, then 5+0+C=6
    movs    r0, #1
    lsls    r0, r0, #31         @ r0 = 0x80000000
    adds    r0, r0, r0          @ r0 = 0, C = 1
    movs    r1, #5
    adcs    r1, r0              @ r1 = 5 + 0 + 1 = 6
    movs    r0, #6
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ SBC: borrow from 0-1 (C=0), then 10-3-(1-C) = 10-3-1 = 6
    movs    r0, #0
    movs    r1, #1
    subs    r0, r0, r1          @ r0 = 0xFFFFFFFF, C = 0 (borrow)
    movs    r0, #10
    movs    r1, #3
    sbcs    r0, r1              @ r0 = 10 - 3 - 1 = 6
    movs    r1, #6
    cmp     r0, r1
    beq     1f
    adds    r7, r7, #1
1:
    @ NEG (rd ≠ rm — tests the correct Rm encoding): r1 = 0 - r0 = -5
    movs    r0, #5
    negs    r1, r0              @ r1 = -5
    adds    r1, r1, r0          @ r1 = -5 + 5 = 0
    cmp     r1, #0
    beq     1f
    adds    r7, r7, #1
1:

    /* ── Sign extension ───────────────────────────────────────────────── */

    @ SXTH: sign-extend halfword 0x8000 → 0xFFFF8000
    movs    r0, #1
    lsls    r0, r0, #15         @ r0 = 0x8000
    sxth    r1, r0              @ r1 = 0xFFFF8000
    movs    r0, #1
    lsls    r0, r0, #15         @ r0 = 0x8000
    mvns    r0, r0              @ r0 = 0xFFFF7FFF
    adds    r0, r0, #1          @ r0 = 0xFFFF8000
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ SXTB: sign-extend byte 0x80 → 0xFFFFFF80
    movs    r0, #0x80
    sxtb    r1, r0              @ r1 = 0xFFFFFF80
    movs    r0, #0x80
    mvns    r0, r0              @ r0 = 0xFFFFFF7F
    adds    r0, r0, #1          @ r0 = 0xFFFFFF80
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:

    /* ── Byte-reverse ─────────────────────────────────────────────────── */

    @ REV: 0x01000000 → 0x00000001
    movs    r0, #1
    lsls    r0, r0, #24         @ r0 = 0x01000000
    rev     r1, r0
    movs    r0, #1
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ REV16: reverse bytes within each halfword
    @   0x00010002 → hi half 0x0001→0x0100, lo half 0x0002→0x0200 → 0x01000200
    movs    r0, #1
    lsls    r0, r0, #16         @ r0 = 0x00010000
    adds    r0, r0, #2          @ r0 = 0x00010002
    rev16   r1, r0              @ r1 = 0x01000200
    movs    r0, #2
    lsls    r0, r0, #8          @ r0 = 0x00000200
    movs    r2, #1
    lsls    r2, r2, #24         @ r2 = 0x01000000
    adds    r0, r0, r2          @ r0 = 0x01000200
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ REVSH: reverse bytes of low halfword, sign-extend
    @   0x0080 → byte-reversed = 0x8000 = -32768 signed → 0xFFFF8000
    movs    r0, #0x80           @ r0 = 0x0080
    revsh   r1, r0              @ r1 = sign_extend(0x8000) = 0xFFFF8000
    movs    r0, #1
    lsls    r0, r0, #15         @ r0 = 0x8000
    mvns    r0, r0              @ r0 = 0xFFFF7FFF
    adds    r0, r0, #1          @ r0 = 0xFFFF8000
    cmp     r1, r0
    beq     1f
    adds    r7, r7, #1
1:

    /* ── Load/store with register offset ──────────────────────────────── */

    @ LDRSB: store 0xFF, load as signed byte → 0xFFFFFFFF
    sub     sp, sp, #4
    movs    r0, #0xFF
    mov     r1, sp
    movs    r2, #0
    strb    r0, [r1, r2]        @ STR byte via register offset
    ldrsb   r3, [r1, r2]        @ load signed byte
    add     sp, sp, #4
    movs    r0, #1
    negs    r0, r0              @ r0 = -1 = 0xFFFFFFFF
    cmp     r3, r0
    beq     1f
    adds    r7, r7, #1
1:
    @ LDRSH: store 0xFF80 (signed = -128), load as signed halfword → 0xFFFFFF80
    sub     sp, sp, #4
    movs    r0, #0xFF
    lsls    r0, r0, #8
    adds    r0, r0, #0x80       @ r0 = 0xFF80
    mov     r1, sp
    movs    r2, #0
    strh    r0, [r1, r2]        @ STR halfword via register offset
    ldrsh   r3, [r1, r2]        @ load signed halfword
    add     sp, sp, #4
    movs    r0, #0x80
    mvns    r0, r0              @ r0 = 0xFFFFFF7F
    adds    r0, r0, #1          @ r0 = 0xFFFFFF80
    cmp     r3, r0
    beq     1f
    adds    r7, r7, #1
1:

    /* ── Block transfer: STMIA / LDMIA ────────────────────────────────── */

    sub     sp, sp, #16
    movs    r0, #10
    movs    r1, #20
    movs    r2, #30
    movs    r3, #40
    mov     r4, sp
    stmia   r4!, {r0, r1, r2, r3}   @ store r0-r3, r4 advances by 16
    mov     r4, sp                   @ reset base
    movs    r0, #0
    movs    r1, #0
    movs    r2, #0
    movs    r3, #0
    ldmia   r4!, {r0, r1, r2, r3}   @ reload; r4 advances by 16
    add     sp, sp, #16
    movs    r5, #10
    cmp     r0, r5
    beq     1f
    adds    r7, r7, #1
1:
    movs    r5, #40
    cmp     r3, r5
    beq     1f
    adds    r7, r7, #1
1:


    /* ── Conditional branches ─────────────────────────────────────────── */

    @ BNE: 1 ≠ 2 → taken
    movs    r0, #1
    movs    r1, #2
    cmp     r0, r1
    bne     1f
    adds    r7, r7, #1
1:
    @ BGE: 5 ≥ 3 (signed) → taken
    movs    r0, #5
    movs    r1, #3
    cmp     r0, r1
    bge     1f
    adds    r7, r7, #1
1:
    @ BLT: 3 < 5 (signed) → taken
    movs    r0, #3
    movs    r1, #5
    cmp     r0, r1
    blt     1f
    adds    r7, r7, #1
1:
    @ BGT: 5 > 3 (signed) → taken
    movs    r0, #5
    movs    r1, #3
    cmp     r0, r1
    bgt     1f
    adds    r7, r7, #1
1:
    @ BLE: 3 ≤ 3 (signed) → taken
    movs    r0, #3
    movs    r1, #3
    cmp     r0, r1
    ble     1f
    adds    r7, r7, #1
1:
    @ BMI: result is negative → N=1 → taken
    movs    r0, #1
    negs    r0, r0              @ r0 = -1, N=1
    bmi     1f
    adds    r7, r7, #1
1:
    @ BPL: positive value → N=0 → taken
    movs    r0, #5              @ N=0
    bpl     1f
    adds    r7, r7, #1
1:
    @ BHI: 5 > 3 (unsigned, C=1 Z=0) → taken
    movs    r0, #5
    movs    r1, #3
    cmp     r0, r1
    bhi     1f
    adds    r7, r7, #1
1:
    @ BCS: carry set after 0x80000000 + 0x80000000 → taken
    movs    r0, #1
    lsls    r0, r0, #31         @ r0 = 0x80000000
    adds    r0, r0, r0          @ C = 1
    bcs     1f
    adds    r7, r7, #1
1:

    /* ── Done ─────────────────────────────────────────────────────────── */
    adds    r0, r7, #0          @ return failure count
    pop     {r4, r5, r6, r7, pc}
