.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * gpio_test.s — Comprehensive RP2040 GPIO test for all 30 pins.
 *
 * All peripheral constants come from os.s (globally exported .equ symbols).
 *
 * Test sequence:
 *   1. Unreset IO_BANK0 and PADS_BANK0 via RESETS
 *   2. Configure all 30 pins as SIO (via IO_BANK0 loop)
 *   3. All outputs HIGH → verify GPIO_IN == ALL_PINS
 *   4. All outputs LOW  → verify GPIO_IN == 0
 *   5. All inputs, no pulls → verify GPIO_IN == 0 (floating reads 0)
 *   6. All inputs, pull-up  → verify GPIO_IN == ALL_PINS
 *   7. All inputs, pull-down → verify GPIO_IN == 0
 *
 * Returns r0=0 on success, or the number of the first failing check.
 *
 * Interactive use — before typing 'r', set breakpoints at the five
 * labelled inspection points:
 *
 *   b all_outputs_high    (view: all 30 pins O1)
 *   b all_outputs_low     (view: all 30 pins O0)
 *   b all_inputs_z        (view: all 30 pins IZ — try 'gpio N 1/0/z' here)
 *   b all_inputs_pullup   (view: all 30 pins I1)
 *   b all_inputs_pulldown (view: all 30 pins I0)
 */

/* ── Peripheral constants from os.s ─────────────────────────────────────── */
.extern RESETS_RESET
.extern RESETS_RESET_DONE
.extern RESETS_GPIO_MASK        /* RESETS_IO_BANK0 | RESETS_PADS_BANK0 */
.extern IO_BANK0_BASE
.extern GPIO_FUNC_SIO
.extern PADS_BANK0_BASE
.extern PADS_INPUT_PUE          /* PADS_PUE | PADS_IE */
.extern PADS_INPUT_PDE          /* PADS_PDE | PADS_IE */
.extern SIO_GPIO_IN
.extern SIO_GPIO_OUT_SET
.extern SIO_GPIO_OUT_CLR
.extern SIO_GPIO_OE
.extern SIO_GPIO_OE_CLR

/* ── Local constants ─────────────────────────────────────────────────────── */
.equ ALL_PINS, 0x3FFFFFFF       /* bitmask for GPIO0–GPIO29 */
.equ NUM_PINS, 30

/* ── Entry point ─────────────────────────────────────────────────────────── */
asm_main:
    push    {r4, r5, r6, r7, lr}
    movs    r7, #0              @ failure code (0 = all passed)

    /* ── 1. Unreset IO_BANK0 and PADS_BANK0 ──────────────────────────── */

    ldr     r0, =RESETS_RESET
    ldr     r1, =RESETS_GPIO_MASK
    ldr     r2, [r0]
    bics    r2, r2, r1          @ clear peripheral reset bits
    str     r2, [r0]
1:
    ldr     r0, =RESETS_RESET_DONE
    ldr     r2, [r0]
    ldr     r1, =RESETS_GPIO_MASK
    ands    r2, r2, r1
    cmp     r2, r1
    bne     1b

    /* ── 2. Configure all 30 pins as SIO via IO_BANK0 ─────────────────── *
     * IO_BANK0 CTRL for pin N = IO_BANK0_BASE + N*8 + 4                   */

    ldr     r5, =IO_BANK0_BASE
    ldr     r6, =GPIO_FUNC_SIO
    movs    r4, #0              @ pin counter
.L_func_loop:
    lsls    r0, r4, #3          @ r0 = pin * 8
    adds    r0, r0, #4          @ r0 = pin * 8 + 4 (CTRL offset)
    str     r6, [r5, r0]        @ write GPIO_FUNC_SIO to CTRL
    adds    r4, r4, #1
    cmp     r4, #NUM_PINS
    blt     .L_func_loop

    /* ── 3. All outputs HIGH ─────────────────────────────────────────── */

    ldr     r0, =SIO_GPIO_OE
    ldr     r1, =ALL_PINS
    str     r1, [r0]            @ all 30 pins → output-enable

    ldr     r0, =SIO_GPIO_OUT_SET
    ldr     r1, =ALL_PINS
    str     r1, [r0]            @ drive all HIGH

all_outputs_high:               @ --- inspection point: view shows all O1 ---
    ldr     r0, =SIO_GPIO_IN
    ldr     r0, [r0]
    ldr     r1, =ALL_PINS
    ands    r0, r0, r1
    cmp     r0, r1
    beq     2f
    movs    r7, #1              @ FAIL: not all pins read high
    b       test_done

    /* ── 4. All outputs LOW ──────────────────────────────────────────── */
2:
    ldr     r0, =SIO_GPIO_OUT_CLR
    ldr     r1, =ALL_PINS
    str     r1, [r0]            @ drive all LOW

all_outputs_low:                @ --- inspection point: view shows all O0 ---
    ldr     r0, =SIO_GPIO_IN
    ldr     r0, [r0]
    ldr     r1, =ALL_PINS
    ands    r0, r0, r1
    cmp     r0, #0
    beq     3f
    movs    r7, #2              @ FAIL: not all pins read low
    b       test_done

    /* ── 5. All inputs, no pulls (floating → reads 0) ────────────────── *
     * PADS_BANK0 offset for pin N = 4 + N*4                               */
3:
    ldr     r0, =SIO_GPIO_OE_CLR
    ldr     r1, =ALL_PINS
    str     r1, [r0]            @ remove all output enables

    ldr     r5, =PADS_BANK0_BASE
    movs    r6, #0              @ clear PUE and PDE
    movs    r4, #0
.L_clr_pad_loop:
    lsls    r0, r4, #2          @ r0 = pin * 4
    adds    r0, r0, #4          @ r0 = pin * 4 + 4 (pad offset)
    str     r6, [r5, r0]
    adds    r4, r4, #1
    cmp     r4, #NUM_PINS
    blt     .L_clr_pad_loop

all_inputs_z:                   @ --- inspection point: view shows all IZ ---
                                @ Try: 'gpio N 1/0/z' to drive individual pins
    ldr     r0, =SIO_GPIO_IN
    ldr     r0, [r0]
    ldr     r1, =ALL_PINS
    ands    r0, r0, r1
    cmp     r0, #0              @ no external drive, no pull → all read 0
    beq     4f
    movs    r7, #3              @ FAIL: floating inputs should read 0
    b       test_done

    /* ── 6. All inputs, pull-up ──────────────────────────────────────── */
4:
    ldr     r5, =PADS_BANK0_BASE
    ldr     r6, =PADS_INPUT_PUE
    movs    r4, #0
.L_pue_loop:
    lsls    r0, r4, #2
    adds    r0, r0, #4
    str     r6, [r5, r0]
    adds    r4, r4, #1
    cmp     r4, #NUM_PINS
    blt     .L_pue_loop

all_inputs_pullup:              @ --- inspection point: view shows all I1 ---
    ldr     r0, =SIO_GPIO_IN
    ldr     r0, [r0]
    ldr     r1, =ALL_PINS
    ands    r0, r0, r1
    cmp     r0, r1
    beq     5f
    movs    r7, #4              @ FAIL: pulled-up inputs should read 1
    b       test_done

    /* ── 7. All inputs, pull-down ────────────────────────────────────── */
5:
    ldr     r5, =PADS_BANK0_BASE
    ldr     r6, =PADS_INPUT_PDE
    movs    r4, #0
.L_pde_loop:
    lsls    r0, r4, #2
    adds    r0, r0, #4
    str     r6, [r5, r0]
    adds    r4, r4, #1
    cmp     r4, #NUM_PINS
    blt     .L_pde_loop

all_inputs_pulldown:            @ --- inspection point: view shows all I0 ---
    ldr     r0, =SIO_GPIO_IN
    ldr     r0, [r0]
    ldr     r1, =ALL_PINS
    ands    r0, r0, r1
    cmp     r0, #0
    beq     test_done
    movs    r7, #5              @ FAIL: pulled-down inputs should read 0

test_done:
    movs    r0, r7              @ r0=0 → pass, r0=N → first failing check
    pop     {r4, r5, r6, r7, pc}

.pool
