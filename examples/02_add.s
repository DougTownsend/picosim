@ Title: Add two numbers
@ About: Puts 3 and 4 in registers, adds and subtracts them, and prints the sum.
@ Level: 1
@
@ What to watch
@   MOVS copies a number into a register. ADDS and SUBS do arithmetic on
@   registers. Step through and watch R0, R1, R2 and R3 change.
@   Hover over a register to see its value in decimal.
@   Try it: change 3 and 4 below (keep the sum under 10), save, press Reload.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

main:
        push    {r7, lr}

        movs    r0, #3          @ R0 = 3
        movs    r1, #4          @ R1 = 4
        adds    r2, r0, r1      @ R2 = R0 + R1 = 7
        subs    r3, r1, r0      @ R3 = R1 - R0 = 1

        @ Print the sum. To show the number 7 we print the character '7',
        @ whose ASCII code is 7 + 0x30 ('0' is 0x30, '1' is 0x31, ...).
        movs    r0, r2
        adds    r0, #'0'        @ R0 = 7 + 0x30 = 0x37, the character '7'
        bl      putchar
        movs    r0, #10         @ new line
        bl      putchar

        movs    r0, #0
        pop     {r7, pc}
