# Level 5 discovery: execution plan

**Status:** written 2026-11-03; built 2026-11-04 (see the tracker in §9).
Everything runs end to end on the stand-in; the board sessions (§6) are
still to do.
**Companion to:** [level5-discovery-proposal.md](level5-discovery-proposal.md),
which explains *why* (the campaigns, the safety rules, the flash procedure).
This file is *what to do, in order*, so the work can start on one word.

**When you say "go":** I start at the first unticked line of the Progress
tracker (§9). Before any code, I check the decisions in §1 with you once.
Then I work through the steps. I stop and ask for you only at the board
sessions in §6.

---

## 1. Decisions to confirm at "go"

These are still open. Each has a default, which I use unless you say
otherwise.

| # | Decision | Default |
|---|---|---|
| D3 | What does the breadboard jumper wire connect? | Until you tell me, **no pin is ever set to output** (`--pins` empty). Pull-ups and pull-downs are still allowed, because they are safe even into a rail. |
| D4 | Raw data in git? | Commit the summaries, plus a **replay subset**: all 59,392 B1 records and a 5 % sample of B2–B5, about 5 MB gzipped. This is enough for `check` to run with no board. The full raw data stays in `~/.cache/picosim/discover/`, with a second copy in `~/picosim-hwfacts-raw/`. Each file's SHA-256 is recorded in `hwfacts/`. |
| D5 | Schedule against Stage 3 | As in the proposal: discovery runs beside Stage 2 in Weeks 2–3, and Stage 3 moves back about 2 weeks. Campaign A feeds Stage 2's region tables. |
| D6 | Level 5 peripheral scope | Core, GPIO, SIO and SysTick now. TIMER and UART (campaign G, second half) come with Stage 4. |
| D7 | Second chip (H) | After D is done (around Week 4), when you have a moment to swap the boards. |
| R5 | Do measured timings replace the textbook Ch. 7 numbers at Levels 1–4? | **No.** The multi-cycle teaching machine keeps the textbook's numbers. The "RP2040 model" column (`timing.py` `m0plus`, used by the heat map, Explain and Testing) takes the measured values at every level, as the APB fix already did. Level 5 shows only measured values. |

---

## 2. Ground rules for the whole job

1. **Stand-in first, board second.** Every piece runs end to end against
   the Unicorn stand-in before it touches the board. Board time goes only
   to the sessions in §6.
2. **Safety rules live in code, not just in this document.** `safety.py`
   holds the never-write list, the allowlist, the Pico W pins
   (GP23/24/25/29) and the `--pins` list. The runner checks every planned
   write against it before uploading a batch. A failed check stops the run;
   it is never a warning.
3. **Capture first, compare later.** Raw records are saved before picosim
   is involved. Nothing in a capture depends on picosim's decoder.
4. **Every batch is checkpointed.** A crash, a pulled cable or a power cycle
   loses at most one batch. `--resume` carries on from there.
5. **No commits by me.** As before, you commit. I tell you when a step is a
   good commit point (marked ✎ in §9).
6. **The existing work stays as it is.** The Testing window, the suite and
   the 143 tests must still pass after every step. The discovery code lives
   in `picosim/discover/` and uses `picosim/hwtest/` without changing its
   behaviour.
7. **Stop conditions.** I stop and report, rather than work around it, if:
   - a safety check fails;
   - the backups for the flash campaign disagree;
   - the agent's integrity check fails twice in a row;
   - a lockup repeats outside the encodings expected to cause one;
   - the stand-in and the chip disagree on something the stand-in models
     exactly.

---

## 3. Architecture, made concrete

### 3.1 Memory layout on the chip

| Range | Size | Use |
|---|---|---|
| `0x20000000–0x2000FFFF` | 64 KB | Batch input: the case table (fixed-size records) and the code blobs they point to |
| `0x20010000–0x20017FFF` | 32 KB | **Slot site S0:** a UDF sled (`0xDE00` fill) with the case slot at `0x20014000` |
| `0x20018000–0x20018FFF` | 4 KB | Scratch window. Its pattern is set again before every case. |
| `0x20019000–0x20019FFF` | 4 KB | Guard: a known pattern, checked after each batch to catch stores outside the window |
| `0x2001A000–0x2003FFFF` | 152 KB | Results buffer: about 1,200 records of 128 bytes per batch |
| `0x20040000–0x200407FF` | 2 KB | SRAM4: the agent's MSP stack (top `0x20040800`) |
| `0x20040800–0x20040FFF` | 2 KB | SRAM4: the cases' PSP stack (starts at `0x20040F00`, so POP and LDM have room above it) |
| `0x20041000–0x20041FFF` | 4 KB | SRAM5: the vector table at `0x20041000` (48 entries, 256-aligned), then the agent's code and variables |

Campaigns A and D also need a second build, **agent_lo**. It is linked at
`0x20000000`, with the batch area moved up, so that SRAM4 and SRAM5 can
themselves be probed and used as code sites.

Extra slot sites for D:
- S1 in SRAM4;
- S2 in SRAM5 (agent_lo only);
- S3 in non-striped SRAM, at `0x21000000 + bank·0x10000`.

The host fills each site's sled before a batch.

### 3.2 How the agent runs a case

The agent always runs in **Handler mode**, and cases always run in Thread
mode on PSP. Starting a case is an exception return, which is the only way
to set every register, the PC, xPSR and PSP in one step:

1. **Handler (MSP):** reload MSP from its constant. Copy the case's code
   into its slot, and set the scratch window's pattern again.
2. Load R4–R11 from the case record. Write an 8-word frame on PSP:
   R0–R3, R12, LR, PC = slot, xPSR (NZCV from the record, T bit set). Set
   PSP, PRIMASK and CONTROL with MSR.
3. `bx` with `0xFFFFFFFD` (return to Thread, PSP). The case runs.
4. The case ends in one of these ways:
   - it falls through or branches into the sled, and the UDF there raises
     a HardFault;
   - it faults itself (HardFault);
   - it runs SVC (SVCall);
   - it locks up (the host detects this, §3.4).
5. **HardFault or SVCall handler:**
   - reload MSP and save R4–R11 at once;
   - read the stacked frame from PSP, and rebuild the SP from before the
     exception (+32, plus 4 if stacked xPSR bit 9 is set);
   - read PRIMASK, CONTROL, MSP and PSP;
   - compare the scratch window with its pattern;
   - write a result record;
   - start the next case (step 1).
6. After the last case it computes a checksum over the agent and the
   sleds, writes the batch footer, and runs `bkpt #0x7F`. The core halts,
   and the host takes over.

How a case ended comes from the stacked return address:
- at the slot: the instruction faulted;
- at slot + its length: it fell through;
- in the sled: a branch landed there, and the address is the target.

**Timing cases** wrap their body in two SysTick reads. R7 holds
`SYST_CVR`; the body runs between `ldr r6,[r7]` and `ldr r5,[r7]`, so it
must not use R5–R7. An empty body gives the overhead, which is measured
every batch. Polling is switched off during timing batches (§3.4).

### 3.3 Record formats (version 1, little-endian, fixed size)

The formats are declared once, in `picosim/discover/formats.py`, with a
`FORMAT_VERSION`. The agent's `.equ` offsets are generated from that file,
so the two can never disagree.

**Case record (96 bytes).** It holds:
- `id`, `kind` (behaviour, timing, probe-read, probe-write or scenario) and
  flags;
- `code_off`, `code_len` and `slot_addr`;
- R0–R12, LR, PSP, NZCV, PRIMASK, CONTROL;
- `scratch_seed`;
- for probes: address, width and value.

**Result record (128 bytes).** It holds:
- `id` and `end` (fault-at-slot, fell-through, sled-landing at an address,
  SVC, or probe result);
- the stacked frame (8 words), R4–R11, the rebuilt SP, PRIMASK, CONTROL,
  MSP and PSP;
- for timing cases, the two SysTick values;
- the number of scratch words that changed, with up to 4 of them inline
  (address and value);
- an overflow flag. When it is set, the host reads the whole window for
  that case, in a re-run of that case alone.

**Batch footer:** the number of cases done, the agent checksum, the sled
checksums and the guard result.

### 3.4 What the host does around each batch

`runner.py`:
1. upload the batch;
2. set the PC to the agent's entry;
3. turn polling off for timing batches;
4. resume and wait, with a timeout;
5. read the footer, then exactly the bytes of results that were produced;
6. check the checksums;
7. save a checkpoint;
8. move to the next batch.

**Recovery ladder**, used on a timeout or a bad checksum:
1. Halt the core and read DHCSR (`0xE000EDF0`). If bit 19 (S_LOCKUP) is
   set, record a **lockup** for the case that was running; the footer says
   which one.
2. `reset halt`, reload the agent, and continue from the next case.
3. Restart OpenOCD (`target.reconnect()`).
4. Ask you to unplug the target and plug it back in, and wait for you.

A case that fails twice is recorded as such and skipped, so one case cannot
stall a whole campaign.

**Before every campaign:**
- `monitor cortex_m vector_catch none`. Otherwise DEMCR.VC_HARDERR
  (`0xE000EDFC` bit 10) would halt on every fault instead of running the
  agent's handler. This was gap 3 in the pre-build review.
- Read DEMCR back to confirm.

---

## 4. Work, step by step

Each step lists its files, what it does, its tests, and when it is done.
"Stand-in" means runs on the Unicorn stand-in with no board.

### Part I: the foundations (no board)

**S1. Debugger additions.** Files: `picosim/hwtest/target.py`,
`picosim/hwtest/rsp.py`.
- New methods:
  - `dhcsr()` returns halted/lockup/sleep;
  - `demcr()` and `vector_catch(none|all)`;
  - `poll(on)` (`monitor poll on|off`);
  - `openocd_version()`;
  - `read_mem_fast(addr, n)`, which uses the largest packet OpenOCD offers
    (from qSupported PacketSize) and re-measures throughput.
- The stand-in answers the same monitor commands.
- Tests: these calls work on the stand-in, and the existing hwtest tests
  are unchanged.
- *Done when:* the 143 tests plus the new ones pass.

**S2. Exceptions in the stand-in.** File: `picosim/hwtest/standin.py`.
- Today the stand-in stops on a fault. It needs ARMv6-M exception entry
  and return written in Python:
  - stack 8 words on the current SP, with the bit-9 realignment;
  - LR = EXC_RETURN;
  - IPSR;
  - the vector from VTOR (`0xE000ED08`, which also becomes a register);
  - exception return on a BX or POP of `0xFFFFFFFx` in Handler mode;
  - MSP/PSP switching with CONTROL.SPSEL.
- It applies to HardFault (faults, UDF, BKPT with no debugger), SVCall,
  PendSV and SysTick.
- Lockup: a fault during HardFault or during stacking sets a lockup flag,
  which DHCSR reports.
- The old "stop in a fault loop" behaviour stays for the hwtest traces,
  chosen by whether VTOR points into SRAM.
- Tests:
  - frame contents;
  - EXC_RETURN values;
  - a return to PSP;
  - lockup on a bad PSP.
- *Done when:* the agent (S4) runs whole batches on the stand-in.

**S3. Independent ARMv6-M encoding table.** File: `picosim/discover/v6m.py`.
- Written from the ARMv6-M Architecture Reference Manual (DDI 0419), not
  from `picosim_core.cpp`. It is a list of (mask, value, name, class,
  fields).
- Classes: data, flag-reading, memory, SP-writing, PC-writing,
  branch-imm, cond-branch, hint, system (CPS/MSR/MRS), SVC, BKPT, UDF,
  UNDEFINED, 32-bit-prefix.
- `safe_inputs(enc, seed)` picks register values by class:
  - memory: base registers point into ROM, or into the scratch window in B3;
  - PC-writing: register values are sled addresses, with bit 0 set or
    clear as the pass needs;
  - SP-writing: Rm is small, at most 0x100, or a valid PSP;
  - anything else: distinct values.
- Tests:
  - every 16-bit value is in exactly one class;
  - the 32-bit prefixes number 6,144 and the rest number 59,392;
  - the per-page counts in proposal §6.B are reproduced.

**S4. The agent.** Files: `picosim/discover/agent.s`, `agent.ld`,
`agent_lo.ld`, `formats.py`, and `build.py` (assembles with
`picosim.sim.assemble`, caches like `image.build_os`).
- It implements §3.2 exactly.
- Size budget: under 3 KB, so that it fits in SRAM5 beside the vectors.
- Tests (stand-in), one hand-written batch:
  - an ADDS (falls through);
  - a B to the sled (sled landing);
  - an LDR from an unmapped address (fault at the slot);
  - an SVC;
  - a STR into the scratch window (a changed word recorded);
  - an `MSR MSP` with garbage (the agent survives);
  - a timing case (two SysTick values).

  Each is checked field by field.

**S5. The runner.** Files: `picosim/discover/runner.py`, `safety.py`.
- Batching, upload, run, read-back, checkpoints and `--resume`, as in §3.4.
- Checkpoint layout:
  - `~/.cache/picosim/discover/<campaign>/<run-id>/state.json`, which
    records the cases done, the seed, the agent hash and the chip identity;
  - `batch-NNNN.bin` files;
  - `log.txt`.
- `--resume` refuses to continue on a different chip, which it checks with
  CHIP_ID and the flash unique ID once those are known.
- Determinism repeat: `--repeat` runs the same cases again, with the batch
  order shuffled from a fixed seed. `compare_runs()` lists every case where
  the two runs disagree.
- Tests (stand-in):
  - a 5-batch run that is killed after batch 2 and resumed gives results
    identical to an uninterrupted run;
  - a sabotaged case (a lockup forced through the stand-in's hook) is
    recorded and skipped;
  - a write to a never-write address in a probe case stops the run before
    upload.

**S6. The facts store.** Files: `picosim/discover/facts.py`, and the
folder `hwfacts/` with a `README.md`.
- Every fact carries its provenance: CHIP_ID, ROM version, date, OpenOCD
  version, SWD kHz, picosim commit (`git rev-parse HEAD`, plus whether the
  tree had uncommitted changes), the agent's SHA-1, the campaign and run
  id, the number of runs, and whether they agreed.
- Writes the JSON files listed in proposal §5.4, plus the replay subset
  from D4.
- Tests: provenance is complete and the JSON validates against a small
  schema.

**S7. Replay checker.** Files: `picosim/discover/check.py`, `replay.py`,
and `tests/test_hwfacts.py`.
- `replay.py` runs one case in a fresh picosim core:
  - set the registers, flags and PRIMASK;
  - load `hwfacts/rom.bin` at 0x0, so ROM reads are real;
  - put the code at the slot and run one instruction (or the timing body);
  - compare.
- **Address mapping:** until Stage 2, a table maps hardware to simulator
  addresses:
  - ROM to itself;
  - slot site to `0x9000`;
  - scratch window to `0xA000`;
  - PSP area to `0xF000`.

  Values that fall in a mapped range are compared with the same rule as
  `image.same()`. `check.py` switches to direct comparison by itself once
  the core has real regions (Stage 2).
- **What can be compared yet:**
  - registers, flags, memory, the next PC, and "faulted or not": now;
  - stacked frames, exception details and the region map: marked
    *pending: exception model* or *pending: Stage 2*, never marked as
    failures (pre-build review gap 4).
- **Timing:** `timing.cycles('m0plus', …)` compared with the measured
  value minus the batch's overhead.
- Output: `hwfacts/report.md`, with differences grouped by class (decoder,
  flags, memory, timing, faults) and each with a minimal example.
- `tests/test_hwfacts.py` runs `check` on the replay subset. It fails on a
  **new** difference. Known ones are listed in
  `hwfacts/known_differences.json`, each with a reason and a to-do. It is
  skipped when `hwfacts/` has no data.
- *Done when:* it runs on the S4/S5 stand-in output, and every difference
  it reports is one picosim and Unicorn are known to differ on.

**S8. The command line and docs.** Files: `picosim/discover/__main__.py`
and `docs/level5-discovery.md` (a user guide: how to run each campaign and
what you'll see).
- `python3 -m picosim.discover <campaign> [--target openocd|standin]
  [--resume RUN] [--repeat] [--pins N,N] [--dry-run]`.
- Campaigns: `identity`, `memmap`, `registers`, `encodings`, `timing`,
  `fuzz`, `flashed`, `peripherals`.
- Other commands: `check`, `status` (every campaign: not run, partial or
  done, with its difference count), `backup-flash`, `restore-flash`.
- `--dry-run` generates the cases and runs the safety checks without
  touching the board.

✎ Commit point: Part I complete, all on the stand-in.

### Part II: campaigns (generators on the stand-in, then board time)

**S9. Campaign 0, Identity.** File: `cases_identity.py` (the cases live in
`picosim/discover/cases/`).
- Through the debugger:
  - CHIP_ID, ROM version (0x13), CPUID, MPU_TYPE;
  - the CoreSight ROM table at `0xE00FF000` (records whether an MTB
    exists);
  - the BPU and DWT comparator counts;
  - DEMCR, and RESETS after `reset halt`;
  - the ROM dump `0x0–0x3FFF`, saved as `hwfacts/rom.bin` with its SHA-256.
- Re-measures SWD throughput and updates the estimates in this file.

**S10. Campaign A, Memory map.** File: `cases_memmap.py`.
- Builds the probe list from `addressmap.h` and the SVD
  (`~/pico-sdk/src/rp2040/hardware_regs/RP2040.svd`), plus the known holes
  (proposal §6.A).
- Each probe is read **first through the debugger** (DAP errors are
  recoverable), then through the core at 8, 16 and 32 bits. Writes are made
  only where `safety.py` allows them.
- A peripheral held in reset is tested by the debugger first, with the
  ladder ready. XIP addresses are left to F.
- Then:
  - the gap scan of the safe peripherals;
  - the SRAM striping function (tags written through the non-striped
    aliases);
  - the SRAM4/5 aliases (with agent_lo);
  - access timing per region class and width (64 repeats, with the
    calibration).
- Output: `memory_map.json`, in a form Stage 2's region tables can be
  generated from.

**S11. Campaign C, Registers.** File: `cases_registers.py`.
- The register list comes from the SVD (1,014 entries, about 1,180 with
  second instances) plus the PPB.
- The **read-side-effect list** is put together before any bulk read: the
  SVD `readAction` registers, the header `RF`/`RWF` registers, and the
  hand list (I2C IC_CLR_*, INTERP POP, DIV_QUOTIENT, UART DR, the FIFOs,
  TIMELR, the spinlocks). It is saved as `hwfacts/read_side_effects.json`
  and its registers are skipped here.
- States:
  - S1, after `reset halt`;
  - S3, after cycling each *allowed* peripheral through RESETS;
  - S2 comes from F.
- Write masks, on the allowlist only: write 0, 0xFFFFFFFF, 0x55555555 and
  0xAAAAAAAA, each through the normal address and through the XOR/SET/CLR
  aliases, then restore the original.
  - Skip the alias sweep for the **PPB and SIO**, which have no atomic
    aliases (+0x1000 is a different address there; pre-build review gap 2).
  - The Pico W pins' CTRL and PADS registers are skipped.
- Output: `registers.json`, which sets the SVD prediction beside the
  measurement and flags each difference.

**S12. Campaign B, Every encoding.** File: `cases_encodings.py`.
- Passes B1–B5, exactly as in proposal §6.B, with inputs from
  `v6m.safe_inputs`.
- BKPT is run by the host, single-stepped (it halts under the debugger).
- WFI runs with a pending interrupt that PRIMASK masks; WFE runs after SEV.
- B1–B3 are run twice (`--repeat`).
- Output: `encodings.json` and the replay subset.
- Board time is about 30 min. Uploading the cases and reading the results
  back is the slow part.

**S13. Campaign D, Timing.** File: `cases_timing.py`, plus hand-written
scenario files `picosim/discover/scenarios/*.s` for D4.
- D1: single forms × code site S0–S3 × alignment × data region.
- D2: ordered pairs.
- D3: the special instructions.
- D4 exceptions:
  - entry and exit latency, and tail-chaining, for SVC, PendSV, SysTick,
    and NVIC IRQs 26–31 pended by software;
  - frames, EXC_RETURN values and IPSR;
  - priorities and PRIMASK;
  - LDM/STM interrupted mid-way (the SysTick fire point swept across the
    instruction);
  - WFI wake latency.
- D5: polling on against polling off.
- Each timing batch runs 5 times with polling off; a value that varies is
  stored with every reading.
- Output: `timing.json` and `exceptions.json`.

✎ Commit point: campaigns 0, A, C, B and D captured.

**S14. Campaign E, Random programs.** Files: `cases_fuzz.py` and
`shrink.py`.
- Random blocks of 8–64 instructions, built from the encodings B found to
  be defined. Memory accesses stay in the scratch window; branches go
  forward or form loops with a fixed bound.
- Seeds are fixed, 1,000 blocks per upload.
- Each block is compared with picosim straight away (this is the one
  campaign that compares while capturing, so it can shrink).
- A block that differs is shrunk on the chip by delta debugging. The
  minimal program is written to `hwtests/e###_<what>.s` as a trace test
  with `@ Kind: trace` and an `@ About:` line naming the difference.
- Runs overnight with `--resume`.

**S15. Campaign F, Flashed programs.** Files: `picosim/discover/flashguard.py`,
`cases_flashed.py`, and `picosim/discover/fw/probe_main.s`.
- `flashguard.py` implements proposal §7.1 step by step and refuses to go
  on if any check fails:
  - identify the flash (`flash probe 0`, `flash info 0`, 2 MB expected);
  - **backup A**: `flash read_bank 0` over SWD;
  - **backup B**: `picotool save --all` in BOOTSEL mode, which needs **you**
    (see §6);
  - the two SHA-256s must match;
  - read the flash unique ID (`picotool info -a`);
  - store the backup twice, plus a `.uf2` that is decoded back and checked
    against the `.bin`;
  - **restore**: `flash write_image erase`, then `verify_image`, a full
    read-back with matching SHA-256 (both printed), and a boot check.
  - `restore-flash` refuses a backup whose unique ID differs from the
    board's. If a backup exists without a confirmed restore, every
    `discover` command refuses to start until the restore is done.
- The probe firmware: picosim's real build (`picosim/CMakeLists.txt` and
  `wrapper_main.c`) with `asm_main` = `bkpt #0x42`.
- In the halted S2 state:
  - dump the safe registers (C);
  - measure clk_sys/ref/peri/usb/adc/rtc with FC0;
  - the SSI/XIP setup, the enabled NVIC interrupts and SysTick;
  - core 1's state, through an OpenOCD session with both cores (no
    `USE_CORE 0`).
- XIP cache: miss and hit costs, line size, ways and replacement
  (confirmed with XIP_CTR_HIT and XIP_CTR_ACC), the NOALLOC and NOCACHE
  aliases, FLUSH, and code run from flash. Each is measured with core 1
  running and with it halted.
- Interference: a timed SRAM loop run many times, giving the histogram of
  USB and stdio interrupts.
- Real time: cycles per TIMER µs.
- Then **G's clock-dependent parts** (UART, ADC, RTC, TIMER need clk_peri
  and the watchdog tick, which are off at reset; pre-build review gap 1).
- The two old checks, if they are still open: 01_hello prints after
  Flash, and skipping FUNCSEL leaves the LED dark.
- **Restore and verify.** F is not finished until this passes.
- Output: `environment.json`.

**S16. Campaign G, Peripheral behaviour (core, GPIO and SIO part under
D6).** File: `cases_peripherals.py`.
- SIO: the divider (edge cases, divide by 0, random cases, the 8-cycle
  result timing, DIRTY and READY), the interpolators against the datasheet
  model, FIFO status with core 1 parked, and the spinlocks.
- GPIO, with no wiring:
  - output-to-GPIO_IN latency through the pin's own pad (only on `--pins`
    pins);
  - PROC_IN_SYNC_BYPASS;
  - the OUTOVER/OEOVER/INOVER overrides;
  - the INTR edge and level bits;
  - pull levels on pins that are not connected.
- SysTick: COUNTFLAG, reload and CALIB.
- TIMER and UART run only within F's flashed state, and only if D6 says so.
- Output: `peripherals/*.json`.

**S17. Campaign H, Second chip.** No new code: a `--chip-label` option and
a `compare-chips` report.
- You swap the boards (§6). Then rerun 0, A, C (S1), B1 with a 10 % sample
  of B2–B5, and D.
- Any difference becomes a chip-revision fact.

### Part III: access and use

**S18. Seeing the facts in the GUI.** Files: `gui/scripts/testing_window.gd`,
`picosim/hwtest/session.py`, `picosim/server.py`, and the guide's Testing
section.
- A third group in the Testing window's tree: **"Hardware facts (Level 5
  discovery)"**. Each campaign shows its status: not run, partial (n of m
  batches), or done. A done campaign also shows its number of differences
  from picosim.
- Selecting a campaign shows:
  - its summary;
  - its provenance;
  - its differences, each with a minimal example and **Open in picosim**
    (which loads an E/B case as a program).
- **Run** and **Resume** start the same code the CLI uses, and progress
  arrives through the existing session queue.
- Campaign F opens a confirmation that lists the backup and restore steps,
  and it cannot start without both backups.
- Everything stays reachable from the CLI.
- Screenshots in Dark and Light at Levels 2–4.

**S19. Using the facts in picosim.** Triage from `hwfacts/report.md`:
- **Decoder, flags or memory differences:** fixed in `picosim_core.cpp`.
  Each fix gets a regression test made from the fact.
- **Timing:** `timing.py` constants and `docs/cycles.md` gain "measured"
  annotations. The textbook-based multi-cycle machine is not changed (R5).
- **Registers:** the reset values and masks in `gpio.py`, and the stand-in.
- **The memory map:** `memory_map.json` becomes the input to Stage 2's
  region tables (weekly plan, Weeks 1–3).
- **Exceptions:** `exceptions.json` becomes the specification and the test
  data for Stage 4's exception model. The *pending* facts in `check` turn
  into real comparisons as each piece lands.
- **Before any change a student would see** (a number in a panel, the
  guide, the heat map), I show you the list and wait for your approval.

✎ Commit point after each triage batch.

---

## 5. Tests added in total

| File | What it checks |
|---|---|
| `tests/test_discover_v6m.py` | Encoding classes cover the space exactly; the counts match |
| `tests/test_discover_agent.py` | Agent batches on the stand-in, the end kinds, the agent surviving garbage in MSP, lockup recovery, resume after a kill |
| `tests/test_discover_safety.py` | Every never-write address and the Pico W pins are refused; `--pins` is honoured; a dry run of every campaign passes the safety checks |
| `tests/test_discover_flashguard.py` | The backup and restore logic, run with fake OpenOCD and picotool commands: a mismatch stops it, an ID mismatch refuses, an unfinished restore blocks other commands |
| `tests/test_hwfacts.py` | Replays the committed facts in picosim; fails on a new difference |
| `tests/test_discover_timing.py` | D's cases (five runs each, calibration in every batch, bodies keep R6/R7), picosim's predictions for hand-worked bodies, D3 and a set of D4 scenarios on the stand-in |
| `tests/test_discover_peripherals.py` | The divider and interpolator models against hand-worked datasheet values, G's cases against the safety rules (no pin driven, the Pico W pins untouched, side-effect reads declared), G on the stand-in |
| `tests/test_discover_fuzz.py` | E's blocks are safe; a planted one-cycle difference is found and shrunk to at most 2 instructions |
| `tests/test_discover_flashed.py` | F on the stand-in; the flash is restored after a full run and after a measurement fails part-way (fake guard) |
| `tests/test_discover_gui.py` | The Testing window's backend commands (status, run, detail, open case) |
| Existing 143 | Unchanged and still passing (235 in all on 2026-11-04) |

---

## 6. Board sessions: what you do and how long it takes

| Session | When | You do | I run | Board time |
|---|---|---|---|---|
| α | After Part I | Wire the probe as for Testing; tell me the jumper's ends (D3) | Campaigns 0, A, C (S1, S3) | ~30 min |
| β | After S12/S13 are written | Nothing beyond leaving it connected | B (with the repeat), then D | ~50 min |
| γ | Any evening | Leave it connected overnight | E with `--resume` | overnight |
| δ | After S15 is written | (1) Hold BOOTSEL and plug in the **target's** USB for backup B. (2) Plug it in normally again. (3) At the end, watch for its USB serial port: the boot check. | F (backups, flash, measure, G's flashed part, restore and verify) | ~40 min |
| ε | When convenient (D7) | Swap the boards: debugprobe firmware on the other Pico | H | ~1 h |

At the start of every session I tell you exactly which cables go where,
and at the end I say clearly that the board is free.

---

## 7. How I verify each step

- `python3 -m unittest discover -s tests`: all pass, every step.
- `python3 -m picosim.hwtest --target standin`: the hwtest suite still all
  identical.
- Every campaign runs end to end on the stand-in (`--target standin`)
  before its board session.
- After each board session: `discover status`, then `discover check`, then
  read `report.md`.
- Determinism: the `--repeat` runs show 0 disagreements. Any disagreement
  is investigated before the fact is accepted.
- Godot: load the project headless with no script errors, then take
  screenshots for S18.

---

## 8. Risks and what I do about them

| Risk | Response |
|---|---|
| Unicorn's M-profile exception support is too thin for S2 | Write exception entry and return entirely in Python around Unicorn's single steps; the stand-in already does this for faults. |
| OpenOCD rejects some monitor command (`poll off`, `vector_catch`) | Fall back to direct register writes: DEMCR through `M`, and turn polling off by not issuing `qXfer` and status queries during a run. Note it in the facts' provenance. |
| A peripheral in reset hangs the bus | The ladder: DAP abort, then reconnect, then power cycle. The address is recorded as "hangs", and the core never probes it. |
| Results larger than estimated | The batch size adapts to the footer count. The record size is fixed, so the read-back time can be predicted. |
| Stage 2 lands halfway through | `check.py` switches mapping by itself. Captures don't change. |
| Measured facts disagree with the textbook | Levels 1–4 keep the textbook (R5). Level 5 and the RP2040-model column show what was measured, with the source shown. |

---

## 9. Progress tracker

Built 2026-11-04. "Stand-in" = runs end to end against the Unicorn
stand-in with tests; "board" = still needs its session in §6.

- [x] Decisions in §1: the defaults were used (no pin driven, D4 replay subset, R5 = No)
- [x] S1 Debugger additions (`target.py`: dhcsr, demcr, vector_catch, poll, openocd_version, read/write_mem_fast)
- [x] S2 Exceptions in the stand-in (frames, EXC_RETURN, priorities, PRIMASK, WFI/WFE, lockup, VC_HARDERR)
- [x] S3 Encoding table (`v6m.py`, group counts as in the proposal)
- [x] S4 Agent (1,008 bytes; `agent.s`, `formats.py`, `build.py`)
- [x] S5 Runner and safety (checkpoints, resume, repeat, recovery ladder, isolation, full-window re-runs)
- [x] S6 Facts store (`facts.py`, `hwfacts/`)
- [x] S7 Replay checker and test hook (`replay.py`, `check.py`, `tests/test_hwfacts.py`)
- [x] S8 CLI and user guide (`python3 -m picosim.discover`, docs/level5-discovery.md) ✎
- [x] S9 Campaign 0 (code; stand-in) — [ ] session α
- [x] S10 Campaign A (code; stand-in) — [ ] session α
- [x] S11 Campaign C (code; stand-in) — [ ] session α
- [x] S12 Campaign B (code; stand-in) — [ ] session β
- [x] S13 Campaign D (code; stand-in: D1–D5 run end to end, `tests/test_discover_timing.py`) — [ ] session β ✎
- [x] S14 Campaign E (code; stand-in, shrinking proven with a planted difference) — [ ] session γ
- [x] S15 Campaign F (code and flashguard; flashguard tested with fakes, F's measurement code on the stand-in) — [ ] session δ
- [x] S16 Campaign G (code; stand-in, with a datasheet model of the divider, interpolators and spinlocks in `interp_model.py`; TIMER/UART in `flashed_part()`, called by F; `tests/test_discover_peripherals.py`) — [ ] sessions α and δ
- [x] S17 Campaign H (`--chip-label`, `compare-chips`) — [ ] session ε
- [x] S18 GUI access (Testing window: Hardware facts group) ✎
- [ ] S19 Triage and fixes: needs real-chip data; the stand-in's findings are listed for approval in the hand-over

Found while building (on the stand-in, before any board time).

How the plan changed in the building:
- D4's scenarios are built in Python (`timing._Asm`: a few Thumb
  encodings, labels and a literal pool), not hand-written
  `scenarios/*.s` files: each scenario's handlers sit at addresses known
  only once its slot is, so generating them was simpler and safer.
- E writes a timing difference as a `@ Kind: bench` test (a trace cannot
  show cycles); other differences stay `@ Kind: trace`.
- G's TIMER and UART part is `peripherals.flashed_part()`, which F calls
  while its firmware is halted, with F's FC0 clock readings (only the
  clk_sys/clk_peri ratio is used).
- `--stages` serves every campaign: each takes only its own stages (B…, D…)
  and refuses an unknown one of its own. A run with `--stages` or `--limit`
  leaves the campaign "partial", never "done".
- A Stop during a batch now ends the run cleanly; that batch runs again on
  resume.

Bugs the stand-in caught in my own code (fixed, with tests):
- The agent restarted the SysTick watchdog before refilling the 4 KB
  scratch window, so the refill tripped it on each batch's first case (B5
  hung waiting on timeouts). The SysTick restart and the clearing of
  pending exceptions are now its last steps before a case.
- A timing store into SRAM4 landed inside the SRAM4 code site's sled (the
  sled check caught it): data addresses now sit outside every code site.
- E compared blocks that used R4 (the memory base) as an ALU source,
  though chip and picosim addresses differ: R4 is now only ever a base.
  Timing replay ran with an empty scratch window while the chip's was
  filled (loads steer branches): replay now uses the same pattern. E's
  shrinker re-ran with the default 64-word window instead of E's 160.
  A systematic difference made every block shrink: each cause is now
  shrunk once and blocks it explains are counted (`explained()`).
- The replay's map put the slot at picosim 0x8000 whatever its offset in
  its word, so with a halfword-aligned slot every address near it lost its
  alignment (word loads faulted in picosim, PC-relative literals moved):
  100 of D1's rows had no prediction. The map now keeps the offset.

The stand-in itself (fixed or recorded):
- Unicorn's Cortex-M0 model runs some ARMv7-M encodings (Thumb-2 data
  processing, CBZ, IT); the stand-in now faults on anything `v6m.py` calls
  undefined.
- Unicorn raises an exception for YIELD; the stand-in now runs YIELD and
  the unallocated hints as NOPs (ARMv6-M), and charges WFE/WFI 2 cycles
  (Table 81), not 1.
- Its exception model is used only once VTOR points into SRAM (Testing's
  traces expect the old fault loop), and DEMCR.VC_HARDERR starts set, so the
  runner sends `cortex_m vector_catch none`, as on the chip.
- It has a model of the SIO divider, interpolators, spinlocks and FIFO_ST
  (`interp_model.py`, written from the datasheet and the SDK headers), so G
  agrees with its own model there by construction: on the stand-in that
  checks G's case code and decoding, not the chip.
- Limits that only the chip can settle: code cannot run from the
  non-striped alias (Unicorn cannot execute from callbacks), so D1's site
  S3 reads "not measured"; LDM/STM/PUSH/POP are atomic in Unicorn, so D4's
  "interrupted" scenarios show "completed" where Arm predicts "abandoned
  and restarted"; exception entry/exit cost its guesses (15/13 cycles);
  its GPIO is picosim's model, so G's 30 GPIO differences on the stand-in
  are picosim's gaps (below), not facts.

Found on the board (2026-11-05, the first real memory-map run):
- Every timed run (A's memmap-timing, D, E's rounds, G's timed stages)
  ended its first batch with "the agent stopped between cases (state
  done, case N of N, timeout)". Timed batches turned OpenOCD's background
  polling off for the whole run, but OpenOCD learns of a halt only by
  polling, so the agent's final BKPT went unseen until the timeout's
  interrupt. Now `Target.run(quiet=…)` keeps polling off only for a quiet
  time (0.25 s + 2 ms a case, doubled up to 4 s when a batch outlasts it)
  and then turns it on while the core runs. OpenOCD 0.12 answers the
  `poll on` at once and polls before its OK, so the stop reply can come
  first, and the client takes it either way. A batch whose agent reached
  "done" now counts as finished even if its halt went unseen. The
  stand-in had recorded `poll off` without acting on it; it now holds the
  stop reply as OpenOCD does, and `tests/test_discover_agent.py` covers
  both. On the board: memmap 4/4 timed batches in about 2 s; a repeat
  agrees on every cycle count (its 4 + 44 value differences are loads of
  uninitialised SRAM: power-up contents in the first run, the first run's
  leftovers in the repeat).

Found on the board (2026-11-10, campaign C's write masks):
- A debugger write to TIMER's TIMEHW through its SET alias (0x40056000)
  is refused by the bus: OpenOCD re-reads the DP and answers E0E. The plain
  write (0x40054000) and the XOR alias (0x40055000) are accepted. C used
  to stop on that one write, on every Run and Resume ("Could not finish:
  writing memory at 0x40056000 failed"). Now a refused write is recorded
  as `'bus error'` in that register's masks, listed in the fact
  `registers.refused_writes`, and the sweep goes on; a write with no
  answer (a hang: reconnect, so the chip is reset) still stops the run.
  The stand-in does not map TIMER (its reads there fail, so C skipped it
  as "not readable"), which is why this never showed before the board;
  `tests/test_discover_resets.py` (RefusedWriteTest) covers it with a fake
  target. Why the SET alias in particular is refused is not yet known (CLR
  is the next write; the next board run says whether it is refused too).
  The next run: SET and CLR are both refused, for TIMEHW and TIMELW.
- That first complete run reported 128 differences from the SVD; 109 were
  the comparison's own mistakes, now fixed (`registers.compare_row`):
  S3 reset values were compared for blocks C never resets (CLOCKS, XOSC,
  PLL_SYS, USB, IO_QSPI, the PPB…: they hold what the boot ROM and OpenOCD
  set), and S1 values (after `reset halt`: the last program's leftovers,
  e.g. PWM counters still running) were compared as reset values; writable
  bits were compared for write-only, write-1-to-clear and self-clearing
  fields, which the SVD marks (oneToClear, the headers' SC/WC) but
  `predicted_mask` counted as writable (`svd.not_stored_mask`); and a field
  with a minimum (I2C HCNT ≥ 6, LCNT ≥ 8, SPKLEN ≥ 1: writing 0 reads the
  minimum) looked partly read-only, because only the 0 and all-ones
  patterns were used (`registers.writable_bits` now uses all four). The
  window had also called them "differences from picosim"; C compares the
  chip with the SVD, and picosim models none of these registers, so they
  are now "differences from the datasheet (SVD)", in yellow.
- The 19 that are real (chip vs SVD; none touches picosim):
  BUSCTRL BUS_PRIORITY_ACK resets to 1 (SVD 0); I2C0/1 IC_RX_TL and
  IC_TX_TL saturate at 15 (SVD: 8 bits); IC_ENABLE.ABORT clears itself (SVD
  does not say so); IC_DMA_TDLR/RDLR hold no written value at all (why is
  not yet known); IO_BANK0 INTR0–3's LEVEL_LOW bits read 1 after reset
  (they mirror the pins, which are low); TIMER DBGPAUSE bit 0 is writable
  but undocumented; the M0+ MPU is smaller than the SVD says (MPU_RNR 3
  bits, RASR without TEX, RBAR's REGION reads RNR).
- The same day's B/D/E/G runs:
  - D1–D3: every one of the 232 single forms and 1600 ordered pairs took
    exactly the cycles picosim's timing.py predicts. D's 27 differences
    are all D4's, exception entry against the Arm TRM: SVC 13 cycles,
    PendSV and IRQ26–31 12, the TRM's 15 (picosim has no exceptions yet:
    Stage 4). D now counts these apart (status `picosim_differences`,
    `compared_with`), and the window reads exceptions.json too (it read
    only timing.json, so none of the 27 was listed).
  - E: no differences.
  - G: 48 interpolator cases where the chip and `interp_model.py` (the
    stand-in's datasheet model) disagree: after a POP the chip's
    accumulator has bits 29/28 set (e.g. ACCUM1 0x3000000B, model
    0x0000000B), plus PEEK values that follow from it. Not yet explained;
    the model is the suspect. The window never listed them (they are in
    `interp_differences`, and G's status gives a list of files, which made
    the window's detail fail: "stat: path should be string … not list").
  - F's flash backup stopped at `flash info 0` (E74): OpenOCD 0.12 prints
    the bank, then fails the rp2040 driver's missing protection check.
    flashguard now reads the bank with `flash banks`.
  - D4's 31 "not as expected" were the comparison's (re-scored from the same
    run: 0 of 174): entry latency was compared with the TRM's 15 cycles,
    which start at the interrupt signal, while D4 starts at the end of the
    trigger store (now recorded, not compared: 12 cycles, SVC 13); the
    stacked PC, "taken 1 instruction after CPSIE" and "preempted after 2"
    expected a pend to act at once, which the ARMv6-M ARM only guarantees
    after DSB/ISB (the chip: 2 NOPs later; now each its own fact, and any
    PC in the slide is a valid frame); the disabled-IRQ check compared the
    whole ISPR (the boot ROM leaves XIP and SIO FIFO IRQs pending), now bit
    26 alone; the interrupted LDM/STM/PUSH/POP compared a sentence with a
    table, now a derived verdict (all four: abandoned and restarted).
  - B1 batch 27: ADD SP,SP (0x44ED) doubled SP to 0x4008xxxx, the next
    exception could not stack, and the core locked up (OpenOCD: "clearing
    lockup after double fault"; the run stopped "between cases"). It, ADD
    SP,PC and MOV SP,PC (v6m's sp_result None) are now never run
    (`encodings.SP_UNCONTROLLED`, fact `encodings.never_run`), and the
    runner treats OpenOCD's lockup message as that case locking up (one
    retry, then recorded) instead of stopping. The stand-in has no lockup.
  - BOOTSEL was held on both Picos, which stops the probe's firmware: every
    BOOTSEL prompt and the Flash backup window now say which Pico is which.
  - Stand-in test runs pruned the board session's log while it was open
    (both in hwtest/logs, newest 20 kept); stand-in logs now go to
    hwtest/logs/standin.

Safety additions:
- The PADS_BANK0 SWCLK/SWD pads and VOLTAGE_SELECT are never written;
  branches to SP (MOV PC,SP, BX SP, BLX SP) are never run; POP {PC} is
  preceded by a store that gives it a known target; XIP_AUX is treated as a
  FIFO (a read has a side effect), not a hole.
- G's and D's tests watch every store the core really makes (a Unicorn
  hook), not only the declared ones, and pass each through `check_write`.

Proposed for S19 (nothing changed in picosim yet; each waits for the
chip's data and your approval, as S19 says):
1. picosim has one SP: MSR MSP changes the SP in use. The chip has MSP and
   PSP (Thread mode on PSP when CONTROL.SPSEL = 1).
2. picosim faults at a BX/BLX/POP to an even address, at the branch; the
   chip completes the branch and faults (INVSTATE) at the target.
3. GPIO details picosim does not model: INOVER (invert/force) in GPIO_IN,
   IO_BANK0 GPIOn_STATUS and INTR0–3, the 2-cycle input synchroniser and
   the pull's rise time, SYSCFG PROC_IN_SYNC_BYPASS, and the bus keeper
   (pull-up and pull-down together).
4. SIO blocks picosim does not have: the divider (8-cycle result,
   DIRTY/READY), the interpolators, the spinlocks and FIFO_ST.
5. SysTick in picosim's own core (now only in the stand-in), with
   COUNTFLAG cleared by a read, RVR = 0 and the 24-bit masks as G measures
   them.
6. Whatever D, F and G measure that differs from `timing.py` (Table 81,
   with APB loads 4 and stores 5 already measured): "measured" notes in
   `timing.py` and `docs/cycles.md`, the textbook machine unchanged (R5).
