.syntax unified
.cpu cortex-m0plus
.thumb

@ Calibration for Testing's benchmarks (picosim/hwtest/bench.py).
@ Empty regions: the cycles the chip counts between bench_start and
@ bench_stop with nothing in between are the fixed cost of measuring,
@ which every benchmark subtracts.  The bl bench_stop is once at a
@ word-aligned address and once at a halfword, as fetching may differ.

main:
        push    {r4, lr}
        movs    r4, #4
loop:
        .balign 4
        bl      bench_start     @ aligned
        bl      bench_stop
        nop
        bl      bench_start     @ halfword
        bl      bench_stop
        subs    r4, r4, #1
        bne     loop
        pop     {r4, pc}
