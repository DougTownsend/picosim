# Level 5 discovery: measuring the real RP2040

Level 5 is meant to show only what is true of a real RP2040. Discovery is
how picosim finds that out: a small **agent** runs on the chip, works
through thousands of test cases at full speed, catches every fault itself,
and leaves its results in SRAM for the computer to read back in bulk. The
results are kept in [`hwfacts/`](../hwfacts/README.md), and picosim is
checked against them on every test run, with no board attached.

The design is in [level5-discovery-proposal.md](level5-discovery-proposal.md)
(why) and [level5-discovery-plan.md](level5-discovery-plan.md) (what, in
order). This page is how to run it.

---

## What you need

The same as for [Testing](hardware-testing.md): the target Pico (a Pico W
here), a second Pico running the debugprobe firmware, three SWD wires (probe
GP2 → target SWCLK, probe GP3 → target SWDIO, GND → GND), and OpenOCD.

Campaign F also needs **picotool** (it makes the second, independent flash
backup over USB) and you, once, to hold BOOTSEL while plugging the target in.

## Running a campaign

```bash
python3 -m picosim.discover identity            # 0: which chip, ROM dump (1 min)
python3 -m picosim.discover memmap              # A: the memory map (~5 min)
python3 -m picosim.discover registers           # C: every register (~5 min)
python3 -m picosim.discover encodings --repeat  # B: every encoding, twice (~1 h)
python3 -m picosim.discover timing              # D: cycles and exceptions (~15 min)
python3 -m picosim.discover fuzz --hours 8      # E: random programs, overnight
python3 -m picosim.discover flashed             # F: a flashed program's world (writes flash; see below)
python3 -m picosim.discover peripherals         # G: SIO, GPIO, SysTick
```

G's TIMER and UART part needs the clocks a flashed program sets up, so
campaign F runs it while its firmware is halted.

Options every campaign takes:

| Option | What it does |
|---|---|
| `--target standin` | No hardware: the Unicorn stand-in (checks the code, not the chip) |
| `--dry-run` | Make every case and run every safety check; nothing is sent to the board |
| `--limit N` | At most N cases per stage (a quick trial); the campaign stays "partial" |
| `--stages B1,D4` | Only these stages (B1–B5, D1–D5); each campaign takes its own, and stays "partial" |
| `--repeat` | Run each stage a second time with its batches reordered, and compare |
| `--fresh` | Start the campaign's runs again instead of carrying on |
| `--pins 21,22` | GPIOs this run may drive. Default: none. GP23/24/25/29 are always refused |
| `--chip-label chip2` | Which board this is (campaign H) |

A campaign that stops part-way (a pulled cable, Ctrl-C, a power cut) loses
at most one batch: run the same command again and it carries on. It refuses
to carry on with a different chip or a changed agent.

```bash
python3 -m picosim.discover status              # every campaign: not run, partial, done
python3 -m picosim.discover check               # picosim against hwfacts/ (writes hwfacts/report.md)
```

The Testing window shows the same: a **Hardware facts (Level 5 discovery)**
group with each campaign's status, its provenance and its differences from
picosim, with Run, Resume and Stop.

## What you'll see

Each campaign prints what it is doing, batch by batch:

```
RP2040 through OpenOCD (openocd, SWD)
new run encodings-B1/20261110-191502: 59392 cases in 91 batches
batch 12 case 0xe7fe: …
facts: hwfacts/encodings.json
```

When it finishes, `status` shows it as done with its number of differences
from picosim, and `check` lists them by class (faults, decoder, flags,
memory, timing), each with its first example.

## Safety

These rules are in [`picosim/discover/safety.py`](../picosim/discover/safety.py),
and every planned write passes through them before a batch is uploaded. A
failed check stops the run; it is never just a warning.

- **Never written:** the core voltage regulator, the clocks and PLLs (except
  the frequency counter, which only measures), the power and watchdog
  blocks, SYSCFG (except the input-synchroniser bypass), the flash
  controller and QSPI pads, USB, DMA, PIO, the SWD and SWCLK pads, the pads'
  voltage select, and AIRCR (which can reset the chip).
- **Pins:** nothing is ever made an output, or handed to a peripheral,
  unless it is in `--pins`. GP23, GP24, GP25 and GP29 belong to the Pico W's
  wireless chip and are never written at all.
- **Reads** of registers whose read changes something (FIFOs, the
  divider's quotient, spinlocks, I2C's clear registers…) are skipped, except
  in campaign G, which tests them on purpose.
- **Code:** the cases never jump to an address chosen by chance. Branch
  targets are in a sled of UDF instructions, and the few encodings that
  would jump to the stack are never run.

If something hangs, the runner halts the core, records a lockup if DHCSR
shows one, resets and reloads, restarts OpenOCD, and as a last step asks you
to unplug the target and plug it back in.

## Campaign F and the flash

Campaign F is the only one that writes the target's flash. It runs only
under the guarantee in proposal §7.1:

1. The whole flash is read twice, by two independent paths: OpenOCD over
   SWD, and picotool over USB with the board in BOOTSEL (you hold BOOTSEL
   and plug the target's USB in when asked). The two SHA-256s must match.
2. The backup is stored twice (`~/picosim-flash-backups/` and the cache),
   with a `.uf2` that is decoded back and checked. Each copy is named by
   the flash's unique ID, so it can never be restored onto the other Pico.
3. The probe firmware is written, the measurements run.
4. **Always, even after an error:** the backup is written back, the whole
   flash is read back and its SHA-256 must equal the backup's (both are
   printed side by side), and the board is booted to check its USB port
   appears.

If a session ends before step 4, every other campaign refuses to start until
you run:

```bash
python3 -m picosim.discover restore-flash
```

The `.uf2` backup can also be dragged onto the RPI-RP2 drive in BOOTSEL mode.

## Board sessions

| Session | You do | Campaigns | Board time |
|---|---|---|---|
| α | Wire the probe as for Testing | 0, A, C | ~30 min |
| β | Leave it connected | B (with `--repeat`), D | ~50 min |
| γ | Leave it connected overnight | E (`--hours 8`) | overnight |
| δ | Hold BOOTSEL and plug the target in once; watch for its USB port at the end | F (and G's clock-dependent part) | ~40 min |
| ε | Swap the boards (debugprobe firmware on the other Pico) | 0, A, C, B1 + 10 % of B2–B5, D with `--chip-label chip2`, then `compare-chips` | ~1 h |

## How picosim is checked

Capture is kept apart from comparison. The chip's raw results are saved
first (`~/.cache/picosim/discover/`, every batch's input and output), and a
replay subset goes into `hwfacts/replay/`. `check` runs each recorded case
in picosim's own core and compares:

- how the case ended (a fault at the instruction, falling through, a branch
  and where it landed, SVC, an endless loop);
- R0–R12, SP, LR and the next PC, and the N, Z, C, V flags;
- every word it changed in the scratch window;
- for timing cases, the cycles against picosim's RP2040 model (`timing.py`).

Until picosim has the chip's memory map (Stage 2), the chip's addresses are
mapped onto picosim's 64 KiB (ROM → 0x0000, the sled → 0x4000–0xBFFF,
scratch → 0xC000, the stack area → 0xF000). Things picosim cannot model yet
(exception frames, lockups, addresses outside its memory) are reported as
*pending*, never as failures. `tests/test_hwfacts.py` fails only on a
difference that `hwfacts/known_differences.json` does not explain.

## The stand-in

`--target standin` runs everything against Unicorn (an emulator built from
QEMU), with picosim's own peripheral model and an ARMv6-M exception model
written for it. It shows that the code works end to end; it is not the
chip. Its results go to `~/.cache/picosim/discover/hwfacts-standin/`, never
to `hwfacts/`.

What only the chip can settle, so the stand-in's numbers for these mean
nothing:

- **Timing** is Table 81 (picosim's own model), so D1 and D2 agree with
  picosim by construction. Exception entry and exit cost 15 and 13 cycles,
  guesses until D4 measures them.
- **Code in non-striped SRAM** (0x21000000) cannot run in Unicorn: D1's
  code site S3 reads "not measured".
- **LDM, STM, PUSH and POP** cannot be interrupted part-way in Unicorn: D4's
  "interrupted" scenarios read "completed".
- **Peripherals** are picosim's GPIO model and G's datasheet model of the
  divider, interpolators and spinlocks. G's GPIO differences on the
  stand-in are the things picosim does not model yet.
