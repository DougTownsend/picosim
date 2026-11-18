"""
The flash chip's 64-bit unique ID, read over SWD (no BOOTSEL, no picotool).

Why: flashguard ties every backup to the board's flash unique ID.  picotool
2.3.1 prints that ID ('flash id' in `picotool info -d`) only when it can
work out the flash size, and it can't when the first 4 KB of the flash is
erased (2026-11-17: a target whose data starts at 0x1000 showed only
'type' and 'revision').  OpenOCD 0.12 has no command for the ID.  So the
core reads it itself, the way the Pico SDK's flash_get_unique_id() does:

    reset halt                        core 1 back in the boot ROM, off the flash
    connect_internal_flash()          ROM 'IF': QSPI pins to the SSI
    flash_exit_xip()                  ROM 'EX': SSI in 8-bit serial mode
    chip select low (IO_QSPI SS_CTRL OUTOVER = 2)
    send 0x4B + 4 dummy bytes + 8 bytes, keep the 13 bytes received
    chip select high (OUTOVER = 3)
    flash_flush_cache()               ROM 'FC'
    flash_enter_cmd_xip()             ROM 'CX': XIP reads work again
    BKPT

0x4B (Read Unique ID) only reads: nothing here can erase or program the
flash.  The ROM routines are the ones OpenOCD's own rp2040 flash driver and
the SDK call.  The ID is bytes 5..12 of the reply, written in that order
(as pico_get_unique_board_id_string() prints it).  It is read twice and
both reads must agree; all-0x00 or all-0xFF (no answer) is refused.

Debugger writes go only to SRAM (code, buffer, stack); the core's own
writes touch IO_QSPI's chip-select override and the SSI, as the SDK does.
"""

import struct

from . import safety

CODE_AT = 0x2003C000       # same scratch area as campaign F's snippets (free after reset halt)
BUF_AT = 0x2003E000
STACK_TOP = 0x2003F000
ROM_MAGIC = b'Mu\x01'      # RP2040 boot ROM: 'M', 'u', version 1, at 0x10
ROM_FUNCS = ('IF', 'EX', 'FC', 'CX')
ID_CMD_BYTES = 13          # 0x4B, 4 dummy, 8 ID

# Assembled from the source below with arm-none-eabi-as (tests check it still
# assembles to these bytes when the toolchain is installed).
SOURCE = r"""
        .syntax unified
        .cpu cortex-m0plus
        .thumb
@ in: r4 = connect_internal_flash, r5 = flash_exit_xip, r6 = flash_flush_cache,
@     r7 = flash_enter_cmd_xip (ROM addresses, Thumb bit set); r0 = result buffer;
@     sp = a stack in free SRAM.  out: 13 bytes at the buffer; r4 = 0 on timeout.
start:  mov     r8, r0
        blx     r4                  @ connect_internal_flash
        blx     r5                  @ flash_exit_xip (SSI: 8-bit frames, TX+RX)
        ldr     r0, =0x4001800C     @ IO_QSPI GPIO_QSPI_SS_CTRL
        ldr     r1, [r0]
        movs    r2, #3
        lsls    r2, r2, #8          @ OUTOVER field (bits 9:8)
        bics    r1, r2
        movs    r3, #2
        lsls    r3, r3, #8
        orrs    r1, r3              @ OUTOVER = 2: chip select driven low
        str     r1, [r0]
        ldr     r3, =0x18000000     @ SSI
        movs    r1, #0x4B           @ Read Unique ID
        str     r1, [r3, #0x60]     @ DR0
        movs    r1, #0
        movs    r2, #12             @ 4 dummy bytes + 8 ID bytes
1:      str     r1, [r3, #0x60]
        subs    r2, #1
        bne     1b
        mov     r0, r8
        movs    r2, #13
        ldr     r4, =200000         @ timeout, in polls
2:      subs    r4, #1
        beq     4f
        ldr     r1, [r3, #0x28]     @ SR
        lsrs    r1, r1, #4          @ RFNE (bit 3) -> carry
        bcc     2b
        ldr     r1, [r3, #0x60]
        strb    r1, [r0]
        adds    r0, #1
        subs    r2, #1
        bne     2b
4:      ldr     r0, =0x4001800C
        ldr     r1, [r0]
        movs    r2, #3
        lsls    r2, r2, #8
        orrs    r1, r2              @ OUTOVER = 3: chip select high
        str     r1, [r0]
        blx     r6                  @ flash_flush_cache
        blx     r7                  @ flash_enter_cmd_xip (XIP reads work again)
        bkpt    #0x4B
        .ltorg
"""
CODE = bytes.fromhex(
    '8046a047a8471348016803221202914302231b02194301600f4b4b21196600210c221966013afcd1'
    '40460d220b4c013c07d0996a0909fad3196e01700130013af5d1044801680322120211430160b047'
    'b8474bbe0c80014000000018400d0300')
BKPT_AT = 0x52             # offset of the final BKPT in CODE


class FlashIdError(Exception):
    pass


def rom_functions(read_mem):
    """{'IF': addr, ...} from the boot ROM's function table, read through
    read_mem(addr, n) -> bytes."""
    head = read_mem(0x10, 0x10)
    if head[:3] != ROM_MAGIC:
        raise FlashIdError(f"the boot ROM's header at 0x10 is {head[:3].hex()}, not the RP2040's "
                           f"{ROM_MAGIC.hex()}: this does not look like an RP2040")
    table = struct.unpack_from('<H', head, 4)[0]
    found = {}
    for _ in range(64):
        code, addr = struct.unpack('<HH', read_mem(table, 4))
        if code == 0:
            break
        found[chr(code & 0xFF) + chr(code >> 8)] = addr
        table += 4
    missing = [f for f in ROM_FUNCS if f not in found]
    if missing:
        raise FlashIdError(f"the boot ROM's function table has no {', '.join(missing)}")
    return {f: found[f] for f in ROM_FUNCS}


def id_from_reply(reply):
    """The ID (16 hex digits, upper case) from the 13 bytes received."""
    if len(reply) != ID_CMD_BYTES:
        raise FlashIdError(f"the flash answered {len(reply)} bytes, not {ID_CMD_BYTES}")
    uid = bytes(reply[5:13])
    if uid in (b'\x00' * 8, b'\xff' * 8):
        raise FlashIdError(f"the flash's answer was all 0x{uid[0]:02X}: no real ID came back")
    return uid.hex().upper()


def _read_once(target, funcs):
    r = target.rsp
    for a, n in ((CODE_AT, len(CODE)), (BUF_AT, 16), (STACK_TOP - 0x100, 0x100)):
        safety.check_write(a, 4, 0, why='flash unique ID')
    r.write_mem(CODE_AT, CODE)
    r.write_mem(BUF_AT, b'\xA5' * 16)                 # so stale bytes can't pass as an answer
    regs = {0: BUF_AT, 4: funcs['IF'], 5: funcs['EX'], 6: funcs['FC'], 7: funcs['CX'], 13: STACK_TOP}
    for n, v in regs.items():
        r.write_reg(n, v)
    r.write_reg(target.xpsr_n, 0x01000000)
    r.write_reg(15, CODE_AT)
    how = target.run(5.0)
    if how != 'halted':
        raise FlashIdError(f"the ID routine did not finish ({how})")
    out = r.read_core_regs()
    if out[15] != CODE_AT + BKPT_AT:
        raise FlashIdError(f"the ID routine stopped at 0x{out[15]:08X}, not at its end "
                           f"(0x{CODE_AT + BKPT_AT:08X}): a fault in the ROM call?")
    if out[4] == 0:
        raise FlashIdError("the flash did not answer the Read Unique ID command (timed out)")
    return id_from_reply(r.read_mem(BUF_AT, ID_CMD_BYTES))


def read_unique_id(target, log=None):
    """The board's flash unique ID over SWD; see the module docstring.
    Resets the chip (reset halt) first; leaves it halted with XIP working."""
    log = log or (lambda s: None)
    target.reset_halt()
    funcs = rom_functions(target.rsp.read_mem)
    first = _read_once(target, funcs)
    second = _read_once(target, funcs)
    if first != second:
        raise FlashIdError(f"two reads of the flash's unique ID disagree ({first}, {second})")
    log(f"Flash unique ID (read over SWD, twice): {first}")
    return first


# XIP on again, nothing else: the same four ROM calls without the 0x4B
# command.  `reset halt` stops the core at the boot ROM's start, before
# anything sets up XIP, and OpenOCD's `flash read_bank` reads through XIP:
# there it got 2 MB of zeros with no error (2026-11-17, the check before
# writing, after the reconnect's reset halt).  Run before every SWD read.
XIP_SOURCE = r"""
        blx     r4                  @ connect_internal_flash
        blx     r5                  @ flash_exit_xip
        blx     r6                  @ flash_flush_cache
        blx     r7                  @ flash_enter_cmd_xip
        bkpt    #0x4C
"""
XIP_CODE = bytes.fromhex('a047a847b047b8474cbe')


def enable_xip(target, log=None):
    """Reset halt, then put the flash back in XIP read mode with the boot
    ROM's own routines, so reads of 0x10000000 return the flash."""
    target.reset_halt()
    funcs = rom_functions(target.rsp.read_mem)
    r = target.rsp
    safety.check_write(CODE_AT, 4, 0, why='XIP on')
    r.write_mem(CODE_AT, XIP_CODE)
    for n, v in {4: funcs['IF'], 5: funcs['EX'], 6: funcs['FC'], 7: funcs['CX'], 13: STACK_TOP}.items():
        r.write_reg(n, v)
    r.write_reg(target.xpsr_n, 0x01000000)
    r.write_reg(15, CODE_AT)
    how = target.run(5.0)
    if how != 'halted':
        raise FlashIdError(f"turning XIP on did not finish ({how})")
    pc = r.read_core_regs()[15]
    if pc != CODE_AT + len(XIP_CODE) - 2:
        raise FlashIdError(f"turning XIP on stopped at 0x{pc:08X}, not at its end: a fault in the ROM call?")
    if log:
        log("Flash set up for reading (XIP on).")
