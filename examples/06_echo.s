@ Title: Echo what you type
@ About: Reads keys from the serial console and prints each one back; q stops it.
@ Level: 2
@
@ What to watch
@   getchar waits until you type a key in the serial console, then puts its
@   ASCII code in R0. Press Run, click in the console and type.
@   The status bar says "waiting for input" while getchar waits.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

main:
        push    {r7, lr}
loop:
        bl      getchar         @ R0 = the next key you type
        cmp     r0, #'q'
        beq     done            @ 'q' ends the program
        bl      putchar         @ print it back
        b       loop            @ and wait for the next key
done:
        movs    r0, #10         @ new line
        bl      putchar
        movs    r0, #0
        pop     {r7, pc}
