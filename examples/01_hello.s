@ Title: Say hello
@ About: Prints "Hi!" in the serial console, one letter at a time.
@ Level: 1
@
@ What to watch
@   Press Step instruction (F10) one line at a time. Before each putchar,
@   R0 holds a letter's ASCII code: 0x48 is 'H', 0x69 is 'i', 0x21 is '!'.
@   Each code turns yellow when it changes.
@   Press Run (F5) to let it go by itself. Press Reload to start again.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

main:
        push    {r7, lr}        @ save where to return to (see 07_function.s)

        movs    r0, #'H'        @ R0 = the letter H (ASCII code 0x48)
        bl      putchar         @ print the letter in R0
        movs    r0, #'i'
        bl      putchar
        movs    r0, #'!'
        bl      putchar
        movs    r0, #10         @ 10 is the "new line" character
        bl      putchar

        movs    r0, #0
        pop     {r7, pc}        @ return: the program is finished
