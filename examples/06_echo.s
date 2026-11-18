@ Title: Echo what you type
@ About: Reads keys from the serial console and prints each one back; q stops it.
@ Level: 2
@
@ What to watch
@   getchar waits until you type a key in the serial console, then puts its
@   ASCII code in R0. Press Run, click in the console and type.
@   The status bar says "waiting for input" while getchar waits.
@   When the input runs out (piped into picosim at the terminal, as in
@   printf hi | picosim --run 06_echo.s) getchar returns -1 (0xFFFFFFFF),
@   which C calls EOF, "end of file"; the program stops then too.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

main:
        push    {r7, lr}
loop:
        bl      getchar         @ R0 = the next key you type
        cmp     r0, #0
        blt     done            @ -1: no more input (EOF) ends the program too
        cmp     r0, #'q'
        beq     done            @ 'q' ends the program
        bl      putchar         @ print it back
        b       loop            @ and wait for the next key
done:
        movs    r0, #10         @ new line
        bl      putchar
        movs    r0, #0
        pop     {r7, pc}
