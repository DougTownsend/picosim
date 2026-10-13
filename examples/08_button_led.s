@ Title: Buttons and LEDs
@ About: Finds which pins your LEDs and buttons are on, then lights an LED while you hold a button.
@ Level: 2
@
@ This program does two jobs. Pick one with Settings (top left, next to
@ Reload) -> FIND_PINS, then "Apply and reload":
@
@ 1. Find my pins (start here with a new circuit)
@    Works with any RP2040 board and any wiring. Click Flash, click in the
@    serial console and press a key. The Pico board drawing (bottom left)
@    shows your real board's pins live while this runs.
@    Part 1, LEDs: every free pin is switched high, then low, about half a
@      second each, and its number is printed. When one of your LEDs lights,
@      read the line: lit on "high" -> LED_PIN = that number, LED_TO_GND =
@      "the LED goes from the pin to GND"; lit on "low" -> the other choice.
@      Turn any switches OFF and don't press buttons during part 1 (a pin
@      driven against a closed switch fights it; the program uses the
@      chip's weakest drive to keep that harmless).
@    Part 2, buttons: press and release each button; every change is
@      printed. "GP14: low" while pressed -> BUTTON_PIN = 14 and
@      BUTTON_TO_GND = "connects the pin to GND"; "high" while pressed ->
@      BUTTON_TO_GND = "connects the pin to 3V3".
@
@ 2. Run it: the LED is on while you hold the button
@    Put the numbers you found into Settings, set FIND_PINS to "run it",
@    Apply and reload, then Flash. The program reads the button's pin from
@    GPIO_IN (an input register) over and over and switches the LED to match.
@    Checking an input again and again like this is called polling.
@
@ In the simulator
@   Press Run. Finding pins: type a key in the console, then in part 2 click
@   pins on the Pico board to see them reported. Running it: click the
@   BUTTON_PIN pin until it shows the "pressed" level (0 when the button goes
@   to GND, 1 when it goes to 3V3) and the LED_PIN pin turns green.
@
@ Wiring (any breadboard, any RP2040 board)
@   LED:    GPIO pin -> resistor (220-1000 ohms) -> LED long leg (+);
@           LED short leg (-) -> GND          (or from 3V3 to the pin)
@   Button: one side to a GPIO pin, the other side to GND (no resistor
@           needed: the chip's own pull-up is used)   (or to 3V3)
@   Use GP0-GP22 or GP26-GP28; 23, 24, 25 and 29 are never used, because
@   some boards (such as the Pico W) use them inside the board.
@
@ How finding pins works
@   Each pin is read twice: once with the chip's weak pull-UP resistor on and
@   once with the pull-DOWN on. A pin with nothing connected follows the pull
@   (1, then 0). A pin that reads 1 both times is held high by something
@   (like a pressed button to 3V3); 0 both times means it is held low.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

@ ── Settings (change them with the Settings button, not here) ──────────────
        .equ    FIND_PINS,      1       @ <- 1: find my pins: show which pins my LEDs and buttons are on; 0: run it: the LED is on while I hold the button
        .equ    LED_PIN,        15      @ <- the GPIO number of your LED
        .equ    BUTTON_PIN,     14      @ <- the GPIO number of your button
        .equ    BUTTON_TO_GND,  1       @ <- 1: pressing the button connects the pin to GND; 0: pressing the button connects the pin to 3V3
        .equ    LED_TO_GND,     1       @ <- 1: the LED goes from the pin to GND; 0: the LED goes from 3V3 to the pin

@ ── RP2040 registers ───────────────────────────────────────────────────────
        .equ    PINS,        0x1C7FFFFF @ GP0-GP22 and GP26-GP28 (one bit each)
        .equ    CTRL0,       0x40014004 @ IO_BANK0: GPn's control register is at CTRL0 + 8n
        .equ    PADS0,       0x4001C004 @ PADS_BANK0: GPn's pad register is at PADS0 + 4n
        .equ    SIO_IN,      0xD0000004 @ read: one bit per pin, 1 = high
        .equ    SIO_OUT_SET, 0xD0000014 @ write a 1 bit: drive that pin high
        .equ    SIO_OUT_CLR, 0xD0000018 @ write a 1 bit: drive that pin low
        .equ    SIO_OE_SET,  0xD0000024 @ write a 1 bit: make that pin an output
        .equ    SIO_OE_CLR,  0xD0000028 @ write a 1 bit: make that pin an input
        .equ    PAD_PULL_UP,   0x5A     @ input on + pull-up
        .equ    PAD_PULL_DOWN, 0x56     @ input on + pull-down
        .equ    PAD_OUTPUT,    0x40     @ input on, no pulls, weakest (2 mA) drive

        .equ    LED_CTRL,    CTRL0 + 8 * LED_PIN        @ what drives the LED's pin
        .equ    BUTTON_PAD,  PADS0 + 4 * BUTTON_PIN     @ the button pin's pad

@ The button's pull resistor makes the pin read "not pressed" while it is up.
        .if     BUTTON_TO_GND
        .equ    BUTTON_PAD_SETTING, PAD_PULL_UP
        .else
        .equ    BUTTON_PAD_SETTING, PAD_PULL_DOWN
        .endif

@ Driving the pin high lights an LED that goes to GND; driving it low lights
@ one that comes from 3V3.
        .if     LED_TO_GND
        .equ    LED_ON,  SIO_OUT_SET
        .equ    LED_OFF, SIO_OUT_CLR
        .else
        .equ    LED_ON,  SIO_OUT_CLR
        .equ    LED_OFF, SIO_OUT_SET
        .endif

@ Waits: the real chip runs about 40 million count-down steps per second.
        .ifdef  PICOSIM_HARDWARE
        .equ    SETTLE, 1000            @ let a pin settle after changing its pull
        .equ    SHORT,  400000          @ about 10 ms between button checks
        .equ    LONG,   20000000        @ about half a second per LED step
        .else
        .equ    SETTLE, 1
        .equ    SHORT,  1
        .equ    LONG,   2
        .endif

main:
        push    {r4, r5, r6, r7, lr}
        .if     FIND_PINS
        b       find_pins
        .else
        b       button_led
        .endif

@ ══ Job 2: the LED is on while the button is held ══════════════════════════
button_led:
        @ The LED pin: controlled by our program (function 5 = SIO), output.
        ldr     r0, =LED_CTRL
        movs    r1, #5
        str     r1, [r0]
        ldr     r4, =1 << LED_PIN       @ R4 = the LED's bit
        ldr     r0, =LED_OFF
        str     r4, [r0]                @ start with the LED off
        ldr     r0, =SIO_OE_SET
        str     r4, [r0]

        @ The button pin: an input with its pull resistor on.
        ldr     r0, =BUTTON_PAD
        movs    r1, #BUTTON_PAD_SETTING
        str     r1, [r0]
        ldr     r5, =1 << BUTTON_PIN    @ R5 = the button's bit

        ldr     r6, =SIO_IN
check:
        ldr     r0, [r6]        @ R0 = the level of every pin
        ands    r0, r5          @ keep only the button's bit (Z = 1 if it is 0)
        .if     BUTTON_TO_GND
        beq     pressed         @ bit is 0: the button connects the pin to GND
        .else
        bne     pressed         @ bit is 1: the button connects the pin to 3V3
        .endif

        ldr     r0, =LED_OFF
        str     r4, [r0]        @ not pressed: LED off
        b       check

pressed:
        ldr     r0, =LED_ON
        str     r4, [r0]        @ pressed: LED on
        b       check           @ and look at the button again, forever
        .ltorg

@ ══ Job 1: find which pins the LEDs and buttons are on ═════════════════════
find_pins:
        .ifdef  PICOSIM_HARDWARE
wait_usb:                               @ on the board, wait until the console is
        bl      stdio_usb_connected     @ connected, so nothing printed is lost
        cmp     r0, #0
        beq     wait_usb
        bl      long_wait
        .endif

        ldr     r0, =msg_start
        bl      print
        bl      getchar                 @ wait for any key

@ ── Part 1: switch each free pin high, then low ───────────────────────────────
@ Every pin is tried, even one that reads as held: an LED on a pin can look
@ "held" to the weak pulls, and that is exactly the pin we want to find.
        ldr     r0, =msg_part1
        bl      print
        bl      getchar                 @ wait until switches are off
        movs    r4, #0                  @ R4 = pin number
part1_loop:
        ldr     r0, =PINS               @ R0 = the pins we may drive
        movs    r5, #1
        lsls    r5, r4                  @ R5 = this pin's bit
        tst     r0, r5
        beq     part1_next

        ldr     r0, =msg_gp
        bl      print
        movs    r0, r4
        bl      print_num
        lsls    r0, r4, #3
        ldr     r1, =CTRL0
        movs    r2, #5                  @ function 5 = SIO: our program drives the pin
        str     r2, [r1, r0]
        lsls    r0, r4, #2
        ldr     r1, =PADS0
        movs    r2, #PAD_OUTPUT
        str     r2, [r1, r0]
        ldr     r0, =SIO_OUT_SET
        str     r5, [r0]                @ high
        ldr     r0, =SIO_OE_SET
        str     r5, [r0]                @ ... and an output
        ldr     r0, =msg_high
        bl      print
        bl      long_wait
        ldr     r0, =SIO_OUT_CLR
        str     r5, [r0]                @ low
        ldr     r0, =msg_low
        bl      print
        bl      long_wait
        ldr     r0, =SIO_OE_CLR
        str     r5, [r0]                @ back to an input
part1_next:
        adds    r4, #1
        cmp     r4, #29
        blt     part1_loop

@ ── Part 2: report every pin whose state changes ──────────────────────────────
        ldr     r0, =msg_part2
        bl      print
        ldr     r2, =prev               @ start from "nothing held", so the first
        movs    r0, #0                  @ check also lists pins that are already
        str     r0, [r2]                @ held high or low
        str     r0, [r2, #4]
part2_loop:
        bl      short_wait
        bl      sample                  @ R0 = held high, R1 = held low
        ldr     r2, =prev
        ldr     r3, [r2]
        eors    r3, r0                  @ pins whose "held high" changed
        ldr     r6, [r2, #4]
        eors    r6, r1                  @ pins whose "held low" changed
        str     r0, [r2]
        str     r1, [r2, #4]
        orrs    r6, r3                  @ R6 = every pin that changed
        beq     part2_loop

        movs    r4, #0
report:
        movs    r5, #1
        lsls    r5, r4
        tst     r5, r6
        beq     report_next
        ldr     r0, =msg_gp
        bl      print
        movs    r0, r4
        bl      print_num
        ldr     r2, =prev
        ldr     r3, [r2]
        tst     r3, r5
        bne     is_high
        ldr     r3, [r2, #4]
        tst     r3, r5
        bne     is_low
        ldr     r0, =msg_free
        b       say
is_high:
        ldr     r0, =msg_is_high
        b       say
is_low:
        ldr     r0, =msg_is_low
say:
        bl      print
report_next:
        adds    r4, #1
        cmp     r4, #29
        blt     report
        b       part2_loop

@ ── sample: R0 = pins held high, R1 = pins held low ──────────────────────────
sample:
        push    {r4, r5, lr}
        movs    r0, #PAD_PULL_UP
        bl      set_pads
        bl      settle
        ldr     r0, =SIO_IN
        ldr     r4, [r0]                @ R4 = levels with pull-ups
        movs    r0, #PAD_PULL_DOWN
        bl      set_pads
        bl      settle
        ldr     r0, =SIO_IN
        ldr     r5, [r0]                @ R5 = levels with pull-downs
        ldr     r2, =PINS
        movs    r0, r4
        ands    r0, r5                  @ 1 even when pulled down: held high
        ands    r0, r2
        movs    r1, r4
        orrs    r1, r5
        mvns    r1, r1                  @ 0 even when pulled up: held low
        ands    r1, r2
        pop     {r4, r5, pc}

@ set_pads: write the pad setting in R0 to every pin in PINS
set_pads:
        push    {r4, lr}
        ldr     r1, =PADS0
        ldr     r2, =PINS
        movs    r3, #0
1:      movs    r4, #1
        lsls    r4, r3
        tst     r4, r2
        beq     2f
        lsls    r4, r3, #2
        str     r0, [r1, r4]
2:      adds    r3, #1
        cmp     r3, #29
        blt     1b
        pop     {r4, pc}

@ waits: count R0 down to 0
settle:
        ldr     r0, =SETTLE
        b       count
short_wait:
        ldr     r0, =SHORT
        b       count
long_wait:
        ldr     r0, =LONG
count:
        subs    r0, #1
        bne     count
        bx      lr

@ print: print the text at address R0 (ends with a 0 byte)
print:
        push    {r4, lr}
        movs    r4, r0
1:      ldrb    r0, [r4]
        cmp     r0, #0
        beq     2f
        bl      putchar
        adds    r4, #1
        b       1b
2:      pop     {r4, pc}

@ print_num: print the number in R0 (0-99) in decimal
print_num:
        push    {r4, r5, lr}
        movs    r4, r0                  @ R4 = ones
        movs    r5, #0                  @ R5 = tens
1:      cmp     r4, #10
        blt     2f
        subs    r4, #10
        adds    r5, #1
        b       1b
2:      cmp     r5, #0
        beq     3f
        movs    r0, r5
        adds    r0, #'0'
        bl      putchar
3:      movs    r0, r4
        adds    r0, #'0'
        bl      putchar
        pop     {r4, r5, pc}

        .ltorg

msg_start:   .asciz "\nFind your pins\nPress any key to start.\n"
msg_part1:   .asciz "\nPart 1, LEDs: each free pin goes high, then low. Watch for an LED to light.\nFirst turn any switches OFF and don't press buttons. Press any key to go.\n"
msg_part2:   .asciz "\nPart 2, buttons: press and release each button.\n(Pins held high or low right now are listed first; LED pins can show up here too.)\n"
msg_gp:      .asciz "GP"
msg_high:    .asciz ": high ... "
msg_low:     .asciz "low\n"
msg_is_high: .asciz ": high\n"
msg_is_low:  .asciz ": low\n"
msg_free:    .asciz ": released (not held)\n"
        .align  2

        .data
prev:   .word   0, 0                    @ last check: held high, held low
