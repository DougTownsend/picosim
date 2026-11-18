@ funcsel_check.s: does a pin follow SIO without FUNCSEL = 5?
@
@ Hardware check (docs: hardware-checks-pending).  The RP2040 datasheet
@ says SIO drives a pin only after the pin's IO_BANK0 CTRL.FUNCSEL is set
@ to 5 (SIO).  picosim follows that.  This program confirms it on a Pico.
@
@ Wiring: an LED from LED_PIN (through its resistor) to GND.
@
@ Run 1: WRITE_FUNCSEL = 0.  The program sets the pad, OE and OUT but never
@        writes CTRL.  Expected on the Pico and in picosim: the LED stays DARK.
@ Run 2: WRITE_FUNCSEL = 1.  The same plus the CTRL write.
@        Expected: the LED is ON (this proves the wiring works).
@
@ Both runs print what they did, then keep the LED as it is for about
@ 10 seconds before main returns.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .equ    WRITE_FUNCSEL, 0        @ <- 0: skip the CTRL write (run 1); 1: write FUNCSEL = 5 (run 2)
        .equ    LED_PIN,       21       @ <- the GPIO number of your LED

        .equ    CTRL0,       0x40014004 @ IO_BANK0: GPn's CTRL is at CTRL0 + 8n
        .equ    PADS0,       0x4001C004 @ PADS_BANK0: GPn's pad is at PADS0 + 4n
        .equ    SIO_OUT_SET, 0xD0000014
        .equ    SIO_OE_SET,  0xD0000024
        .equ    PAD_OUTPUT,  0x40       @ input on, no pulls, output allowed (OD = 0)

        .ifdef  PICOSIM_HARDWARE
        .equ    WAIT, 40000000          @ about 10 s at 125 MHz (3 cycles per loop)
        .else
        .equ    WAIT, 2
        .endif

main:
        push    {r4, lr}
        ldr     r4, =1 << LED_PIN

        ldr     r0, =PADS0 + 4 * LED_PIN
        movs    r1, #PAD_OUTPUT
        str     r1, [r0]                @ the pad: no pulls, output not disabled

        .if     WRITE_FUNCSEL
        ldr     r0, =CTRL0 + 8 * LED_PIN
        movs    r1, #5
        str     r1, [r0]                @ FUNCSEL = 5: the pin follows SIO
        .endif

        ldr     r0, =SIO_OE_SET
        str     r4, [r0]                @ SIO: make the pin an output
        ldr     r0, =SIO_OUT_SET
        str     r4, [r0]                @ SIO: drive it high (LED on, if SIO owns the pin)

        ldr     r0, =msg
        bl      print_str

        ldr     r0, =WAIT
1:      subs    r0, r0, #1
        bne     1b

        movs    r0, #0
        pop     {r4, pc}

print_str:                              @ print the zero-ended string at R0
        push    {r4, lr}
        mov     r4, r0
2:      ldrb    r0, [r4]
        cmp     r0, #0
        beq     3f
        bl      putchar
        adds    r4, r4, #1
        b       2b
3:      pop     {r4, pc}

        .align  2
msg:
        .if     WRITE_FUNCSEL
        .asciz  "FUNCSEL = 5 written: the LED should be ON\n"
        .else
        .asciz  "FUNCSEL not written: the LED should stay DARK\n"
        .endif
