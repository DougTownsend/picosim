@ Title: Open test
@ About: A program for checking that File > Open loads a new program and every panel follows it.
@ Level: 2
@
@ How to use it
@   1. Open any example first (say 05_blink_led.s) and step it a few times.
@   2. Click Open... and pick this file. Right away you should see:
@      - the window title and the console say open_test.s
@      - this file's lines in the Disassembly (look for print, multiply, answer)
@      - the challenge bar below with this file's two checks
@      - the CPU diagram reset: registers 0, the FSM waiting at FETCH_ADDR.
@        Every program starts in the same built-in OS, so this part looks
@        the same for every file you open.
@   3. Press Step instruction 4 times. Now you are in main and the diagram
@      shows this program's own instructions (push {r4, r5, lr} first).
@   4. Press Run. The console prints OPEN OK, GP4 and GP5 light up on the
@      Pico board (no example uses those pins), the Memory tab shows 42 at
@      "answer", and the challenge bar says both checks passed.
@
@ Challenge: Open this file and press Run: both checks below should pass.
@ Expect: output "OPEN OK\n"
@ Expect: R3 = 42

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .equ    CTRL0,       0x40014004 @ IO_BANK0: GPn's control register is at CTRL0 + 8n
        .equ    SIO_OUT_SET, 0xD0000014 @ write a 1 bit: drive that pin high
        .equ    SIO_OE_SET,  0xD0000024 @ write a 1 bit: make that pin an output
        .equ    PINS,        (1 << 4) | (1 << 5)   @ GP4 and GP5

main:
        push    {r4, r5, lr}

@ 1. Print the message one character at a time
        ldr     r4, =message    @ R4 = address of the next character
print:
        ldrb    r0, [r4]        @ R0 = the character
        cmp     r0, #0          @ the 0 byte marks the end
        beq     printed
        bl      putchar
        adds    r4, r4, #1
        b       print
printed:

@ 2. Work out 6 x 7 in a function and keep it in memory
        movs    r0, #6
        movs    r1, #7
        bl      multiply        @ R0 = 42
        ldr     r1, =answer
        str     r0, [r1]        @ answer = 42 (look in the Memory tab)

@ 3. Turn on GP4 and GP5
        movs    r5, #5          @ function 5 = SIO: our program controls the pin
        ldr     r0, =CTRL0 + 8 * 4
        str     r5, [r0]        @ GP4
        ldr     r0, =CTRL0 + 8 * 5
        str     r5, [r0]        @ GP5
        ldr     r2, =PINS
        ldr     r0, =SIO_OE_SET
        str     r2, [r0]        @ both pins are now outputs
        ldr     r0, =SIO_OUT_SET
        str     r2, [r0]        @ and both are high

@ 4. Read the answer back for the R3 = 42 check
        ldr     r1, =answer
        ldr     r3, [r1]

        movs    r0, #0
        pop     {r4, r5, pc}

@ R0 = R0 x R1, by adding R0 to itself R1 times (R1 must be at least 1)
multiply:
        movs    r2, r0          @ R2 = the number to add
        movs    r0, #0          @ R0 = the total so far
mul_loop:
        adds    r0, r0, r2
        subs    r1, r1, #1
        bne     mul_loop
        bx      lr

        .data
message:
        .asciz  "OPEN OK\n"
        .align  2
answer:
        .word   0
