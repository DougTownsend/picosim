"""
Behaviour at the edges, where the simulator must act like the real Pico
(or at least never misbehave silently):

1. Cortex-M0+ faults: unaligned accesses, BX/BLX/POP to an address with
   bit 0 clear, undefined and non-ARMv6-M encodings, unmapped addresses.
   After a fault the PC points back at the instruction that caused it.
2. The other ARMv6-M instructions: hints, CPS, barriers, MRS / MSR.
3. Memory: the RP2040's map (ROM, flash, SRAM and SRAM0-3's non-striped
   view); stores into ROM or flash stop the program; accesses at the end of
   SRAM never touch the registers.
4. GPIO: a pin follows SIO only when its FUNCSEL is 5 (SIO).
5. The ELF loader shows data in code as data, with exact label addresses.
6. The GUI backend keeps running whatever a program or a client does.
7. The interactive prompt survives bad input.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from picosim._picosim_core import CPUCore
from picosim.loader import load_program
from picosim.cpu import (SimulatorError, stop_reason, unmapped_reason, HARDWARE_FAULT, MODEL_LIMIT,
                         TEACHING_STOP)
from picosim.memmap import USER_BASE as CODE, SRAM_BASE, SRAM_END
from test_server import start

STACK = SRAM_BASE + 0x8000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = ".syntax unified\n.cpu cortex-m0plus\n.thumb\nmain:\n"


def core_with(hws, regs=None):
    """A bare core with `hws` at CODE (flash) and SP in SRAM."""
    c = CPUCore()
    a = CODE
    for h in hws:
        c.write16(a, h)
        a += 2
    for r, v in (regs or {}).items():
        c.set_reg(r, v)
    c.sp = STACK
    c.pc = CODE
    return c


def run_one(hws, regs=None, cycle=False):
    """Execute one instruction (atomically or clock by clock); returns the core."""
    c = core_with(hws, regs)
    if cycle:
        while not c.step_cycle()['last']:
            pass
    else:
        c.step()
    return c


class ProgramFile:
    def __init__(self, body):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'prog.s')
        with open(self.path, 'w') as f:
            f.write(HEADER + body + "\n")

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        self.dir.cleanup()


class FaultTest(unittest.TestCase):
    HF, TS = HARDWARE_FAULT, TEACHING_STOP
    CASES = [
        # (what, encodings, registers, words the message must contain, stop reason)
        ("unaligned word store", [0x6001], {0: SRAM_BASE + 0x4002}, "Unaligned", HF),
        ("unaligned halfword load", [0x8801], {0: SRAM_BASE + 0x4001}, "Unaligned", HF),
        ("unaligned word load at the top of SRAM", [0x6801], {0: SRAM_END - 2}, "Unaligned", HF),
        ("unaligned word load, non-striped SRAM", [0x6801], {0: 0x21010002}, "Unaligned", HF),
        ("BX PC (faults at the instruction, as on the chip)", [0x4778], {}, "BX PC at 0x10000100", HF),
        ("BLX PC (faults at the instruction, as on the chip)", [0x47F8], {}, "BLX PC at 0x10000100", HF),
        ("UDF", [0xDE00], {}, "Undefined instruction 0xDE00 at 0x10000100", HF),
        ("store into flash", [0x6001], {0: CODE + 0x100}, "Write to read-only memory", TS),
        ("store into ROM", [0x7001], {0: 0x2FFC}, "Write to read-only memory", TS),
        ("IT (Thumb-2 only)", [0xBF08], {}, "Undefined", HF),
        ("0xB8xx is not ADD SP", [0xB802], {}, "Undefined", HF),
        ("REV with op 2", [0xBA81], {}, "Undefined", HF),
        ("CBZ (Thumb-2 only)", [0xB110], {}, "Undefined", HF),
        ("MOVW (Thumb-2 only)", [0xF240, 0x0005], {}, "Undefined instruction 0xF2400005", HF),
    ]

    def test_faults(self):
        for what, hws, regs, words, reason in self.CASES:
            for cycle in (False, True):
                with self.subTest(case=what, cycle=cycle):
                    c = core_with(hws, regs)
                    with self.assertRaises(RuntimeError) as cm:
                        if cycle:
                            while not c.step_cycle()['last']:
                                pass
                        else:
                            c.step()
                    self.assertIn(words, str(cm.exception))
                    self.assertEqual(stop_reason(cm.exception), reason)
                    self.assertEqual(c.pc, CODE, "the PC points at the faulting instruction")
                    self.assertFalse(c.in_insn)

    def stops_at_target(self, c, how, cycle):
        """A branch to an even address: as on the RP2040 (B4) the branch
        completes and the HardFault (INVSTATE) comes at the target, so the
        first step lands there and the next one stops there, naming the branch.
        Stepping again stops again."""
        target = CODE + 0x10
        step = (lambda: [None for _ in iter(lambda: c.step_cycle()['last'], True)]) if cycle else c.step
        step()
        self.assertEqual(c.pc, target)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, how + ".*bit 0 clear") as cm:
                step()
            self.assertEqual(c.pc, target, "picosim stops at the target, where the Pico faults")
            self.assertFalse(c.in_insn)
        self.assertIn("at 0x10000100", str(cm.exception), "the message names the branch")

    def test_bx_blx_even(self):
        for hw, how in ((0x4700, "BX R0"), (0x4780, "BLX R0")):
            for cycle in (False, True):
                with self.subTest(how=how, cycle=cycle):
                    c = core_with([hw], {0: CODE + 0x10})
                    self.stops_at_target(c, how, cycle)
                    if hw == 0x4780:
                        self.assertEqual(c.lr, CODE + 2 | 1, "BLX sets LR, as on the chip")

    def test_pop_pc_even(self):
        for cycle in (False, True):
            with self.subTest(cycle=cycle):
                c = core_with([0xBD00])            # POP {PC}
                c.write32(STACK, CODE + 0x10)      # bit 0 clear
                self.stops_at_target(c, "POP {PC}", cycle)
                self.assertEqual(c.sp, STACK + 4)

    def test_moving_the_pc_away_drops_the_stop(self):
        c = core_with([0x4700], {0: CODE + 0x10})
        c.step()
        c.write16(CODE + 0x20, 0xBF00)
        c.pc = CODE + 0x20                 # e.g. the user moved the PC
        c.step()
        self.assertEqual(c.pc, CODE + 0x22)

    def test_not_strict(self):
        """Scratch cores (cycle tables, timing) may turn the checks off."""
        c = core_with([0x4700], {0: CODE + 0x10})
        c.strict = False
        c.step()
        self.assertEqual(c.pc, CODE + 0x10)

    def test_bus_fault(self):
        """Where the Pico has nothing too: a bus fault, a HardFault on the Pico."""
        for body, words in [("ldr r1, =0x21040000\nstr r0, [r1]\nbx lr", "writes to 0x21040000"),
                            ("ldr r1, =0x20042000\nldr r0, [r1]\nbx lr", "reads from 0x20042000"),
                            ("ldr r1, =0x15000000\nldr r0, [r1]\nbx lr", "XIP cache's own memory"),
                            ("ldr r0, =0x30000001\nbx r0", "jumped to 0x30000000")]:
            with self.subTest(body=body.splitlines()[0]), ProgramFile(body) as path:
                cpu = load_program(path).cpu
                with self.assertRaises(SimulatorError) as cm:
                    cpu._core.run(1000)
                msg = str(cm.exception)
                self.assertIn("Bus fault", msg)
                self.assertIn(words, msg)
                self.assertIn("HardFault too", msg)
                self.assertEqual(stop_reason(cm.exception), HARDWARE_FAULT)

    def test_not_simulated(self):
        """Where the Pico has something picosim does not simulate: the stop
        says it is picosim's limit, and never that the Pico would fault."""
        for body, words in [("ldr r1, =0x40054028\nldr r0, [r1]\nbx lr", "reads from 0x40054028"),
                            ("ldr r1, =0x13000000\nldr r0, [r1]\nbx lr", "XIP cache"),
                            ("ldr r1, =0xE000E010\nldr r0, [r1]\nbx lr", "SysTick"),
                            ("ldr r1, =0xD0000060\nldr r0, [r1]\nbx lr", "divider")]:
            with self.subTest(body=body.splitlines()[0]), ProgramFile(body) as path:
                cpu = load_program(path).cpu
                with self.assertRaises(SimulatorError) as cm:
                    cpu._core.run(1000)
                msg = str(cm.exception)
                self.assertIn("Not simulated", msg)
                self.assertIn("limit of picosim", msg)
                self.assertIn(words, msg)
                self.assertNotIn("HardFault", msg)
                self.assertEqual(stop_reason(cm.exception), MODEL_LIMIT)
                with self.assertRaises(SimulatorError) as cm:      # the Python step path too
                    cpu.step()
                self.assertEqual(cm.exception.reason, MODEL_LIMIT)

    def test_unmapped_reasons_follow_the_chip(self):
        """Every address the chip was probed at (hwfacts/memory_map.json): a
        fault on the chip is HARDWARE_FAULT here; an access that worked on the
        chip is never called a HardFault."""
        import json
        from picosim import memmap
        with open(os.path.join(ROOT, 'hwfacts', 'memory_map.json')) as f:
            probes = json.load(f)['probes']
        checked = 0
        for a, p in probes.items():
            a = int(a, 16)
            ops = (p.get('core') or {}).values()
            if not ops or memmap.is_memory(a) or 0xD0000000 <= a < 0xD0000040:
                continue
            faulted = all(not o['ok'] for o in ops)
            worked = all(o['ok'] for o in ops)
            reason = unmapped_reason(a)[0]
            with self.subTest(addr=hex(a)):
                if faulted:
                    self.assertEqual(reason, HARDWARE_FAULT)
                elif worked and not (0x40000000 <= a < 0x40020000 and memmap.kind(a) == 'io'
                                     and self._gpio_has(a)):
                    self.assertEqual(reason, MODEL_LIMIT)
            checked += 1
        self.assertGreater(checked, 200)

    @staticmethod
    def _gpio_has(a):
        from picosim.gpio import GPIO
        return GPIO().handles(a)

    def test_error_messages_are_hex(self):
        c = core_with([0xDE00])
        with self.assertRaisesRegex(RuntimeError, "at 0x10000100"):
            c.step()


class SystemInstructionTest(unittest.TestCase):
    def test_hints_and_barriers(self):
        for hws in ([0xBF00], [0xBF10], [0xBF20], [0xBF30], [0xBF40], [0xF3BF, 0x8F4F],
                    [0xF3BF, 0x8F5F], [0xF3BF, 0x8F6F]):
            for cycle in (False, True):
                with self.subTest(hw=hex(hws[0]), cycle=cycle):
                    c = run_one(hws, {0: 7}, cycle)
                    self.assertEqual(c.pc, CODE + 2 * len(hws))
                    self.assertEqual(c.regs[0], 7)

    def test_cps(self):
        c = run_one([0xB672])                  # CPSID i
        self.assertEqual(c.PRIMASK, 1)
        c = core_with([0xB662])                # CPSIE i
        c.PRIMASK = 1
        c.step()
        self.assertEqual(c.PRIMASK, 0)

    def test_mrs_msr(self):
        for cycle in (False, True):
            c = core_with([0xF3EF, 0x8000])    # MRS R0, APSR
            c.N, c.Z, c.C, c.V = 1, 0, 1, 1
            if cycle:
                while not c.step_cycle()['last']:
                    pass
            else:
                c.step()
            self.assertEqual(c.regs[0], 0xB0000000)
            c = run_one([0xF381, 0x8800], {1: 0x50000000}, cycle)   # MSR APSR, R1
            self.assertEqual((c.N, c.Z, c.C, c.V), (0, 1, 0, 1))
            c = run_one([0xF381, 0x8808], {1: SRAM_BASE + 0x4003}, cycle)   # MSR MSP, R1
            self.assertEqual(c.sp, SRAM_BASE + 0x4000)

    def test_assembled(self):
        """The real assembler's encodings run (NOP is MOV R8, R8 for M0+)."""
        body = "nop\nwfi\nwfe\nsev\nyield\ncpsid i\ncpsie i\ndmb\ndsb\nisb\n" \
               "movs r1, #0\ncmp r1, #0\nmrs r0, apsr\nmsr apsr, r0\nmrs r2, primask\nbx lr"
        with ProgramFile(body) as path:
            cpu = load_program(path).cpu
            cpu._core.run(1000)
            self.assertTrue(cpu.halted)
            self.assertEqual(cpu.regs[0] >> 28, 0b0110)            # Z and C after CMP 0, 0


class MemorySafetyTest(unittest.TestCase):
    def test_end_of_sram_does_not_wrap(self):
        """A debugger write running past the end of SRAM keeps the bytes
        inside it and drops the rest: nothing wraps round, no register changes."""
        c = CPUCore()
        c.set_reg(0, 0x12345678)
        c.write32(SRAM_END - 2, 0xAABBCCDD)
        self.assertEqual(c.regs[0], 0x12345678)
        self.assertEqual(c.read16(SRAM_END - 2), 0xCCDD)
        self.assertEqual(c.read16(SRAM_BASE), 0)

    def test_slice_bounds(self):
        c = CPUCore()
        for addr, n in [(CODE, 0xFFFFFFFF), (SRAM_END - 1, 2), (SRAM_END, 1), (0x4000, 1),
                        (0x3FFF, 2), (0x40014000, 4)]:
            with self.assertRaises(RuntimeError):
                c.get_mem_slice(addr, n)
        self.assertEqual(len(c.get_mem_slice(SRAM_END - 1, 1)), 1)
        self.assertEqual(c.get_mem_slice(CODE + 0x1000, 4), b'\xff' * 4)    # erased flash
        with self.assertRaises(RuntimeError):
            c.set_mem_byte(0x40014000, 1)

    def test_memory_map(self):
        from picosim import memmap
        self.assertEqual([(n, hex(a), hex(b)) for n, a, b, _ in memmap.REGIONS],
                         [('ROM', '0x0', '0x4000'), ('Flash', '0x10000000', '0x10200000'),
                          ('SRAM', '0x20000000', '0x20042000')])
        self.assertEqual(memmap.STACK_TOP, 0x20042000)
        for a, k in [(0, 'rom'), (0x3FFF, 'rom'), (0x4000, 'io'), (0x10000000, 'flash'),
                     (0x101FFFFF, 'flash'), (0x10200000, 'io'), (0x20041FFF, 'sram'),
                     (0x20042000, 'io'), (0x21000000, 'sram'), (0x2103FFFF, 'sram'),
                     (0x21040000, 'io'), (0x20FFFFFF, 'io'), (0xD0000000, 'io')]:
            self.assertEqual(memmap.kind(a), k, hex(a))
        for a in (0x10000000, 0x10000100, 0x1000FFFE):
            self.assertEqual(memmap.slot_addr(memmap.code_slot(a)), a)
        for a in (0, 0x10010000, SRAM_BASE):
            self.assertIsNone(memmap.code_slot(a))
        self.assertEqual((memmap.OS_BASE, memmap.OS_END, memmap.USER_BASE, memmap.MAIN_PTR),
                         (0x10000000, 0x10000100, 0x10000100, 0x100000FC))

    def test_program_layout(self):
        """The OS in flash's first 256 bytes, then the program's code and
        constants; variables in SRAM; SP at the top of SRAM; no veneers (a BL
        from the program reaches putchar directly)."""
        body = ("ldr r1, =count\nldr r0, [r1]\nadds r0, #1\nstr r0, [r1]\n"
                "ldr r1, =buf\nstr r0, [r1]\nbx lr\n"
                ".section .rodata\ntable: .word 7\n.data\ncount: .word 41\n.bss\nbuf: .space 8")
        with ProgramFile(body) as path:
            prog = load_program(path)
        labels = {v: k for k, v in prog.sym_map.items()}
        self.assertEqual(prog.main_addr, CODE)
        self.assertFalse(any('veneer' in n for n in prog.sym_map.values()))
        self.assertTrue(all(0x10000000 <= labels[n] < CODE for n in ('_start', 'putchar', 'getchar')))
        self.assertEqual(labels['count'], SRAM_BASE)
        self.assertEqual(labels['buf'], SRAM_BASE + 4)
        self.assertTrue(CODE < labels['table'] < SRAM_BASE)
        self.assertEqual(set(prog.sections), {'text', 'data', 'bss'})
        c = prog.cpu._core
        self.assertEqual(c.sp, 0x20042000)
        c.run(1000)
        self.assertTrue(c.halted)
        self.assertEqual(c.read32(SRAM_BASE), 42)
        self.assertEqual(c.read32(SRAM_BASE + 4), 42)

    def test_store_into_code_stops_with_a_clear_message(self):
        body = "ldr r1, =total\nmovs r0, #5\nstr r0, [r1]\nbx lr\n.align 2\ntotal: .word 0"
        with ProgramFile(body) as path:
            cpu = load_program(path).cpu
            with self.assertRaises(RuntimeError) as cm:     # the core's own faults are RuntimeErrors
                cpu._core.run(1000)
        msg = str(cm.exception)
        for words in ("Write to read-only memory", "flash", ".data", "0x20000000"):
            self.assertIn(words, msg)
        self.assertEqual(cpu.pc, CODE + 4, "the PC points at the store")


class NonStripedSramTest(unittest.TestCase):
    """SRAM0-3 at 0x21000000 + bank * 0x10000, the same memory as the striped
    0x20000000-0x2003FFFF (RP2040 datasheet 2.6.2; measured on a Pico:
    hwfacts/memory_map.json "sram")."""

    @staticmethod
    def striped(bank, word):
        return SRAM_BASE + 16 * word + 4 * bank

    @staticmethod
    def flat(bank, word):
        return 0x21000000 + 0x10000 * bank + 4 * word

    def test_round_trip_every_bank(self):
        import random
        rng = random.Random(2040)
        c = CPUCore()
        for bank in range(4):
            for word in (0, 1, 0x1234, 0x3FFF) + tuple(rng.randrange(0x4000) for _ in range(200)):
                s, f = self.striped(bank, word), self.flat(bank, word)
                v = rng.getrandbits(32)
                c.write32(f, v)
                self.assertEqual(c.read32(s), v)
                c.write32(s, v ^ 0xFFFFFFFF)
                self.assertEqual(c.read32(f), v ^ 0xFFFFFFFF)
                c.write16(f + 2, 0xBEEF)
                self.assertEqual(c.read16(s + 2), 0xBEEF)
                c.write8(s + 1, 0x5A)
                self.assertEqual(c.read8(f + 1), 0x5A)

    def test_measured_sample(self):
        """The chip's own sample: the striped word at A is bank (A >> 2) & 3,
        word (A - 0x20000000) >> 4."""
        import json
        from picosim import memmap
        with open(os.path.join(ROOT, 'hwfacts', 'memory_map.json')) as f:
            sram = json.load(f)['sram']
        self.assertTrue(sram['confirmed'])
        c = CPUCore()
        for e in sram['sample']:
            a = int(e['striped'], 16)
            f = self.flat(e['bank'], e['word'])
            self.assertEqual(memmap.ns_to_striped(f), a)
            self.assertEqual(memmap.striped_to_ns(a), f)
            c.write32(a, 0xB0000000 | a & 0xFFFF)
            self.assertEqual(c.read32(f), 0xB0000000 | a & 0xFFFF)
        for a in sram['past']:          # past the end of SRAM (0x21040000 among them): not memory
            self.assertEqual(memmap.kind(int(a, 16)), 'io')

    def test_program_uses_both_views(self):
        """A program stores through the non-striped view and loads through
        the striped one (and back), runs code there, and faults like the chip
        on an unaligned access or past SRAM3."""
        body = ("ldr r1, =0x21030010\nldr r0, =0x12345678\nstr r0, [r1]\n"   # bank 3, word 4
                "ldr r2, =0x2000004C\nldr r3, [r2]\n"                         # striped 16 * 4 + 12
                "movs r0, #0x77\nstrb r0, [r2, #1]\nldrh r4, [r1]\n"
                "bx lr")
        with ProgramFile(body) as path:
            cpu = load_program(path).cpu
            cpu._core.run(1000)
            self.assertTrue(cpu.halted)
            self.assertEqual(cpu.regs[3], 0x12345678)
            self.assertEqual(cpu.regs[4], 0x7778)
        c = core_with([0x6808], {1: 0x21020002})               # LDR R0, [R1]: unaligned
        with self.assertRaisesRegex(RuntimeError, "Unaligned") as cm:
            c.step()
        self.assertEqual(stop_reason(cm.exception), HARDWARE_FAULT)
        # code at a non-striped address: MOVS R0, #42; BX LR with LR = halt
        c = CPUCore()
        c.write16(0x21000100, 0x202A)
        c.write16(0x21000102, 0x4770)
        self.assertEqual(c.read16(self.striped(0, 0x40)), 0x202A)
        c.set_reg(14, 0xFFFFFFFF)
        c.pc = 0x21000100
        c.run(10)
        self.assertTrue(c.halted)
        self.assertEqual(c.regs[0], 42)

    def test_ranges(self):
        from picosim import memmap
        c = CPUCore()
        self.assertEqual(memmap.span(0x21000000, 0x40000), (0x21000000, 0x21040000))
        self.assertIsNone(memmap.span(0x2103FFFE, 4))
        for addr, n in [(0x2103FFFE, 4), (0x21040000, 1), (0x20FFFFFF, 2)]:
            with self.assertRaises(RuntimeError):
                c.get_mem_slice(addr, n)
        c.load_memory(bytes(range(16)), 0x2100FFF8)              # across SRAM0 into SRAM1
        self.assertEqual(c.get_mem_slice(0x2100FFF8, 16), bytes(range(16)))
        self.assertEqual(c.read32(self.striped(1, 0)), 0x0B0A0908)
        img = memmap.Image()
        img.write(0x2100FFF9, bytes(range(1, 40)))
        self.assertEqual(img.read(0x2100FFF9, 39), bytes(range(1, 40)))
        self.assertEqual(img.read(self.striped(1, 1), 4), bytes([12, 13, 14, 15]))
        img.load_into(c)
        self.assertEqual(c.get_mem_slice(0x2100FFF9, 39), bytes(range(1, 40)))


class GpioFuncselTest(unittest.TestCase):
    PIN15 = ("ldr r0, =0xD0000024\nldr r1, =1 << 15\nstr r1, [r0]\n"
             "ldr r0, =0xD0000014\nstr r1, [r0]\nbkpt")

    def pin15(self, body):
        with ProgramFile(body) as path:
            cpu = load_program(path).cpu
            cpu._core.run(1000)
            return cpu.gpio.pin_states()[15]

    def test_needs_funcsel_sio(self):
        p = self.pin15(self.PIN15)
        self.assertEqual((p['dir'], p['level']), ('in', None))
        self.assertTrue(p['sio_unrouted'])
        p = self.pin15("ldr r0, =0x4001407C\nmovs r1, #5\nstr r1, [r0]\n" + self.PIN15)
        self.assertEqual((p['dir'], p['level']), ('out', 1))
        self.assertFalse(p['sio_unrouted'])

    def test_overrides_work_without_sio(self):
        # OEOVER = OUTOVER = force high (what test_asm_files/gpio.s does)
        p = self.pin15("ldr r0, =0x4001407C\nldr r1, =0x331F\nstr r1, [r0]\nbkpt")
        self.assertEqual((p['dir'], p['level']), ('out', 1))

    def test_banks_out_of_reset(self):
        """As the Pico SDK leaves them: polling RESET_DONE for IO_BANK0 ends."""
        p = self.pin15("ldr r0, =0x4000C008\nldr r2, =(1 << 5) | (1 << 8)\n"
                       "1: ldr r1, [r0]\nands r1, r2\ncmp r1, r2\nbne 1b\nbkpt")
        self.assertEqual(p['pin'], 15)


class DisassemblyTest(unittest.TestCase):
    def test_data_in_code(self):
        body = ("ldr r0, =0x12345678\nbx lr\n.ltorg\n"
                "msg: .asciz \"Hello\\n\"\nodd: .asciz \"GP\"\n.align 2\nnums: .word 1, 0xDEADBEEF")
        with ProgramFile(body) as path:
            prog = load_program(path)
        asm, syms = prog.asm_map, prog.sym_map
        labels = {v: k for k, v in syms.items()}
        self.assertEqual(asm[CODE + 4].split()[0], '.word')               # the literal pool
        self.assertIn('0x12345678', asm[CODE + 4])
        self.assertEqual(asm[labels['msg']], '.ascii   "Hello\\n\\0"')       # 7 bytes
        self.assertEqual(labels['odd'] % 2, 1)                             # exact, not rounded down
        self.assertEqual(asm[labels['odd']], '.ascii   "GP\\0"')
        self.assertEqual(asm[labels['nums'] + 4], '.word    0xDEADBEEF')
        self.assertTrue(asm[CODE].startswith('ldr'))

    def test_os_literal_pool(self):
        from picosim.sim import load_elf
        from picosim.loader import build_os_elf
        _, _, asm, _ = load_elf(build_os_elf())
        self.assertEqual(asm[0x1000000C], '.word    0x100000FC')


class ServerRobustnessTest(unittest.TestCase):
    def test_bad_svc_at_full_speed(self):
        with ProgramFile("svc #9\nbx lr") as path:
            c = start(path)
            c.until(lambda m: m['type'] == 'state' and m.get('loaded'))
            c.send(cmd='run', cps=0)
            m, _ = c.until(lambda m: m['type'] == 'error')
            self.assertIn('Unknown SVC #9', m['message'])
            st, _ = c.until(lambda m: m['type'] == 'state' and not m['running'])
            self.assertIsNotNone(st['fault'])
            self.assertEqual(st['pc'], st['insn_addr'])
            c.send(cmd='reload')          # still alive
            st, _ = c.until(lambda m: m['type'] == 'state' and m['fault'] is None)
            self.assertFalse(st['halted'])

    def test_huge_write_and_bad_json(self):
        with ProgramFile("movs r0, #1\nldr r1, =0x10000000\nmovs r2, #0\nsubs r2, #1\nsvc #0\nbx lr") as path:
            c = start(path)
            c.until(lambda m: m['type'] == 'state' and m.get('loaded'))
            c.s.sendall(b'[1, 2]\n"text"\nnot json\n{"cmd": "nosuch"}\n')
            c.send(cmd='run', cps=0)
            m, _ = c.until(lambda m: m['type'] == 'error' and 'not all inside' in m['message'])
            c.send(cmd='state')
            c.until(lambda m: m['type'] == 'state')

    def test_listing_raw_for_data(self):
        with ProgramFile("bx lr\n.align 2\n.word 0xCAFEF00D") as path:
            c = start(path)
            m, _ = c.until(lambda m: m['type'] == 'program')
        rows = {r['addr']: r for r in m['listing']}
        self.assertEqual(rows[CODE + 4]['raw'], 'CAFEF00D')


class InteractivePromptTest(unittest.TestCase):
    def test_bad_input(self):
        cmds = "s abc\nx zz\nx 0x30000000\nb nolabel\nreg r0 xyz\nreg r99 1\ngpio 99 1\n" \
               "b loop\nr\nx 0xD0000004 1\nfoo\nq\n"
        r = subprocess.run([sys.executable, '-m', 'picosim.sim',
                            os.path.join(ROOT, 'examples', '03_count_loop.s')],
                           input=cmds, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('Traceback', r.stdout + r.stderr)
        self.assertIn("Breakpoint hit at 0x10000104", r.stdout)
        self.assertIn("Nothing is at address 0x30000000", r.stdout)
        self.assertIn("0xD0000004: 0x00000000", r.stdout)


if __name__ == '__main__':
    unittest.main()


class StateDocsTest(unittest.TestCase):
    """The guide's "FSM states" page (gui/scripts/state_info.gd) documents
    exactly the states the core uses, with the right sequences."""

    def test_every_state_documented(self):
        import re
        core = open(os.path.join(ROOT, 'picosim', 'picosim_core.cpp')).read()
        used = set(re.findall(r'add\("([A-Z0-9_]+)"', core))
        info = open(os.path.join(ROOT, 'gui', 'scripts', 'state_info.gd')).read()
        order = re.search(r'const ORDER := \[(.*?)\]', info, re.S).group(1)
        documented = set(re.findall(r'"([A-Z0-9_]+)"', order))
        self.assertEqual(used, documented)
        for st in used:
            self.assertIn(f'\t"{st}": {{', info, f"{st} has no entry in STATES")

    def test_sequences_match_core(self):
        from picosim.cycles import trace, _compress
        import re
        info = open(os.path.join(ROOT, 'gui', 'scripts', 'state_info.gd')).read()
        rows = dict(re.findall(r'\["([^"]+)", "([^"]+)", \d', info))
        checks = {   # encoding -> the SEQUENCES row that describes it
            (0x1842, 0): "Operate", (0x6842, 0): "Load", (0x6042, 0): "Store",
            (0xA001, 0): "ADR", (0xE000, 0): "B, B<cond>", (0x4770, 0): "BX",
            (0x4798, 0): "BLX", (0xF000, 0xF800): "BL (32-bit)", (0xDF02, 0): "SVC",
            (0xBE00, 0): "BKPT", (0xBF00, 0): "NOP", (0xF3BF, 0x8F5F): "DMB",
            (0xF3EF, 0x8000): "MRS"}
        for (hw1, hw2), prefix in checks.items():
            with self.subTest(row=prefix):
                st = trace(hw1, hw2)
                after = ' → '.join(st[3:])
                row = next(v for k, v in rows.items() if k.startswith(prefix))
                self.assertEqual(row, after)
