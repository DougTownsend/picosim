@ Title: Recursion and the stack
@ About: factorial(10) and fib(9) written recursively: deep chains of BL, PUSH {.., LR} and POP {.., PC}, with every saved word on the stack compared. Prints 3628800 and 34.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4, lr}
        movs    r0, #10
        bl      fact
        bl      print_dec
        movs    r0, #9
        bl      fib
        bl      print_dec
        pop     {r4, pc}

@ r0 = n! (n small enough to fit)
        .type   fact, %function
fact:
        push    {r4, lr}
        movs    r4, r0
        cmp     r0, #1
        bls     1f
        subs    r0, r0, #1
        bl      fact
        muls    r0, r4, r0
        pop     {r4, pc}
1:      movs    r0, #1
        pop     {r4, pc}

@ r0 = fib(n): fib(0) = 0, fib(1) = 1
        .type   fib, %function
fib:
        push    {r4, r5, lr}
        cmp     r0, #2
        blo     1f
        movs    r4, r0
        subs    r0, r0, #1
        bl      fib
        movs    r5, r0
        subs    r0, r4, #2
        bl      fib
        adds    r0, r0, r5
1:      pop     {r4, r5, pc}

@ print r0 in decimal and a new line (division by subtracting powers of 10)
        .type   print_dec, %function
print_dec:
        push    {r4-r6, lr}
        ldr     r4, =powers
        movs    r6, #0                  @ printed a digit yet?
digit:
        ldr     r5, [r4]
        cmp     r5, #0
        beq     done
        movs    r1, #0
sub10:
        cmp     r0, r5
        blo     out
        subs    r0, r0, r5
        adds    r1, r1, #1
        b       sub10
out:
        adds    r4, r4, #4
        orrs    r6, r1
        cmp     r6, #0
        beq     digit
        push    {r0}
        movs    r0, r1
        adds    r0, #'0'
        bl      putchar
        pop     {r0}
        b       digit
done:
        cmp     r6, #0
        bne     1f
        movs    r0, #'0'
        bl      putchar
1:      movs    r0, #10
        bl      putchar
        pop     {r4-r6, pc}
        .ltorg

        .data
        .balign 4
powers: .word   1000000000, 100000000, 10000000, 1000000, 100000, 10000, 1000, 100, 10, 1, 0
