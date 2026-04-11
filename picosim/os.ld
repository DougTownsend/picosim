/* Linker script for the simulator OS — occupies 0x0000-0x2FFF.
   The simulator writes the user main() Thumb address to 0x2FFC before
   starting execution, and _start reads it via an indirect call. */
ENTRY(_start)

MEMORY
{
    OS (rwx) : ORIGIN = 0x0000, LENGTH = 0x3000
}

SECTIONS
{
    . = 0x0000;

    .text :
    {
        *(.text.startup)
        *(.text*)
        *(.rodata*)
    } > OS

    .data :
    {
        *(.data*)
    } > OS

    .bss :
    {
        *(.bss*)
        *(COMMON)
    } > OS

    . = ALIGN(4);
    _os_end = .;
}
