"""
Behaviour at the edges, where the simulator must act like the real Pico
(or at least never misbehave silently):

1. Cortex-M0+ faults: unaligned accesses, BX/BLX/POP to an address with
   bit 0 clear, undefined and non-ARMv6-M encodings, unmapped addresses.
   After a fault the PC points back at the instruction that caused it.
2. The other ARMv6-M instructions: hints, CPS, barriers, MRS / MSR.
3. Memory safety: accesses near the top of RAM never touch the registers.
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
from picosim.cpu import SimulatorError
from test_server import start

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = ".syntax unified\n.cpu cortex-m0plus\n.thumb\nmain:\n"


def core_with(hws, regs=None):
    c = CPUCore()
    a = 0x3000
    for h in hws:
        c.write16(a, h)
        a += 2
    for r, v in (regs or {}).items():
        c.set_reg(r, v)
    c.sp = 0x8000
    c.pc = 0x3000
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
    CASES = [
        # (what, encodings, registers, words the message must contain)
        ("unaligned word store", [0x6001], {0: 0x4002}, "Unaligned"),
        ("unaligned halfword load", [0x8801], {0: 0x4001}, "Unaligned"),
        ("unaligned word load at the top of RAM", [0x6801], {0: 0xFFFE}, "Unaligned"),
        ("BX to an even address", [0x4700], {0: 0x3010}, "bit 0 clear"),
        ("BLX to an even address", [0x4780], {0: 0x3010}, "bit 0 clear"),
        ("UDF", [0xDE00], {}, "Undefined instruction 0xDE00 at 0x3000"),
        ("IT (Thumb-2 only)", [0xBF08], {}, "Undefined"),
        ("0xB8xx is not ADD SP", [0xB802], {}, "Undefined"),
        ("REV with op 2", [0xBA81], {}, "Undefined"),
        ("CBZ (Thumb-2 only)", [0xB110], {}, "Undefined"),
        ("MOVW (Thumb-2 only)", [0xF240, 0x0005], {}, "Undefined instruction 0xF2400005"),
    ]

    def test_faults(self):
        for what, hws, regs, words in self.CASES:
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
                    self.assertEqual(c.pc, 0x3000, "the PC points at the faulting instruction")
                    self.assertFalse(c.in_insn)

    def test_pop_pc_even(self):
        c = core_with([0xBD00])            # POP {PC}
        c.write32(0x8000, 0x3010)          # bit 0 clear
        with self.assertRaisesRegex(RuntimeError, "POP {PC}.*bit 0 clear"):
            c.step()

    def test_not_strict(self):
        """Scratch cores (cycle tables, timing) may turn the checks off."""
        c = core_with([0x4700], {0: 0x3010})
        c.strict = False
        c.step()
        self.assertEqual(c.pc, 0x3010)

    def test_bus_fault(self):
        for body, words in [("ldr r1, =0x40054028\nldr r0, [r1]\nbx lr", "reads from 0x40054028"),
                            ("ldr r1, =0x20000000\nstr r0, [r1]\nbx lr", "Pico's SRAM"),
                            ("ldr r0, =0x20000001\nbx r0", "jumped to 0x20000000")]:
            with self.subTest(body=body.splitlines()[0]), ProgramFile(body) as path:
                cpu = load_program(path).cpu
                with self.assertRaises(SimulatorError) as cm:
                    cpu._core.run(1000)
                self.assertIn("Bus fault", str(cm.exception))
                self.assertIn(words, str(cm.exception))

    def test_error_messages_are_hex(self):
        c = core_with([0xDE00])
        with self.assertRaisesRegex(RuntimeError, "at 0x3000"):
            c.step()


class SystemInstructionTest(unittest.TestCase):
    def test_hints_and_barriers(self):
        for hws in ([0xBF00], [0xBF10], [0xBF20], [0xBF30], [0xBF40], [0xF3BF, 0x8F4F],
                    [0xF3BF, 0x8F5F], [0xF3BF, 0x8F6F]):
            for cycle in (False, True):
                with self.subTest(hw=hex(hws[0]), cycle=cycle):
                    c = run_one(hws, {0: 7}, cycle)
                    self.assertEqual(c.pc, 0x3000 + 2 * len(hws))
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
            c = run_one([0xF381, 0x8808], {1: 0x4003}, cycle)       # MSR MSP, R1
            self.assertEqual(c.sp, 0x4000)

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
    def test_top_of_ram_wraps(self):
        c = CPUCore()
        c.set_reg(0, 0x12345678)
        c.write32(0xFFFE, 0xAABBCCDD)
        self.assertEqual(c.regs[0], 0x12345678)
        self.assertEqual(c.read32(0xFFFE), 0xAABBCCDD)
        self.assertEqual(c.read16(0x0000), 0xAABB)

    def test_slice_bounds(self):
        c = CPUCore()
        for addr, n in [(0x3000, 0xFFFFFFFF), (0xFFFF, 2), (0x10000, 1)]:
            with self.assertRaises(RuntimeError):
                c.get_mem_slice(addr, n)
        self.assertEqual(len(c.get_mem_slice(0xFFFF, 1)), 1)


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
        self.assertEqual(asm[0x3004].split()[0], '.word')                 # the literal pool
        self.assertIn('0x12345678', asm[0x3004])
        self.assertEqual(asm[labels['msg']], '.ascii   "Hello\\n\\0"')       # 7 bytes
        self.assertEqual(labels['odd'] % 2, 1)                             # exact, not rounded down
        self.assertEqual(asm[labels['odd']], '.ascii   "GP\\0"')
        self.assertEqual(asm[labels['nums'] + 4], '.word    0xDEADBEEF')
        self.assertTrue(asm[0x3000].startswith('ldr'))

    def test_os_literal_pool(self):
        from picosim.sim import load_elf
        from picosim.loader import build_os_elf
        _, _, asm, _ = load_elf(build_os_elf())
        self.assertEqual(asm[0x000C], '.word    0x00002FFC')


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
        with ProgramFile("movs r0, #1\nldr r1, =0x3000\nmovs r2, #0\nsubs r2, #1\nsvc #0\nbx lr") as path:
            c = start(path)
            c.until(lambda m: m['type'] == 'state' and m.get('loaded'))
            c.s.sendall(b'[1, 2]\n"text"\nnot json\n{"cmd": "nosuch"}\n')
            c.send(cmd='run', cps=0)
            m, _ = c.until(lambda m: m['type'] == 'error' and 'past the end of RAM' in m['message'])
            c.send(cmd='state')
            c.until(lambda m: m['type'] == 'state')

    def test_listing_raw_for_data(self):
        with ProgramFile("bx lr\n.align 2\n.word 0xCAFEF00D") as path:
            c = start(path)
            m, _ = c.until(lambda m: m['type'] == 'program')
        rows = {r['addr']: r for r in m['listing']}
        self.assertEqual(rows[0x3004]['raw'], 'CAFEF00D')


class InteractivePromptTest(unittest.TestCase):
    def test_bad_input(self):
        cmds = "s abc\nx zz\nx 0x20000000\nb nolabel\nreg r0 xyz\nreg r99 1\ngpio 99 1\n" \
               "b loop\nr\nx 0xD0000004 1\nfoo\nq\n"
        r = subprocess.run([sys.executable, '-m', 'picosim.sim',
                            os.path.join(ROOT, 'examples', '03_count_loop.s')],
                           input=cmds, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('Traceback', r.stdout + r.stderr)
        self.assertIn("Breakpoint hit at 0x3004", r.stdout)
        self.assertIn("Nothing is at address 0x20000000", r.stdout)
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
