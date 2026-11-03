@ Title: A fault
@ About: A word load from an address that is not a multiple of 4. The Cortex-M0+ cannot do that: the chip takes a HardFault, and picosim must stop at the same instruction. The test passes when both fault there.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4, lr}
        ldr     r1, =data
        ldr     r0, [r1]                @ aligned: fine
        ldrh    r0, [r1, #2]            @ halfword at +2: fine
        adds    r1, r1, #2
        ldr     r0, [r1]                @ word at +2: a HardFault
        movs    r0, #1                  @ never runs
        pop     {r4, pc}
        .ltorg

        .data
        .balign 4
data:   .word   0x11223344, 0x55667788
