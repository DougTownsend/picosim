.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * os.s — Minimal simulator operating system.
 *
 * Memory layout (the RP2040's, see memmap.py):
 *   0x10000000-0x100000FB  OS code (this file), at the start of flash
 *   0x100000FC-0x100000FF  "main pointer" — the simulator writes the Thumb
 *                          address of the user's main() here before starting.
 *   0x10000100-            User code and constants, in flash
 *   0x20000000-0x20041FFF  User variables (.data, .bss) and the stack, in SRAM;
 *                          SP starts at 0x20042000
 *
 * Execution flow:
 *   _start  →  reads main pointer from 0x100000FC  →  BLX to user main()
 *           →  when main() returns, executes BKPT #0 to halt the simulator.
 *
 * I/O stubs (Pico SDK compatible):
 *   putchar(r0)  — SVC #2: simulator prints r0 as an ASCII character
 *   getchar()    — SVC #3: simulator reads one ASCII character into r0
 *
 * These match the calling convention of the Pico SDK's putchar()/getchar()
 * so the same .s file runs unmodified on both the simulator and the Pico.
 *
 * In interactive mode the simulator keeps the prompt alive after a halt so
 * the user can still inspect registers and memory.
 */

.equ MAIN_PTR, 0x100000FC   /* where the simulator stores main's Thumb addr (memmap.MAIN_PTR) */

/* ── Boot entry point ──────────────────────────────────────────────────────── */

.text
.global _start
.type   _start, %function
_start:
    ldr  r0, =MAIN_PTR   /* r0 = &main_ptr                    */
    ldr  r0, [r0]         /* r0 = Thumb address of user main() */
    blx  r0               /* call main(); LR = return address  */

    /* main() returned — halt the simulator */
    bkpt #0

    /* Safety net: infinite loop (never reached in normal operation) */
.L_halt:
    b    .L_halt

/* Literal pool for the ldr r0, =MAIN_PTR above */
.pool

/* ── I/O stubs ─────────────────────────────────────────────────────────────── */

/*
 * int putchar(int c)   [r0 = character, returns r0 unchanged]
 * SVC #2: simulator writes chr(r0) to stdout.
 */
.global putchar
.type   putchar, %function
putchar:
    svc  #2
    bx   lr

/*
 * int getchar(void)   [returns character in r0, or -1 on EOF]
 * SVC #3: simulator reads one character from stdin into r0.
 */
.global getchar
.type   getchar, %function
getchar:
    svc  #3
    bx   lr

/* ── Benchmark markers ─────────────────────────────────────────────────────── */

/*
 * bench_start / bench_stop: mark a region of the program to time.  They do
 * nothing here (picosim counts the region's cycles itself); when the program
 * runs on a real RP2040 under Testing (picosim/hwtest/os_hw.s) they read the
 * chip's SysTick cycle counter.  Every register and flag is left unchanged.
 */
.global bench_start
.type   bench_start, %function
bench_start:
    bx   lr

.global bench_stop
.type   bench_stop, %function
bench_stop:
    bx   lr
