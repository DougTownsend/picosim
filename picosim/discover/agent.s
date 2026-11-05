@ The Level 5 discovery agent: runs batches of test cases on the RP2040.
@
@ The host loads it (and a batch) into SRAM, points the PC at agent_start
@ and lets the core run.  For every case in the batch the agent:
@   1. copies the case's code into its slot (inside a UDF sled) and resets
@      the scratch window's pattern,
@   2. starts the case by an exception return, which sets R0-R3, R12, LR,
@      PC, xPSR and PSP in one step (R4-R11, PRIMASK and CONTROL are set
@      just before), so the case runs in Thread mode on PSP,
@   3. catches however it ends in `capture` (falling into the sled or a
@      branch there hits a UDF: HardFault; a fault: HardFault; SVC: SVCall;
@      any interrupt the case pends), and writes one result record.
@ After the last case it checks itself and the sled, returns to Thread mode
@ and stops on BKPT #0x7F, and the host reads the results.
@
@ The agent runs on MSP, which `capture` reloads before anything else, so a
@ case that wrecks MSP or PSP cannot break it.  Every address and offset
@ comes from layout.inc, which build.py generates from formats.py.

    .syntax unified
    .cpu cortex-m0plus
    .thumb
    .include "layout.inc"

    .section .vectors, "ax"
    .align 8
    .global vectors
vectors:
    .word MSP_TOP
    .word agent_start + 1
    .rept 46                        @ NMI, HardFault, SVCall, PendSV, SysTick, IRQ 0-31: all captured
    .word capture + 1
    .endr

    .text
    .thumb_func
    .global agent_start
agent_start:                        @ Thread mode, privileged, from the host
    ldr r0, =VTOR_REG
    ldr r1, =vectors
    str r1, [r0]
    ldr r0, =MSP_TOP
    msr msp, r0
    ldr r3, =VARS
    movs r0, #STATE_START
    str r0, [r3, #V_STATE]
    str r0, [r3, #V_FILLED]
    ldr r2, =BATCH
    ldr r1, [r2, #H_START]
    str r1, [r3, #V_INDEX]
    lsls r1, r1, #7                 @ RESULT_SIZE = 128
    ldr r0, [r2, #H_RESULTS]
    adds r0, r0, r1
    str r0, [r3, #V_RESPTR]
    svc #0                          @ into Handler mode: capture sees STATE_START and starts
    b .

@ ── every exception lands here ───────────────────────────────────────────────
    .thumb_func
capture:
    ldr r0, =SYST_CVR
    ldr r0, [r0]                    @ SysTick first of all (exception entry timing)
    mrs r1, msp                     @ MSP as the exception left it
    ldr r2, =MSP_TOP
    msr msp, r2                     @ a fresh stack, whatever the case did
    ldr r3, =VARS
    ldr r2, [r3, #V_STATE]
    cmp r2, #STATE_RUNNING
    beq record
    cmp r2, #STATE_START
    beq next_case
    b agent_fault                   @ the agent itself faulted

record:
    movs r2, #STATE_BUSY
    str r2, [r3, #V_STATE]
    ldr r2, [r3, #V_RESPTR]
    str r4, [r2, #R_R4]
    str r5, [r2, #R_R5]
    str r6, [r2, #R_R6]
    str r7, [r2, #R_R7]
    mov r4, r8
    str r4, [r2, #R_R8]
    mov r4, r9
    str r4, [r2, #R_R9]
    mov r4, r10
    str r4, [r2, #R_R10]
    mov r4, r11
    str r4, [r2, #R_R11]
    str r0, [r2, #R_SYSTICK]
    str r1, [r2, #R_MSP]
    mrs r0, psp
    str r0, [r2, #R_PSP]
    mov r4, lr
    str r4, [r2, #R_EXC_RETURN]
    mrs r5, primask
    str r5, [r2, #R_PRIMASK]
    mrs r5, control
    str r5, [r2, #R_CONTROL]
    mrs r5, ipsr
    str r5, [r2, #R_IPSR]
    movs r5, #4                     @ EXC_RETURN bit 2: the frame is on PSP, else on MSP
    tst r4, r5
    bne 1f
    mov r0, r1
1:  str r0, [r2, #R_FRAME]
    ldm r0!, {r4, r5, r6, r7}
    str r4, [r2, #R_F_R0]
    str r5, [r2, #R_F_R1]
    str r6, [r2, #R_F_R2]
    str r7, [r2, #R_F_R3]
    ldm r0!, {r4, r5, r6, r7}
    str r4, [r2, #R_F_R12]
    str r5, [r2, #R_F_LR]
    str r6, [r2, #R_F_PC]
    str r7, [r2, #R_F_XPSR]
    ldr r4, [r3, #V_CASE]
    ldr r5, [r4, #C_ID]
    str r5, [r2, #R_ID]

    ldr r5, [r4, #C_SLOT]           @ put the sled back over the slot
    ldr r6, [r4, #C_CODE_LEN]
    ldr r7, =SLED_HALF
2:  cmp r6, #0
    ble 3f
    strh r7, [r5]
    adds r5, #2
    subs r6, #2
    b 2b
3:
    movs r0, #0
    mvns r0, r0
    str r0, [r2, #R_N_CHANGED]      @ NOT_CHECKED unless checked below
    str r0, [r2, #R_OFF01]
    str r0, [r2, #R_OFF23]
    ldr r0, =BATCH
    ldr r1, [r0, #H_FLAGS]
    movs r5, #(HF_NO_SCRATCH | HF_KEEP_SCRATCH)
    tst r1, r5
    bne scratch_done
    ldr r5, [r0, #H_SCRATCH]        @ r5 = address, r6 = words left, r7 = expected
    ldr r6, [r0, #H_SCRATCH_WORDS]
    ldr r7, [r4, #C_SEED]
    ldr r1, =PATTERN_STEP
    movs r0, #0                     @ r0 = changed words
4:  ldr r3, [r5]
    cmp r3, r7
    bne changed
5:  adds r5, #4
    adds r7, r7, r1
    subs r6, #1
    bne 4b
    str r0, [r2, #R_N_CHANGED]
    b scratch_done
changed:
    str r7, [r5]                    @ restore it for the next case
    cmp r0, #4
    bhs 6f
    push {r1, r6}
    ldr r6, =BATCH
    ldr r6, [r6, #H_SCRATCH]
    subs r6, r5, r6                 @ offset in the window
    lsls r1, r0, #1
    adds r1, r1, r2
    adds r1, #R_OFF01
    strh r6, [r1]
    lsls r1, r0, #2
    adds r1, r1, r2
    str r3, [r1, #R_VAL0]
    pop {r1, r6}
6:  adds r0, #1
    b 5b
scratch_done:
    ldr r3, =VARS
    adds r2, #RESULT_SIZE
    str r2, [r3, #V_RESPTR]
    ldr r0, [r3, #V_INDEX]
    adds r0, #1
    str r0, [r3, #V_INDEX]

@ ── start the next case ──────────────────────────────────────────────────────
next_case:
    ldr r3, =VARS
    movs r0, #STATE_BUSY
    str r0, [r3, #V_STATE]
    ldr r0, =VTOR_REG               @ a scenario may have moved the vector table
    ldr r1, =vectors
    str r1, [r0]
    ldr r0, =NVIC_ICER              @ no interrupt enabled or pending from an earlier case
    movs r1, #0
    mvns r1, r1
    str r1, [r0]
    ldr r0, =NVIC_ICPR
    str r1, [r0]
    ldr r0, =BATCH
    ldr r1, [r3, #V_INDEX]
    ldr r2, [r0, #H_N_CASES]
    cmp r1, r2
    blo 9f
    b finish                        @ (too far for a conditional branch)
9:  lsls r2, r1, #5                 @ index * 96
    lsls r4, r1, #6
    adds r2, r2, r4
    ldr r4, [r0, #H_CASES]
    adds r4, r4, r2
    str r4, [r3, #V_CASE]

    ldr r5, [r4, #C_CODE]           @ copy the code into the slot
    ldr r6, [r4, #C_SLOT]
    ldr r7, [r4, #C_CODE_LEN]
2:  cmp r7, #0
    ble 3f
    ldrh r1, [r5]
    strh r1, [r6]
    adds r5, #2
    adds r6, #2
    subs r7, #2
    b 2b
3:
    ldr r1, [r0, #H_FLAGS]          @ the scratch pattern, if the seed changed
    movs r2, #HF_NO_SCRATCH
    tst r1, r2
    bne 5f
    ldr r2, [r4, #C_SEED]
    ldr r1, [r3, #V_FILLED]
    cmp r1, #0
    beq 4f
    ldr r1, [r3, #V_SEED]
    cmp r1, r2
    beq 5f
4:  str r2, [r3, #V_SEED]
    movs r1, #1
    str r1, [r3, #V_FILLED]
    ldr r5, [r0, #H_SCRATCH]
    ldr r6, [r0, #H_SCRATCH_WORDS]
    ldr r1, =PATTERN_STEP
6:  str r2, [r5]
    adds r5, #4
    adds r2, r2, r1
    subs r6, #1
    bne 6b
5:
    ldr r0, [r4, #C_PSP]            @ the frame the exception return pops
    subs r0, #32
    msr psp, r0
    ldr r1, [r4, #C_R0]
    str r1, [r0, #0]
    ldr r1, [r4, #C_R1]
    str r1, [r0, #4]
    ldr r1, [r4, #C_R2]
    str r1, [r0, #8]
    ldr r1, [r4, #C_R3]
    str r1, [r0, #12]
    ldr r1, [r4, #C_R12]
    str r1, [r0, #16]
    ldr r1, [r4, #C_LR]
    str r1, [r0, #20]
    ldr r1, [r4, #C_SLOT]
    str r1, [r0, #24]
    ldr r1, [r4, #C_XPSR]
    ldr r2, =0x01000000             @ the Thumb bit
    orrs r1, r2
    str r1, [r0, #28]
    ldr r1, [r4, #C_PRIMASK]
    msr primask, r1
    ldr r1, [r4, #C_CONTROL]
    msr control, r1                 @ in Handler mode only nPRIV takes effect
    ldr r1, [r4, #C_R8]
    mov r8, r1
    ldr r1, [r4, #C_R9]
    mov r9, r1
    ldr r1, [r4, #C_R10]
    mov r10, r1
    ldr r1, [r4, #C_R11]
    mov r11, r1
    ldr r0, =ICSR_REG               @ last of all, so nothing the agent did above
    ldr r1, =(1 << 27) | (1 << 25)  @ (filling the scratch window) can fire into the case:
    str r1, [r0]                    @ PENDSVCLR, PENDSTCLR
    ldr r0, =BATCH
    ldr r1, [r0, #H_FLAGS]          @ SysTick restarted for every case
    movs r2, #(HF_SYSTICK | HF_WATCHDOG)
    tst r1, r2
    beq 8f
    ldr r2, =0xFFFFFF
    movs r6, #5                     @ ENABLE, processor clock
    movs r5, #HF_WATCHDOG
    tst r1, r5
    beq 7f
    ldr r2, [r0, #H_WD_RELOAD]
    movs r6, #7                     @ ... and TICKINT: the watchdog
7:  ldr r1, =SYST_RVR
    str r2, [r1]
    ldr r1, =SYST_CVR
    str r2, [r1]                    @ any write clears the count
    ldr r1, =SYST_CSR
    str r6, [r1]
8:  movs r1, #STATE_RUNNING
    str r1, [r3, #V_STATE]
    ldr r0, =EXC_RETURN_PSP
    adds r4, #C_R4
    ldm r4, {r4, r5, r6, r7}
    bx r0                           @ the case runs

    .ltorg

@ ── end of the batch ─────────────────────────────────────────────────────────
finish:
    ldr r0, =SYST_CSR               @ no more watchdog interrupts
    ldr r1, [r0]
    movs r2, #2
    bics r1, r2
    str r1, [r0]
    ldr r0, =ICSR_REG
    ldr r1, =(1 << 25)              @ PENDSTCLR
    str r1, [r0]
    ldr r0, =vectors                @ checksum the agent's own code
    ldr r1, =agent_end
    ldr r2, =0x811C9DC5
    ldr r5, =0x01000193
1:  ldr r4, [r0]
    eors r2, r4
    muls r2, r5, r2
    adds r0, #4
    cmp r0, r1
    blo 1b
    str r2, [r3, #V_SUM]
    ldr r0, =BATCH                  @ the sled must be all UDFs again
    ldr r1, [r0, #H_SLED]
    ldr r2, [r0, #H_SLED_WORDS]
    ldr r4, [r0, #H_SLED_FILL]
    movs r5, #0
    movs r6, #0
2:  ldr r7, [r1]
    cmp r7, r4
    beq 3f
    adds r5, #1
    cmp r6, #0
    bne 3f
    mov r6, r1
3:  adds r1, #4
    subs r2, #1
    bne 2b
    str r5, [r3, #V_SLED_BAD]
    str r6, [r3, #V_SLED_FIRST]
    ldr r1, [r0, #H_GUARD]          @ the guard must be untouched
    ldr r2, [r0, #H_GUARD_WORDS]
    ldr r4, =GUARD_KEY
    movs r5, #0
    movs r6, #0
4:  ldr r7, [r1]
    eors r7, r1
    cmp r7, r4
    beq 5f
    adds r5, #1
    cmp r6, #0
    bne 5f
    mov r6, r1
5:  adds r1, #4
    subs r2, #1
    bne 4b
    str r5, [r3, #V_GUARD_BAD]
    str r6, [r3, #V_GUARD_FIRST]
    movs r0, #STATE_DONE
    str r0, [r3, #V_STATE]
back_to_thread:                     @ return to Thread mode, so the next batch starts clean
    movs r0, #0
    msr primask, r0
    ldr r0, =MSP_TOP
    subs r0, #32
    ldr r1, =stopped
    str r1, [r0, #24]
    ldr r1, =0x01000000
    str r1, [r0, #28]
    msr msp, r0
    ldr r0, =EXC_RETURN_MSP
    bx r0
stopped:
    ldr r3, =VARS
    ldr r0, [r3, #V_STATE]
    cmp r0, #STATE_DONE
    bne 1f
    bkpt #BKPT_DONE
    b stopped
1:  bkpt #BKPT_AGENT_FAULT
    b stopped

agent_fault:                        @ the agent's own code faulted: keep what can be known
    ldr r3, =VARS
    str r1, [r3, #V_FAULT_MSP]
    ldr r0, [r1, #24]
    str r0, [r3, #V_FAULT_PC]
    mov r0, lr
    str r0, [r3, #V_FAULT_LR]
    movs r0, #STATE_AGENT_FAULT
    str r0, [r3, #V_STATE]
    b back_to_thread

    .ltorg
    .align 2
    .global agent_end
agent_end:
