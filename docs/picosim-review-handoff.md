# PicoSim review and implementation handoff

Review date 2026-11-17 · `stage2-memory-map` · base commit `8f6d0f4c2732c15fc7592112ac092a5db7eab2b2`

## Scope and implementation order

Plan for a locally run terminal application installed from a source checkout. The primary audiences are introductory computer engineering students and researchers running or sharing RP2040 comparisons. Use the GUI appendix only if the Godot frontend stays in scope. Distributable installers and a hosted service are deferred.

| Priority | Work | Why |
| --- | --- | --- |
| P1 for classroom use | CLI-01 through CLI-04 | Make the first lesson approachable and make tracing, inspecting memory, and restarting reliable. |
| P1 for research use | RES-01 through RES-05 | Preserve evidence provenance, accurately report comparison coverage, and distinguish hardware behavior from simulator limits. |
| P2 for local runs and sharing | SEC-02 and SEC-03 | Protect local files and make research exports portable. |
| P2 for performance | PERF-01 and PERF-03 | Bound long GPIO runs and keep no-board verification practical. |
| If maintaining Godot | EDU-01 through EDU-05, PERF-02, SEC-01 | The appendix preserves the observed graphical and local-backend findings. |

## What the reviewers used

Three reviewers worked from introductory computer engineering scenarios. One started with the graphical examples and challenges, one attempted a first RP2040 timing and accuracy assignment, and one profiled common lab activity and reviewed the shared-code and desktop trust boundaries.

- GUI exercises used the real Godot 4.7.2 controls in a headless test with the Python backend, sample copies in a temporary location, and editor launching stubbed. The trial exercised Level 1 and Level 2 flows. It provides behavioral evidence. Visual rendering and real editor integration remain unverified.
- CLI and offline review used macOS arm64, Python 3.14.7, GNU Arm GCC 16.2.0, and Unicorn 2.1.4. No physical Pico was used during the review. Hardware observations come from the repository's previously recorded captures and official documentation.
- The checkout had ongoing edits and measurement files when reviewed. Findings refer to the working tree at review time. Initial CLI use refreshed existing ignored ELF outputs alongside examples and test programs. Application source and example source were left as found.
- The terminal-first follow-up ran the Hello, Count, Memory, Blink, and Echo lessons, the supplied alignment fault test, and a stand-in benchmark export using scratch copies. It checked the prompt, trace modes, symbol lookups, Ctrl+C, piped input, and output channels on the same Mac.

## Strengths to preserve

- **A small, useful terminal debugger.** The prompt can run to labeled breakpoints, single-step, disassemble around a label, print registers and flags, and inspect memory. The examples already form a progression. Hello, Add, and Count printed `Hi!`, `7`, and `12345`.
- **Faults teach a concrete hardware rule.** The alignment example explained the faulting instruction and address, showed why the access failed, and suggested byte operations or aligned data. The interactive prompt remained available for inspection, and batch mode reported failure on stderr with exit 1.
- **The research foundation is concrete.** The project already preserves recorded observations, provenance, hashed replay artifacts, known differences, and separate stand-in storage. Stepwise comparison exposes registers, flags, memory, and OS calls. The APB load/store correction is a good example of using measurements to improve a timing model. [Hardware evidence index](../hwfacts/README.md) · [Hardware Testing guide](hardware-testing.md#results-from-a-real-rp2040-2026-10-29)
- **The assembler already uses a direct argument list and atomic ELF replacement.** These properties can carry over to the private build directory proposed in SEC-02. [Assembler](../picosim/sim.py#L421)
- **The graphical trial demonstrated a teaching pattern worth reusing in terminal help.** Stepping from the character move to `putchar` connected `R0 = 72 = 0x48 = 'H'` directly to the corresponding output.

## Terminal classroom workflow

### CLI-01 · Give a student a first run and a first useful breakpoint

**Observed.** The Hello lesson ran and printed `Hi!`. Opening it interactively started at the simulator's `_start` routine with a full register dump and `ldr r0, [pc, #8]`. That is a different first line from the learner's source. The existing `b main` followed by `r` reached the first instruction of the lesson. In Count, `b loop`, `r`, and `s` correctly let the student follow R4 and the next instruction. [Install and quick check](../installation.md#quick-check) · [Interactive shell](../picosim/sim.py#L230) · [Hello source](../examples/01_hello.s)

**Change.** Put a “first five minutes” terminal walkthrough near the top of the README. Cover a repository checkout, virtual environment, interpreter-specific pip install, and a smoke run whose expected output is `Hi!`. Show `b main`, `r`, then stepping through the first character and inspecting R0. Explain which line is next, how `0x48` represents `H`, and why startup code appears. Reuse the existing examples and explanations. An opt-in start-at-main mode can use `Program.run_to_main()` if student feedback still warrants it. Keep the startup behavior documented and available. [Existing start-at-main implementation](../picosim/loader.py#L42)

**Done when.** From a fresh source checkout with the documented prerequisites, a student can print `Hi!`, stop at `main`, and connect a register value to the printed character using only the walkthrough. The path works without Godot and links to one next exercise each for branches and memory.

### CLI-02 · Resolve data labels in the memory lab and check the stored answer

**Observed.** In the interactive `04_memory.s` session, `x numbers 4` and `x total 1` rejected the labels. Looking up `0x20000000` and `0x20000010` directly worked and printed the expected words and the final value 100. The display could already name both symbols. The address parser uses a name map restricted to disassembled addresses. The exercise's existing challenge checks R2 even though its lesson goal includes the stored value. [CLI memory command](../picosim/sim.py#L327) · [Label-map construction](../picosim/loader.py#L174) · [Memory lesson](../examples/04_memory.s#L5)

**Change.** Use a lookup for defined code and data names in memory commands while retaining executable-address rules for breakpoints and disassembly. Build name lookup from ELF symbols so aliases can survive a single-name-per-address display map. Keep the coursework instructions terminal-first with commands such as `x numbers 4`, `r`, and `x total 1`. After the manual exercise works, consider an opt-in CLI challenge command that reuses the existing evaluator and checks final stored SRAM. See EDU-02 for the demonstrated false pass and memory expectation. [Symbol loading](../picosim/sim.py#L43) · [Challenge evaluation](../picosim/challenge.py#L85)

**Done when.** The stock memory example shows 10, 20, 30, and 40 by label before the run and 100 by label at the end. The five-word edit shows 150 at `total`, while a variant that only computes 150 in R2 shows the wrong stored value. Existing `b main` and `b loop` workflows still stop on code. Unknown labels and breakpoints on data yield actionable errors.

### CLI-03 · Make batch output and instruction tracing predictable

**Observed.** `--trace --run` printed assembly/load messages and `Hi!` with zero instruction rows. `--trace -n 4` printed four rows, including `_start` and `main`. The native untraced batch path bypasses the Python trace hook. Ordinary `--run` also mixes loader status and guest output on stdout. The unaligned-access lesson already returns exit 1 with its diagnostic on stderr in batch mode. [Documented command](../README.md#usage) · [Run dispatch and timing](../picosim/sim.py#L559) · [Trace hook](../picosim/cpu.py#L189)

**Change.** Honor tracing when explicitly requested while keeping the native fast path for ordinary runs. Reuse the Python trace path or provide an equivalent native callback, with the same halt and fault behavior across modes. Define a scriptable output contract, such as a documented `--quiet` option where batch stdout contains only guest stdout. Send diagnostic and trace data to documented channels or files. Label `--time` as host execution throughput and `--cycle-table` as the multi-cycle teaching table. [Teaching cycle table](../picosim/cycles.py#L1)

**Done when.** The README's trace command shows the instructions through Hello and the program still prints `Hi!`. An ordinary quiet batch run pipes exactly `Hi!\n` to another process. A script can distinguish faults using the nonzero exit and stderr, and the timing outputs name their units and model.

### CLI-04 · Let students stop, inspect, and rerun a lesson

**Observed.** During an ordinary Blink run at the interactive prompt, Ctrl+C ended PicoSim with a Python traceback. `reset` was unrecognized in the loop lesson, so another attempt required quitting and relaunching. Piping `hiq` into Echo completed, while piping `hi` ended at host EOF and kept looping until the harness stopped it. The OS call returns an EOF sentinel, and the bundled lesson only looks for `q`. [Interactive loop](../picosim/sim.py#L230) · [Run command](../picosim/sim.py#L304) · [Terminal input](../picosim/cpu.py#L223) · [Echo lesson](../examples/06_echo.s#L14)

**Change.** Make Ctrl+C during interactive execution return to the prompt at a safe instruction boundary, with current PC and stop reason. Add a small reset-from-loaded-image or reload-after-edit workflow. If both are provided, explain which recompiles and whether breakpoints persist. Resolve retained labels against the new image after a rebuild and report breakpoints that no longer resolve. Make build failure visibly preserve the last successful revision. Teach EOF in Echo and have the sample exit on it. Restore terminal settings on every input exit and document what batch cancellation returns.

**Done when.** A student runs Blink, interrupts it, inspects state, and can step or start over in the same session. After editing Hello, a successful reload runs the new source and a failed edit reports which build is active. Both `printf hiq` and `printf hi` terminate when piped into the updated Echo lesson with defined output.

## Research and hardware fidelity

### RES-01 · Attach evidence and calibration to each result when it is recorded

**Observed.** `python3 -m picosim.hwtest --target standin --bench hwtests/b03_branch_timing.s` identified the emulator at connection time. CLI result rows and the exported Markdown labeled its values `RP2040`. The GUI bench detail uses “measured on the chip” in that same stand-in mode. The stand-in imports PicoSim's timing rules for SysTick, making it a way to learn the workflow with the same timing assumptions. A stand-in run exported after disconnect produced an empty target and lost calibration. Reconnecting to a mocked second target before export attributed the earlier result to the new target. [CLI labels](../picosim/hwtest/__main__.py#L53) · [GUI timing detail](../gui/scripts/testing_window.gd#L994) · [Result capture](../picosim/hwtest/session.py#L161) · [Stand-in timing](../picosim/hwtest/standin.py#L9) · [Export](../picosim/hwtest/session.py#L377)

**Change.** Stamp each result at run time with evidence kind, target metadata, run timestamp with offset, calibration and its status, relevant source or image hash, and execution location. Model at least `standin`, `physical measurement`, and `recorded hardware replay` where those surfaces apply. Include the model assumptions and what was compared in a separate field. Generate table columns, detail headers, and Markdown from the row's captured provenance. Group mixed-target exports by run or show the target per result. Treat historical data with missing provenance as unknown.

**Done when.** Export a stand-in benchmark immediately, after disconnect, and after connecting a mocked different target. The same result carries its original target and calibration every time. CLI, Markdown, and JSON agree, and none describes a stand-in count as a physical measurement.

### RES-02 · Make the replay verdict independent of case order and empty inputs

**Observed.** `Result.add` only appends a new difference while creating a group. An ordinary synthetic comparison with a known and an unknown difference in the same `(status, class, name)` group reported two differences and zero new groups when known was processed first. Reversing the order reported the unknown group. Also, in a clean stand-in cache, `discover check --standin` printed `0 cases`, returned success, and titled the report as an RP2040 comparison. [Result grouping](../picosim/discover/check.py#L244) · [Replay discovery and report](../picosim/discover/check.py#L292) · [CLI exit status](../picosim/discover/__main__.py#L169)

**Change.** Record known and new cases independently of group creation, with separate counts and at least one reproducible case ID and example for each status. Make the exit code depend on observed new cases. Report empty input and runs without comparable evidence explicitly and return a non-success status for a requested validation. Label the evidence source and include a denominator or per-campaign counts for compared, partially compared, pending, skipped, and calibration cases. Keep failure summaries machine-readable for regression checks.

**Done when.** Both orders of the known/unknown fixture produce the same counts, show the unknown case, and fail the validation. A variation with only known differences still returns the documented status. An empty stand-in cache gives a clear next step, and a genuine recorded replay identifies its input datasets.

### RES-03 · Distinguish simulator coverage gaps from hardware faults

**Observed.** A normal load at `0x21000000` stopped the simulator with a message identifying the unimplemented non-striped SRAM mirror and then asserting that the same access would HardFault on the Pico. The datasheet describes that address as a valid SRAM alias. For Testing, code moves from `0x10000000` to `0x20020000` while data and stack addresses stay fixed. The earlier GUI trace footer misstated this mapping. [Trace footer](../gui/scripts/testing_window.gd#L984) · [Relocation implementation](../picosim/hwtest/image.py#L35) · [Fault formatter](../picosim/cpu.py#L27) · [README hardware behavior](../README.md#like-the-real-chip) · [RP2040 datasheet §2.6.2](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf#page=122)

**Change.** Expose the actual code mapping in CLI exports. Display raw and normalized PC when both are relevant. Reuse that metadata in the optional GUI. Give execution stops a reason that distinguishes a hardware-modeled fault, a simulator limitation, and a teaching stop. Update README and the Guide to use those distinctions. As a later fidelity increment, implement the four non-striped SRAM views over the same striped storage using the documented bank/word mapping. Existing replay relocation is evidence for testing the behavior. [Discovery replay mapping](../picosim/discover/replay.py#L36)

**Done when.** A trace shows `0x10000100` mapping to `0x20020100` while the stack/data addresses stay the same. Before alias implementation, the unsupported-address message identifies the model limitation. After implementation, reads and writes round-trip between aliases and the striped view across all four banks, and alignment and range failures remain correct.

### RES-04 · Give students a reproducible timing and accuracy lab

**Observed.** The learner could run the loop and the branch timing suite but had to reconcile the CLI's 52 executions with the Testing trace's 43 user trace steps, which included six OS calls. Time values are also easy to confuse across the Python wall-clock report, teaching datapath, Table 81 model, stand-in, and physical capture. The timing model explicitly assumes a cache hit for XIP and leaves SVC/BKPT without fixed timing. [Timing code and assumptions](../picosim/timing.py#L4) · [Testing limits](hardware-testing.md#what-testing-cannot-see) · [RP2040 datasheet Table 81 and §2.6.3](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf)

**Change.** Add one `03_count_loop` or branch-timing assignment that defines the measured region, predicts taken versus untaken branch cost, runs the model and stand-in, and then inspects a saved hardware replay without equipment. The 32-branch stand-in benchmark shows 64 cycles taken and 32 untaken. The independent `timing.D1.beq_2_taken.S0.word` and `timing.D1.beq_2_not_taken.S0.word` facts record two and one cycles per branch. Use the two datasets to teach how to compare equivalent quantities. Explain which count includes startup, OS calls, halt, or untimed instructions. Show an always-visible timing source and scope wherever a cycle or time value is displayed. For publishable results, export the dataset/run IDs and checksums, model version and settings, relevant addresses and alignment, calibration, clock, SRAM versus XIP/cache conditions, sample/coverage counts, and exact source or image identity. Label missing metadata. Mark captures relying on unpublished raw inputs or a dirty build accordingly, and add a second board or hardware revision before making broad RP2040-wide claims. Use the current Campaign F file as a labeled example of clock and cache conditions. It records 658 SysTick cycles cold and 157 warm for its specific eight-iteration flash loop. That observation belongs to this capture and workload. [Recorded branch timing](../hwfacts/timing.json#L730) · [Working-tree Campaign F capture](../hwfacts/environment.json#L173) · [Capture revision fields](../picosim/discover/runner.py#L205)

**Done when.** Give a classmate only the lab and its export. They can repeat it offline, identify what “identical” compares, explain the 52-versus-43 boundary, point to the recorded physical dataset, and identify which claims apply to warm-cache, SRAM, and teaching-only scenarios.

### RES-05 · Report trace limits and local capture status explicitly

**Observed.** Running Testing against a temporary copy of `03_count_loop.s` with `--target standin --max-steps 2` returned exit 0 and a headline verdict of `IDENTICAL`. The explanatory note mentioned the cap. The full run contains 43 trace rows. Separately, a clean profile's `discover status` showed every campaign as “not run” even though the checkout included recorded hardware facts and 15 replay manifests. [Trace verdict](../picosim/hwtest/lockstep.py#L392) · [CLI aggregation](../picosim/hwtest/__main__.py#L56) · [Local status lookup](../picosim/discover/campaign.py#L250)

**Change.** Give traces explicit complete agreement, bounded-prefix agreement, inconclusive, and disagreement states. Carry stop reason, number of rows compared, and OS-call/I/O normalization into visible summaries and export. Document the exit behavior for callers that intentionally accept a bounded trace. Label run status as local and expose the available published capture alongside it, with an open/replay action.

**Done when.** The capped example prominently reads “Agreement through 2 steps · step limit reached” in the CLI and export. A full run retains its own completed verdict and an early discrepancy still fails. A clean source checkout distinguishes an absent local run from evidence it can replay from the repository.

## Performance and verification speed

### PERF-01 · Bound GPIO event history during long terminal runs

**Measured.** A bounded direct `core.run(n)` of stock `05_blink_led.s` on Apple M1 Pro, with no board snapshots, left 23, 233, 1,163, and 2,326 pending GP15 history entries at 1k, 10k, 50k, and 100k instructions. The corresponding median times across three fresh clones were 1.39 ms, 11.74 ms, 96.23 ms, and 290.69 ms. Reading `gpio.pin_states()` folded the observed history to zero. Python profiling at 50k assigned 0.266 of 0.271 seconds of instrumented time to `GPIO._update` inclusively. As a secondary measure on the shared core, the earlier GUI backend's replay for Step Back took about 0.30–0.32 seconds at 100k instructions and 20.95 seconds at 1M. These measurements cover the backend, with no GUI rendering. [GPIO write path](../picosim/gpio.py#L213) · [History and advance](../picosim/gpio.py#L275) · [Rewind](../picosim/server.py#L891)

**Change.** Fold expired pad events before applying each relevant MMIO or external-input mutation. Use the old configuration for that advancement and keep events still needed by synchronization, edge latching, and delayed pull resolution. Centralize the eviction rule and preserve the ordering of pin history, IRQ events, and cycle counter updates. Profile again before changing the per-pin work. Measure ordinary terminal runs and the native execution loop again. If maintaining the GUI, rebench Step Back before adding any checkpoints.

**Done when.** A 100k-instruction software-only Blink run keeps history proportional to the current synchronization or pending-transition window without periodic pin snapshots. Compare native batched execution with small-step execution for SIO reads, INTR edge flags, edge ordering, and pending pull timing at the same cycle. Retest the existing GPIO fidelity cases and report median and tail latency at 1k, 10k, 100k, and 1M where applicable.

### PERF-03 · Keep Campaign F's no-board smoke test bounded

**Observed.** The full suite was stopped after several minutes in the stand-in run of Campaign F. The interrupt landed in the GPIO pull-latency coarse sweep, waiting for an emulated probe call. The exact total duration and individual call outcomes were unmeasured. The physical measurement intentionally sweeps many delays with both synchronizer settings. [Stand-in test](../tests/test_discover_flashed.py#L67) · [Campaign F dispatch](../picosim/discover/cases/flashed.py#L365) · [Coarse and fine sweep](../picosim/discover/cases/peripherals.py#L1824)

**Change.** Record per-stage and per-call outcomes first. For the routine no-board test, use a small representative delay set and retain separate tests for successful result, timeout, and restoration with fake transport or guards. Keep the exhaustive physical capture profile with its current sweep, repetitions, and provenance. Treat any stand-in omissions or budget exhaustion as explicit coverage status.

**Done when.** The default stand-in scenario completes within a documented test budget and reports which probes ran. The physical profile still schedules the same cases. A mocked timeout produces a bounded, readable result and the restoration regression still passes.

## Local file handling and report sharing

These findings come from source inspection and ordinary workflows. Normal simulator entry points can run locally without a GUI server.

### SEC-02 · Keep local builds bounded and their artifacts private

**Evidence.** The normal assembly path invokes GNU Arm tools with the user's permissions and inherited environment, without a subprocess timeout. Assembly can include files. The loader emits a sibling ELF and may build the bundled OS in the package directory. A read-only lesson copy failed at the sibling ELF write. [Tool invocation](../picosim/sim.py#L421) · [ELF parsing](../picosim/sim.py#L22) · [Build outputs](../picosim/loader.py#L58) · [GNU assembler includes](https://sourceware.org/binutils/docs/as/Include.html) · [GNU assembler binary includes](https://sourceware.org/binutils/docs/as/Incbin.html)

**Change.** Put simulation intermediates in a private temporary workspace or content-keyed per-user cache. Retain an explicit output path for intentional artifact exports. Use a trusted toolchain path, bounded compiler time and diagnostics, and reliable cancellation and child cleanup. Preserve supported relative includes from the chosen project. For courses that accept untrusted submissions, run both the toolchain and ELF processing in an isolated worker with access limited to the submitted project and toolchain. A future shared runner would also need per-job execution limits and hardware-free workers.

**Done when.** A trusted lesson and a project-local include assemble from a read-only directory. Simulated compile timeout and cancellation terminate cleanly, and failed builds leave any previous successful output usable. The CLI keeps its optional physical-device actions explicit.

### SEC-03 · Make CLI research exports shareable by default

**Evidence.** A stand-in benchmark exported from a scratch directory copied the absolute local assembly path to both Markdown and JSON. The same JSON already contained the compiled image digest. Discovery's normal replay report lists manifest basenames, which is a useful precedent. [Benchmark result fields](../picosim/hwtest/bench.py#L163) · [Report export](../picosim/hwtest/session.py#L377) · [Markdown source field](../picosim/hwtest/session.py#L420) · [Replay manifest list](../picosim/discover/check.py#L322)

**Change.** Make the default export use a basename or project-relative name, image digest, evidence kind, model settings, and version. Keep the local path available for execution, with explicit opt-in if needed for sharing. Follow RES-01 and RES-04 for provenance and timing scope. Apply the same policy to Heat CSV only if the GUI stays in scope. [Heat metadata](../picosim/server.py#L1297)

**Done when.** A CLI benchmark run from a nested scratch folder exports a readable source name and the original digest while omitting the absolute path. Default JSON and Markdown agree, and rerunning the file locally still works.

## Verification baseline

- **Focused software suite.** 89 tests passed on the later working-tree snapshot. It covered teaching, practice and rewind behavior, server commands, timing, live cycles, GPIO fidelity, and hardware-facts integrity. An earlier snapshot taken during the GPIO edits failed two pull-related checks. Repeating the focused suite after the concurrent GPIO edits passed. The optional full-suite run on the earlier snapshot was stopped during Campaign F's stand-in GPIO sweep, after several minutes without a completed result. See PERF-03.
- **Offline hardware replay.** A fresh run over 15 manifests processed **97,623 cases**. It reported **97,254 matches, 77 differences, 36 cases pending the exception model, and 256 skipped**. The checker reported zero new kinds under the grouping behavior described in RES-02. Its comparison notes include **2,565** cases with unset-memory fields excluded and **9,720** cases where replay drops ROM stores to match the recorded hardware behavior. Those figures describe the specific replay coverage. The older `hwfacts/report.md` contains 82,437 cases and omits B3–B5 from its source list. [Older recorded summary](../hwfacts/report.md) · [Replay interpretation](level5-discovery.md#how-picosim-is-checked)
- **Terminal follow-up.** The supported breakpoints and numerical memory inspection worked. The follow-up also reproduced the gaps in trace output, data-label lookup, Ctrl+C, and piped EOF described in CLI-02 through CLI-04, and confirmed how stand-in exports handle source paths. These were scratch smoke checks. The earlier focused suite and recorded hardware replay results above were carried forward.
- **On-device and visual scope.** USB serial, SWD, flashing, restore, live RP2040 timing, and visual rendering remain unverified by this review. The report uses source inspection, stand-in runs, stored hardware measurements, and headless desktop interactions.

Rerun these checks in a scratch copy. Use a report path outside the source tree, since the replay command otherwise updates the repository summary.

```sh
# Run from the scratch copy with the project dependencies installed.
PYTHONPATH=tests python3 -B -m unittest \
  test_teaching test_friction test_server test_timing test_live_cycles \
  test_gpio_fidelity test_hwfacts_integrity
python3 -B -m picosim.discover check --report /tmp/picosim-hardware-replay.md
```

## Appendix for the optional Godot frontend

These observations are retained for a future decision to maintain the graphical interface. The terminal priorities above can proceed independently.

### EDU-01 · Open an example where its instructions are usable

**Observed.** From a fresh Level 1 welcome screen, select **Add up a list in memory**. It opens at Level 1. The exercise says to open the Memory tab and go to `numbers`, but the tab is hidden. Raising to Level 2 reveals the tab and changes the fresh execution position from the first instruction of `main` to an OS startup instruction. A student following the lab has to discover both the level menu and why the first line changed. [Welcome actions](../gui/scripts/welcome.gd#L136) · [Level handling](../gui/scripts/main.gd#L685) · [Boot options](../gui/scripts/main.gd#L1034) · [Memory exercise](../examples/04_memory.s#L5)

**Change.** Route the welcome screen and Examples menu through the same level-aware launch. For a higher-level example show an **Open at Level 2** action with the relevant outcome, such as “Shows Memory and the stack.” Keep an explicit option to stay at the current level. Set the level before loading and offer **Go to main** or explain where to step from when startup code is visible. Add one direct link from the memory exercise to the memory guide and one from the GPIO exercises to pin setup.

**Done when.** From a clean Level 1 profile, open example 04 via either entry point, follow the offered Level 2 path, and navigate to `numbers` without looking up menu instructions. The first executable row's context is clear. Choosing to stay at Level 1 preserves the lower-level layout and still points to the missing view.

### EDU-02 · Check the memory result and mark previous build results as stale

**Observed.** In the fifth-number exercise, adding 50 and setting the count to five produces `R2 = 150`. Changing the last store to use `R0` instead writes 50 at `total` (`0x20000014` in the modified program), yet the exercise passes. In a separate Hello trial, a successful edited challenge stayed green after the next save failed assembly. The status also returned to the previous program's ordinary halted display. In a second trial, loading a different invalid file with Add paused left Run enabled. Run printed the previous Add lesson's `7`. [Exercise expectations](../examples/04_memory.s#L11) · [Challenge evaluator](../picosim/challenge.py#L85) · [Load failure](../picosim/server.py#L703) · [GUI state](../gui/scripts/main.gd#L1208) · [Challenge display](../gui/scripts/challenge_bar.gd#L45)

**Change.** Extend the challenge language with an expectation such as `@ Expect: mem32[total] = 150`. Resolve the label from the loaded image and read an aligned initialized SRAM word using a side-effect-free inspection path. Report the observed value and distinguish an unresolved label from an incorrect total. Retain the register check if it helps teach the difference between computing and storing. Track the source revision associated with the loaded image and challenge result. On failed reload, mark the previous image and result stale, keep the compiler error visible, and disable actions against the edited program until it builds. If running the last successful image is valuable, give it an explicitly labeled action.

**Done when.** The correct fifth-value edit passes, a missing store or a store of 50 fails even with `R2 = 150`, and an unresolved label yields a readable diagnostic. Introduce an ordinary typo after a green result. The students can tell which source revision failed, and a subsequent successful save clears the error and re-enables the run.

### EDU-03 · Make the first lesson keyboard usable and the step labels teachable

**Observed.** Five Tab presses on the fresh welcome screen left focus unset. The eight example buttons, Open, and Read the guide use `FOCUS_NONE`. In Say hello, one Level 1 Step over `putchar` jumped the instruction count from 2 to 5 because hidden OS instructions were counted. After a normal halt, Explain directed the student to step even though Step was disabled. The invalid edit `movs r0, #'Hello'` pointed at the correct line and offered generic punctuation/register advice. [Welcome controls](../gui/scripts/welcome.gd#L136) · [Status counter](../gui/scripts/main.gd#L1208) · [OS step-over](../picosim/server.py#L807) · [Explain state](../picosim/explain.py#L996) · [Assembler hints](../picosim/errors.py#L60)

**Change.** Give welcome actions a predictable Tab and Shift+Tab order, visible focus, and Enter/Space activation. Keep simulator shortcuts active after moving focus to the execution surface. In Level 1 show a visible-step count or explicitly label the retired count and hidden OS instructions. Make Explain respond to halt, input wait, breakpoint, and fault states. Use the existing **Your first five minutes** guide as an optional three-step exercise around the character move, printing, and Step Back. Introduce the initial `push {r7, lr}` as function-entry setup and link to the explanation for interested students. Teach that `#'H'` is one character and add source-aware advice for a multi-character immediate near the line-specific error. In the count challenge, reuse the guide's hint to predict the first value that will be compared without being printed. [First five minutes](../gui/scripts/guide.gd#L1135) · [Loop guide](../gui/scripts/guide.gd#L1424)

**Done when.** A keyboard-only student opens Say hello, runs it, views its explanation, and restarts or steps back. One click across `putchar` explains the count delta. A halted program suggests an available action and the multi-character immediate points to the one-character rule.

### EDU-04 · Reduce setup and editor friction

**Observed.** Opening a sample normally opens an external editor immediately. If the student copy cannot be created, the fallback loads and opens the distributed original. Running an assembly file creates a sibling ELF, and loading the bundled OS may create a file inside the package. Running a read-only copy failed at the sibling ELF write. The editable install and toolchain prerequisites are documented, but this test machine had `python3 -m pip` and no standalone `pip` command. [Installation](../installation.md) · [Example-copy fallback](../gui/scripts/welcome.gd#L42) · [Build paths](../picosim/loader.py#L58) · [Student assembly path](../picosim/loader.py#L119)

**Change.** Provide a built-in **Run example** path and a separate **Edit my copy** action with the destination shown. On copy failure, offer a writable location or run the read-only demo without opening the original for editing. Use the private build output from SEC-02 for read-only classrooms. Point students to the source checkout setup guide from CLI-01 and list Godot as an optional prerequisite for this frontend.

**Done when.** An intro student can get `Hi!` from a read-only examples folder, even without a configured editor. Opening an editable copy shows where it was saved. A missing tool yields a short next step and the supported verification command. Test one clean install on each platform the release advertises.

### EDU-05 · Keep level navigation from advancing the CPU

**Observed.** After pausing partway through `movs r0, r2` in Level 3, switching to Level 4 completed that instruction, changed R0 from 3 to 7, and started the pipeline counters afresh. Switching back to Level 1 with the diagram open produced Godot's “Cannot deselect tabs, deselection is not enabled” error. The level selector reads as a control for which material is visible. [Level auto-selection](../gui/scripts/main.gd#L701) · [Backend model switch](../picosim/server.py#L1136) · [Memory tabs](../gui/scripts/memory_panel.gd#L59)

**Change.** Treat levels as controls for visibility. Give the CPU model a separate action with a clear instruction-boundary rule. If the student explicitly chooses to finish an active instruction to switch models, explain that action and when the new cycle counter starts. Unhide Disassembly before selecting it and hiding Memory on the way back to Level 1.

**Done when.** Raising the level halfway through an instruction leaves registers, PC, and instruction progress intact. A model switch follows its stated boundary behavior and clearly labels its counters. Returning to Level 1 with the diagram open produces a visible Disassembly tab and no tab error.

### PERF-02 · Stream a compact walkthrough from the loaded revision

**Measured.** In five warm backend trials, the 52-row Count view took about 48 ms to build in multi-cycle mode and 58 ms in pipeline mode. A 500-row Blink view took about 98 ms and 206 ms respectively. The multi-cycle intermediate JSON representation was 4.43 MB versus 0.70 MB compact, and the pipeline intermediate was 2.37 MB versus 0.19 MB compact. Profiling assigned 0.407 of 0.540 instrumented seconds to repeated pipeline view calls. The backend rebuilds synchronously from the path on disk and discards much of the built state after producing it. After a failed edit that can also make the walkthrough disagree with the program still loaded. [Walkthrough command](../picosim/server.py#L1349) · [Walkthrough builders](../picosim/walkthrough.py#L136) · [Compaction](../picosim/walkthrough.py#L577) · [Pipeline view](../picosim/pipeline.py#L92) · [Initial image clone](../picosim/loader.py#L35)

**Change.** Generate from a clone of the committed, initially loaded image and capture its revision, execution model, input state, and requested limit. Reuse the compact row contract without first generating unused detailed snapshots. Compute full detail when requested. Send a first page or chunk quickly, support cancellation and stale-result rejection on reload or model switch, and keep the event loop able to process Pause and Step. Apply EDU-02's last-build label if a stale image is explicitly opened.

**Done when.** The first walkthrough page for 500-row Blink arrives within a documented latency budget, with p95 reported on the same host as the baseline. Opening an unchanged walkthrough causes zero assembler invocations. Compare compact rows to the old output for Count and Blink and verify Go here, branch markers, input waits, and detail inspection. A failed source revision produces a clear stale notice and a completed rebuild invalidates old results.

### SEC-01 · Give the desktop GUI a private command channel

**Evidence.** The backend binds loopback, accepts its first TCP connection, and routes commands to `cmd_*` handlers without authenticating the GUI. Its local command set includes file selection and export, serial/device control, flash and testing. The receive loop accumulates partial lines, dispatches parsed JSON, and uses blocking sends. [Listener, framing, and dispatch](../picosim/server.py#L92) · [GUI launcher](../picosim/sim.py#L493) · [Export](../picosim/server.py#L1335) · [Hardware operations](../picosim/server.py#L1505)

**Change.** Keep the desktop protocol private. Prefer a launcher-owned channel with OS access controls or an authenticated connection with a per-launch credential passed privately. Validate connection identity before exposing state or accepting commands. Use an explicit command registry with typed inputs, frame and workload bounds, and bounded queues or timeouts. Treat file and hardware actions as separate capabilities. Preserve the desktop user's file-picker and hardware workflows with explicit device actions and confirmations.

**Done when.** Integration tests confirm that a newly launched GUI connects, opens a local lesson, and exits cleanly. Unit tests with mock transports establish that an unauthenticated or expired session never invokes any handler, malformed requests fail cleanly, and a stalled peer cannot keep the worker alive indefinitely. Use fake devices to exercise flash and debug authorization.
