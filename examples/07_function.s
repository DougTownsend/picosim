@ Title: Call a function
@ About: Uses BL to call two small functions and prints 4 and 6.
@ Level: 2
@
@ What to watch
@   BL jumps to a function and saves the way back in LR. BX LR returns.
@   A function that calls another one must first save LR on the stack with
@   PUSH, and returns with POP {PC}. Watch LR, SP and the PC, and the stack
@   in the Memory tab.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

main:
        push    {r7, lr}
        movs    r0, #2
        bl      twice           @ R0 = 2 + 2 = 4
        bl      print_digit     @ prints 4
        movs    r0, #3
        bl      twice           @ R0 = 3 + 3 = 6
        bl      print_digit     @ prints 6
        movs    r0, #10         @ new line
        bl      putchar
        movs    r0, #0
        pop     {r7, pc}

@ twice: R0 = R0 + R0.  It calls nothing, so LR can stay where it is.
twice:
        adds    r0, r0, r0
        bx      lr              @ return to the address in LR

@ print_digit: prints the number in R0 (0-9) as a digit.  It calls putchar,
@ which overwrites LR, so it saves LR on the stack first.
print_digit:
        push    {lr}
        adds    r0, #'0'
        bl      putchar
        pop     {pc}            @ return: the saved LR goes straight into the PC
