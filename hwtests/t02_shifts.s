@ Title: Shifts and rotates
@ About: LSLS, LSRS, ASRS and RORS by a register (amounts 0, 1, 4, 31, 32, 33, 255 and 256, where only the bottom byte counts) and by an immediate (including LSRS/ASRS #32), on six values. The carry flag out of each shift is the classic place for a simulator to be wrong.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        movs    r4, #0                  @ offset into values
vloop:
        ldr     r3, =values
        ldr     r6, [r3, r4]
        @ immediate shifts
        movs    r0, r6                  @ (LSLS #0: only N and Z change)
        lsls    r0, r6, #1
        lsls    r0, r6, #31
        lsrs    r0, r6, #1
        lsrs    r0, r6, #31
        lsrs    r0, r6, #32
        asrs    r0, r6, #1
        asrs    r0, r6, #31
        asrs    r0, r6, #32
        movs    r5, #0                  @ offset into amounts
aloop:
        ldr     r3, =amounts
        ldr     r7, [r3, r5]
        cmp     r6, r7                  @ a carry that depends on the pair
        movs    r0, r6
        lsls    r0, r7
        movs    r0, r6
        lsrs    r0, r7
        cmp     r7, r6
        movs    r0, r6
        asrs    r0, r7
        movs    r0, r6
        rors    r0, r7
        adds    r5, r5, #4
        cmp     r5, #32
        bne     aloop
        adds    r4, r4, #4
        cmp     r4, #24
        bne     vloop
        pop     {r4-r7, pc}
        .ltorg

        .data
        .balign 4
values:  .word  0x00000001, 0x80000000, 0xF0F0F0F0, 0x7FFFFFFF, 0xFFFFFFFF, 0x12345678
amounts: .word  0, 1, 4, 31, 32, 33, 255, 256
