/* Linker script for the program, on the RP2040's memory map (memmap.py):
   code and constants in flash from 0x10000100, variables in SRAM from
   0x20000000.  picosim's OS takes flash's first 256 bytes, 0x10000000-
   0x100000FF (os.s / os.ld); the stack starts at the top of SRAM,
   0x20042000, and grows down.

   picosim loads .data straight into SRAM.  On a real Pico the SDK's start-up
   code copies it there from flash; either way the program sees the same
   addresses. */
ENTRY(main)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x10000100, LENGTH = 2M - 0x100
    SRAM  (rwx) : ORIGIN = 0x20000000, LENGTH = 264K
}

SECTIONS
{
    .text :
    {
        *(.text.startup)
        *(.text*)
        *(.rodata*)
    } > FLASH

    .data :
    {
        *(.data*)
    } > SRAM

    .bss (NOLOAD) :
    {
        *(.bss*)
        *(COMMON)
    } > SRAM

    . = ALIGN(4);
    _end = .;
}
