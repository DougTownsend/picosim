# hwfacts: what the real RP2040 does

Measured by Level 5 discovery (`python3 -m picosim.discover`, see
[docs/level5-discovery.md](../docs/level5-discovery.md)). Only real-chip
results are kept here. The stand-in's results go to
`~/.cache/picosim/discover/hwfacts-standin/` so emulator output can never be
taken for a measurement.

| File | Campaign | What |
|---|---|---|
| `identity.json` | 0 | CHIP_ID, ROM version, CPUID, debug parts, SWD speed |
| `rom.bin` | 0 | The 16 KiB boot ROM (picosim's replay loads it at 0x0) |
| `memory_map.json` | A | Every region and probe: debugger and core, 8/16/32-bit, writes, striping, access timing |
| `registers.json` | C | Every SVD register: reset values (S1, S3), writable bits, aliases, next to the SVD's prediction |
| `read_side_effects.json` | C | Registers never read blindly (campaign G reads them on purpose) |
| `encodings.json` | B | Every 16-bit encoding and sampled 32-bit: how each ended, picosim's differences |
| `timing.json`, `exceptions.json` | D | Cycles per form, site, alignment and data region; exception entry/exit, frames |
| `fuzz.json` | E | Random programs: blocks run, differences found (minimal tests in `hwtests/e###_*.s`) |
| `environment.json` | F | A flashed program's clocks, XIP cache, interference, real time |
| `peripherals/*.json` | G | SIO divider, interpolators, spinlocks, GPIO, SysTick (TIMER and UART from F) |
| `chip_differences.json` | H | chip1 against chip2 |
| `replay/*.bin.gz` + `.json` | B, D | The raw records `check` replays with no board (all of B1, 5 % of B2–B5) |
| `raw_index.json` | all | Every agent run on the chip (finished or not): its files' SHA-256, batches done (kept in `~/.cache/picosim/discover/` and `~/picosim-hwfacts-raw/`; `python3 -m picosim.discover verify-raw` checks both) |
| `known_differences.json` | — | Differences that are understood, each with a reason and a to-do |
| `report.md` | — | The last `python3 -m picosim.discover check` |

Every fact records its provenance: the chip (CHIP_ID, ROM version), the
date, OpenOCD's version and the SWD speed, the picosim commit (and whether
the tree had uncommitted changes), the agent's hash, the campaign and run,
how many runs there were and whether they agreed.

`tests/test_hwfacts.py` replays `replay/` in picosim on every test run and
fails on any difference not in `known_differences.json`.

A facts file whose provenance has `rebuilt_offline` was compared with
picosim again from the saved raw records (`--offline`, no board), after the
comparison changed; its `date` is still when it was measured.
`tests/test_hwfacts_integrity.py` checks that every file here passes the
schema, that all of them come from one real chip, and that every replay
matches its SHA-256 and names an indexed run.
