@ Title: Loads and stores
@ About: Every load and store width (LDR, LDRH, LDRB, the sign-extending LDRSH and LDRSB, STR, STRH, STRB) with immediate and register offsets, SP-relative, literal and ADR loads, LDM/STM with and without write-back, and PUSH/POP of many registers. Each written byte is read back from the chip.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        ldr     r1, =pattern
        ldr     r2, =buf
        @ loads of every width
        ldr     r0, [r1]
        ldr     r0, [r1, #4]
        ldrh    r0, [r1]
        ldrh    r0, [r1, #2]
        ldrh    r0, [r1, #6]
        ldrb    r0, [r1]
        ldrb    r0, [r1, #1]
        ldrb    r0, [r1, #3]
        movs    r3, #1
        ldrsb   r0, [r1, r3]            @ 0x7F: positive
        movs    r3, #3
        ldrsb   r0, [r1, r3]            @ 0x80: negative
        movs    r3, #2
        ldrsb   r0, [r1, r3]            @ 0xFF: -1
        movs    r3, #6
        ldrsh   r0, [r1, r3]            @ 0x8000: negative
        movs    r3, #4
        ldrsh   r0, [r1, r3]
        ldr     r0, [r1, r3]
        ldrh    r0, [r1, r3]
        ldrb    r0, [r1, r3]
        @ stores of every width, read back as words
        ldr     r0, =0xA1B2C3D4
        str     r0, [r2]
        strh    r0, [r2, #4]
        strh    r0, [r2, #10]
        strb    r0, [r2, #8]
        strb    r0, [r2, #15]
        movs    r3, #12
        str     r0, [r2, r3]
        movs    r3, #17
        strb    r0, [r2, r3]
        movs    r3, #18
        strh    r0, [r2, r3]
        ldr     r4, [r2, #4]
        ldr     r4, [r2, #8]
        ldr     r4, [r2, #16]
        @ literal pool and ADR
        ldr     r0, =0x12345678
        adr     r0, pattern_copy
        ldr     r0, [r0]
        @ SP-relative
        sub     sp, #16
        str     r0, [sp, #4]
        ldr     r5, [sp, #4]
        add     r5, sp, #8
        str     r1, [sp, #12]
        add     sp, #16
        @ multiple loads and stores
        ldr     r2, =buf
        movs    r4, #4
        movs    r5, #5
        movs    r6, #6
        stm     r2!, {r4-r6}            @ write-back: r2 moves 12 bytes
        subs    r2, r2, #12
        ldm     r2!, {r4, r5}           @ write-back
        ldm     r2, {r2, r3}            @ base in the list: no write-back
        ldr     r2, =buf
        ldm     r2, {r0-r3}
        push    {r0-r7}
        movs    r0, #0
        movs    r7, #0
        pop     {r0-r7}
        @ copy 16 bytes, one at a time
        ldr     r1, =pattern
        ldr     r2, =buf
        movs    r3, #0
copy:
        ldrb    r0, [r1, r3]
        strb    r0, [r2, r3]
        adds    r3, r3, #1
        cmp     r3, #16
        blo     copy
        pop     {r4-r7, pc}

        .balign 4
pattern_copy:
        .word   0xCAFEF00D
        .ltorg

        .data
        .balign 4
pattern: .word  0x80FF7F01, 0x80001234, 0xDEADBEEF, 0x12345678
buf:     .space 32
