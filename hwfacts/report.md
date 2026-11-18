# picosim against recorded RP2040 captures

Evidence: recorded RP2040 captures (hwfacts/replay: measured on the chip, replayed with no board).

Verdict: **PASS**: no new differences (1 known differences).

97623 cases: 97328 match (90 of them timing calibration), 1 differ (1 known, 0 new in 0 kinds not yet known), 36 pending: exception model, 1 pending: Stage 2, 1 not compared: memory the agent never set, 256 skipped. Compared: 97239 of 97623.

Compared in part (12293 cases): 2565 cases loaded memory the agent never set (those registers not compared); 9728 cases stored into ROM (dropped, as on the chip; for students picosim stops there on purpose).

## Sources

| Dataset | Run | SHA-256 | Cases | Compared | Match | Differ (known / new) | In part | Pending | Not compared | Skipped | Calibration |
|---|---|---|---|---|---|---|---|---|---|---|---|
| encodings-B1.json | 20261111-002711 | 78c8e5d445f2 | 59386 | 59105 | 59105 | 0 (0 / 0) | 12286 | 24 | 1 | 256 | 0 |
| encodings-B2.json | 20261110-145854 | c0679124587b | 19393 | 19393 | 19393 | 0 (0 / 0) | 3 | 0 | 0 | 0 | 0 |
| encodings-B3.json | 20261110-152202 | ff7d76527122 | 5400 | 5397 | 5397 | 0 (0 / 0) | 1 | 3 | 0 | 0 | 0 |
| encodings-B4.json | 20261112-144025 | d7d359e52251 | 3110 | 3101 | 3101 | 0 (0 / 0) | 2 | 9 | 0 | 0 | 0 |
| encodings-B5.json | 20261112-144532 | 18fbad1f267f | 6676 | 6675 | 6674 | 1 (1 / 0) | 1 | 1 | 0 | 0 | 0 |
| timing-D1-S0-halfword.json | 20261110-143420 | 68515637c512 | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D1-S0-word.json | 20261110-143420 | 74411ff5d331 | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D1-S1-halfword.json | 20261110-143420 | 955069e1cfc7 | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D1-S1-word.json | 20261110-143420 | 80f0b1609bbe | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D1-S2-halfword.json | 20261110-143420 | a128e8986a0e | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D1-S2-word.json | 20261110-143420 | e24d350aca4b | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D1-S3-halfword.json | 20261110-143420 | 49ea28a86b7e | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D1-S3-word.json | 20261110-143420 | ed3e756afe73 | 242 | 236 | 242 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 6 |
| timing-D2.json | 20261110-143520 | b7f075d7c5a3 | 1680 | 1640 | 1680 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 40 |
| timing-D3.json | 20261110-143611 | 8105ac931d91 | 42 | 40 | 42 | 0 (0 / 0) | 0 | 0 | 0 | 0 | 2 |

## Differences

### decoder

| Encoding | Cases | Example | What | Known |
|---|---|---|---|---|
| MRS Rd,spec_reg | 1 | `eff30a87` r0–r3 0x376A9C42, 0x8D34362F, 0x4F9E9947, 0x70365CA3 NZCV 1001 (case 1342308397 of encodings-B5) | r7: chip 0x20040800, picosim 0x20040F00 | MRS Rd, MSP while Thread mode runs on PSP (CONTROL.SPSEL = 1, as the agent does): the RP2040 returns MSP. picosim has one SP and returns the SP in use. |

## Pending: exception model

### faults

| Encoding | Cases | Example | What | Known |
|---|---|---|---|---|
| BLX Rm | 12 | `e947` r0–r3 0x00000088, 0x00000AE4, 0x000007C4, 0x00000280 NZCV 1011 (case 18409 of encodings-B1) | a HardFault at 0x20040F00 | not compared yet |
| BX Rm | 11 | `6947` r0–r3 0x00000E40, 0x00000ED0, 0x00000314, 0x00000934 NZCV 1000 (case 18281 of encodings-B1) | a HardFault at 0x20040F00 | not compared yet |
| LDM Rn{!},{list} | 10 | `00c8` r0–r3 0x00002D58, 0x000006F8, 0x00000388, 0x000006C8 NZCV 0100 (case 51200 of encodings-B1) | a HardFault at 0x3401D400 | not compared yet |
| ADD PC,SP,PC | 1 | `ef44` r0–r3 0x00000EB0, 0x00000F38, 0x00000AF8, 0x000008E8 NZCV 1011 (case 17647 of encodings-B1) | a HardFault at 0x40054F04 | not compared yet |
| ADD PC,PC | 1 | `ff44` r0–r3 0x000008F8, 0x000001A8, 0x00000724, 0x00000508 NZCV 0101 (case 17663 of encodings-B1) | a HardFault at 0x40028008 | not compared yet |
| MRS Rd,spec_reg | 1 | `e7f3818d` r0–r3 0xCF3FC4B9, 0x52C1ABB9, 0xF60D1A08, 0xFE7F32B7 NZCV 1011 (case 1342233265 of encodings-B5) | the chip: lockup | not compared yet |

## Pending: stage 2

### faults

| Encoding | Cases | Example | What | Known |
|---|---|---|---|---|
| LDM Rn{!},{list} | 1 | `00ca` r0–r3 0x00000C78, 0x000007A4, 0x00000B5C, 0x00000EF4 NZCV 1001 (case 51712 of encodings-B1) | picosim has nothing at an address the case used | not compared yet |

## Not compared: memory the agent never set

### faults

| Encoding | Cases | Example | What | Known |
|---|---|---|---|---|
| POP {list} | 1 | `00bc` r0–r3 0x00000C88, 0x0000087C, 0x00000AD8, 0x000001B8 NZCV 0111 (case 48128 of encodings-B1) | picosim: POP {} (an empty register list, UNPREDICTABLE) at 0x20014000 jumped to 0xBAD103C0 with bit 0 clear. The Cortex-M0+ only runs Thumb code: like the Pico, picosim made the jump and stops here, where the  | not compared yet |

