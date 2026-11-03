.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * os_hw.s — picosim's built-in OS for a real RP2040 under Testing.
 *
 * Testing loads a program into the RP2040's SRAM through the debug port and
 * runs it there.  This file takes the place of os.s: every routine a program
 * can call starts at the same offset as in os.s, and it is linked at
 * 0x20000000 with the program at 0x20003000, so every address on the chip is
 * the simulator's address + 0x20000000 (image.py checks this).
 *
 *   _start              the same as os.s (MAIN_PTR moves to SRAM)
 *   putchar / getchar   BKPT #2 / #3: the chip stops, the test runner does
 *                       the I/O (reads R0, or puts the next input in R0)
 *                       and returns to the caller, as picosim's SVC does
 *   bench_start / stop  read the SysTick counter into BENCH_TABLE; registers,
 *                       flags and the program's memory are left unchanged
 *
 * BENCH_TABLE (in the OS's part of SRAM, which picosim never compares):
 *   +0  number of regions recorded      +4  SysTick value at the last bench_start
 *   +8  [start, stop] SysTick values, 8 bytes per region (BENCH_MAX regions)
 */

.equ MAIN_PTR,    0x20002FFC
.equ SYST_CVR,    0xE000E018
.equ BENCH_TABLE, 0x20002000
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
    bkpt #2
    bx   lr

.global getchar
.type   getchar, %function
getchar:
    bkpt #3
    bx   lr

.global bench_start
.type   bench_start, %function
bench_start:
    b    hw_bench_start

.global bench_stop
.type   bench_stop, %function
bench_stop:
    b    hw_bench_stop

/* The SysTick read is the third instruction of both routines, so the time
 * between the two reads is the region plus a fixed overhead, which the
 * calibration test measures (an empty region). */
.type hw_bench_start, %function
hw_bench_start:
    push {r0, r1}
    ldr  r0, =SYST_CVR
    ldr  r0, [r0]               @ SysTick now (it counts down)
    ldr  r1, =BENCH_TABLE
    str  r0, [r1, #4]
    pop  {r0, r1}
    bx   lr

.type hw_bench_stop, %function
hw_bench_stop:
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
    bx   lr
.pool
