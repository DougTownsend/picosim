/* os_hw.s on the RP2040 (image.py): its routines a program calls at
   0x20020000, where the chip holds its copy of picosim's flash (os.s is at
   0x10000000); its chip-only routines (.text.hw) just below, at 0x2001FC00. */
ENTRY(_start)

MEMORY
{
    OS (rx)  : ORIGIN = 0x20020000, LENGTH = 0x100
    HW (rx)  : ORIGIN = 0x2001FC00, LENGTH = 0x400
}

SECTIONS
{
    .text : { *(.text) *(.rodata*) } > OS
    .text.hw : { *(.text.hw) } > HW
}
