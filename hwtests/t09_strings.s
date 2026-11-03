@ Title: Strings
@ About: strlen, an in-place reverse and a copy of a zero-terminated string, mixing byte loads and stores with word-sized pointer arithmetic. Prints the string, reversed and copied.
@ Kind: trace
@ Calls: putchar prints the string, then the reversed string, then the copy, one character per call and each followed by a new line, so the results are checked as printed output too.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r6, lr}
        ldr     r0, =text
        bl      strlen                  @ r0 = length
        movs    r4, r0
        ldr     r0, =text
        bl      puts
        @ reverse in place
        ldr     r1, =text
        adds    r2, r1, r4
        subs    r2, r2, #1
swap:
        cmp     r1, r2
        bhs     reversed
        ldrb    r5, [r1]
        ldrb    r6, [r2]
        strb    r6, [r1]
        strb    r5, [r2]
        adds    r1, r1, #1
        subs    r2, r2, #1
        b       swap
reversed:
        ldr     r0, =text
        bl      puts
        @ copy it, terminator and all
        ldr     r1, =text
        ldr     r2, =copy
copy_loop:
        ldrb    r3, [r1]
        strb    r3, [r2]
        adds    r1, r1, #1
        adds    r2, r2, #1
        cmp     r3, #0
        bne     copy_loop
        ldr     r0, =copy
        bl      puts
        pop     {r4-r6, pc}

        .type   strlen, %function
strlen:
        movs    r1, r0
1:      ldrb    r2, [r1]
        cmp     r2, #0
        beq     2f
        adds    r1, r1, #1
        b       1b
2:      subs    r0, r1, r0
        bx      lr

@ print the string at r0 and a new line
        .type   puts, %function
puts:
        push    {r4, lr}
        movs    r4, r0
1:      ldrb    r0, [r4]
        cmp     r0, #0
        beq     2f
        bl      putchar
        adds    r4, r4, #1
        b       1b
2:      movs    r0, #10
        bl      putchar
        pop     {r4, pc}
        .ltorg

        .data
text:   .asciz  "Pico RP2040"
copy:   .space  16
