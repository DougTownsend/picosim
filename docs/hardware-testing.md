# Testing picosim against a real RP2040

Testing runs a program on a real RP2040 and in picosim at the same time and
compares the two:

- **Step by step (trace).** After every instruction it compares R0–R15, the
  N, Z, C and V flags, and every byte the instruction wrote to memory (read
  back from the chip). When the program ends it also compares all of the
  program's memory, what it printed, and the GPIO registers it wrote.
- **Timing (benchmark).** For regions of a program marked with
  `bl bench_start` / `bl bench_stop`, it compares the clock cycles the chip
  really takes with what picosim's RP2040 model (datasheet Table 81) predicts.
  A program without them is timed in stretches: every run of its own code
  between OS calls (see [Timing a program without regions](#timing-a-program-without-regions)).

Open it from the toolbar's mode menu: **Testing (vs. real RP2040)…** (Level 2
and up). Or run it from a terminal:

```bash
python3 -m picosim.hwtest                          # the whole suite, through a debug probe
python3 -m picosim.hwtest --report results.md      # …and save a report (+ results.json)
python3 -m picosim.hwtest --target standin         # no hardware: the stand-in emulator
python3 -m picosim.hwtest hwtests/t02_shifts.s     # one test
python3 -m picosim.hwtest --bench my_program.s     # time the regions of your own program
```

The exit status is 0 when every test is identical, so the suite can run in
scripts.

---

## How it works

### Why a debug probe, and not USB

Comparing intermediate states needs the chip to stop after every
instruction, so that its registers and memory can be read. Only the debug
port (SWD) can do that. The Cortex-M0+ has no debug-monitor exception, so a
program cannot single-step itself and report over USB. SWD also lets
picosim load each test straight into the chip's SRAM, so nothing is
flashed and the target needs no firmware of its own.

The SWD port is the 3-pin **DEBUG** header at the end of the Pico (also on
the Pico W). It is a wired port, not wireless.

### The same addresses, moved

picosim runs programs in a flat 64 KiB space: the built-in OS at `0x0000`
and the program at `0x3000`. The RP2040 cannot run code there (its ROM is
there), so Testing links the same source for SRAM instead:

| | built-in OS | program | top of stack |
| --- | --- | --- | --- |
| picosim | `0x0000` (`os.s`) | `0x3000` | `0x10000` |
| RP2040 | `0x20000000` (`picosim/hwtest/os_hw.s`) | `0x20003000` | `0x20010000` |

Every address on the chip is picosim's address + `0x20000000`. A register or
memory word holding an address differs by exactly that, and the comparison
allows for it. Everything else must be equal bit for bit. Before every run,
`picosim/hwtest/image.py` checks that the two builds really are laid out
this way: the same labels at the same offsets, and the same bytes except
address literals.

### The built-in OS on the chip

`os_hw.s` has every routine of `os.s` at the same offset:

- `putchar` / `getchar` are `BKPT` instructions. The chip stops there, the
  test runner does the I/O (it reads R0, or puts the next input character in
  R0) and returns to the caller, just as picosim's SVC does. No OS code runs
  on the chip, so it cannot disturb the comparison.
- `bench_start` / `bench_stop` read the SysTick counter into a table in the
  OS's part of SRAM, leaving every register, flag and byte of the program
  unchanged. (In picosim they are a plain `bx lr`.)

In the Testing window, click a step marked **call**, or a Timing region, to
see what that call did, how picosim and the chip each did it, why it is done
that way, and when it happened, with the run's own values (the character
printed, the two SysTick readings and the calibration subtracted, the line
of the `bl`). `picosim/hwtest/oscalls.py` writes these. A test's `@ Calls:`
line says why that test calls the OS at all; it is shown when the test is
selected.

### Preparing the chip

Before each test the chip is put in the state picosim starts in:

1. `reset halt`, so core 0 stops before the boot ROM runs.
2. IO_BANK0 and PADS_BANK0 are reset and released, as the Pico SDK leaves
   them before `main`.
3. The SIO's GPIO outputs and output enables are cleared. The debugger
   cannot reach the SIO (0xD0000000): it is on each core's private I/O
   port, not on the bus the debug port uses. So this, and reading GPIO_OUT
   and GPIO_OE at the end, is done by three instructions run on the core.
4. SysTick is set to count processor clock cycles, enabled only for
   benchmarks.
5. The 64 KiB image is written to `0x20000000` and **read back** to check
   it.
6. The registers are set to picosim's registers at `main`, with addresses
   moved.

### Comparing a step

| Mark | Meaning |
| --- | --- |
| ✓ | Registers, flags and every written byte are identical. |
| call | A call into the built-in OS. picosim runs its OS; the chip is given the same result. Then both are compared. |
| I/O | The instruction loaded a value from a peripheral and the chip's value differs: a real pin level, or a register picosim models more simply. picosim takes the chip's value and carries on. This is information, not a failure. |
| ✗ | A real difference, in the registers, flags or memory named. Unless **Stop at the first difference** is ticked, picosim takes the chip's state and carries on, so a single run shows every difference. |

A **HardFault** on the chip is detected through xPSR's exception number. If
picosim faults at the same instruction, that step passes. The 8-word
exception frame the chip pushes on the stack is left out of the final
memory comparison.

For GPIO, only `GPIO_OUT`, `GPIO_OE` and the CTRL/PADS registers the
program wrote count towards the verdict. Differences in registers the
program never touched are listed for information.

### Timing

On the chip a benchmark runs at full speed from SRAM. There are no wait
states, which is what Table 81 assumes. The SysTick reads in
`bench_start`/`bench_stop` bracket the region. The cycles between the two
reads are the region plus a fixed cost of measuring. Once per connection,
a **calibration** run (`picosim/hwtest/calibrate.s`) measures that cost
with empty regions, once with the `bl bench_stop` word-aligned and once
halfword-aligned, and every region subtracts it. Table 81 predicts
21 cycles of overhead (LDR, STR, POP {r0,r1}, BX LR, BL, B, PUSH {r0,r1},
LDR, plus the reading LDR). The calibration result is itself a first check
of those rows.

In picosim the same program runs instruction by instruction. Each region
adds up the model's cycles for exactly the instructions between the return
from `bench_start` and the `bl bench_stop`. So **Difference = chip − model**
is in the region's own instructions, and **Per instr.** divides it by the
repetition count in the region's comment (`@ MULS x 64`).

#### Timing a program without regions

A program with no `bench_start` / `bench_stop` (most examples, or your own
`gpio.s`) is timed in **stretches**: each run of the program's own code
between the OS. A stretch starts at main or at the return from `putchar` /
`getchar`, and runs up to and including the next call or main's return.
So an echo loop gives "start of main → getchar" (its set-up),
"after getchar → putchar" and "after putchar → getchar".

For this the chip runs a timed build of `os_hw.s` (`--defsym TIMED=1`):

- The runner starts the chip at `hw_go`, which reads SysTick and calls main
  with `blx r0`.
- `putchar` / `getchar` branch to routines that read SysTick and record the
  stretch, stop at a `BKPT` for the runner's I/O, then read SysTick again
  before returning.
- Main returns into `hw_go`, which records the last stretch.

Both reads sit on the program's side of the `BKPT`, so the time the core
spends stopped for I/O is not counted. Every path from one read to the next
costs the same in Table 81: after the read, LDR =, STR, POP and a 2-cycle
branch; before the next read, the program's own call or return, then B,
PUSH, LDR = and the reading LDR. That is 18 cycles of overhead beyond what
picosim counts. A second calibration (`calibrate_calls.s`) measures it once
per connection, with calls at word and halfword addresses and with main's
first and last stretches. Its `steady` flag shows whether every path really
costs the same on the chip.

A program that never ends (an echo loop with no `q` in its input) is stopped
after 100 OS calls on both machines, and those stretches are compared. The
untimed `os_hw.s` that traces and region benchmarks use assembles to the
same bytes as before.

SysTick counts processor clock cycles at whatever clock the chip runs.
After `reset halt` that is the ring oscillator (about 6 MHz). Cycle counts
do not depend on the clock speed.

---

## What you need

### Hardware

- **Two Picos** (Pico or Pico W), or one Pico plus a Raspberry Pi Debug
  Probe.
- On the **probe** Pico: Raspberry Pi's debugprobe firmware.
  1. Download `debugprobe_on_pico.uf2` from
     <https://github.com/raspberrypi/debugprobe/releases>.
  2. Hold BOOTSEL while plugging the probe Pico in.
  3. Copy the file to the `RPI-RP2` drive.
- The **target** Pico needs nothing on it. Testing loads every test into its
  RAM. A program already in its flash is not changed, but it does not run
  while Testing has the core stopped.

### Wiring

| Probe Pico | Target Pico |
| --- | --- |
| GP2 | SWCLK (DEBUG header) |
| GP3 | SWDIO (DEBUG header) |
| GND | GND (DEBUG header, middle pin) |

Power the target from its own USB cable, or connect the probe's VSYS to the
target's VSYS. Keep the wires short (under about 20 cm). The probe's UART
pins (GP4/GP5) are not needed.

### Software

- **OpenOCD 0.12 or newer**, with RP2040 support:
  - macOS: `brew install open-ocd`
  - Linux: your distribution's `openocd` (0.12+), or Raspberry Pi's build
  - Windows: the Raspberry Pi Pico VS Code extension installs one under
    `~/.pico-sdk/openocd/`, and picosim looks there too
- Optional: **Unicorn**, for the stand-in, if you want to try Testing
  without hardware: `pip install unicorn` (or `pip install -e ".[hwtest]"`).

Another GDB server works too (pyOCD, a J-Link GDB server, OpenOCD started by
hand). Choose **GDB server already running** and give its `host:port`.

---

## The suite (`hwtests/`)

| File | Checks |
| --- | --- |
| `t01_add_sub_flags.s` | ADDS, ADCS, SUBS, SBCS, RSBS, CMP, CMN on all pairs of 8 edge values: results and N, Z, C, V |
| `t02_shifts.s` | LSLS/LSRS/ASRS/RORS by register (0, 1, 4, 31, 32, 33, 255, 256) and by immediate (including #32): results and carry out |
| `t03_logic_multiply_extend.s` | ANDS, ORRS, EORS, BICS, MVNS, TST, MULS (C and V kept), REV, REV16, REVSH, SXTB/H, UXTB/H |
| `t04_load_store.s` | every load/store width, sign extension, register and immediate offsets, SP-relative, literal, ADR, LDM/STM with and without write-back, PUSH/POP |
| `t05_branches.s` | all 14 conditions under all 16 NZCV combinations; B, BL, BX, BLX, POP {PC}, MOV PC, ADD PC (jump table) |
| `t06_high_registers.s` | R8–R12, SP arithmetic, MRS/MSR (APSR, PRIMASK, CONTROL, MSP, PSP), CPSID/CPSIE, barriers |
| `t07_recursion.s` | recursive factorial and Fibonacci: deep stacks of saved registers |
| `t08_algorithms.s` | CRC-32 (prints CBF43926), bubble sort, GCD |
| `t09_strings.s` | strlen, in-place reverse, copy |
| `t10_gpio.s` | SIO OE/OUT/SET/CLR/XOR, GPIO_IN, pull-up/pull-down, IO_BANK0 CTRL, PADS, atomic SET/CLR aliases (pins chosen with ⚙ Settings) |
| `t11_fault.s` | an unaligned word load: both must fault at the same instruction |
| `b01_alu_timing.s` | 64× each of MOVS, ADDS, ADCS, LSLS, EORS, MULS, CMP, MOV high, ADD/SUB SP, SXTB, REV, NOP |
| `b02_memory_timing.s` | LDR/STR (immediate, register, byte, halfword, signed, SP, literal), PUSH/POP of 4 and 8, LDM/STM |
| `b03_branch_timing.s` | B taken, B<cond> taken / not taken, BL + BX LR, BLX, POP {PC}, a counted loop |
| `b04_io_timing.s` | SIO loads/stores (1 cycle in Table 81) and APB loads (IO_BANK0, PADS, RESETS: not in Table 81) |
| `b05_alignment_timing.s` | BL and branch targets at word vs. halfword addresses |
| `b06_program_timing.s` | bubble sort, CRC-32, a 256-byte copy, a dot product, recursive fib(10) |

### Writing your own

Any picosim program is a test. Load it in the main window and use **The
loaded program**, or add a file to `hwtests/`. A suite file starts with:

```asm
@ Title: What it is
@ About: What it checks, in a sentence or two.
@ Kind: trace            @ or: bench
@ Input: "abc\n"         @ optional: what getchar returns, in order
```

Without `bench_start` / `bench_stop`, Timing times every stretch between OS
calls (above). To time a particular part of a program:

```asm
        bl      bench_start     @ LDRB from SRAM x 32
        .rept   32
        ldrb    r3, [r1]
        .endr
        bl      bench_stop
```

Rules:

- Use `bl putchar` / `bl getchar`, not `svc`.
- Keep timed regions under 16,777,215 cycles (SysTick is 24 bits).
- Don't print inside a timed region: on the chip, the debugger stops the
  core at each `putchar`.
- Testing builds do not define `PICOSIM_HARDWARE`, so the code is exactly
  what picosim runs.
- Arithmetic that combines two addresses (adding two pointers) cannot match
  after the move to SRAM. Comparing or subtracting pointers is fine.

---

## What Testing cannot see

- **The pipeline's inside.** SWD sees the machine between instructions.
  The 2-stage pipeline shows only through timing, which is what the
  benchmarks measure.
- **Flash (XIP) timing.** Tests run from SRAM. A program flashed with
  picosim's **Flash** button runs from flash through the XIP cache, which
  adds wait states on a cache miss (mostly on the first pass through a
  loop).
- **Interrupts, exceptions other than HardFault, the second core, and
  peripherals other than RESETS, IO_BANK0, PADS_BANK0 and the SIO's GPIO
  registers.** picosim does not model them.

## Results from a real RP2040 (2026-10-29)

The first run on hardware (a Pico, with a second Pico as the debug probe):

- **All 11 step-by-step tests: identical.** Every register, flag and memory
  write after every instruction, the HardFault, and the GPIO registers.
- **Calibration: 21 cycles at both alignments**, exactly as Table 81
  predicts. That confirms LDR, STR, PUSH, POP, BL, B and BX LR.
- **5 of 6 timing tests: identical to the cycle**: every ALU instruction
  (MULS is 1 cycle), every load and store to SRAM, the SIO (1 cycle),
  branches (taken 2, not taken 1), BL/BX/BLX/POP {PC}, LDM/STM/PUSH/POP,
  word vs. halfword alignment (no effect), and whole programs (sort,
  CRC-32, copy, dot product, recursion).
- **One difference: loads from APB peripherals take 4 cycles, not 2.**
  32 LDRs from IO_BANK0, PADS_BANK0 and RESETS each took 128 cycles, where
  Table 81 gives 64: the AHB-to-APB bridge adds 2 wait cycles to every
  load. picosim's RP2040 model now uses this (`APB_LOAD_EXTRA` in
  `picosim/timing.py`), and so do the Performance window, the 2-stage
  pipeline diagram, the heat map and Explain this step.
- **Stores to APB peripherals take 5 cycles, not 2** (measured on the
  second run, with store regions added to `b04_io_timing.s`): 32 STRs to
  PADS_BANK0 or IO_BANK0 took 160 cycles, and 16 LDR + STR pairs took 144
  = 16 × (4 + 5). `APB_STORE_EXTRA = 3`.

### Fixed in picosim by Testing

- **PADS register read-back.** picosim returned only the IE/PUE/PDE bits,
  so an untouched pad read `0x40`; the RP2040's reset value is `0x56`
  (input enable, 4 mA drive, pull-down, Schmitt trigger). `picosim/gpio.py`
  now keeps the whole register. A pin is still drawn floating until the
  program sets its pulls; floating and pulled-down pins both read 0.
- **Atomic register aliases.** Writes to a register's address + `0x1000`
  (XOR), `0x2000` (SET) or `0x3000` (CLR) bus-faulted in picosim but work
  on the chip (datasheet §2.1.2). They now work for RESETS, IO_BANK0 and
  PADS_BANK0.
- **APB load timing**, above.

### Found in the stand-in

- Unicorn keeps PSP's bits [1:0]. ARMv6-M says they read as 0, as picosim
  and the real chip do; the stand-in corrects for it.

## Troubleshooting

Every connection writes a **session log**: each message to and from the
debugger, and everything OpenOCD printed. The Testing window's **Log** button
opens it. Each connection has its own file in `~/.cache/picosim/hwtest/logs/`
(on macOS with platformdirs, `~/Library/Caches/picosim/hwtest/logs/`), named by
date and time; the newest 20 are kept. Send it along when reporting a problem.

Running the suite again, in the same connection or a new one, gives the same
results: every test starts from a reset chip and a freshly loaded image. Two
picosim windows (or a command-line run beside the window) can work at once;
they never share a log or a half-built program file.

If the connection drops in the middle of a test, or an answer from the
debugger is not the kind the request expects (the conversation is out of
step), picosim reconnects (and restarts OpenOCD if it stopped) and runs the
test again once. OpenOCD sends empty "keep-alive" packets while a command
takes a while; picosim skips them.

The benchmark overhead (the cycles the two SysTick reads themselves cost) is
measured automatically once per connection, with empty regions; the timing
results show it ("21, 21 cycles"). Reconnecting measures it again. If that also
fails, the run stops with one message saying what OpenOCD said last; the
tests after it show "not run".

| Message | What to do |
| --- | --- |
| OpenOCD was not found | Install it (see Software), or choose GDB server / Stand-in. |
| No debug probe was found | The probe Pico is not plugged in, or is not running `debugprobe_on_pico.uf2`. |
| The probe is there but cannot reach the target | Check the three SWD wires and that the target is powered. |
| SRAM did not keep the program | Lower **SWD kHz** (try 1000) and use shorter wires. |
| A test stops with "still running when the time limit ran out" | The program never returns (an LED blinker). Traces stop at the step limit, benchmarks after 20 s; finished regions are still reported. |
| The target resets in the middle of a run | A program in its flash may have started the watchdog. Hold BOOTSEL while powering the target, then connect. |
