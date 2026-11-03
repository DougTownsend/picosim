@ Title: Peripheral access timing
@ About: Loads and stores to the SIO (Table 81: 1 cycle, it is the single-cycle I/O port) and loads and stores to APB peripherals (IO_BANK0, PADS_BANK0, RESETS), whose bus-bridge wait cycles Table 81 leaves out. A real RP2040 measured APB loads at 4 cycles, which picosim now uses; the store regions measure stores. Nothing is changed: every store writes a register's own reset value.
@ Kind: bench

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        ldr     r1, =0xD0000000         @ SIO
        ldr     r2, =0x40014000         @ IO_BANK0 (APB)
        ldr     r4, =0x4001C000         @ PADS_BANK0 (APB)
        ldr     r5, =0x4000C000         @ RESETS (APB)
        ldr     r6, =buf
        movs    r0, #0

        bl      bench_start             @ LDR from SRAM x 32 (for comparison)
        .rept   32
        ldr     r3, [r6]
        .endr
        bl      bench_stop

        bl      bench_start             @ LDR from SIO GPIO_IN x 32
        .rept   32
        ldr     r3, [r1, #4]
        .endr
        bl      bench_stop

        bl      bench_start             @ STR to SIO GPIO_OUT_CLR (value 0) x 32
        .rept   32
        str     r0, [r1, #0x18]
        .endr
        bl      bench_stop

        bl      bench_start             @ LDR from IO_BANK0 GPIO0_STATUS (APB) x 32
        .rept   32
        ldr     r3, [r2]
        .endr
        bl      bench_stop

        bl      bench_start             @ LDR from PADS_BANK0 VOLTAGE_SELECT (APB) x 32
        .rept   32
        ldr     r3, [r4]
        .endr
        bl      bench_stop

        bl      bench_start             @ LDR from RESETS RESET_DONE (APB) x 32
        .rept   32
        ldr     r3, [r5, #8]
        .endr
        bl      bench_stop

        movs    r0, #0                  @ PADS_BANK0 VOLTAGE_SELECT: 0 = 3.3 V, its reset value
        bl      bench_start             @ STR to PADS_BANK0 VOLTAGE_SELECT (APB) x 32
        .rept   32
        str     r0, [r4]
        .endr
        bl      bench_stop

        movs    r0, #0x1F               @ GPIO0_CTRL: FUNCSEL 0x1F (no function), its reset value
        bl      bench_start             @ STR to IO_BANK0 GPIO0_CTRL (APB) x 32
        .rept   32
        str     r0, [r2, #4]
        .endr
        bl      bench_stop

        ldr     r0, =0x40014000
        bl      bench_start             @ LDR + STR, IO_BANK0 (APB) x 16
        .rept   16
        ldr     r3, [r0, #4]
        str     r3, [r0, #4]
        .endr
        bl      bench_stop

        pop     {r4-r7, pc}
        .ltorg

        .data
        .balign 4
buf:    .word   0
