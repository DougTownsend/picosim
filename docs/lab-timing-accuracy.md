# Lab: timing and accuracy

How many cycles does a branch take on the RP2040, and how sure can you be?
In this lab you count what runs in a small loop, predict what branches
cost, check picosim against the stand-in, and then look at the
measurements recorded from a real chip. You need no board: everything runs
on your computer from the picosim folder.

You will learn to:

- say exactly which instructions a count includes (startup, OS calls, the
  halt);
- say what "identical" compares, and what it does not;
- predict taken and not-taken branch costs from the datasheet;
- tell a measurement from a model, and compare quantities that are really
  the same;
- say which claims hold for code in SRAM, which for flash with a warm
  cache, and which are only about the teaching machines.

Time: about an hour. Hand in the answers marked **Q** and the two reports
you export.

## 0. Set up

Work on copies, so nothing in the picosim folder changes. From the picosim
folder:

```bash
mkdir -p ~/timing-lab/out
cp examples/03_count_loop.s hwtests/b03_branch_timing.s ~/timing-lab/
cd ~/timing-lab
```

The stand-in needs Unicorn (`pip install unicorn`). Check it with
`python3 -m picosim.hwtest --target standin --max-steps 1 03_count_loop.s`.

## 1. Count what runs

`03_count_loop.s` prints `12345`. Run it to the end:

```bash
picosim 03_count_loop.s --time
```

It prints `Executed 52 instructions`. The time and the instructions per
second on that line are how fast **your computer** ran the simulator. They
say nothing about how long the RP2040 would take.

**Q1.** Before reading on, count the instructions yourself from the source.
Where do the 52 come from?

<details><summary>Check your count</summary>

| Part | Instructions | Why |
| --- | ---: | --- |
| Startup (`_start` in picosim's built-in OS) | 3 | `ldr`, `ldr`, `blx r0` to call `main` |
| `main` before the loop | 2 | `push`, `movs r4, #1` |
| The loop, 5 times | 30 | `movs`, `adds`, `bl putchar`, `adds`, `cmp`, `bne` = 6 each |
| After the loop | 4 | `movs r0, #10`, `bl putchar`, `movs r0, #0`, `pop` |
| Inside `putchar`, 6 calls | 12 | `svc #2`, `bx lr` each |
| The halt | 1 | `bkpt #0` when `main` returns |
| **Total** | **52** | |

The program's own code is 2 + 30 + 4 = 36 instructions.
</details>

## 2. What "identical" compares

Now run the same program in Testing, on the stand-in, and save a report:

```bash
python3 -m picosim.hwtest --target standin 03_count_loop.s --report out/count.md
```

It reads `IDENTICAL  Complete agreement · 43 steps · the program ended`,
then `43 steps compared: 37 identical, 6 OS calls`.

**Q2.** Why 43 and not 52?

<details><summary>Check your answer</summary>

Testing starts both machines at `main`, so the 3 startup instructions are
never compared. Each call to `putchar` is **one** step: picosim runs its
`svc #2; bx lr`, and on the chip the test runner does the printing instead,
so 12 OS instructions become 6 call rows. The final `bkpt` is a step.

36 program instructions + 6 calls + 1 `bkpt` = 43. And 52 − 43 = 9:
3 startup instructions, plus 12 − 6 = 6 OS instructions folded into calls.
The report's *Normalized* line says the same thing.
</details>

**Q3.** Read the *Compared* line in `out/count.md`. Name three things a
trace compares, and one thing it does **not** compare. (Hint: a trace never
looks at a clock. "Identical" here means the same registers, flags, memory,
output and GPIO registers, not the same timing.)

**Q4.** Run it again with a step limit:

```bash
python3 -m picosim.hwtest --target standin --max-steps 2 03_count_loop.s --report out/capped.md
echo $?
```

It reads `BOUNDED  Agreement through 2 steps · step limit reached` and exits
with status 3. Why is that not reported as identical? What does
`--accept-bounded` change, and when would you use it?

**Q5.** Find the *Addresses* line in `out/count.md`. On the chip, the
program starting at `0x10000100` in picosim runs at `0x20020100`. Which
addresses do **not** change, and why does Testing move the code at all?
(See [The same addresses, moved](hardware-testing.md#the-same-addresses-moved).)

## 3. Predict branch costs

`b03_branch_timing.s` times regions between `bl bench_start` and
`bl bench_stop`. The RP2040 datasheet's Table 81 gives a branch's cost:

| Instruction | Cycles (Table 81) |
| --- | ---: |
| `B` (always taken) | 2 |
| `B<cond>` taken (`BEQ` when Z = 1) | 2 |
| `B<cond>` not taken (`BNE` when Z = 1) | 1 |
| `SUBS` | 1 |

A taken branch costs one more cycle because the Cortex-M0+'s 2-stage
pipeline has already fetched the next instruction and must throw it away.

**Q6.** Predict the cycles for these regions before you run anything:

- `BEQ taken x 32`
- `BNE not taken x 32`
- `loop of 100: SUBS + BNE` (100 `SUBS`, 99 taken `BNE`, then 1 not taken)

<details><summary>Check your predictions</summary>

32 × 2 = 64; 32 × 1 = 32; 100 × 1 + 99 × 2 + 1 × 1 = 299.
</details>

## 4. Run the model and the stand-in

```bash
python3 -m picosim.hwtest --target standin --bench b03_branch_timing.s --report out/b03.md
```

The three regions read 64, 32 and 299 in both columns. Before you
celebrate, read the lines under `cycles from:`:

- **picosim**: Table 81 cycles added up per instruction.
- **stand-in**: the emulator's SysTick, which counts **picosim's own Table 81
  model**, minus 21 cycles of measuring overhead. This is **not a
  measurement**.

**Q7.** The stand-in agrees with picosim to the cycle. What does that tell
you about the real RP2040? (Nothing: the two numbers come from the same
model, so they agree by construction. The stand-in run checks the workflow
and your arithmetic, not the chip.)

**Q8.** The *Scope* line says what a region covers. Is the `bl bench_stop`
counted? Is the 21-cycle overhead in the stand-in column? Which line of
`out/b03.md` says how the overhead was obtained, and what is its status?

## 5. The recorded physical data

The real measurements live in `hwfacts/`. Level 5's campaign D timed every
instruction form on a real Pico ("chip1") on 2026-11-10. Print the two
branch facts and where they came from (run this from the picosim folder):

```bash
python3 - <<'EOF'
import json
t = json.load(open('hwfacts/timing.json'))
facts = {f['id']: f for f in t['facts']}
for k in ('timing.D1.beq_2_taken.S0.word', 'timing.D1.beq_2_not_taken.S0.word',
          'timing.D1.overhead.empty.S0.word'):
    f = facts[k]
    print(k, 'value', f['value'], 'count', f['count'], 'raw', f['raw'])
p = t['provenance']
print(p['chip_label'], p['date'], 'run', p['run_id'], p['picosim'], t['sites']['S0'])
m = json.load(open('hwfacts/replay/timing-D1-S0-word.json'))
print('replay', m['run_id'], m['cases'], 'cases, sha256', m['sha256'])
EOF
```

You should see:

| Fact | Value | Repetitions in the body | Raw readings (5 runs) |
| --- | ---: | ---: | --- |
| `timing.D1.beq_2_taken.S0.word` | 2 | 16 | 34, 34, 34, 34, 34 |
| `timing.D1.beq_2_not_taken.S0.word` | 1 | 16 | 18, 18, 18, 18, 18 |
| `timing.D1.overhead.empty.S0.word` | 2 | 0 | 2, 2, 2, 2, 2 |

The provenance says: chip1, 2026-11-10 14:35:19, run `20261110-143420`,
picosim commit `73db057…` with uncommitted changes (`dirty: true`), code
site S0 (striped SRAM, `0x20010000`–`0x20018000`).

**Q9.** Compare equivalent quantities. The stand-in's region is 32
branches with a 21-cycle overhead already taken off. Campaign D's body is
16 branches between two back-to-back SysTick reads, whose own cost is 2.
Show that both say "2 cycles per taken BEQ, 1 per not-taken BNE".

<details><summary>Check your working</summary>

Campaign D: (34 − 2) / 16 = 2 and (18 − 2) / 16 = 1.
Stand-in: 64 / 32 = 2 and 32 / 32 = 1.
Same per-instruction cost, different harness, different overhead, different
repetition count. Only the per-instruction numbers are comparable.
</details>

**Q10.** Inspect the saved replay without a board. The replay file's
SHA-256 must match the manifest's:

```bash
shasum -a 256 hwfacts/replay/timing-D1-S0-word.bin.gz      # Linux: sha256sum
```

To replay every recorded case in picosim (a few minutes), write the report
outside the picosim folder, because by default the command updates
`hwfacts/report.md`:

```bash
python3 -B -m picosim.discover check --report ~/timing-lab/out/replay.md
```

Which datasets (manifests) does the report list, and how many cases came
from `timing-D1-S0-word`?

## 6. Which claims apply where

Every number in this lab belongs to a scenario. Fill in the last column.

| Number | Where it comes from | Applies to |
| --- | --- | --- |
| 52 instructions, 43 steps | picosim; Testing on the stand-in | Instruction counts, not time |
| `--time`'s seconds and instructions/second | Your computer running picosim | Nothing about the RP2040 |
| 64 / 32 / 299 (picosim column) | Table 81 model | Code and data in SRAM, zero wait states |
| 64 / 32 / 299 (stand-in column) | The same model, counted by the emulator | Not a measurement |
| 2 / 1 per branch | Campaign D on chip1, code in striped SRAM | **Q11.** ? |
| 160 cycles for `BEQ taken x 32` (the region's detail in the Testing window) | picosim's multi-cycle teaching machine | **Q12.** ? |
| 657 cold, 157 warm | Campaign F on chip1, code in flash | **Q13.** ? |

The last row is from `hwfacts/environment.json` (`environment.flash_loop`):
a loop of 16 `NOP`s, `SUBS` and `BNE`, 8 times, kept in **flash** and run
through the XIP cache, timed with SysTick at a measured clk_sys of
125,001 kHz. The first run (cold cache, just flushed) took 657 cycles; the
second (warm) took 157. Table 81 with no wait states predicts 157 for this
code: 2 for the SysTick reads, 2 for the `BLX`, 7 × (16 + 1 + 2) + (16 + 1 +
1) = 151 for the loop, and 2 for the `BX LR`. Check that arithmetic.

**Q14.** What does the 500-cycle difference between cold and warm tell you,
and why may you **not** say "flash code on the RP2040 costs 500 extra
cycles"? (It is one capture of one workload on one board. The capture's
provenance also says `run_id: null` and `dirty: true`: label it that way if
you quote it.)

## 7. What you can and cannot claim

- Everything recorded here is from **one board** (chip1). Before claiming
  something about every RP2040, a second board or another hardware revision
  has to agree.
- The facts files are summaries. Their full raw records are kept off the
  repository (`hwfacts/raw_index.json` lists their SHA-256), so a classmate
  can re-check the replays but not the unpublished raw records. Say so when
  you quote a fact.
- A capture made with `dirty: true` came from a picosim checkout with
  uncommitted changes. Say so too.
- The 2026-10-29 Testing run on a real Pico (see
  [Results from a real RP2040](hardware-testing.md#results-from-a-real-rp2040-2026-10-29))
  is a written summary from before results carried provenance: its target
  and calibration details beyond that summary are unknown.

## 8. What to hand in

1. Your answers to Q1–Q14.
2. `out/count.md` and `out/b03.md` with their `.json` files. Each result in
   them records its evidence (`standin`), target, run ID and time, the
   source's SHA-256, the image digest, the picosim commit, the calibration
   and its status, what was compared and the model's assumptions. The
   reports name your files by file name only, never by their full path.
3. One sentence for each of: a claim this lab supports for SRAM code, a
   claim it supports for warm-cache flash code (for this capture only), and
   a number that is only about a teaching machine.

A classmate with only this page and your exported reports should be able
to repeat everything here with no board.
