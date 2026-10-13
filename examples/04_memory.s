@ Title: Add up a list in memory
@ About: Loads four numbers from memory with LDR, adds them up, and stores the total with STR.
@ Level: 2
@
@ What to watch
@   The numbers are stored in memory, not in registers. LDR copies a word
@   (4 bytes) from memory into a register; STR copies one back.
@   Open the Memory tab and Go to "numbers" to see them; at the end the total
@   (10 + 20 + 30 + 40 = 100 = 0x64) appears in "total", just after them.

        .syntax unified
        .cpu cortex-m0plus
        .thumb

main:
        push    {r7, lr}

        ldr     r1, =numbers    @ R1 = the address of the first number
        movs    r2, #0          @ R2 = running total
        movs    r3, #4          @ R3 = how many numbers are left
loop:
        ldr     r0, [r1]        @ R0 = the number at address R1
        adds    r2, r2, r0      @ add it to the total
        adds    r1, #4          @ the next word is 4 bytes further on
        subs    r3, #1          @ one fewer left (sets Z when it reaches 0)
        bne     loop

        ldr     r1, =total      @ R1 = the address of "total"
        str     r2, [r1]        @ store the total in memory

        movs    r0, #0
        pop     {r7, pc}

        .data
numbers:
        .word   10, 20, 30, 40
total:
        .word   0
