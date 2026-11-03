@ Title: Add, subtract and the flags
@ About: ADDS, ADCS, SUBS, SBCS, RSBS, CMP and CMN on every pair of eight edge values (0, 1, 2, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFE, 0xFFFFFFFF). After every instruction the result and N, Z, C, V must be the chip's.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        movs    r4, #0                  @ offset of the first operand in values
outer:
        movs    r5, #0                  @ offset of the second operand
inner:
        ldr     r3, =values
        ldr     r6, [r3, r4]            @ a
        ldr     r7, [r3, r5]            @ b
        movs    r0, r6
        adds    r0, r0, r7              @ ADDS a + b
        movs    r1, r6
        subs    r1, r1, r7              @ SUBS a - b
        cmp     r6, r7                  @ CMP: C = no borrow
        movs    r2, r6
        adcs    r2, r7                  @ ADCS a + b + C
        cmn     r6, r7                  @ CMN: flags of a + b
        movs    r2, r6
        sbcs    r2, r7                  @ SBCS a - b - !C
        rsbs    r2, r7, #0              @ RSBS (NEGS) 0 - b
        adds    r2, r6, #7              @ ADDS imm3
        subs    r2, r6, #1              @ SUBS imm3
        movs    r2, r6
        adds    r2, #200                @ ADDS imm8
        subs    r2, #255                @ SUBS imm8
        cmp     r7, #128                @ CMP imm8
        adds    r5, r5, #4
        cmp     r5, #32
        bne     inner
        adds    r4, r4, #4
        cmp     r4, #32
        bne     outer
        movs    r0, #'O'
        bl      putchar
        movs    r0, #'K'
        bl      putchar
        movs    r0, #10
        bl      putchar
        pop     {r4-r7, pc}
        .ltorg

        .data
        .balign 4
values: .word   0, 1, 2, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFE, 0xFFFFFFFF
