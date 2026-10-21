@ Title: Count with a loop
@ About: Prints 12345 using a loop that compares and branches.
@ Level: 1
@
@ What to watch
@   CMP subtracts two numbers only to set the flags N Z C V; it keeps no
@   result. BNE ("branch if not equal") jumps back to loop while Z = 0.
@   Step through: the Z flag turns on when R4 reaches 6, and the loop ends.
@
@ Your turn (picosim checks it when the program finishes)
@ Challenge: Make it count up to 9, printing 123456789.
@ Expect: output "123456789\n"

        .syntax unified
        .cpu cortex-m0plus
        .thumb

main:
        push    {r4, lr}        @ we use R4, so save it (and the return address)

        movs    r4, #1          @ R4 = the counter, starting at 1
loop:
        movs    r0, r4          @ copy the counter to R0 for printing
        adds    r0, #'0'        @ turn the number into its digit character
        bl      putchar
        adds    r4, r4, #1      @ counter = counter + 1
        cmp     r4, #6          @ compare the counter with 6 (sets the flags)
        bne     loop            @ not equal yet? go round again

        movs    r0, #10         @ new line
        bl      putchar
        movs    r0, #0
        pop     {r4, pc}
