.syntax unified
.cpu cortex-m0plus
.thumb

@ Calibration for timing a program with no bench_start / bench_stop, in
@ stretches between OS calls (picosim/hwtest/bench.py).  A stretch between
@ two calls in a row is just the second bl, which picosim counts; the cycles
@ the chip counts beyond picosim's are the fixed cost of measuring, which
@ every stretch subtracts.  The calls are once at word-aligned addresses and
@ once at halfwords; the first stretch starts at main and the last ends with
@ main's return, which must cost the same.

main:
        push    {r4, lr}
        movs    r4, #4
loop:
        .balign 4
        bl      getchar         @ aligned
        bl      putchar
        nop
        bl      getchar         @ halfword
        bl      putchar
        subs    r4, r4, #1
        bne     loop
        pop     {r4, pc}
