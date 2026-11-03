@ Title: Branch and call timing
@ About: Taken B (2 cycles), taken and not-taken conditional branches (2 and 1), BL (3) with BX LR (2), BLX with a register, POP {PC} (3 + registers), and a counted loop. Branches empty the 2-stage pipeline; these regions show what that costs on the chip.
@ Kind: bench

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}

        bl      bench_start             @ B (always taken) x 32
        .rept   32
        b       1f
1:
        .endr
        bl      bench_stop

        cmp     r0, r0                  @ Z = 1
        bl      bench_start             @ BEQ taken x 32
        .rept   32
        beq     1f
1:
        .endr
        bl      bench_stop

        cmp     r0, r0
        bl      bench_start             @ BNE not taken x 32
        .rept   32
        bne     1f
1:
        .endr
        bl      bench_stop

        bl      bench_start             @ BL + BX LR x 16
        .rept   16
        bl      leaf
        .endr
        bl      bench_stop

        ldr     r3, =leaf
        bl      bench_start             @ BLX Rm + BX LR x 16
        .rept   16
        blx     r3
        .endr
        bl      bench_stop

        bl      bench_start             @ BL + PUSH {LR} + POP {PC} x 16
        .rept   16
        bl      framed
        .endr
        bl      bench_stop

        movs    r4, #100
        bl      bench_start             @ loop of 100: SUBS + BNE
1:      subs    r4, r4, #1
        bne     1b
        bl      bench_stop

        pop     {r4-r7, pc}

        .type   leaf, %function
leaf:
        bx      lr

        .type   framed, %function
framed:
        push    {lr}
        pop     {pc}
        .ltorg
