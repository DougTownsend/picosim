@ Title: Small programs
@ About: Real code rather than single instructions: CRC-32 of "123456789" bit by bit (prints CBF43926, the standard check value), a bubble sort of eight numbers, and Euclid's GCD by subtraction. Every intermediate step is compared.
@ Kind: trace
@ Calls: putchar prints the CRC-32 as eight hex digits and a new line, one character per call, so the final answer is checked as printed output too.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        @ CRC-32 (reflected, polynomial 0xEDB88320)
        ldr     r1, =message
        movs    r2, #9                  @ length
        movs    r0, #0
        mvns    r0, r0                  @ crc = 0xFFFFFFFF
        ldr     r3, =0xEDB88320
byte:
        ldrb    r4, [r1]
        adds    r1, r1, #1
        eors    r0, r4
        movs    r5, #8
bit:
        lsrs    r0, r0, #1              @ C = the bit shifted out
        bcc     1f
        eors    r0, r3
1:      subs    r5, r5, #1
        bne     bit
        subs    r2, r2, #1
        bne     byte
        mvns    r0, r0
        bl      print_hex
        @ bubble sort
        ldr     r4, =numbers
        movs    r5, #7                  @ passes
pass:
        movs    r6, #0
cmpswap:
        lsls    r7, r6, #2
        adds    r7, r4, r7
        ldr     r0, [r7]
        ldr     r1, [r7, #4]
        cmp     r0, r1
        ble     noswap
        str     r1, [r7]
        str     r0, [r7, #4]
noswap:
        adds    r6, r6, #1
        cmp     r6, r5
        blt     cmpswap
        subs    r5, r5, #1
        bne     pass
        @ gcd(1071, 462) = 21
        ldr     r0, =1071
        ldr     r1, =462
gcd:
        cmp     r0, r1
        beq     gcd_done
        bhi     1f
        subs    r1, r1, r0
        b       gcd
1:      subs    r0, r0, r1
        b       gcd
gcd_done:
        pop     {r4-r7, pc}

@ print r0 as 8 hex digits and a new line
        .type   print_hex, %function
print_hex:
        push    {r4, r5, lr}
        movs    r4, r0
        movs    r5, #8
1:      lsrs    r0, r4, #28
        lsls    r4, r4, #4
        cmp     r0, #10
        blo     2f
        adds    r0, #'A' - 10 - '0'
2:      adds    r0, #'0'
        bl      putchar
        subs    r5, r5, #1
        bne     1b
        movs    r0, #10
        bl      putchar
        pop     {r4, r5, pc}
        .ltorg

        .data
message: .ascii "123456789"
        .balign 4
numbers: .word  42, -7, 1000, 3, 0, -2000, 19, 3
