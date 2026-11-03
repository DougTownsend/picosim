@ Title: Fetch alignment timing
@ About: The Cortex-M0+ fetches 32 bits at a time, so a 32-bit BL, or a branch target, at an address that is not a multiple of 4 might cost more. Table 81 says it does not; the same code is timed word-aligned and halfword-aligned to check.
@ Kind: bench

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}

        .balign 4
        bl      bench_start             @ BL at word-aligned addresses x 16
        .rept   16
        bl      leaf
        nop
        .endr
        bl      bench_stop

        .balign 4
        nop
        bl      bench_start             @ BL at halfword addresses x 16
        .rept   16
        bl      leaf
        nop
        .endr
        bl      bench_stop

        bl      bench_start             @ B to word-aligned targets x 16
        .rept   16
        b       1f
        .balign 4
1:
        .endr
        bl      bench_stop

        bl      bench_start             @ B to halfword targets x 16
        .rept   16
        b       1f
        .balign 4
        nop
1:
        .endr
        bl      bench_stop

        pop     {r4-r7, pc}

        .balign 4
        .type   leaf, %function
leaf:
        bx      lr
