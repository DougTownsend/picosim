# picosim

An ARMv6-M (Cortex-M0+) simulator for learning ARM assembly.

---

## Installation

Install Python 3, a C++ compiler and `arm-none-eabi-gcc`, then run from the
repository folder:

```bash
pip install -e .
```

Use `-e` (editable): the graphical simulator and the examples are read from
this folder. See [installation.md](installation.md) for platform-specific
instructions and [DEPENDENCIES.md](DEPENDENCIES.md) for the full list.

---

## Usage

```bash
picosim <file.s>           # step through it at an interactive prompt
picosim --run <file.s>     # run to completion
picosim --trace --run <file.s>   # run, printing each instruction
picosim -n 100 <file.s>    # run 100 instructions, then print the registers
picosim --time <file.s>    # run to completion and report instructions/second
picosim --uf2 <file.s>     # build a .uf2 for the Raspberry Pi Pico
picosim --flash <file.s>   # build and upload a .uf2 to the Pico
picosim --gui [file.s]     # open the graphical simulator (Godot 4)
picosim --cycle-table      # print per-instruction cycle counts
```

The simulator assembles and links your `.s` file automatically. Without
`--run`, `-n` or `--time` it stops before the first instruction and gives you
a prompt where you can step, run to a breakpoint and inspect registers and
memory; the prompt stays open after the program halts.

### Interactive commands

| Command | Description |
|---------|-------------|
| `s [n]` | Step one instruction (or n) |
| `r` | Run until the program halts or reaches a breakpoint |
| `p` | Print registers, flags and the current instruction |
| `x <addr> [n]` | Show n words of memory (default 8) at a hex address or label; I/O registers work too |
| `d [addr] [n]` | Disassemble n instructions (default 10) from addr (default: the PC) |
| `b <addr>` / `db <addr>` | Set / delete a breakpoint at a hex address or label |
| `lb` | List breakpoints |
| `reg <rN> <value>` | Set a register, e.g. `reg r0 42` or `reg pc 0x3000` |
| `gpio <pin> 0\|1\|z` | Drive an input pin high, low or floating |
| `q` | Quit |
| `h` | Show help |
| Enter | Repeat the last command |

### Like the real chip

picosim runs only Cortex-M0+ (ARMv6-M) instructions, and stops with an
explanation wherever a real Pico would crash with a HardFault:

- a word or halfword load/store to an address that is not a multiple of its size;
- `bx`, `blx` or `pop {pc}` to an address with bit 0 clear (put `.thumb_func`
  before a label whose address you load with `ldr r0, =label`);
- an instruction the M0+ does not have (e.g. code assembled for a Cortex-M4);
- an address that is neither RAM (0x0000–0xFFFF) nor a simulated I/O register
  (SIO, IO_BANK0, PADS_BANK0, RESETS).

As on the RP2040, a GPIO pin follows the SIO output registers only after its
IO_BANK0 CTRL register selects function 5 (SIO).

### I/O

Two I/O functions are provided by the simulator OS and are compatible with the
Raspberry Pi Pico SDK, so the same `.s` file runs on both the simulator and real
hardware:

| Function | Behaviour |
|----------|-----------|
| `putchar(r0)` | Print the character in r0 to the terminal |
| `getchar()` → r0 | Read one character from the keyboard (no Enter needed) |

### Deploying to a Raspberry Pi Pico

The `--uf2` flag compiles your assembly into a `.uf2` image that you can
drag-and-drop onto a Pico in bootloader mode:

```bash
picosim --uf2 echo_test.s     # produces echo_test.uf2 next to echo_test.s
picosim --flash echo_test.s   # builds and uploads it to a connected Pico
```

`--flash` uses `picotool` when available and can force a running compatible
Pico into bootloader mode. Alternatively, it copies the UF2 to a mounted
`RPI-RP2` drive. Install `picotool` with `brew install picotool` if needed.

USB serial output is lost until a terminal opens the port, so the firmware
waits up to 5 seconds at start-up for one (picosim's console connects by
itself after Flash). A board with no computer attached starts its program
after those 5 seconds.

See [test_asm_files/echo_test.s](test_asm_files/echo_test.s) for a working example.

The build wraps your assembly in a small C stub that calls `stdio_init_all()`
before jumping to your code, so `putchar` and `getchar` work over USB serial
exactly as they do in the simulator.

The SDK is cloned automatically to `~/pico-sdk` on first use (requires `git`).
Subsequent builds reuse the cached SDK objects — only your assembly and the
final link step are redone.

> **Note:** if `main:` is your entry-point label the build renames it to
> `asm_main` automatically.  Alternatively, name it `asm_main:` in your source
> to make the intent explicit (the files in `test_asm_files/` do this).

### Example

```asm
.syntax unified
.cpu cortex-m0plus
.thumb

main:
    push  {r7, lr}

    @ print 'H'
    movs  r0, #'H'
    bl    putchar

    movs  r0, #0
    pop   {r7, pc}
```

Run with:
```bash
picosim hello.s
```

---

## Graphical simulator

```bash
picosim --gui test_asm_files/insn_coverage_test.s
```

This needs Godot 4 (4.7 is tested; `godot` on your PATH, or set
`PICOSIM_GODOT` to the binary) and an editable install (`pip install -e .`),
because the Godot project is read from this folder. The Godot project lives in [gui/](gui/) and talks to a Python backend
([picosim/server.py](picosim/server.py)) over a local TCP socket.

### For beginners: levels and examples

With no program loaded, picosim opens on a welcome page listing the example
programs in [examples/](examples/). One click opens one, starting with
`01_hello.s`. Each example begins with comments that say what it does and
what to watch. The toolbar's **Examples** menu opens them later too.

The **Level** menu (top right, remembered between runs) controls how much of
the app is shown. Students can start at level 1 and move up as the course
goes on:

| Level | Shows |
|---|---|
| 1 · Basics | Step instruction, Run, the registers and flags (in hex, decimal or signed), the disassembly and the serial console |
| 2 · Memory & I/O | + the Memory tab and stack, the Pico board, Pico (USB serial) mode |
| 3 · Inside the CPU | + Step cycle, the CPU diagram and cycle inspector, MAR/MDR/IR, the program walkthrough |
| 4 · Pipeline & performance | + the 2-stage pipeline and the Performance window (the whole app) |

The examples that use the Pico's pins (`05_blink_led.s`, `08_button_led.s`)
work with any RP2040 board and any breadboard layout: the pins (and, for the
button, which way the button and LED are wired) are **program settings**.
Students set them with the toolbar's **⚙ Settings** button; picosim stores
their values per program in the user's data folder and fills them in when it
assembles (for the simulator, the walkthroughs and Flash), so the program
file is never edited. To give your own program settings, mark `.equ` lines
with a comment starting `<-`:

```
        .equ    LED_PIN,   15   @ <- the GPIO number of your LED
        .equ    WIRED_GND, 1    @ <- 1: wired to GND; 0: wired to 3V3
```

Help text of the form `value: meaning; value: meaning` becomes a drop-down.

`08_button_led.s` ("Buttons and LEDs") also works out those settings for a
student's own circuit: with its `FIND_PINS` setting on "find my pins" it
switches each pin in turn while printing its number (to find LEDs), then
reports every pin that changes as buttons are pressed.

In Pico (USB serial) mode, while a flashed board is connected, the Pico
board drawing shows the real board's pins live. The firmware picosim builds
uses the RP2040's second core to send the pin levels over USB about 50 times
a second, as `\x02P<levels>,<directions>\x03` frames; picosim removes these
from the console text (another serial monitor would show them).

The guide (F1) lists only the topics for the current level. Pass
`--level=N` after Godot's `--` to override the level for one run, e.g. for
classroom demos or screenshots.

- **Registers** (top left): R0–R12, SP, LR, PC, the NZCV flags and the
  datapath registers MAR, MDR, IR and IR2. Anything changed by the last step
  is shown in yellow. Type a value and press Enter to change a register, or
  click a flag to toggle it.
- **Memory** (bottom left): the *Disassembly* tab follows the PC; click the
  ● column to toggle a breakpoint. The *Memory* tab has a hex dump with
  go-to, byte editing and a stack view.
- **Raspberry Pi Pico** (bottom left): the board with its real 40-pin
  header. Every GPIO shows its live level right on its pin: a green **1**
  for an output driven high, **0** for low, blue for an input that reads 1,
  and blank when floating. Pins changed by the last step get a yellow ring,
  the drawing's on-board LED lights when GP25 is high (as on a plain Pico;
  on a Pico W that LED is not on GP25, so the examples use an LED wired to
  a GPIO pin instead), and the most recent I/O
  register write is decoded underneath (for example
  `IO_BANK0 GPIO20_CTRL ← 0x331F`). Click an input pin to drive it
  (Z → 1 → 0 → Z). Hover a pin to see its details.
- **Zoom**: use the `−` / `+` buttons in the toolbar, Ctrl/Cmd `+` / `−`
  (Ctrl/Cmd `0` resets), or Ctrl/Cmd + mouse wheel. The zoom level is
  remembered, and the default follows the screen's Retina scale.
- **USB Serial** (right): output from `putchar`. Click the output area and
  type to feed `getchar` one key at a time, or type a line in the box below.
  In *Pico (USB serial)* mode the same pane talks to a real board. *Flash*
  builds and uploads the program, then connects.
- **Show CPU Diagram**: a block diagram of the multi-cycle datapath, plus a
  cycle inspector. For each clock cycle it shows the FSM state and phase,
  the control signals asserted, the value on the bus, the wires carrying
  data, and every register, flag or memory byte that changed (old → new).
  Use *Prev* and *Next* to review earlier cycles of the instruction.
  Click a block or tri-state buffer for what it does, its connections and
  its Python model (`picosim/components.py`).
- **2-stage pipeline** (machine menu next to *Show CPU Diagram*): the
  diagram becomes the Pico's Cortex-M0+ pipeline (Fetch | IF/EX |
  Decode + Execute), with each instruction in flight drawn as a labelled
  token. *Step cycle* advances one pipeline cycle. Taken branches show the
  predict-not-taken flush and refill, and multi-cycle instructions make the
  next one wait. A strip shows the last 8 cycles.
- **Performance** (F2): cycles and CPI of the run so far on the multi-cycle
  machine, the RP2040's 2-stage Cortex-M0+ pipeline (RP2040 datasheet
  Table 81 counts) and a 3-stage teaching pipeline; datapath use,
  instruction mix, design options (multiplier, memory wait states) and a
  cycle-by-cycle pipeline chart. See `picosim/timing.py` for exactly what
  is and is not modelled.

| Key | Action |
|-----|--------|
| F11 | step one clock cycle |
| F10 | step one instruction (finishes the current one) |
| F5  | run / pause (the speed slider sets cycles per second) |
| F2  | Performance window |
| F1  | user guide |
| Ctrl/Cmd+O | open a `.s` file |
| Ctrl/Cmd+R | reload (re-assemble and reset) |

Every instruction takes several clock cycles, as described in the course
text's datapath chapter. See [docs/cycles.md](docs/cycles.md) for the count
and state sequence of each instruction.

Run the tests with `python3 -m unittest discover tests`.

