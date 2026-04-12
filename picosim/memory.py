"""
Unified memory system for the ARMv6-M simulator.

MemoryBlock  — base class for any memory-mapped region.
FlatRAM      — bytearray-backed RAM for the 16-bit flat address space.
Memory       — routes reads/writes to the correct registered block.
"""

import struct


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


class FlatRAM(MemoryBlock):
    """Bytearray-backed flat RAM covering the simulator's 16-bit address space."""

    def __init__(self, data: bytearray):
        super().__init__(base=0, size=len(data))
        self.data = data

    def read8(self, addr):
        return self.data[addr & 0xFFFF]

    def read16(self, addr):
        return struct.unpack_from('<H', self.data, addr & 0xFFFF)[0]

    def read32(self, addr):
        return struct.unpack_from('<I', self.data, addr & 0xFFFF)[0]

    def write8(self, addr, val):
        self.data[addr & 0xFFFF] = val & 0xFF

    def write16(self, addr, val):
        struct.pack_into('<H', self.data, addr & 0xFFFF, val & 0xFFFF)

    def write32(self, addr, val):
        struct.pack_into('<I', self.data, addr & 0xFFFF, val & 0xFFFFFFFF)


class Memory:
    """
    Unified memory interface that routes reads/writes to registered blocks.

    Peripheral blocks are checked first (in registration order).
    The FlatRAM is the fallback for any address not claimed by a peripheral.
    """

    def __init__(self, ram: FlatRAM):
        self._ram = ram
        self._peripherals: list[MemoryBlock] = []

    def add_block(self, block: MemoryBlock):
        """Register a peripheral memory block."""
        self._peripherals.append(block)

    def _find(self, addr) -> MemoryBlock:
        for block in self._peripherals:
            if block.handles(addr):
                return block
        return self._ram

    def read8(self, addr):            return self._find(addr).read8(addr)
    def read16(self, addr):           return self._find(addr).read16(addr)
    def read32(self, addr):           return self._find(addr).read32(addr)
    def write8(self, addr, val):      self._find(addr).write8(addr, val)
    def write16(self, addr, val):     self._find(addr).write16(addr, val)
    def write32(self, addr, val):     self._find(addr).write32(addr, val)
