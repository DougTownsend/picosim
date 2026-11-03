@ Title: Load and store timing
@ About: Loads and stores to SRAM: LDR and STR with immediate and register offsets, the byte and halfword forms, SP-relative and literal loads (2 cycles each in Table 81), and the multi-register PUSH, POP, LDM and STM (1 + one per register).
@ Kind: bench

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        ldr     r1, =buf
        movs    r2, #8
        movs    r0, #0
        b       1f
        .ltorg                          @ (the literal for buf, in reach)
1:

        bl      bench_start             @ LDR Rt, [Rn, #imm] x 64
        .rept   64
        ldr     r3, [r1, #4]
        .endr
        bl      bench_stop

        bl      bench_start             @ STR Rt, [Rn, #imm] x 64
        .rept   64
        str     r0, [r1, #4]
        .endr
        bl      bench_stop

        bl      bench_start             @ LDR Rt, [Rn, Rm] x 64
        .rept   64
        ldr     r3, [r1, r2]
        .endr
        bl      bench_stop

        bl      bench_start             @ LDRB Rt, [Rn, #imm] x 64
        .rept   64
        ldrb    r3, [r1, #1]
        .endr
        bl      bench_stop

        bl      bench_start             @ STRH Rt, [Rn, #imm] x 64
        .rept   64
        strh    r0, [r1, #2]
        .endr
        bl      bench_stop

        bl      bench_start             @ LDRSB Rt, [Rn, Rm] x 64
        .rept   64
        ldrsb   r3, [r1, r2]
        .endr
        bl      bench_stop

        sub     sp, #8
        bl      bench_start             @ LDR Rt, [SP, #imm] x 64
        .rept   64
        ldr     r3, [sp, #4]
        .endr
        bl      bench_stop
        add     sp, #8

        bl      bench_start             @ LDR Rt, =literal x 64
        .rept   64
        ldr     r3, =0x12345678
        .endr
        bl      bench_stop
        b       1f
        .ltorg
1:
        bl      bench_start             @ PUSH {r0-r3} + POP {r0-r3} x 16
        .rept   16
        push    {r0-r3}
        pop     {r0-r3}
        .endr
        bl      bench_stop

        bl      bench_start             @ PUSH {r0-r7} + POP {r0-r7} x 16
        .rept   16
        push    {r0-r7}
        pop     {r0-r7}
        .endr
        bl      bench_stop

        ldr     r4, =buf
        bl      bench_start             @ STM Rn!, {r0-r3} + LDM Rn!, {r0-r3} x 16
        .rept   16
        stm     r4!, {r0-r3}
        subs    r4, r4, #16
        ldm     r4!, {r0-r3}
        subs    r4, r4, #16
        .endr
        bl      bench_stop

        pop     {r4-r7, pc}
        .ltorg

        .data
        .balign 4
buf:    .space  64
