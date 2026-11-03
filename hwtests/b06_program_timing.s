@ Title: Whole-program timing
@ About: Real code timed end to end: a bubble sort of 16 numbers, CRC-32 of 32 bytes, copying 256 bytes a word at a time, a 16-element dot product with MULS, and recursive fib(10). This is where small per-instruction differences add up.
@ Kind: bench

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}

        bl      bench_start             @ bubble sort, 16 numbers
        ldr     r4, =numbers
        movs    r5, #15
pass:
        movs    r6, #0
cmpswap:
        lsls    r7, r6, #2
        adds    r7, r4, r7
        ldr     r0, [r7]
        ldr     r1, [r7, #4]
        cmp     r0, r1
        ble     noswap
        str     r1, [r7]
        str     r0, [r7, #4]
noswap:
        adds    r6, r6, #1
        cmp     r6, r5
        blt     cmpswap
        subs    r5, r5, #1
        bne     pass
        bl      bench_stop

        bl      bench_start             @ CRC-32, 32 bytes, bit by bit
        ldr     r1, =numbers
        movs    r2, #32
        movs    r0, #0
        mvns    r0, r0
        ldr     r3, =0xEDB88320
byte:
        ldrb    r4, [r1]
        adds    r1, r1, #1
        eors    r0, r4
        movs    r5, #8
bit:
        lsrs    r0, r0, #1
        bcc     1f
        eors    r0, r3
1:      subs    r5, r5, #1
        bne     bit
        subs    r2, r2, #1
        bne     byte
        bl      bench_stop

        bl      bench_start             @ copy 256 bytes, LDM/STM 4 words at a time
        ldr     r0, =numbers
        ldr     r1, =dest
        movs    r2, #16
copy:
        ldm     r0!, {r3-r6}
        stm     r1!, {r3-r6}
        subs    r2, r2, #1
        bne     copy
        bl      bench_stop

        bl      bench_start             @ dot product of 16 words with MULS
        ldr     r0, =numbers
        movs    r1, #16
        movs    r2, #0
dot:
        ldm     r0!, {r3}
        muls    r3, r3, r3
        adds    r2, r2, r3
        subs    r1, r1, #1
        bne     dot
        bl      bench_stop

        bl      bench_start             @ recursive fib(10)
        movs    r0, #10
        bl      fib
        bl      bench_stop

        pop     {r4-r7, pc}

        .type   fib, %function
fib:
        push    {r4, r5, lr}
        cmp     r0, #2
        blo     1f
        movs    r4, r0
        subs    r0, r0, #1
        bl      fib
        movs    r5, r0
        subs    r0, r4, #2
        bl      fib
        adds    r0, r0, r5
1:      pop     {r4, r5, pc}
        .ltorg

        .data
        .balign 4
numbers: .word  42, -7, 1000, 3, 0, -2000, 19, 3, 77, 12, -1, 500, 8, 64, -64, 1
         .space 192
dest:    .space 256
