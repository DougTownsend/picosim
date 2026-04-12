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

asm_main:
    push    {r7, lr}
    sub     sp, #8
    add     r7, sp, #0

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