.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * os_hw.s — picosim's built-in OS for a real RP2040 under Testing.
 *
 * Testing loads a program into the RP2040's SRAM through the debug port and
 * runs it there.  This file takes the place of os.s: every routine a program
 * can call starts at the same offset as in os.s, and it is linked at
 * 0x20020000 with the program at 0x20020100, so every code address on the
 * chip is the simulator's address + 0x10020000; data addresses are the same
 * (image.py checks this).  The routines only the chip runs are in .text.hw,
 * just below, at 0x2001FC00.
 *
 *   _start              the same as os.s (MAIN_PTR moves with the code)
 *   putchar / getchar   BKPT #2 / #3: the chip stops, the test runner does
 *                       the I/O (reads R0, or puts the next input in R0)
 *                       and returns to the caller, as picosim's SVC does
 *   bench_start / stop  read the SysTick counter into BENCH_TABLE; registers,
 *                       flags and the program's memory are left unchanged
 *
 * Built with --defsym TIMED=1 (image.build(timed=True)) for timing a program
 * that has no bench_start / bench_stop: every stretch of it between the OS
 * is a region.  The runner starts the chip at hw_go, which reads SysTick and
 * calls main; putchar and getchar read it before their BKPT and again after
 * it, and main's return reads it a last time.  Each pair goes into
 * BENCH_TABLE as bench_stop's do.
 *
 * BENCH_TABLE (in SRAM the program does not use, image.DATA_LIMIT):
 *   +0  number of regions recorded      +4  SysTick value at the last bench_start
 *   +8  [start, stop] SysTick values, 8 bytes per region (BENCH_MAX regions)
 */

.equ MAIN_PTR,    0x200200FC     /* os.s's 0x100000FC, moved with the code */
.equ SYST_CVR,    0xE000E018
.equ BENCH_TABLE, 0x2001E000     /* image.BENCH_TABLE */
.equ BENCH_MAX,   250

.text
.global _start
.type   _start, %function
_start:
    ldr  r0, =MAIN_PTR
    ldr  r0, [r0]
    blx  r0
    bkpt #0
.L_halt:
    b    .L_halt
.pool

.global putchar
.type   putchar, %function
putchar:
.ifdef TIMED
    b    hw_putchar
.else
    bkpt #2
.endif
    bx   lr

.global getchar
.type   getchar, %function
getchar:
.ifdef TIMED
    b    hw_getchar
.else
    bkpt #3
.endif
    bx   lr

.global bench_start
.type   bench_start, %function
bench_start:
    b    hw_bench_start

.global bench_stop
.type   bench_stop, %function
bench_stop:
    b    hw_bench_stop

.section .text.hw, "ax"

/* The SysTick read is the third instruction of both routines, so the time
 * between the two reads is the region plus a fixed overhead, which the
 * calibration test measures (an empty region). */
.macro SYSTICK_START            @ SysTick into BENCH_TABLE+4; all else unchanged
    push {r0, r1}
    ldr  r0, =SYST_CVR
    ldr  r0, [r0]               @ SysTick now (it counts down)
    ldr  r1, =BENCH_TABLE
    str  r0, [r1, #4]
    pop  {r0, r1}
.endm

.macro SYSTICK_STOP             @ the pair into the table; all else unchanged
    push {r0, r1}
    ldr  r0, =SYST_CVR
    ldr  r0, [r0]               @ SysTick now
    push {r2, r3}
    mrs  r3, apsr               @ keep the caller's flags
    ldr  r1, =BENCH_TABLE
    ldr  r2, [r1]               @ regions recorded so far
    cmp  r2, #BENCH_MAX
    bhs  1f                     @ table full: drop it
    adds r2, r2, #1
    str  r2, [r1]
    lsls r2, r2, #3             @ region n's pair is at +8n (n counted from 1)
    adds r1, r1, r2
    str  r0, [r1, #4]           @ stop
    ldr  r0, =BENCH_TABLE
    ldr  r0, [r0, #4]
    str  r0, [r1]               @ start
1:
    msr  apsr_nzcvq, r3
    pop  {r2, r3}
    pop  {r0, r1}
.endm

.type hw_bench_start, %function
hw_bench_start:
    SYSTICK_START
    bx   lr

.type hw_bench_stop, %function
hw_bench_stop:
    SYSTICK_STOP
    bx   lr

.ifdef TIMED
/* Every path from one SysTick read to the next costs the same as Table 81
 * counts it: after a read, LDR =, STR, POP {r0,r1} and a 2-cycle branch
 * (BX LR, or BLX r0 into main); before the next, the program's own call or
 * return, a B, PUSH {r0,r1}, LDR = and the reading LDR. */
.global hw_go
.type hw_go, %function
hw_go:                          @ the runner starts the chip here, r0 = main
    SYSTICK_START
    blx  r0
    b    hw_end                 @ main returns here

.type hw_putchar, %function
hw_putchar:
    SYSTICK_STOP
.global hw_putchar_bkpt
hw_putchar_bkpt:
    bkpt #2                     @ the runner prints R0 and continues
    SYSTICK_START
    bx   lr

.type hw_getchar, %function
hw_getchar:
    SYSTICK_STOP
.global hw_getchar_bkpt
hw_getchar_bkpt:
    bkpt #3                     @ the runner puts the next input in R0 and continues
    SYSTICK_START
    bx   lr

.type hw_end, %function
hw_end:
    SYSTICK_STOP
.global hw_end_bkpt
hw_end_bkpt:
    bkpt #0
.endif
.pool
