# Stage 5: the second core (dual-core RP2040)

**Status:** planned 2026-11-12, not started. Comes after Stage 4.
**Goal:** picosim runs both of the RP2040's Cortex-M0+ cores, as the chip
does: the same clock, shared memory and peripherals, core 1 asleep until
core 0 launches it, the SIO FIFOs and spinlocks, and bus contention
measured on the real chip. The textbook says the second core may come
"later on", and this is that.

---

## 1. Decisions (defaults used unless you say otherwise)

| # | Decision | Default |
|---|---|---|
| E1 | When | **Stage 5, after Stage 4.** It needs Stage 4's events and exceptions (WFE/SEV, NVIC, the SIO_IRQ_PROC lines) and proposal 4's SIO blocks. |
| E2 | Which levels show it | **Levels 1–2:** one core, plus a small "Core 1: asleep" badge, and only once a program uses core 1. **Levels 3–4:** core tabs and everything in §4. **Level 5:** the measured facts. |
| E3 | How students start core 1 | A `launch_core1` OS call: R0 = entry, R1 = stack top (0 means the default 0x20041000, the top of SCRATCH_X, as the Pico SDK uses). It does the real FIFO handshake, so students can step through it. Writing the handshake by hand also works. |
| E4 | What keeps the two cores in time | The RP2040 model's cycle counts (`timing.py`, Table 81 plus the measured APB numbers), never the textbook's multi-cycle counts. Each core has a cycle clock, and picosim always moves the core that is behind. Each access is placed at the cycle it really happens inside its instruction, so races come out as on the chip. |
| E5 | The multi-cycle diagram at Levels 3–4 | It animates the **selected** core's textbook states, as now. The other core moves by whole instructions when the shared clock passes it. The textbook machine keeps its own numbers (same rule as R5). |
| E6 | When a program ends | When core 0's `main` returns, as with the SDK. Core 1 is stopped where it is and its state stays on screen. |
| E7 | Console | One shared stream, as on a real Pico. At Levels 3–4 a dim `0`/`1` marker in the margin shows which core printed each line (it can be turned off). |
| E8 | Comparing with the chip | Over SWD, dual-core tests compare the final state of both cores, memory, and timed regions. The chip cannot be stopped every cycle for both cores, so no per-instruction lockstep. Single-core tests keep their lockstep traces. |

---

## 2. What the chip does (the model to build)

Each item is checked against the RP2040 datasheet's Bus Fabric, SIO and
Bootrom (core 1 launch) sections while building, and against the board in §5.

- **Per core:** registers, flags, PC, PRIMASK/CONTROL, NVIC and SysTick,
  `SIO.CPUID` (0 or 1), the divider, the two interpolators, and the event
  register used by WFE/SEV.
- **Shared:** ROM, flash/XIP, SRAM, all APB/AHB peripherals, GPIO,
  the 32 spinlocks (`SPINLOCK0–31` at 0xD0000100–0xD000017C).
- **The FIFOs:** two 8-word FIFOs, one each way. Each core sees
  `FIFO_WR` (0xD0000054) as its outgoing FIFO and `FIFO_RD` (0xD0000058)
  as its incoming FIFO. `FIFO_ST` (0xD0000050) has VLD, RDY and the sticky
  WOF/ROE error bits. A write sets the event flag of the other core, and
  SIO_IRQ_PROCn fires when data is waiting.
- **Spinlocks:** a read claims the lock (it returns non-zero if the claim
  worked, 0 if it was already held). Any write releases it.
  `SPINLOCK_ST` shows all 32 locks.
- **Core 1 at reset:** it runs from the boot ROM, sleeps in WFE, and waits
  for the launch words `0, 0, 1, VTOR, SP, entry` in the FIFO, echoing each
  one back. A wrong word starts the sequence again. picosim's ROM (empty
  today) gets our own short loop that does this, written from the
  datasheet's description.
- **Contention:** the SRAM is 4 striped banks plus 2 single banks
  (SCRATCH_X, SCRATCH_Y), each behind its own crossbar port. Two cores on
  the same port in the same cycle means one waits. `BUSCTRL_BUS_PRIORITY`
  sets priorities. SIO accesses never collide (each core has its own
  IOPORT). How equal priorities are settled, and the cost at the
  ROM, XIP and APB ports, is **measured** in §5, not assumed.

---

## 3. Build steps

Every step: all existing tests pass, the stand-in hwtest suite passes,
and single-core programs behave exactly as before (same cycles, same
traces, same screenshots).

**T1. Shared bus (refactor, nothing visible changes).**
- Move `rom`, `sram`, `flash`, and the peripheral callbacks out of
  `CPUCore` (`picosim_core.cpp`) into a `Bus` that both cores point to.
- A `System` holds `Bus` + `CPUCore[2]` + the scheduler. Core 1 is parked.
- `cpu.py` keeps its API for core 0 and adds `cores[i]`. The server
  commands take an optional `core` argument (default 0).
- Profiling (`recent`, `prof`, `itrace`, the heat tables) stays per core.
- Tests: the whole suite unchanged, plus a test that core 0 sees exactly
  the memory it saw before.

**T2. Per-core SIO, events and launch.**
- CPUID, FIFOs + FIFO_ST, spinlocks + SPINLOCK_ST, the event register:
  WFE sleeps until an event, SEV and FIFO writes send one, and WFE with the
  event already set clears it and goes on.
- WFE/WFI in `timing.py` and `state_info.gd` lose "nothing waits here".
- The ROM core 1 loop and `launch_core1` in `os.s`.
- Tests for each register, the 8-deep limits (WOF/ROE), claim and
  release, the launch handshake (including a wrong word), WFE/SEV
  ordering, and FIFO wake-up.

**T3. Scheduler.**
- Both cores on one clock (E4). No contention yet.
- `run`, `step`, "run to main", breakpoints and stop-on-fault work with two
  cores. A fault on either core stops the run and names the core.
- Tests: two cores that never share a port give the same cycle counts
  as each core alone. A shared counter race gives the same result on
  every run.

**T4. Board: campaign I (§5).** Measure, then build the contention model.

**T5. Contention model and dual-core Testing.**
- The arbiter in the scheduler, from campaign I's facts. "Measured" notes
  in `timing.py` and `docs/cycles.md`.
- Testing window: dual-core tests (`@ Cores: 2` header). OpenOCD runs both
  cores (`USE_CORE` unset, gdb ports 3333 and 3334). The runner loads
  core 1's entry through the launch handshake as a real program would.
  The verdict compares the final state, memory, and timed regions (E8).
- The stand-in gets a second Unicorn core that shares memory, using
  picosim's arbiter. Like everything else it uses, this agrees with picosim
  by construction.

**T6. GUI (§4).**

**T7. Teaching text and examples (§4), screenshots, commit.**

---

## 4. What students see

- **Core tabs** (`Core 0 ●  Core 1 ○`, filled = running, hollow = asleep
  in WFE) above the datapath diagram. The diagram, Registers and the
  walkthrough follow the selected tab.
- **Code view:** two PC arrows, blue for core 0 and orange for core 1.
- **Memory tab:** shared. A cell flashes in the colour of the core that
  touched it. "This step read …" names the core.
- **SIO panel (new):** the two FIFOs drawn as 8-slot queues with their
  words, FIFO_ST bits, and 32 spinlock lights that show which core holds
  each lock.
- **Timeline lanes:** one lane per core, per cycle: running, waiting for
  the bus (contention), or asleep (WFE).
- **Step** moves the shared clock. "Step core 1 to its next instruction"
  follows one core.
- **Heat map:** a layer per core (core 0, core 1, both).
- **Explain this step:** FIFO, spinlock, WFE/SEV and contention
  explanations, with the values from the run.
- **Guide pages:** "Two cores", "Starting core 1", "FIFOs", "Spinlocks and
  races", "When cores share memory" (Levels 3–4; a short version for
  Levels 1–2).
- **Examples:**
  1. `two_cores_hello`: each core prints its CPUID.
  2. `race`: both cores add 1 to a shared counter 1000 times with
     LDR/ADDS/STR, and the total comes out short.
  3. `race_fixed`: the same with a spinlock, and the total is 2000.
  4. `fifo_pingpong`: a number passed back and forth, with each core
     adding 1.
  5. `producer_consumer`: core 1 computes and core 0 prints.
  6. `bank_conflict`: the same loop on striped SRAM and on one bank,
     timed, to show contention.
- The Pico board view is unchanged (GPIO is shared). A pin written by
  core 1 shows the same as one written by core 0.

---

## 5. Board session: campaign I (dual core)

The same wiring as Testing. Nothing is flashed. Before it runs:
`preflight`. Then about 1 hour.

- The Level 5 agent runs on **both** cores (SWD multidrop: core 1 is its
  own debug target).
- What is measured:
  - The launch handshake: words, echoes, and the cycles from the last
    word to core 1's first instruction.
  - FIFO: write-to-VLD latency, full and empty behaviour, WOF/ROE.
  - WFE/SEV: wake-up latency, and an event sent before the WFE.
  - Spinlocks: claim and release timing, and two cores claiming in the
    same cycle.
  - Contention: both cores loading or storing, same bank vs different
    banks, striped vs SCRATCH_X/Y, ROM, XIP (cached and uncached), APB,
    each with BUS_PRIORITY = equal, core 0 high, core 1 high.
  - The race example's final count on the chip vs picosim.
- Output: `hwfacts/dual_core.json`, compared in `check` and shown in the
  Testing window's Hardware facts group.

---

## 6. Schedule

The dates for Stages 3–4 are estimates (Stage 2 finished early, so
Stage 3 moves up a week). They move if a stage takes longer.

| Weeks | Dates (2026–27) | Work |
|---|---|---|
| — | Nov 12–15 | Level 5 board work (B, G gpio-bypass, old checks), commit, merge |
| 3–5 | Nov 16 – Dec 6 | Stage 3: inside views (Thanksgiving week light) |
| 6–9 | Dec 7 – Jan 10 | Stage 4: TIMER, SysTick/NVIC/exceptions, UART, SIO blocks (holiday slack included) |
| 10 | Jan 11–17 | Stage 5 T1 (shared bus refactor) |
| 11 | Jan 18–24 | T2 (SIO, events, launch) + T3 (scheduler) |
| 12 | Jan 25–31 | T4 board session (campaign I) + T5 (contention, dual-core Testing) |
| 13 | Feb 1–7 | T6 GUI |
| 14 | Feb 8–14 | T7 teaching text, examples, screenshots, commit |

---

## 7. Risks

- **The refactor touches every memory access.** T1 changes nothing
  visible and is checked against the full test suite and the stand-in
  hwtest suite before anything else is built on it.
- **Speed.** Two cores with per-access interleaving may be slower to run.
  When core 1 is asleep, the scheduler runs core 0 alone at today's speed.
  `run` is measured before and after T3.
- **Contention we cannot explain.** If the chip's arbitration does not
  follow a simple rule, picosim keeps the measured table and says so in
  the guide, rather than guessing.
- **Debugging core 1 over SWD.** OpenOCD's two-core RP2040 setup is new
  to this project. Campaign I starts with a trial: halt, read and resume
  core 1 only.

---

## 8. Progress tracker

- [ ] E1–E8 confirmed
- [ ] T1 Shared bus
- [ ] T2 SIO, events, launch
- [ ] T3 Scheduler
- [ ] T4 Campaign I on the board
- [ ] T5 Contention model, dual-core Testing
- [ ] T6 GUI
- [ ] T7 Teaching text, examples, commit
