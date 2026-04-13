/* peripherals.s — RP2040 peripheral constants for both the simulator and hardware.
 *
 * All symbols are defined as .global .equ (absolute) so they can be used by
 * any assembly file via .extern and ldr Rd, =SYMBOL.
 *
 * Linking:
 *   Simulator  — compile_asm() includes this file alongside the user source
 *   UF2        — CMakeLists.txt lists it as a source file
 */

.syntax unified
.cpu cortex-m0plus
.thumb

/* ── RESETS ────────────────────────────────────────────────────────────────────
 * Take IO_BANK0 and PADS_BANK0 out of reset before using GPIO.
 * Write ~mask to RESETS_RESET, then poll RESETS_RESET_DONE until bits are set.
 * ─────────────────────────────────────────────────────────────────────────── */
.global RESETS_RESET
.equ    RESETS_RESET,       0x4000C000  /* write: clear bit to unreset */
.global RESETS_RESET_DONE
.equ    RESETS_RESET_DONE,  0x4000C008  /* read:  bit set when unreset complete */
.global RESETS_IO_BANK0
.equ    RESETS_IO_BANK0,    (1 << 5)    /* IO_BANK0 bit in RESETS */
.global RESETS_PADS_BANK0
.equ    RESETS_PADS_BANK0,  (1 << 8)    /* PADS_BANK0 bit in RESETS */
/* combined mask — use with ldr r0, =RESETS_GPIO_MASK */
.global RESETS_GPIO_MASK
.equ    RESETS_GPIO_MASK,   (RESETS_IO_BANK0 | RESETS_PADS_BANK0)

/* ── IO_BANK0 — GPIO function select ──────────────────────────────────────────
 * GPIOx CTRL register = IO_BANK0_BASE + x*8 + 4
 * Write GPIO_FUNC_SIO (5) to connect pin to the SIO block.
 * ─────────────────────────────────────────────────────────────────────────── */
.global IO_BANK0_BASE
.equ    IO_BANK0_BASE,      0x40014000
.global GPIO_FUNC_SIO
.equ    GPIO_FUNC_SIO,      5           /* SIO function number */

/* ── PADS_BANK0 — pad control ─────────────────────────────────────────────────
 * GPIOx pad register = PADS_BANK0_BASE + x*4 + 4
 * ─────────────────────────────────────────────────────────────────────────── */
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

/* ── SIO — fast single-cycle GPIO read/write ──────────────────────────────────
 * Use after IO_BANK0 setup.  Write a bitmask (bit N = GPIO pin N).
 * ─────────────────────────────────────────────────────────────────────────── */
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
