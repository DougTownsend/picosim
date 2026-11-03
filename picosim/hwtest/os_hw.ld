/* os_hw.s on the RP2040: the start of SRAM, where os.s's 0x0000 maps to. */
ENTRY(_start)

MEMORY
{
    OS (rwx) : ORIGIN = 0x20000000, LENGTH = 0x3000
}

SECTIONS
{
    . = 0x20000000;
    .text : { *(.text*) *(.rodata*) } > OS
    .data : { *(.data*) } > OS
    .bss  : { *(.bss*) *(COMMON) } > OS
}
