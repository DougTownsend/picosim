@ Title: ALU instruction timing
@ About: 64 copies of one instruction per region: MOVS, ADDS, ADCS, LSLS, EORS, MULS (the RP2040's single-cycle multiplier), CMP, MOV with a high register, ADD/SUB SP, SXTB, REV and NOP. Table 81 says each takes 1 cycle; the chip says what it really takes.
@ Kind: bench

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        movs    r0, #3
        movs    r1, #5
        movs    r2, #7

        bl      bench_start             @ MOVS Rd, #imm x 64
        .rept   64
        movs    r3, #1
        .endr
        bl      bench_stop

        bl      bench_start             @ ADDS Rd, Rn, Rm x 64
        .rept   64
        adds    r3, r0, r1
        .endr
        bl      bench_stop

        bl      bench_start             @ ADCS Rdn, Rm x 64
        .rept   64
        adcs    r3, r1
        .endr
        bl      bench_stop

        bl      bench_start             @ LSLS Rd, Rm, #imm x 64
        .rept   64
        lsls    r3, r1, #3
        .endr
        bl      bench_stop

        bl      bench_start             @ EORS Rdn, Rm x 64
        .rept   64
        eors    r3, r1
        .endr
        bl      bench_stop

        bl      bench_start             @ MULS Rd, Rn, Rd x 64
        .rept   64
        muls    r3, r1, r3
        .endr
        bl      bench_stop

        bl      bench_start             @ CMP Rn, Rm x 64
        .rept   64
        cmp     r0, r1
        .endr
        bl      bench_stop

        bl      bench_start             @ MOV R8, Rm (high register) x 64
        .rept   64
        mov     r8, r1
        .endr
        bl      bench_stop

        bl      bench_start             @ ADD SP / SUB SP, #imm x 64
        .rept   32
        sub     sp, #8
        add     sp, #8
        .endr
        bl      bench_stop

        bl      bench_start             @ SXTB Rd, Rm x 64
        .rept   64
        sxtb    r3, r2
        .endr
        bl      bench_stop

        bl      bench_start             @ REV Rd, Rm x 64
        .rept   64
        rev     r3, r2
        .endr
        bl      bench_stop

        bl      bench_start             @ NOP x 64
        .rept   64
        nop
        .endr
        bl      bench_stop

        pop     {r4-r7, pc}
