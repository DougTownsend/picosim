.syntax unified
.cpu cortex-m0plus
.thumb

/*
 * echo_test.s — Reads characters from stdin and echoes them back.
 * Exits the loop (and returns from main) when 'q' is typed.
 *
 * Compatible with the Pico SDK: link against the SDK and the same .s file
 * works over USB serial on the Pico.
 */

.equ gpio_basei, 0x40014000
.equ gpio20_offset, 0xa4
.equ gpio20_out_mask, 0x100

asm_main:
    push    {r7, lr}
    sub     sp, #8
    add     r7, sp, #0

    //Step 1 is getting the GPIO ctrl register address into a register (R3)
    ldr     r1, =gpio_basei
    ldr     r2, =gpio20_offset
    adds     r3, r1, r2'

    //Step 2 is enabling the output of the GPIO by setting bits 12 and 13 to 1.
    movs    r4, #3
    lsls    r4, r4, #12
    LDR     R5, [R3, #0]
    ORrs      R5, R5, R4

    //Step 3 is setting bit 9 to 1. That allows us to then just control the output
    // with bit 8. Bit 8 = 0, output 0. Bit 8 = 1, output 1.
    movs    r4, #1
    lsls    r4, r4, #9
    ORrs      R5, R5, R4
    STR     R5, [R3, #0]
    
    //Step 4 is setting bit 8 to 1 to turn on the LED
    LDR     R5, [R3, #0]
    ldr     r4, =gpio20_out_mask
    ORrs      R5, R5, R4
    STR     R5, [R3, #0]

.Lloop:
    bl      getchar         /* r0 = next character from stdin    */
    cmp     r0, #'q'        /* exit loop on 'q'                  */
    beq     .Ldone
    bl      putchar         /* echo the character back           */
    b       .Lloop

.Ldone:
    ldr r0, =newline
    ldrb r0, [r0]
    bl putchar
    movs    r0, #0          /* return 0                          */
    mov     sp, r7
    add     sp, #8
    pop     {r7, pc}

.align 2
newline:
    .byte '\n'
    .align 2
