# Proposal: collecting the hardware data for Level 5

**Status:** proposal only. Nothing in it has been built. Written 2026-11-03.
**Asks you for:** approval, and the decisions in §11.

---

## 1. Summary

Level 5 is meant to show only what is true of a real RP2040. Today picosim
matches the chip for one narrow case: one core, code in SRAM, no interrupts,
and four peripherals. This proposal is a way to measure everything else
Level 5 needs from the chip itself, and to store the results so picosim can
be checked against them later **without the board**.

The core idea: **run the experiments on the chip, not through the debugger.**
Single-stepping over SWD manages about 140 instructions a second (measured in
today's 14:18 session log). That is fine for the current 11 traces but far too
slow to cover the whole instruction set. Instead, a small **discovery agent**
runs on the chip in SRAM. It works through thousands of test cases at full
speed, catches every fault itself, and writes its results to a buffer in
SRAM. The host then reads the buffer out in bulk, at about 90 KB/s (measured
in the same log), and the next batch runs.

The work is split into nine **campaigns**. Each one answers one group of
questions:

| # | Campaign | What it finds out | Board time |
|---|---|---|---|
| 0 | Identity | Which chip, ROM and debug features we have, plus a ROM dump | 1 min |
| A | Memory map | What every address range does for reads and writes at each width: works, faults, ignored or aliased, and how many cycles it takes | ~5 min |
| C | Registers | Reset values and the real writable bits of every peripheral and core register (~1,180 registers) | ~5 min |
| B | Every encoding | What every 16-bit Thumb encoding (all 59,392) and a structured sample of the 32-bit ones do to registers, flags, memory and PC | ~30 min |
| D | Timing | Cycles for every instruction form, from each code region and to each data region; instruction pairs; exception entry and exit | ~15 min |
| E | Random programs | Random instruction sequences compared on state and cycles (differential fuzzing) | open-ended (overnight) |
| F | Flashed programs | What a program flashed with picosim's **Flash** button really runs in: clocks, XIP cache timing, USB interrupt interference | ~15 min, **needs a flash write** |
| G | Peripheral behaviour | SIO divider and interpolators, GPIO latency, TIMER, UART, SysTick (Stage 4 scope) | ~10 min per peripheral |
| H | Second chip | Repeats 0, A, C, B and D on your other Pico | ~1 h, you swap boards |

All the results go into `hwfacts/`, a versioned set of data files, and every
fact records where it came from. A new command checks picosim against all of
them, and the test suite runs it too. From then on the hardware is only
needed to measure *new* things.

---

## 2. What Level 5 needs (defining "all the data")

Level 5 is the hardware-true level discussed earlier: a datasheet-style block
diagram (core, IOPORT/SIO, bus crossbar, SRAM banks, APB bridge, ROM,
XIP/flash, PPB). Every value it shows carries a source tag (**Measured**,
**Datasheet**, or **Not modelled**). For it to be true for *any* program within
its scope, picosim needs:

| Level 5 element | Data needed |
|---|---|
| Registers panel | R0–R15, xPSR (APSR, IPSR, EPSR), PRIMASK, CONTROL, MSP and PSP, including which bits exist (for example PSP[1:0] read 0) |
| Instruction behaviour | The result of every encoding, including UNDEFINED and UNPREDICTABLE ones: registers, flags, memory, the next PC, or a fault |
| Memory panel at real addresses | The region map; behaviour for each width and direction (works, faults, ignored, reads as zero, aliased); SRAM striping and aliases; the ROM contents |
| Peripheral registers | Reset values; writable masks; atomic alias (XOR/SET/CLR) support; which reads have side effects |
| Cycle counter, pipeline strip | Cycles for every instruction form × code region × data region × alignment; interaction effects; exception entry and exit costs |
| Exceptions | Stacked frame contents and alignment, EXC_RETURN values, IPSR, priority bits, PRIMASK masking, tail-chaining, what happens to interrupted LDM/STM |
| Flashed programs | Clock frequencies, XIP cache geometry and miss cost, interference from the SDK's USB and core-1 code, real-time conversion (cycles per µs) |
| Peripherals in scope | Dynamic behaviour of the peripherals Level 5 models: GPIO, SIO, TIMER, UART, SysTick (see decision 6 in §11) |

§6 maps each row to the campaign that supplies it.

---

## 3. What we have, and the facts that shape the design

**Already verified on the chip (2026-10-29):**
- all 11 traces identical
- calibration 21/21
- Table 81 timing for SRAM, SIO and branches
- APB loads take 4 cycles and APB stores take 5
- PADS reset value 0x56, and the atomic aliases for RESETS, IO_BANK0 and PADS

**Constraints found while preparing this proposal:**

1. **The SWD link is slow per round trip and fast in bulk.** About 140
   single-steps/s, about 90 KB/s reads and about 72 KB/s writes in 4 KiB
   packets (today's session log). So the work must be batched.
2. **The debugger and the core see different things.** The debugger can't
   reach the SIO. A failed debugger access shows up as an access-port error,
   not a HardFault. So fault behaviour must be measured by **core**
   accesses, with the core's own fault handler.
3. **The pipeline's inside can't be observed.** ARMv6-M has no ETM and no
   DWT cycle counter. Campaign 0 reads the CoreSight ROM table at 0xE00FF000
   to confirm whether this chip has an MTB. SysTick is the only cycle clock,
   and it counts every cycle.
4. **The datasheet is a starting guess, and the chip decides.** Table 81 was
   already wrong for APB. The SDK's metadata (pico-sdk 2.3.1 at
   `~/pico-sdk`) has `RP2040.svd` with 1,014 registers, ~1,180 counting the
   second instances of I2C/PIO/PLL/SPI/UART. The headers give each field's
   `_RESET` and `_ACCESS`. That gives us a prediction for every register,
   which the chip then confirms or overturns.
5. **The metadata is incomplete about read side effects.** The SVD marks 43
   registers with `readAction` (spinlocks, FIFOs, TIMELR, UARTDR …). But it
   misses some I checked. `I2C IC_CLR_INTR` is marked "RO", yet reading it
   clears interrupts. `SIO INTERP0_POP_LANE0` is marked "RO", yet reading it
   advances the interpolator. Reading `SIO DIV_QUOTIENT` clears DIRTY. So the
   "never read blindly" list has to be put together by hand from the
   datasheet's descriptions as well as the metadata.
6. **Debugger traffic shares the core's bus.** While the core runs,
   OpenOCD polls it about every 100 ms. A poll that lands inside a timed
   region could add cycles. The benchmarks have been exact so far, but over
   thousands of regions it would show. Timed runs will use
   `monitor poll off`, and one test compares poll on with poll off.
7. **A flashed program runs in a different environment from Testing.**
   `wrapper_main.c` calls `stdio_init_all()` (USB, with its IRQ on core 0)
   and starts core 1, which runs `report_pins` from flash every 20 ms. So
   flashed code shares the XIP cache and the bus, and is interrupted.
   Testing's SRAM, reset-halt state is the clean case. Level 5 needs both
   cases measured.
8. **The board can't be bricked by software.** The RP2040 boot ROM is mask
   ROM, and BOOTSEL always works. The real risks are electrical (driving a
   pin into something on your breadboard, or the core voltage regulator) and
   lockups that need a power cycle. §7 covers both.

---

## 4. Approaches considered

| Approach | Accuracy | Coverage | Speed | Verdict |
|---|---|---|---|---|
| More hand-written lockstep tests (today's method) | Exact | Only what someone thinks to write | ~140 insn/s | Keep for debugging single cases, not for discovery |
| **On-chip agent + bulk read-back** | Exact (the chip's own behaviour) | Anything the core can do | 10⁵–10⁶ cases per minute of transfer | **Chosen as the base of everything** |
| **Exhaustive 16-bit encoding sweep** | Exact | 100% of 16-bit encodings | ~30 min | **Chosen** (campaign B). The space is only 65,536, so sampling would waste certainty |
| **Random differential fuzzing** | Exact per case | Combinations nobody thought of | Overnight = millions of blocks | **Chosen** (campaign E) for interactions and timing in context |
| **SVD/datasheet as the prediction** | Datasheet quality | Every register | Instant | **Chosen**, but only as the prediction each test checks |
| Another emulator as the reference (Unicorn/QEMU, rp2040js, Renode) | Not ground truth; none model RP2040 timing | Partial | Fast | Rejected as truth. Unicorn stays as the stand-in |
| Logic analyser or scope on pins | Exact externally | Pin timing only | Manual | Not needed: GPIO latency can be measured internally (an output pin reads back through its own pad) |
| Arm RTL / microarchitecture docs | — | — | — | Not available: the Cortex-M0+ RTL isn't public. The pipeline stays "consistent with measured timing", not "observed" |

---

## 5. Design

### 5.1 The discovery agent (on the chip)

`picosim/discover/agent.s` is a small runtime, loaded into SRAM over SWD
like the current tests are:

- **Its own vector table.** VTOR (0xE000ED08, 256-byte aligned per the SDK
  header) points at it, with handlers for HardFault, SVCall, PendSV, SysTick
  and IRQs 0–31.
- **A case loop.** For each case in a table the host uploaded, it:
  1. sets up every input: R0–R12, SP (PSP), LR, NZCV, PRIMASK, CONTROL, and
     the scratch window's pattern;
  2. jumps to the case's slot, which holds the instruction(s) under test,
     followed by a **UDF sled** (UDF #0, 0xDE00, filling several KB on both
     sides);
  3. any way the case ends lands in a handler: falling through or branching
     into the sled raises HardFault on the UDF; a fault in the instruction
     itself raises HardFault there; SVC raises SVCall.
- **Recording.** The handler reads the 8-word stacked frame (R0–R3, R12,
  LR, return address, xPSR). It takes R4–R11 live, rebuilds the
  pre-exception SP from the frame address (+32, +4 more if stacked xPSR
  bit 9 shows the stack was realigned), and reads PRIMASK, CONTROL, MSP and
  PSP with MRS. It then compares the scratch window with its pattern,
  records the changed words, and appends a fixed-size record to the results
  buffer.
  - The stacked return address tells the cases apart: the slot itself means
    the instruction faulted, slot+2 or slot+4 means it fell through,
    anywhere in the sled means a branch landed there.
- **Two stacks, so a test can't kill the agent.** Cases run in Thread mode
  on PSP, and the agent runs on MSP. The handler reloads MSP from a constant
  *before* its first push, so even an `MSR MSP, Rn` with garbage in it can't
  break the handler.
- **The end of a batch is one BKPT.** The core then halts, the host reads
  the results and uploads the next batch.
- **Timing cases** read SysTick CVR just before and just after the region
  under test, as `os_hw.s` does now. The calibration step is the same.
- **An integrity check.** After each batch the agent checksums its own
  code. If a stray store hit it, the host reloads the agent and reruns that
  batch one case at a time to find the culprit.

Memory layout: the agent and its vectors go in SRAM5 (0x20041000, 4 KB) and
the stacks in SRAM4. The case slot and sled, the scratch window, and the case
and results tables use the striped SRAM0–3. Campaign A, which probes SRAM4/5
themselves, uses a second build of the agent linked into SRAM0.

### 5.2 The host runner

New package `picosim/discover/`, built on the existing `target.py` and
`rsp.py`:

- `cases.py`: one generator per campaign. Seeds are fixed, so every run
  produces the same cases.
- `v6m.py`: a small ARMv6-M encoding table written from the Architecture
  Reference Manual, **separate from picosim's decoder**. It is used only to
  label cases and to choose safe inputs, never to judge results. So a bug in
  picosim's decoder can't hide in the data.
- `runner.py`: splits cases into batches, uploads, runs, reads back,
  enforces timeouts, recovers, and saves a checkpoint after every batch
  (`~/.cache/picosim/discover/<campaign>/<run>/`), so `--resume` continues
  after a crash or a power cycle.
  - Recovery, in order: halt → read DHCSR (S_LOCKUP, bit 19) and record a
    lockup if set → `reset halt` → restart OpenOCD → ask you to power-cycle.
- `facts.py`: writes `hwfacts/`.
- `check.py`: replays every recorded case in picosim and compares
  (`python3 -m picosim.discover check`). Until Stage 2 lands it uses the
  existing +0x20000000 address rule from `image.py`; afterwards it compares
  directly. `tests/test_hwfacts.py` runs it, so the normal test run checks
  against the hardware without any board attached.
- Additions to `target.py`: read DHCSR, `poll off/on`, recover from a stuck
  access-port transfer (DAP ABORT), and optionally reach core 1 (OpenOCD
  without `USE_CORE 0`), which campaign F needs.

**Capture is kept separate from comparison.** The chip's raw results are
saved first, and picosim is compared afterwards. One capture serves every
future version of picosim, and the Stage 2 changes don't invalidate any data.

### 5.3 Rules for trusting a result

- Every case starts from a fully specified state. Nothing is left over from
  earlier cases.
- Behaviour campaigns (A, B, C) run **twice**, the second time with the
  batches in a different order. A fact is accepted only if both runs agree.
- Timing measurements run **5 times** with polling off. A fact is accepted
  only if all 5 agree, otherwise it is stored as "varies", with every value.
- The core is never halted inside a timed region.
- Every fact records: the chip (CHIP_ID revision, ROM version), the date, the
  OpenOCD version, the SWD speed, the picosim commit and the agent's hash.

### 5.4 The facts store (`hwfacts/`)

Small JSON summaries are committed. Large raw files are compressed (see
decision 4 in §11). An example fact:

```json
{ "id": "timing.load.apb.word", "value": 4, "unit": "cycles",
  "source": "measured", "campaign": "D1", "runs": 5, "consistent": true,
  "prediction": { "value": 2, "from": "RP2040 datasheet Table 81" },
  "chip": { "chip_id": "0x…", "rom": 3 }, "date": "2026-11-…", "agent": "…" }
```

Files:
- `identity.json`
- `rom.bin` (16 KiB; the boot ROM source is public under BSD-3 in
  raspberrypi/pico-bootrom)
- `memory_map.json`
- `registers.json`
- `encodings.json` + `raw/encodings.bin.gz`
- `timing.json`
- `exceptions.json`
- `environment.json` (from F)
- `peripherals/*.json`

---

## 6. The campaigns in detail

### Campaign 0: Identity (first, 1 min)

- **Reads:**
  - SYSINFO CHIP_ID (0x40000000: revision in bits 31:28)
  - ROM version byte (0x00000013)
  - CPUID (0xE000ED00; the SDK expects 0x410CC601)
  - MPU_TYPE (the SDK expects 8 regions)
  - the CoreSight ROM table at 0xE00FF000 (which debug and trace parts
    exist)
  - BPU and DWT comparator counts
  - RESETS state after `reset halt`
- **Dumps:** the boot ROM, 0x0–0x3FFF.
- **Re-measures:** SWD read and write throughput, so the time estimates
  can be updated.

### Campaign A: Memory map (~5 min)

**Probe list.** Generated from `addressmap.h` and the SVD:
- each region's first word, middle and last word, and the words just past
  its end;
- each peripheral's first and last register, the first offset past it, and
  the end of its 4 KB window;
- the +0x1000/+0x2000/+0x3000 alias windows of each peripheral;
- known holes: 0x00004000, 0x0FFFFFFC, 0x20042000, 0x20080000, 0x21040000,
  0x30000000, 0x40070000, 0x50400000, 0x60000000, 0xA0000000, 0xD0000180+,
  0xE0100000, 0xFFFFFFFC.

**Per probe:**
1. read once through the **debugger** first, which is recoverable if the
   access hangs;
2. read through the **core**, at 8, 16 and 32 bits: fault or value;
3. write through the core, only for classes allowed in §7 (RAM, ROM, holes,
   and the allowlisted peripherals): fault, ignored, or stored.

That is about 2,000 probes.

**Gap scan.** Reads every word of the 4 KB window of each peripheral that is
safe to read (~25 × 1,024 words), to learn whether unused offsets read as
zero, fault, or mirror other registers.

**SRAM structure:**
- writes unique tags through the non-striped aliases (SRAM0–3 at 0x21000000
  + n·0x10000) and reads them back through the striped window, which gives
  the striping function;
- checks SRAM4/5 aliases and what lies past 0x20042000.

**Access timing.** LDR/STR at each width, 64 repeats, to each region class:
- striped SRAM, the SRAM4/5 banks, non-striped SRAM
- ROM
- APB, AHB peripherals (DMA 0x50000000, USB DPRAM 0x50100000, PIO
  0x50200000)
- SIO, the PPB (SysTick/NVIC/SCB), XIP_CTRL
- instruction fetch from each SRAM class

**Two unknowns get special care:**
1. **A peripheral held in reset.** Accessing one might hang the bus, and we
   don't know yet. The debugger tries it first, then the core, with the
   recovery ladder ready.
2. **XIP/flash addresses in the reset-halt state.** Flash isn't set up
   then, so these accesses may hang. They are only probed in campaign F,
   once the SDK has set flash up.

- **Output:** `memory_map.json`. This is the data **Stage 2 needs**: its region
  tables can come from measurements instead of the datasheet.

### Campaign C: Registers (~5 min)

- **What:** all ~1,180 SVD registers, plus the core's SCB/NVIC/SysTick/MPU
  registers.
- **States:**
  - S1: after `reset halt`, Testing's starting state.
  - S3: after cycling each safe peripheral through RESETS, which gives the
    pure reset values.
  - S2: at `asm_main` entry in the real firmware (from campaign F).
- **Reads:** through the debugger (fast). SIO is read through the core, and
  a sample of registers is read both ways to check that the two views agree.
  Registers on the read-side-effect list (SVD `readAction` + header
  `RF`/`RWF` + the hand-made list in §3.5) are skipped here and tested on
  purpose in G.
- **Writable masks (allowlisted registers only, §7):** writes 0x00000000,
  0xFFFFFFFF, 0x55555555 and 0xAAAAAAAA, each through the normal address and
  the XOR/SET/CLR aliases. It reads back after each write and restores the
  original at the end. This gives the real writable bits, how reserved bits
  behave, and alias support per register. For example: does SIO have
  aliases (the datasheet says no)? How many NVIC IPR bits exist (the SDK
  says bits 7:6)? Which ISER bits 26–31 exist? Is CONTROL.nPRIV
  implemented?
- **Output:** `registers.json`: for each register, the SVD prediction next to
  the S1/S2/S3 reset values and masks, with differences flagged.

### Campaign B: Every encoding (~30 min including the repeat run)

All counts are exact; I checked that the sums add up to 59,392.

| Pass | Cases | What it settles |
|---|---|---|
| **B1 classify** | All 59,392 16-bit encodings, once each, from a safe state: R0–R12 small distinct values (addresses fall in ROM, which is harmless to read and write), SP valid, LR pointing into the sled | Defined or faults; whether it changes PC; which registers and flags change; UNDEFINED encodings (for example ARMv7-only CBZ and IT) |
| **B2 data operations** | 23,040 data encodings (shift/add/sub/mov/cmp immediates 16,384; register ALU 1,024; high-register forms 768, of which the ones that write PC also run in B4; ADR/ADD SP 4,096; ADD/SUB SP, extend, REV 3 × 256) × 4 register sets (edge values 0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF … and seeded random) × 4 NZCV inputs; × 16 NZCV inputs for the ones that read flags (ADC, SBC, shifts by 0) → ~370,000–400,000 cases | Result and NZCV for every encoding |
| **B3 memory** | 27,648 load/store encodings (literal, register offset, immediate, SP-relative, LDM/STM, PUSH/POP) × 2 base placements × 2 data patterns ≈ 110,000 cases. The host picks inputs so every address lands in a 4 KB scratch window, plus deliberate edge cases (unaligned, window edges, the base register in the LDM/STM list) | Loaded values, stored bytes, write-back, faults |
| **B4 control flow and special pages** | B<cond> 3,584 × 16 NZCV; B 2,048 (all reach the sled); BX/BLX 256 × target kinds (in the sled with bit 0 set or clear, unmapped); PC-writing MOV/ADD; POP {PC}. Separate handling for: BKPT 0xBExx (halts under the debugger, so the host handles it); hints and IT 0xBFxx (WFI/WFE run with a pending IRQ that PRIMASK masks, so they wake); CPS 0xB660–0xB67F; SVC; UDF | Branch targets, condition truth tables, Thumb-bit faults, hint and sleep behaviour |
| **B5 32-bit** | First halfwords 0xF000–0xF7FF × 64 structured second halfwords (all 16 values of bits 15:12 × 4); 0xE800–0xEFFF and 0xF800–0xFFFF × 4 each; MRS/MSR for every SYSm value; DMB/DSB/ISB option fields. BL immediates kept to targets in the sled or deliberately unmapped (never ROM, which would run unknown code) ≈ 150,000 cases | BL, MSR, MRS, the barriers, UDF.W, and which 32-bit encodings are UNDEFINED |

- **Size:** about 66 MB of records, about 12 min to read at 90 KB/s, doubled
  for the determinism repeat. Running the cases takes under a minute (about
  700,000 cases × ~300 cycles at ~6 MHz).
- **Lockups:** expected only where a case loads garbage into PSP (MOV SP,
  MSR PSP). B2 gives those encodings safe register values. Any lockup that
  still happens is recorded as a lockup, which is itself a fact.
- **Output:** `encodings.json` (per encoding class: matches picosim, or the
  list of differences) and the raw records.

### Campaign D: Timing (~15 min)

- **D1 single forms:** each of ~70 instruction forms × 64 repeats, run from
  each code region (striped SRAM, SRAM4, SRAM5, non-striped) × word and
  halfword alignment. Memory forms also go to each data region from A.
  About 1,100 regions × 5 runs.
- **D2 pairs:** every ordered pair of ~40 representative forms (1,600
  pairs). This finds interaction costs: a load followed by use of its
  result, a store followed by a load of the same address, chains of MULS,
  a branch after a flag-setting instruction.
- **D3 special instructions:** DMB/DSB/ISB, MRS/MSR, CPSID/CPSIE,
  NOP/YIELD/SEV, and the SVC instruction itself.
- **D4 exceptions:**
  - entry latency (from pending to the first handler instruction) and exit
    latency (from `BX EXC_RETURN` to the next Thread instruction), for SVC,
    PendSV (ICSR), SysTick and NVIC IRQs 26–31 (software-pended, unused by
    hardware);
  - tail-chaining (two pending at once);
  - the frame contents and stack realignment, EXC_RETURN values for
    MSP/PSP, IPSR;
  - priorities and PRIMASK;
  - what happens when an interrupt arrives during LDM/STM/PUSH/POP
    (abandoned and restarted, or completed first: measured by sweeping the
    SysTick fire point across the instruction);
  - WFI wake latency.
- **D5:** polling on versus off, to measure how much the debugger disturbs
  timing.
- **Output:** `timing.json` and `exceptions.json`. These feed `timing.py`,
  the pipeline strip, the heat map and Explain this step.

### Campaign E: Random programs (open-ended)

- **Blocks:** random blocks of 8–64 instructions, from the encodings B
  found to be defined. Memory operations stay in the scratch window, and
  branches go forward or form bounded loops.
- **Per block:** final registers, flags, changed memory and SysTick cycles.
- **Speed:** 1,000 blocks per upload. 100,000 blocks take about 15 min, and
  an overnight run gives about 3 million.
- **Differences:** any difference is shrunk on the chip (cutting the block
  down until the difference disappears) to a minimal program. That program
  is saved into `hwtests/` as a new regression test.
- **Why:** this catches combinations B and D can't: flag chains, load/store
  ordering, and timing in real context.

### Campaign F: Flashed programs (~15 min, **writes the target's flash**)

1. Back up the target's whole flash using the procedure in §7.1. Nothing is
   written until the two independent backups match.
2. Build picosim's real firmware (same `CMakeLists.txt` and
   `wrapper_main.c`) with an `asm_main` that BKPTs on entry. Flash it with
   OpenOCD `program` (or by UF2 drag), then `reset run` and wait for the
   halt.
3. **Dump S2:**
   - every safe register (campaign C);
   - the clock frequencies, measured by the chip's own frequency counter
     (CLOCKS FC0, a measurement-only exception to the CLOCKS write ban) for
     clk_sys, ref, peri, usb, adc and rtc;
   - the SSI/XIP setup;
   - which NVIC interrupts are enabled, and their priorities;
   - SysTick;
   - core 1's state.
4. **XIP cache:** miss and hit costs; line size; ways and replacement policy
   (address patterns that collide in a set, confirmed with XIP_CTR_HIT and
   XIP_CTR_ACC); the NOALLOC and NOCACHE aliases; flush; loops of code run
   from flash (first pass against later passes). Each is measured **with
   core 1 running and with core 1 halted**.
5. **Interference:** a fixed SRAM loop timed many times inside `asm_main`.
   The spread shows how often the USB/stdio interrupts run on core 0 and how
   long each takes.
6. **Real time:** clk_sys cycles per TIMER microsecond tick.
7. **The two old checks from the 2026-10-13 audit**, if Week 1 hasn't done
   them yet: 01_hello prints after Flash, and skipping FUNCSEL leaves the LED
   dark.
8. Restore and verify the original flash (§7.1). Campaign F only counts as
   finished once that verification passes.

- **Output:** `environment.json`. This is what lets Level 5 say what happens
  to a *flashed* program, which is different from what happens in Testing.

### Campaign G: Peripheral behaviour (Stage 4 scope, ~10 min each)

- **SIO:**
  - divider: edge cases including divide by 0, plus random cases; result
    timing (the datasheet says 8 cycles); DIRTY and READY;
  - interpolators: random cases compared against the datasheet model;
  - FIFO status with core 1 parked;
  - spinlocks.
- **GPIO** (no wiring needed):
  - latency from an output write to the change showing in GPIO_IN, read back
    through the pin's own pad;
  - the input synchroniser's effect (SYSCFG PROC_IN_SYNC_BYPASS);
  - the IO_BANK0 OUTOVER/OEOVER/INOVER overrides;
  - edge and level bits in INTR;
  - pull-up and pull-down levels on unconnected pins.
- **TIMER:** tick rate; alarm-to-IRQ latency; the TIMELR/TIMEHR latch.
- **UART:** with internal loopback (UARTCR.LBE): FIFO depth, flag timing,
  cycles per character at a given baud rate, receive timeout.
- **SysTick:** COUNTFLAG cleared on read; reload; CALIB.

### Campaign H: Second chip (~1 h, you swap boards)

Put the debugprobe firmware on the other Pico and swap their roles. Then
rerun 0, A, C (S1), B1 + a 10% sample of B2–B5, and D. Matching results
show the facts hold for the RP2040 in general and not just for one board. A
difference becomes a recorded chip-revision fact.

### Coverage: every need in §2 has a source

| Need | Campaigns |
|---|---|
| Registers panel bits | B (MRS/MSR), C (PPB), D4 |
| Instruction behaviour | B (all encodings), E (combinations) |
| Memory map, aliases, ROM | 0, A, F (XIP) |
| Peripheral registers | C, G |
| Cycle counts and interactions | A (access), D, E, F (XIP) |
| Exceptions | D4, B (SVC/UDF/BKPT) |
| Flashed-program environment | F |
| Modelled peripherals | G |
| Chip independence | H |

---

## 7. Safety rules

**Never written (read-only):**
- **VREG_AND_CHIP_RESET**: the core voltage regulator; the one write that
  could harm the chip.
- **CLOCKS** (except the FC0 frequency-counter registers), **PLL_SYS,
  PLL_USB, XOSC, ROSC**: stopping a clock loses the debugger's access.
- **PSM**: it can power off the core or the SRAM.
- **WATCHDOG**: its trigger bit resets the chip.
- **SYSCFG**: it can disconnect debug (except PROC_IN_SYNC_BYPASS in G).
- **SSI, IO_QSPI, PADS_QSPI, XIP_CTRL** (except FLUSH and the counters in
  F): these control the flash.
- **USB**: it would connect a USB device to your computer.
- **AIRCR** SYSRESETREQ, except in one planned test.
- DMA trigger aliases, PIO SM_ENABLE.

**Written** (allowlist): IO_BANK0, PADS_BANK0, RESETS for allowed
peripherals only, SIO GPIO/interp/div, TIMER, PWM (outputs off), UART, SPI,
I2C (disabled), ADC, RTC, BUSCTRL performance counters, the PPB (NVIC, SCB,
SysTick, MPU), and the agent's SRAM.

**Pins:**
- No pin is ever set to output except those named in a run-time `--pins`
  list.
- The default list excludes GP19 and GP21 (your saved button and LED).
- **The target is a Pico W** (answered 2026-11-03). GP23, GP24, GP25 and
  GP29 are wired to the wireless chip (CYW43439):
  - GP23 = WL_ON, its power switch;
  - GP24 = SPI data and interrupt;
  - GP25 = chip-select;
  - GP29 = SPI clock, and also VSYS ÷ 3 while GP25 is high.

  For these four pins, every campaign is **read-only**: no output, no
  function select (IO_BANK0 GPIOn_CTRL), and no PADS writes. A pull-up on
  GP23 alone would switch the wireless chip on. Campaign C's write-mask
  tests skip their CTRL and PADS registers, and its masks for the other
  pins apply to these four.
- The SWD header sits near the middle of a Pico W, not at the end. The
  wiring is the same.
- Campaign F's firmware is built with the SDK's default board (`pico`), as
  picosim's Flash button builds it now. It runs unchanged on a Pico W,
  because it never uses the LED or the wireless chip.
- Recommended: unplug the breadboard during campaigns.

**Hangs:** the recovery ladder in §5.2, checkpoints after every batch, and
accesses that might hang tried through the debugger before the core.

### 7.1 Flash backup and restore (campaign F)

You approved writing the target's flash on one condition: the original
program must come back, and nothing may change permanently. This section is
how that is guaranteed and proven.

**What on the board can be changed at all:**

| Part | Can it be changed? |
|---|---|
| RP2040 boot ROM | No. It is mask ROM, fixed when the chip was made. This is also why BOOTSEL recovery always works. |
| RP2040 fuses / OTP | The RP2040 has none. (The newer RP2350 does; this chip doesn't.) |
| RP2040 registers, SRAM | Lost at every power-off. Nothing persists. |
| CYW43439 wireless chip | Not touched: its pins are read-only (§7). It has no storage of its own; the RP2040 loads its firmware from flash at each boot. |
| **External QSPI flash (2 MB)** | **Yes. This is the only thing campaign F changes, and it is restored bit for bit.** |
| The flash chip's own settings (status-register protection bits, one-time lock bits for its security registers) | Only special commands (Write Status Register, security-register program) change these. Neither tool sends them: OpenOCD's `rp2040_flash` driver and picotool both use the boot ROM's flash routines, which only read, erase and program. The quad-enable bit set by the SDK's boot2 is already set by the program on the board now, and the discovery firmware uses the same boot2 (W25Q080, which is also what the Pico W board uses), so it is never rewritten. |

**Before any write:**
1. **Identify the flash.** Read the flash's JEDEC ID and size through
   OpenOCD (`flash probe 0` / `flash info 0`), expecting 2 MB. Read the
   flash's 64-bit unique ID. If either read fails or the size is
   unexpected, stop.
2. **Backup A through SWD:** OpenOCD `flash read_bank 0`, the whole chip.
3. **Backup B through USB, an independent path.** Put the board in BOOTSEL
   mode, then run `picotool save --all`, the whole chip, read by the boot
   ROM. Entering BOOTSEL doesn't write to flash.
4. **Compare A and B byte for byte** (SHA-256). If they differ, read both
   again. If they still differ, **stop: nothing is written.**
5. **Store the backup in two places:**
   `~/picosim-flash-backups/<date>-<unique ID>.bin` with its `.sha256`,
   plus a copy in picosim's cache. Also save a `.uf2` version, decoded back
   and checked against the `.bin`, so the board can be restored by
   drag-and-drop with no probe at all. Files are named by the flash's unique
   ID, so a backup can never be restored onto your other Pico by mistake.
   The restore step refuses an ID mismatch.

**Restore (the last step of campaign F, and also run at the start of the
next session if a session ends before it):**
6. Write the backup over the whole chip: OpenOCD
   `flash write_image erase <backup>.bin 0x10000000`, then `verify_image`.
7. **Prove the flash is identical:** read the whole chip again with
   `flash read_bank` and compare its SHA-256 with the backup's. You get both
   checksums printed side by side.
8. **Boot check:** `reset run` and confirm the original program behaves as
   before. For a picosim program, its USB serial port appears.
9. The backup files are kept; only you delete them.

**If something goes wrong in the middle** (the cable is pulled, power is
lost, a bug, OpenOCD crashes), the flash may be left half-written. That is
not permanent. The boot ROM's BOOTSEL mode doesn't depend on flash, so the
`.uf2` backup can always be dragged onto the `RPI-RP2` drive, or written
back through SWD.

**The only physical trace** is one extra erase/program cycle on each flash
sector, out of the roughly 100,000 the flash chip is rated for. It can't be
measured and doesn't affect anything.

---

## 8. What will still be unknown afterwards

- **The pipeline and gates inside the core.** They can't be observed.
  Level 5 shows them as "consistent with measured timing".
- **Bus arbitration in general.** Only the measured cases will be known:
  one core with no DMA, and F's core-1 and USB cases. Two cores and DMA
  working at once are not covered.
- **Analog properties:** drive current, input thresholds, pull resistor
  values. These need a meter. Level 5 doesn't need them.
- **ROSC frequency versus voltage and temperature.** Cycle counts don't
  depend on it, and F measures the 125 MHz setup the SDK uses.
- **USB timing beyond what F measures.** It depends on the host computer.

---

## 9. Logic checks I ran on this plan

1. **Throughput.** The figures are from today's real session log, not
   guesses: steps per second came from counting step packets, bulk speed
   from 4 KiB `m`/`M` packets per second. Every board-time estimate uses
   them, and Campaign 0 re-measures them.
2. **Encoding counts.** The 16-bit space minus the 32-bit prefixes
   (0xE800–0xFFFF, 6,144) gives 59,392. The per-page counts in B sum to
   exactly that.
3. **A case can't kill the agent.** Cases run on PSP and handlers on MSP,
   and MSP is reloaded before the first push. HardFault isn't masked by
   PRIMASK, so CPSID can't block the capture. A stray store is caught by
   the checksum. The one way left to fail is garbage in PSP (stacking
   fails, so the core locks up); it is limited to known encodings, which
   get safe inputs, and still recovered and recorded.
4. **BKPT.** With the debugger attached, BKPT halts instead of faulting, so
   its page is run by the host and never by the agent's loop.
5. **WFI/WFE could sleep forever.** They run with an enabled interrupt
   pending and masked by PRIMASK, which still wakes WFI. WFE is preceded by SEV. A batch
   timeout catches anything else.
6. **Ordering.** A runs before B, because B5's unmapped BL targets and B3's
   edge addresses must be known not to hang. C's read-side-effect list is
   built before any bulk read. F's flash dump happens before any flash
   write.
7. **The truth can't be biased by picosim.** Inputs are chosen by an
   independent encoding table, results come from the chip, and picosim is
   compared afterwards.
8. **Metadata gaps.** The incomplete read-side-effect tags were found by
   checking real headers (I2C IC_CLR_INTR, INTERP POP, DIV_QUOTIENT). That
   is why the "don't read" list is assembled by hand as well as from the
   metadata.
9. **Measurement disturbance.** Debugger polling is switched off during
   timed regions, and D5 measures its effect.
10. **Two environments.** Testing (SRAM, reset halt, ~6 MHz ROSC) and
    flashed programs (125 MHz, XIP, core 1 running, USB interrupts) are
    measured separately. Level 5 has to say which one it is showing.

---

## 10. Implementation steps and fit with the weekly plan

| Step | Work | Sessions (estimate) |
|---|---|---|
| 1 | Agent, runner, recovery, facts store, `check` + test hook | 2 |
| 2 | Campaign 0 + A (gives Stage 2 its measured map) | 1 |
| 3 | Campaign C | 1 |
| 4 | Campaign B + independent encoding table | 2 |
| 5 | Campaign D | 1–2 |
| 6 | Campaign E | 1 |
| 7 | Campaign F | 1 |
| 8 | Campaign G (with Stage 4), H (when convenient) | later |

Board time for 0, A, C, B and D is about 1 hour, plus an optional overnight
run of E.

**Fit with the weekly plan:** Week 1's open items stay first (the hardware
run with the APB fix, the two old checks, the core memory-map start).

- **Week 2:** steps 1–3 alongside the Python part of Stage 2. A feeds
  Stage 2 directly, so Stage 2's regions match the measured chip.
- **Week 3:** steps 4–5 alongside the Stage 2 GUI work.
- **Week 4** (Thanksgiving, light): step 6 running overnight, then step 7.

This pushes Stage 3 (the "inside" views) back by about two weeks, unless
you'd rather keep Stage 3 in Week 4 and run the discovery work more slowly
(decision 5).

New code: `picosim/discover/`, `hwfacts/`, `tests/test_hwfacts.py`, and small
additions to `picosim/hwtest/target.py`. The Testing window, the existing
suite and the 137 tests are unchanged.

---

## 11. Decisions needed from you

1. ~~**Board:** Pico or Pico W?~~ **Answered: Pico W.** §7 is updated.
2. ~~**Flashing**~~ **Answered:** allowed, on condition the original comes
   back exactly. The procedure is §7.1.
3. **Breadboard:** staying connected. It holds only the SWD dupont wires and
   one jumper wire, with no other parts. **Still needed:** which two points
   does the jumper wire connect? A GPIO pin it touches becomes read-only.
4. **Raw data:** commit the compressed raw records (~15–25 MB, so anyone
   can re-check without a board), or keep them in `~/.cache` and commit
   only the summaries?
5. **Schedule:** push Stage 3 back about two weeks as in §10, or run
   discovery more slowly beside it?
6. **Level 5 peripheral scope:** core + GPIO only at first (campaign G
   later), or TIMER and UART too (G becomes part of this work)?
7. **Second chip (H):** do it, and roughly when?
