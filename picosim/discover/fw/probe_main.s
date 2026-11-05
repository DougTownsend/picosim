@ Campaign F's firmware: picosim's own Flash build (wrapper_main.c: USB
@ stdio, core 1 running report_pins) with a main that stops at once, so the
@ host can look at the state a flashed program starts in and run its
@ measurements in it.
@
@ flash_loop is code that stays in flash (XIP): the host calls it with
@ r0 = iterations to time code run from flash.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        bkpt    #0x42                   @ the host takes over here
        b       main

        .global flash_loop
        .type   flash_loop, %function
        .align  4
flash_loop:                             @ 16 NOPs per iteration, then subs/bne
1:      nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        subs    r0, #1
        bne     1b
        bx      lr
