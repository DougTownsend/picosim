@ An LED program that sets the output enable but never selects function 5 (SIO).
        .syntax unified
        .cpu cortex-m0plus
        .thumb
        .equ    LED_PIN,     15         @ <- the GPIO number of your LED
        .equ    SIO_OE_SET,  0xD0000024
        .equ    SIO_OUT_SET, 0xD0000014
        .equ    LED_MASK,    1 << LED_PIN
main:
        push    {r4, lr}
        ldr     r0, =SIO_OE_SET
        ldr     r4, =LED_MASK
        str     r4, [r0]
        ldr     r0, =SIO_OUT_SET
        str     r4, [r0]
        movs    r0, #0
        pop     {r4, pc}
