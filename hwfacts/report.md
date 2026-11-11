# picosim against the RP2040

82437 cases: 82118 match, 40 differ (0 kinds not yet known), 23 pending: exception model, 0 pending: Stage 2, 0 not compared: memory the agent never set, 256 skipped.

Compared in part: 2561 cases loaded memory the agent never set (those registers not compared); 9720 cases stored into ROM (dropped, as on the chip; for students picosim stops there on purpose).

Sources: encodings-B1.json, encodings-B2.json, timing-D1-S0-halfword.json, timing-D1-S0-word.json, timing-D1-S1-halfword.json, timing-D1-S1-word.json, timing-D1-S2-halfword.json, timing-D1-S2-word.json, timing-D1-S3-halfword.json, timing-D1-S3-word.json, timing-D2.json, timing-D3.json

## Differences

### faults

| Encoding | Cases | Example | What | Known |
|---|---|---|---|---|
| CPS (should-be bits wrong) | 30 | `60b6` r0–r3 0x0000071C, 0x00000890, 0x00000BE4, 0x000005E0 NZCV 0101 | the chip runs it and falls through; picosim stops with an error at 0x20014000: Undefined instruction 0xB660 at 0x20014000: this is not a Cortex-M0+ (ARMv6-M) instruction, so the Pico would stop with a HardFault. If it is data, keep it out of the path the program runs through. | UNPREDICTABLE encoding: CPS with its should-be bits 3:0 not 0010. The RP2040 ignores bits 3:0: 0xB660-0xB66F act as CPSIE i and 0xB670-0xB67F as CPSID i (PRIMASK read back, all 32 forms). picosim stops with 'Undefined instruction ... not a Cortex-M0+ instruction'. |
| POP {list} | 1 | `00bc` r0–r3 0x00000C88, 0x0000087C, 0x00000AD8, 0x000001B8 NZCV 0111 | the chip branches to 0x20017466; picosim runs it and falls through | UNPREDICTABLE encoding: POP {} (0xBC00, empty register list). The RP2040 loads PC from [SP] and leaves SP unchanged (it branched to the word at SP). picosim does nothing and falls through. |
| LDM Rn{!},{list} | 1 | `00ca` r0–r3 0x00000C78, 0x000007A4, 0x00000B5C, 0x00000EF4 NZCV 1001 | the chip branches to 0x200139D8; picosim runs it and falls through | UNPREDICTABLE encoding: LDM Rn!,{} (0xC800-0xCF00, empty register list). The RP2040 loads PC from [Rn] (7 of 8 then HardFault on the ROM word they load; 0xCA00 runs boot-ROM code and lands in the sled), Rn unchanged. picosim does nothing and falls through. |

### decoder

| Encoding | Cases | Example | What | Known |
|---|---|---|---|---|
| STM Rn!,{list} | 8 | `00c0` r0–r3 0x00000590, 0x0000035C, 0x00000638, 0x00000A74 NZCV 1100 | r0: chip 0x00000594, picosim 0x00000590 | UNPREDICTABLE encoding: STM Rn!,{} (0xC000-0xC700, empty register list). The RP2040 adds 4 to Rn (it stores one word; B's cases aim it at ROM, so what it stores is not seen). picosim leaves Rn unchanged. |

## Pending: exception model

### faults

| Encoding | Cases | Example | What | Known |
|---|---|---|---|---|
| BX Rm | 7 | `6947` r0–r3 0x00000E40, 0x00000ED0, 0x00000314, 0x00000934 NZCV 1000 | a HardFault at 0x20040F00 | not compared yet |
| BLX Rm | 7 | `e947` r0–r3 0x00000088, 0x00000AE4, 0x000007C4, 0x00000280 NZCV 1011 | a HardFault at 0x20040F00 | not compared yet |
| LDM Rn{!},{list} | 7 | `00c8` r0–r3 0x00002D58, 0x000006F8, 0x00000388, 0x000006C8 NZCV 0100 | a HardFault at 0x3401D400 | not compared yet |
| ADD PC,SP,PC | 1 | `ef44` r0–r3 0x00000EB0, 0x00000F38, 0x00000AF8, 0x000008E8 NZCV 1011 | a HardFault at 0x40054F04 | not compared yet |
| ADD PC,PC | 1 | `ff44` r0–r3 0x000008F8, 0x000001A8, 0x00000724, 0x00000508 NZCV 0101 | a HardFault at 0x40028008 | not compared yet |

