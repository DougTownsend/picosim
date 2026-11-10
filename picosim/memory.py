"""
Memory-mapped I/O for the ARMv6-M simulator.

MemoryBlock  — base class for any memory-mapped region (the I/O registers).
Memory       — routes reads/writes to the registered blocks.

ROM, flash and SRAM live in the C++ core (memmap.py); only addresses outside
them come here, and an address no block claims is a bus fault (cpu.py).
"""


class MemoryBlock:
    """
    Base class for a contiguous memory-mapped region.

    Subclasses override read8/read16/read32 and write8/write16/write32.
    Subclasses that cover multiple disjoint ranges (e.g. GPIO peripherals)
    may override handles() directly.
    """

    def __init__(self, base=0, size=0):
        self.base = base
        self.size = size

    def handles(self, addr):
        addr &= 0xFFFFFFFF
        return self.base <= addr < self.base + self.size

    def read8(self, addr):            return 0
    def read16(self, addr):           return 0
    def read32(self, addr):           return 0
    def write8(self, addr, val):      pass
    def write16(self, addr, val):     pass
    def write32(self, addr, val):     pass


class Memory:
    """
    Routes I/O reads/writes to registered blocks.  `image` is the program's
    memory as loaded (a memmap.Image), which the CPU copies into the core.
    """

    def __init__(self, image):
        self.image = image
        self._peripherals: list[MemoryBlock] = []

    def add_block(self, block: MemoryBlock):
        """Register a peripheral memory block."""
        self._peripherals.append(block)

    def peripheral(self, addr):
        """The peripheral block at `addr`, or None if no peripheral claims it."""
        for block in self._peripherals:
            if block.handles(addr):
                return block
        return None
