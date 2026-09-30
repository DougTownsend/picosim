# Cycle counts in the multi-cycle teaching machine

picosim executes every instruction as a sequence of clocked FSM states on the
teaching datapath from Chapter 7 of the course text: a single 32-bit bus,
MAR/MDR, IR/IR2, a separate address adder, the ALU and the register file.
Each row below comes from clocking a real encoding through the simulator
core, so this table always matches the simulator. Regenerate it with:

```bash
picosim --cycle-table
```

Every instruction starts with the shared fetch:
`FETCH_ADDR → FETCH_MEMORY → FETCH_IR` (3 cycles). A 32-bit encoding such as
`BL` adds `FETCH2_ADDR → FETCH2_MEMORY → FETCH2_IR` (3 more). "Textbook"
marks the rows whose count Chapter 7 gives; "new" marks sequences this
simulator defines in the same style, for instructions the chapter does not
cover.

| Instruction | Cycles | Textbook | States after fetch |
| --- | ---: | --- | --- |
| `ADDS R2, R0, R1` | 6 | matches | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `ADDS R2, R0, #5` | 6 | matches | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `MOVS R2, #5` | 6 | matches | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `CMP R0, #1` | 6 | matches | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `LSLS R1, R0, #3` | 6 | matches | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `ANDS / ORRS / EORS / MVNS / MULS ...` | 6 | new | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `MOV R8, R0 (high register)` | 6 | new | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `ADD SP, #8 / SUB SP, #8` | 6 | new | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `SXTB / UXTH / REV ...` | 6 | new | DECODE → FETCH_OPERANDS → EXECUTE_COMMIT |
| `LDR R2, [R0, #4]` | 7 | matches | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT |
| `LDRB / LDRH (immediate)` | 7 | matches | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT |
| `LDR R2, [R0, R1] / LDRSB / LDRSH` | 7 | new | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT |
| `LDR R2, [SP, #4]` | 7 | new | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT |
| `STR R2, [R0, #4]` | 7 | matches | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT |
| `STRB / STRH (immediate)` | 7 | matches | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT |
| `LDR R0, =literal` | 7 | matches | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT |
| `ADR R0, label` | 6 | matches | DECODE → EVALUATE_ADDRESS → STORE_RESULT |
| `B label` | 5 | matches | DECODE → EXECUTE_PC |
| `B<cond> label` | 5 | matches | DECODE → EXECUTE_PC |
| `BX LR` | 6 | matches | DECODE → FETCH_OPERANDS → EXECUTE_PC |
| `BLX R3` | 7 | new | DECODE → FETCH_OPERANDS → LINK → EXECUTE_PC |
| `BL label` | 9 | matches | DECODE → LINK → EXECUTE_PC |
| `PUSH {R4, LR}` | 10 | new | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT → FETCH_OPERANDS → STORE_RESULT → WRITEBACK |
| `POP {R4, PC}` | 10 | new | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT → FETCH_OPERANDS → EXECUTE_PC → WRITEBACK |
| `STMIA R0!, {R1, R2}` | 10 | new | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT → FETCH_OPERANDS → STORE_RESULT → WRITEBACK |
| `LDMIA R0!, {R1, R2}` | 10 | new | DECODE → EVALUATE_ADDRESS → FETCH_OPERANDS → STORE_RESULT → FETCH_OPERANDS → STORE_RESULT → WRITEBACK |
| `SVC #n` | 5 | new | DECODE → SVC_CALL |
| `BKPT` | 5 | new | DECODE → HALT |

Multi-register transfers (PUSH, POP, LDM, STM) take fetch (3) + DECODE + EVALUATE_ADDRESS + 2 per register + 1 WRITEBACK when the base register is updated.
