@ Title: Blink an LED
@ About: Blinks an LED on a GPIO pin of your choice (GP15 unless you change LED_PIN).
@ Level: 2
@
@ What to watch
@   The RP2040's pins are controlled by writing to special addresses
@   (memory-mapped I/O). In the simulator, watch the LED_PIN pin on the Pico
@   board turn green and back. Set the speed slider to Max and press Run;
@   press Pause to stop.
@
@ Wiring (any breadboard, any RP2040 board)
@   Not sure which pin your LED is on? Open the "Buttons and LEDs" example
@   with its FIND_PINS setting on "find my pins": it tells you.
@   GPIO pin LED_PIN -> resistor (220-1000 ohms) -> LED long leg (+)
@   LED short leg (-) -> GND
@   Click Settings (top left, next to Reload), set LED_PIN to whichever GPIO
@   you wired, and click "Apply and reload", then Flash. An LED wired the
@   other way round (from 3V3 to the pin) blinks too.
@   Use GP0-GP22 or GP26-GP28. Avoid 23, 24, 25 and 29: on some boards (such
@   as the Pico W) they are used inside the board.
@   The board's own LED is not used: it is on a different pin on different
@   boards, and on the Pico W a program cannot reach it through GPIO at all.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .equ    LED_PIN,     15         @ <- the GPIO number of your LED

        .equ    GPIO_CTRL,   0x40014004 + 8 * LED_PIN  @ IO_BANK0: picks what drives the pin
        .equ    SIO_OE_SET,  0xD0000024 @ write a 1 bit: make that pin an output
        .equ    SIO_OUT_XOR, 0xD000001C @ write a 1 bit: flip that pin
        .equ    LED_MASK,    1 << LED_PIN               @ the pin's bit

@ How long to wait between flips. The Pico build defines PICOSIM_HARDWARE, so
@ the real chip counts to 10 million (about a quarter of a second) and the
@ simulator only to 20.
        .ifdef  PICOSIM_HARDWARE
        .equ    DELAY, 10000000
        .else
        .equ    DELAY, 20
        .endif

main:
        push    {r4, r5, r7, lr}

        ldr     r0, =GPIO_CTRL
        movs    r1, #5          @ function 5 = SIO: our program controls the pin
        str     r1, [r0]
        ldr     r0, =SIO_OE_SET
        ldr     r4, =LED_MASK   @ R4 = the bit for the LED's pin
        str     r4, [r0]        @ the pin is now an output

        ldr     r5, =SIO_OUT_XOR
blink:
        str     r4, [r5]        @ flip the LED: off -> on, or on -> off
        ldr     r0, =DELAY
wait:
        subs    r0, #1          @ count down to 0 to waste a little time
        bne     wait
        b       blink           @ and again, forever
