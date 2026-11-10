/* Linker script for the simulator OS — the first 256 bytes of flash,
   0x10000000-0x100000FF (memmap.py; on a Pico this is where the boot code,
   boot2, sits).  The program follows at 0x10000100 (link.ld).
   The simulator writes the user main() Thumb address to 0x100000FC before
   starting execution, and _start reads it via an indirect call. */
ENTRY(_start)

MEMORY
{
    OS (rx) : ORIGIN = 0x10000000, LENGTH = 0x100
}

SECTIONS
{
    .text :
    {
        *(.text.startup)
        *(.text*)
        *(.rodata*)
    } > OS

    . = ALIGN(4);
    _os_end = .;
}
