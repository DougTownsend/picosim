/* Linker script for user code — loaded at 0x3000 in the flat 16-bit address space.
   Addresses 0x0000-0x2FFF are reserved for the operating system (os.s / os.ld). */
ENTRY(main)

MEMORY
{
    USER (rwx) : ORIGIN = 0x3000, LENGTH = 0xD000
}

SECTIONS
{
    . = 0x3000;

    .text :
    {
        *(.text.startup)
        *(.text*)
        *(.rodata*)
    } > USER

    .data :
    {
        *(.data*)
    } > USER

    .bss :
    {
        *(.bss*)
        *(COMMON)
    } > USER

    . = ALIGN(4);
    _end = .;
}
