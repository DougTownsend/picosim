.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * pc_offset_test.s — Tests every PC-relative instruction on ARMv6-M.
 *
 * Covered instructions
 *   1. LDR  Rt, [PC, #imm8*4]   (T1, 16-bit)  at a 4-byte-aligned address
 *   2. LDR  Rt, [PC, #imm8*4]   (T1, 16-bit)  at a 2-mod-4 address
 *   3. ADR  Rd, label            (16-bit ADD PC,#imm8*4) at 4-byte-aligned
 *   4. ADR  Rd, label            (16-bit ADD PC,#imm8*4) at 2-mod-4
 *
 * ARM rule: the PC value used in address calculations is
 *   Align(instruction_addr + 4, 4) = (instruction_addr + 4) & ~3
 *
 * Returns 0 if all four tests pass, otherwise the number of failures.
 */

asm_main:
    push    {r7, lr}
    movs    r7, #0          @ r7 = failure counter

    /* ── Test 1: LDR literal at a 4-byte-aligned address ──────────────────
     * .balign 4 guarantees the ldr below sits at a multiple of 4.
     * Expected: r0 = 0x11 = 17
     */
    .balign 4
t1_ldr:
    ldr     r0, .Lt1        @ PC = (t1_ldr+4) & ~3 = t1_ldr+4 (already 4-aligned)
    movs    r1, #17
    cmp     r0, r1
    beq     .Lp1
    adds    r7, r7, #1      @ test 1 failed
.Lp1:

    /* ── Test 2: LDR literal at a 2-mod-4 address ─────────────────────────
     * .balign 4 then nop shifts to 2 mod 4.
     * Expected: r0 = 0x22 = 34
     */
    .balign 4
    nop                     @ now at 4-aligned+2 = 2 mod 4
t2_ldr:
    ldr     r0, .Lt2        @ PC = (t2_ldr+4) & ~3 = t2_ldr+2  (alignment rounds down)
    movs    r1, #34
    cmp     r0, r1
    beq     .Lp2
    adds    r7, r7, #1      @ test 2 failed
.Lp2:

    /* ── Test 3: ADR at a 4-byte-aligned address ───────────────────────────
     * adr rd, label  assembles to  add rd, pc, #imm8*4
     * Then we ldr from the address to verify it is correct.
     * Expected: load from .Lt3 = 51
     */
    .balign 4
t3_adr:
    adr     r0, .Lt3        @ r0 = address of .Lt3
    ldr     r0, [r0]        @ load word from that address
    movs    r1, #51
    cmp     r0, r1
    beq     .Lp3
    adds    r7, r7, #1      @ test 3 failed
.Lp3:

    /* ── Test 4: ADR at a 2-mod-4 address ─────────────────────────────────
     * Expected: load from .Lt4 = 68
     */
    .balign 4
    nop
t4_adr:
    adr     r0, .Lt4
    ldr     r0, [r0]
    movs    r1, #68
    cmp     r0, r1
    beq     .Lp4
    adds    r7, r7, #1      @ test 4 failed
.Lp4:

    /* ── Return result ─────────────────────────────────────────────────────
     * r0 = 0 means all tests passed.
     */
    adds    r0, r7, #0
    pop     {r7, pc}

    /* Literal pool — must be 4-byte aligned */
    .align 2
.Lt1:   .word 17
.Lt2:   .word 34
.Lt3:   .word 51
.Lt4:   .word 68
