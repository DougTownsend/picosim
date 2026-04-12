.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * os.s — Minimal simulator operating system.
 *
 * Memory layout:
 *   0x0000-0x2FFB  OS code/data (this file)
 *   0x2FFC-0x2FFF  "main pointer" — the simulator writes the Thumb address
 *                  of the user's main() here before starting execution.
 *   0x3000-0xFFFF  User code
 *
 * Execution flow:
 *   _start  →  reads main pointer from 0x2FFC  →  BLX to user main()
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

.equ MAIN_PTR, 0x2FFC   /* address where simulator stores main's Thumb addr */

/* ── RP2040 GPIO peripheral constants ─────────────────────────────────────────
 *
 * RESETS — take IO_BANK0 and PADS_BANK0 out of reset before using GPIO.
 *   Write ~(RESETS_IO_BANK0 | RESETS_PADS_BANK0) to RESETS_RESET, then
 *   poll RESETS_RESET_DONE until those bits are set.
 *
 * IO_BANK0 — select the SIO function for each pin:
 *   GPIOx CTRL register = IO_BANK0_BASE + x*8 + 4
 *   Write GPIO_FUNC_SIO (5) to CTRL to connect the pin to the SIO block.
 *
 * PADS_BANK0 — configure pull-up/pull-down and input enable per pin:
 *   GPIOx pad register = PADS_BANK0_BASE + x*4 + 4
 *
 * SIO — fast single-cycle GPIO read/write (use after IO_BANK0 setup):
 *   Write a bitmask (bit N = GPIO pin N) to the SET/CLR/XOR registers.
 * ─────────────────────────────────────────────────────────────────────────── */

/* RESETS */
.global RESETS_RESET
.equ    RESETS_RESET,       0x4000C000  /* write: clear bit to unreset */
.global RESETS_RESET_DONE
.equ    RESETS_RESET_DONE,  0x4000C008  /* read:  bit set when unreset complete */
.global RESETS_IO_BANK0
.equ    RESETS_IO_BANK0,    (1 << 5)    /* IO_BANK0 bit */
.global RESETS_PADS_BANK0
.equ    RESETS_PADS_BANK0,  (1 << 8)    /* PADS_BANK0 bit */
/* combined mask — use with ldr r0, =RESETS_GPIO_MASK */
.global RESETS_GPIO_MASK
.equ    RESETS_GPIO_MASK,   (RESETS_IO_BANK0 | RESETS_PADS_BANK0)

/* IO_BANK0 — GPIO function select (GPIOx CTRL = IO_BANK0_BASE + x*8 + 4) */
.global IO_BANK0_BASE
.equ    IO_BANK0_BASE,      0x40014000
.global GPIO_FUNC_SIO
.equ    GPIO_FUNC_SIO,      5           /* SIO function number */

/* PADS_BANK0 — pad control (GPIOx = PADS_BANK0_BASE + x*4 + 4) */
.global PADS_BANK0_BASE
.equ    PADS_BANK0_BASE,    0x4001C000
.global PADS_PDE
.equ    PADS_PDE,           (1 << 2)    /* pull-down enable */
.global PADS_PUE
.equ    PADS_PUE,           (1 << 3)    /* pull-up enable */
.global PADS_IE
.equ    PADS_IE,            (1 << 6)    /* input enable (set for input pins) */
/* combined pad values — use with ldr r0, =PADS_INPUT_PUE / PADS_INPUT_PDE */
.global PADS_INPUT_PUE
.equ    PADS_INPUT_PUE,     (PADS_PUE | PADS_IE)  /* pull-up + input-enable */
.global PADS_INPUT_PDE
.equ    PADS_INPUT_PDE,     (PADS_PDE | PADS_IE)  /* pull-down + input-enable */

/* SIO — GPIO control */
.global SIO_GPIO_IN
.equ    SIO_GPIO_IN,        0xD0000004  /* read current level of all GPIO pins */
.global SIO_GPIO_OUT
.equ    SIO_GPIO_OUT,       0xD0000010  /* write: set all output levels */
.global SIO_GPIO_OUT_SET
.equ    SIO_GPIO_OUT_SET,   0xD0000014  /* write: drive selected pins high */
.global SIO_GPIO_OUT_CLR
.equ    SIO_GPIO_OUT_CLR,   0xD0000018  /* write: drive selected pins low */
.global SIO_GPIO_OUT_XOR
.equ    SIO_GPIO_OUT_XOR,   0xD000001C  /* write: toggle selected pins */
.global SIO_GPIO_OE
.equ    SIO_GPIO_OE,        0xD0000020  /* write: set output-enable mask */
.global SIO_GPIO_OE_SET
.equ    SIO_GPIO_OE_SET,    0xD0000024  /* write: make selected pins outputs */
.global SIO_GPIO_OE_CLR
.equ    SIO_GPIO_OE_CLR,    0xD0000028  /* write: make selected pins inputs */
.global SIO_GPIO_OE_XOR
.equ    SIO_GPIO_OE_XOR,    0xD000002C  /* write: toggle output-enable */

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
