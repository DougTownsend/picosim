.syntax unified
.cpu cortex-m0plus
.thumb

main:
        push    {r7, lr}
        sub     sp, sp, #8
        add     r7, sp, #0
        adds    r3, r7, #6
        movs    r2, #0
        strh    r2, [r3]
        b       .L2
.L3:
        adds    r3, r7, #6
        ldrh    r3, [r3]
        uxth    r3, r3
        adds    r3, r3, #1
        uxth    r2, r3
        adds    r3, r7, #6
        strh    r2, [r3]
.L2:
        adds    r3, r7, #6
        ldrh    r3, [r3]
        uxth    r3, r3
        ldr     r2, .L5
        cmp     r3, r2
        bls     .L3
        movs    r3, #0
        movs    r0, r3
        mov     sp, r7
        add     sp, sp, #8
        pop     {r7, pc}
        .align 2
.L5:
        .word   100