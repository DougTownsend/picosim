@ A main that forgets pop {pc}: the CPU runs on into empty memory.
        .syntax unified
        .cpu cortex-m0plus
        .thumb
main:
        push    {r7, lr}
        movs    r0, #'A'
        bl      putchar
        movs    r0, #0
