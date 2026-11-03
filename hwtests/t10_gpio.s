@ Title: GPIO registers
@ About: Drives one pin as an output through the SIO (OE, OUT, SET, CLR, XOR, read back through GPIO_IN), reads another pin with its pull-up and then its pull-down, and uses the RP2040's atomic SET/CLR register aliases. Values read from the chip's peripherals that picosim models differently (other pins, reset values) show as I/O differences. Choose the pins with ⚙ Settings: connect nothing to them.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .equ    OUT_PIN,  22            @ <- the GPIO this test drives high and low (connect nothing to it)
        .equ    IN_PIN,   21            @ <- the GPIO this test reads with its pull-up and pull-down (connect nothing to it)

        .equ    SIO,      0xD0000000
        .equ    GPIO_IN,  0x04
        .equ    GPIO_OUT, 0x10
        .equ    OUT_SET,  0x14
        .equ    OUT_CLR,  0x18
        .equ    OUT_XOR,  0x1C
        .equ    GPIO_OE,  0x20
        .equ    OE_SET,   0x24
        .equ    OE_CLR,   0x28
        .equ    IO_BANK0, 0x40014000
        .equ    PADS,     0x4001C000
        .equ    RESET_DONE, 0x4000C008

        .text
main:
        push    {r4-r7, lr}
        @ IO_BANK0 and PADS_BANK0 are out of reset
        ldr     r0, =RESET_DONE
        ldr     r1, [r0]
        ldr     r2, =(1 << 5) | (1 << 8)
        ands    r1, r2
        @ connect OUT_PIN to the SIO and drive it
        ldr     r4, =IO_BANK0 + OUT_PIN * 8 + 4
        ldr     r0, [r4]                @ CTRL's reset value, 0x1F
        movs    r1, #5
        str     r1, [r4]
        ldr     r0, [r4]
        ldr     r5, =SIO
        ldr     r6, =1 << OUT_PIN
        str     r6, [r5, #OE_SET]
        str     r6, [r5, #OUT_SET]
        ldr     r0, [r5, #GPIO_OUT]
        ldr     r0, [r5, #GPIO_OE]
        ldr     r0, [r5, #GPIO_IN]      @ every pin: the chip's others may differ
        ands    r0, r6                  @ just ours: 1
        str     r6, [r5, #OUT_XOR]
        ldr     r0, [r5, #GPIO_IN]
        ands    r0, r6                  @ 0
        str     r6, [r5, #OUT_XOR]
        str     r6, [r5, #OUT_CLR]
        ldr     r0, [r5, #GPIO_OUT]
        str     r6, [r5, #OE_CLR]
        @ IN_PIN with a pull-up, then a pull-down
        ldr     r4, =PADS + 4 + IN_PIN * 4
        ldr     r0, [r4]                @ the pad's reset value, 0x56
        ldr     r7, =IO_BANK0 + IN_PIN * 8 + 4
        movs    r1, #5
        str     r1, [r7]
        ldr     r6, =1 << IN_PIN
        movs    r1, #0x48               @ IE | PUE
        str     r1, [r4]
        ldr     r0, [r4]
        ldr     r0, [r5, #GPIO_IN]
        ands    r0, r6                  @ pulled up: 1
        movs    r1, #0x44               @ IE | PDE
        str     r1, [r4]
        ldr     r0, [r5, #GPIO_IN]
        ands    r0, r6                  @ pulled down: 0
        @ atomic aliases: + 0x2000 sets bits, + 0x3000 clears them
        ldr     r3, =0x2000
        adds    r2, r4, r3
        movs    r1, #0x08               @ set PUE
        str     r1, [r2]
        ldr     r3, =0x3000
        adds    r2, r4, r3
        movs    r1, #0x04               @ clear PDE
        str     r1, [r2]
        ldr     r0, [r4]                @ 0x48
        ldr     r0, [r5, #GPIO_IN]
        ands    r0, r6                  @ pulled up again: 1
        @ put both pins back as they were
        movs    r1, #0x56
        str     r1, [r4]
        movs    r1, #0x1F
        str     r1, [r7]
        ldr     r4, =IO_BANK0 + OUT_PIN * 8 + 4
        str     r1, [r4]
        pop     {r4-r7, pc}
        .ltorg
