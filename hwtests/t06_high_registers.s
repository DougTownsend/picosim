@ Title: High registers, SP and special registers
@ About: MOV, ADD and CMP with R8-R12, the stack pointer (ADD/SUB SP, ADD Rd, SP, #imm, MOV to and from SP), and the special-register instructions: MRS/MSR APSR, MRS PRIMASK and CONTROL, MSR/MRS PSP, CPSID/CPSIE, NOP, DMB, DSB, ISB.
@ Kind: trace

        .syntax unified
        .cpu cortex-m0plus
        .thumb

        .text
main:
        push    {r4-r7, lr}
        ldr     r0, =0x89ABCDEF
        movs    r1, #7
        mov     r8, r0
        mov     r9, r1
        mov     r10, r8
        mov     r11, sp
        mov     r12, r9
        add     r1, r8                  @ ADD Rdn, Rm (no flags)
        add     r8, r1
        add     r12, r12
        cmp     r8, r1                  @ CMP with a high register
        cmp     r1, r12
        mov     r0, r10
        mov     r2, r11
        mov     r3, r12
        @ the stack pointer
        sub     sp, #24
        add     r4, sp, #20
        add     sp, #8
        mov     r5, sp
        add     sp, #16
        movs    r5, #12
        add     r5, sp                  @ ADD Rdn, SP
        @ special registers
        mrs     r0, APSR
        movs    r1, #0
        cmp     r1, #1                  @ N=1, C=0
        mrs     r0, APSR
        ldr     r1, =0xA0000000
        msr     APSR_nzcvq, r1
        mrs     r0, APSR
        mrs     r0, PRIMASK
        cpsid   i
        mrs     r0, PRIMASK
        cpsie   i
        mrs     r0, PRIMASK
        mrs     r0, CONTROL
        mrs     r0, MSP
        ldr     r1, =0x12345673         @ PSP's bottom two bits read as 0
        msr     PSP, r1
        mrs     r0, PSP
        nop
        dmb
        dsb
        isb
        mov     r8, r1
        movs    r0, #0
        mov     r9, r0
        mov     r10, r0
        mov     r11, r0
        mov     r12, r0
        pop     {r4-r7, pc}
        .ltorg
