.syntax unified
.cpu cortex-m0plus
.thumb

asm_main:
    push {r4, lr}
    movs r4, #0
    ldr  r0, =1000000
.Lloop:
    adds r4, r4, #1
    cmp  r4, r0
    blt  .Lloop
    movs r0, #0
    pop {r4, pc}
