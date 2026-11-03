@ Title: Branches and calls
@ About: All 14 conditional branches under all 16 combinations of N, Z, C and V (set with MSR), then every way to change the PC: B, BL, BX, BLX, POP {PC}, MOV PC and ADD PC (a jump table).
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

@ TRY cc: shift a 1 into R1 if B<cc> is taken, a 0 if not
        .macro  TRY cc
        msr     APSR_nzcvq, r0
        b\cc    1f
        lsls    r1, r1, #1
        b       2f
1:      lsls    r1, r1, #1
        adds    r1, r1, #1
2:
        .endm

        .text
main:
        push    {r4-r7, lr}
        movs    r5, #0                  @ NZCV, 0 to 15
flags:
        lsls    r0, r5, #28             @ the flags in APSR's top four bits
        movs    r1, #0
        TRY     eq
        TRY     ne
        TRY     cs
        TRY     cc
        TRY     mi
        TRY     pl
        TRY     vs
        TRY     vc
        TRY     hi
        TRY     ls
        TRY     ge
        TRY     lt
        TRY     gt
        TRY     le
        adds    r5, r5, #1
        cmp     r5, #16
        bne     flags

        @ calls and returns
        bl      leaf                    @ BL, returns with BX LR
        bl      nested                  @ returns with POP {PC}
        ldr     r3, =leaf
        blx     r3                      @ BLX register (bit 0 set: Thumb)
        adr     r3, target_b
        adds    r3, r3, #1
        bx      r3                      @ BX register
        nop
        .balign 4
target_b:
        ldr     r3, =after_mov
        mov     pc, r3                  @ MOV PC (bit 0 is ignored)
        nop
        .balign 2
after_mov:
        movs    r4, #0                  @ a jump table: case 0, 1, 2
table:
        lsls    r2, r4, #1              @ each case is 2 bytes apart (a B)
        add     pc, r2                  @ ADD PC, Rm: PC reads as this + 4
        nop
        b       case0
        b       case1
        b       case2
case0:
        movs    r6, #10
        b       next
case1:
        movs    r6, #11
        b       next
case2:
        movs    r6, #12
next:
        adds    r4, r4, #1
        cmp     r4, #3
        bne     table
        pop     {r4-r7, pc}

        .type   leaf, %function
leaf:
        adds    r7, r7, #1
        bx      lr

        .type   nested, %function
nested:
        push    {r4, lr}
        bl      leaf
        pop     {r4, pc}
        .ltorg
