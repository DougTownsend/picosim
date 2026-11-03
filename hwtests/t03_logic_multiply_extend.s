@ Title: Logic, multiply and extend
@ About: ANDS, ORRS, EORS, BICS, MVNS, TST and MULS on every pair of six values (MULS sets N and Z and must leave C and V alone), then REV, REV16, REVSH, SXTB, SXTH, UXTB and UXTH on each value.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        movs    r4, #0
outer:
        movs    r5, #0
inner:
        ldr     r3, =values
        ldr     r6, [r3, r4]
        ldr     r7, [r3, r5]
        cmp     r6, r7                  @ C and V from a compare, to see who keeps them
        movs    r0, r6
        ands    r0, r7
        movs    r0, r6
        orrs    r0, r7
        movs    r0, r6
        eors    r0, r7
        movs    r0, r6
        bics    r0, r7
        mvns    r0, r7
        tst     r6, r7
        cmp     r7, r6
        movs    r0, r6
        muls    r0, r7, r0
        adds    r5, r5, #4
        cmp     r5, #24
        bne     inner
        @ byte order and extension, on a
        rev     r0, r6
        rev16   r0, r6
        revsh   r0, r6
        sxtb    r0, r6
        sxth    r0, r6
        uxtb    r0, r6
        uxth    r0, r6
        adds    r4, r4, #4
        cmp     r4, #24
        bne     outer
        pop     {r4-r7, pc}
        .ltorg

        .data
        .balign 4
values: .word   0, 1, 0x7FFFFFFF, 0x80000080, 0xFFFF8000, 0x12345678
