@ A width-suffix mistake: add instead of adds.
        .syntax unified
        .cpu cortex-m0plus
        .thumb
main:
        push    {r7, lr}
        movs    r0, #3
        add     r0, r0, #4
        pop     {r7, pc}
