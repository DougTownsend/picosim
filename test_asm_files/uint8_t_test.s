.syntax unified
.cpu cortex-m0plus
.thumb

main:
        push    {r7, lr}
        sub     sp, sp, #8
        add     r7, sp, #0
        adds    r3, r7, #7
        movs    r2, #0
        strb    r2, [r3]
        b       .L2
.L3:
        adds    r3, r7, #7
        ldrb    r3, [r3]
        uxtb    r3, r3
        adds    r3, r3, #1
        uxtb    r2, r3
        adds    r3, r7, #7
        strb    r2, [r3]
.L2:
        adds    r3, r7, #7
        ldrb    r3, [r3]
        uxtb    r2, r3
        ldr     r3, .L5
        ldrb    r3, [r3]
        cmp     r2, r3
        bcc     .L3
        movs    r3, #0
        movs    r0, r3
        mov     sp, r7
        add     sp, sp, #8
        pop     {r7, pc}
.L5:
        .word   .Llimit         @ address of the byte constant (GCC stores pointer here)
.Llimit:
        .byte   200
        .align  2