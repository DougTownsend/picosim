# picosim

An ARMv6-M (Cortex-M0+) simulator for learning ARM assembly.

---

## Installation

Install Python 3, a C++ compiler and `arm-none-eabi-gcc`, then from a
checkout of this repository:

```bash
python3 -m venv .venv            # a private Python for picosim
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python3 -m pip install -e .      # this Python's pip, whatever `pip` means here
picosim --run examples/01_hello.s
```

The last line should print `Hi!` (after a few "Assembling..." lines).
Use `-e` (editable): the graphical simulator and the examples are read from
this folder. The graphical simulator also needs Godot 4; the terminal does
not. See [installation.md](installation.md) for platform-specific
instructions and [DEPENDENCIES.md](DEPENDENCIES.md) for the full list.

---

## Your first five minutes (at the terminal)

Open the first lesson at the prompt:

```text
$ picosim examples/01_hello.s
ARMv6-M Simulator  (type 'h' for help)
  The next instruction is in _start: picosim's start-up code, which runs
  first (as on a Pico) and then calls your main. ...
  PC 0x10000000: ldr      r0, [pc, #8]
  PC Label: _start
```

The first instruction is not in your file. Like a real Pico, the chip runs
some start-up code first (picosim's is `picosim/os.s`, three instructions),
and that code calls your `main`. Skip it with a breakpoint:

```text
sim> b main             # stop when the program reaches main
sim> r                  # run until then
  Breakpoint hit at 0x10000100
  PC 0x10000100: push     {r7, lr}
  Next line: 01_hello.s line 20: push    {r7, lr} ...
sim> s 2                # step two instructions: push, then movs r0, #'H'
  Next line: 01_hello.s line 23: bl      putchar ...
sim> p r0
  r0 = 0x00000048 = 72 = 'H'
```

`Next line` is the line that runs when you step again. R0 now holds the
letter H as a number: its ASCII code is 72, which is 0x48 in hex. `putchar`
prints the character whose code is in R0, so stepping over the `bl` prints
`H`. Type `r` to run to the end (`Hi!`), `reset` to start again, and `q` to
quit. If you edit the file, `reload` builds it again without quitting.

Next: a branch in [03_count_loop.s](examples/03_count_loop.s) (`b loop`,
then `r` and `p r4` each time round), and memory in
[04_memory.s](examples/04_memory.s) (`x numbers 4`, then `r`, then
`x total 1`).

---

## Usage

```bash
picosim <file.s>           # step through it at an interactive prompt
picosim --run <file.s>     # run to completion
picosim --trace --run <file.s>   # run, printing each instruction (to stderr)
picosim -q --run <file.s>  # run; print only the program's output
picosim -n 100 <file.s>    # run 100 instructions, then print the registers
picosim --time <file.s>    # run to completion and report instructions/second
picosim --uf2 <file.s>     # build a .uf2 for the Raspberry Pi Pico
picosim --flash <file.s>   # build and upload a .uf2 to the Pico
picosim --gui [file.s]     # open the graphical simulator (Godot 4)
picosim --cycle-table      # print per-instruction cycle counts
```

The simulator assembles and links your `.s` file automatically, into a
private folder (your cache folder, or `PICOSIM_BUILD_DIR`), so it never
writes next to your file and read-only lesson folders work; `--elf PATH` also
saves the linked program. `.include` finds files next to the `.s` file.
Without `--run`, `-n` or `--time` it stops before the first instruction and
gives you a prompt where you can step, run to a breakpoint and inspect
registers and memory; the prompt stays open after the program halts.

For scripts: **stdout is only what the program prints**. picosim's own
messages ("Assembling..."), the `--trace` rows, `--time`'s report and errors
go to stderr, and `-q` leaves out the status messages, so
`picosim -q --run examples/01_hello.s | cat` prints exactly `Hi!`. The exit
status is the program's exit code (0 when `main` returns), 1 for a build
error or a fault, and 130 when stopped with Ctrl+C. `--time` measures how
fast this computer simulates (host time), not the Pico; `--cycle-table` is
the teaching multi-cycle datapath's table, not RP2040 timing (the Testing
window and `docs/hardware-testing.md` cover the chip's own cycles).

### Interactive commands

| Command | Description |
|---------|-------------|
| `s [n]` | Step one instruction (or n) |
| `r` | Run until the program halts or reaches a breakpoint |
| Ctrl+C while running | Stop after the current instruction and come back to the prompt |
| `p [rN]` | Print registers, flags and the next instruction and source line (or one register in hex, decimal and as a character) |
| `x <addr> [n]` | Show n words of memory (default 8) at a hex address or any label, code or data (`x total 1`); I/O registers work too |
| `d [addr] [n]` | Disassemble n instructions (default 10) from addr (default: the PC) |
| `b <addr>` / `db <addr>` | Set / delete a breakpoint at a hex address or code label (not data) |
| `reset` | Start the same build over from its first instruction; breakpoints stay |
| `reload` | Build the file again after an edit and start over; breakpoints stay where their label still is. If the edit does not build, the old build stays loaded |
| `lb` | List breakpoints |
| `reg <rN> <value>` | Set a register, e.g. `reg r0 42` or `reg pc 0x10000100` |
| `gpio <pin> 0\|1\|z` | Drive an input pin high, low or floating |
| `q` | Quit |
| `h` | Show help |
| Enter | Repeat the last command |

### Like the real chip

picosim runs only Cortex-M0+ (ARMv6-M) instructions, and stops with an
explanation wherever a real Pico would crash with a HardFault:

- a word or halfword load/store to an address that is not a multiple of its size;
- `bx`, `blx` or `pop {pc}` to an address with bit 0 clear (put `.thumb_func`
  before a label whose address you load with `ldr r0, =label`). As on the
  RP2040 the jump happens and the fault comes at the target, so picosim stops
  there and names the branch that went there; `bx pc` and `blx pc` stop at the
  instruction itself, as the chip does;
- an instruction the M0+ does not have (e.g. code assembled for a Cortex-M4);
- an address where the RP2040 has nothing either (past SRAM at `0x20042000`,
  `0x21040000`, `0x30000000`…): "Bus fault", a HardFault on the Pico too;
- a store into flash or ROM (on a Pico the store would simply be lost): put
  variables after `.data` or `.bss`, which go in SRAM.

picosim says when a stop is its own limit rather than the chip's. An access
to a part of the Pico that picosim does not simulate (TIMER, UART,
SysTick/NVIC, DMA, PIO, the XIP cache's other views of flash) stops with
"Not simulated: … a limit of picosim, not a mistake in the program". A store
into ROM or flash is a teaching stop: the Pico ignores it. Every stop
carries one of these reasons (`picosim.cpu.stop_reason()`): `hardware-fault`
(a HardFault on the Pico too), `model-limit` (works on the Pico) or
`teaching-stop` (the Pico would go on); the terminal prints it after the
error.

Encodings the ARM manual calls UNPREDICTABLE, which the assembler never
writes but hand-made `.hword` data can (an empty register list such as
`pop {}`, CPS or MRS/MSR with "should be" bits wrong), run exactly as the
RP2040 runs them: this was measured on the chip (`hwfacts/`).

### Memory map

picosim uses the RP2040's own addresses (`picosim/memmap.py`):

| Addresses | What is there |
| --- | --- |
| `0x00000000`–`0x00003FFF` | ROM: the Pico's boot ROM (empty in picosim) |
| `0x10000000`–`0x100000FF` | flash: picosim's built-in OS (`_start`, `putchar`, `getchar`) |
| `0x10000100` upward | flash: the program's code and constants (`.text`, `.rodata`), read-only |
| `0x20000000`–`0x20041FFF` | SRAM: the program's variables (`.data`, `.bss`) from the bottom, the stack from the top (SP starts at `0x20042000`) |
| `0x21000000`–`0x2103FFFF` | SRAM0–3 again, without striping: bank N at `0x21000000 + N×0x10000`, the same memory as `0x20000000`–`0x2003FFFF` (datasheet §2.6.2; measured on a Pico) |
| anything else | the simulated I/O registers; elsewhere a bus fault where the Pico has nothing either, or "Not simulated" where the Pico has something picosim lacks |

The OS sits in flash, where a Pico's start-up code and `putchar` also are,
so a `bl putchar` reaches it directly. Code runs from flash with no wait
states, as from the Pico's XIP cache.

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

Clicking an example's title runs it as it is, with no editor. Its **Edit my
copy** button saves the student's own copy in `Documents/picosim` (only the
first time; after that their copy is kept; the page shows where), loads that
copy and opens it in a text editor (VS Code when installed, otherwise the
system's editor for `.s` files), so they can change it for its challenge. If
the copy cannot be saved, picosim offers to choose another folder or just run
the example; it never opens the original for editing. A classroom can set
`PICOSIM_COPIES_DIR` (where copies go) and `PICOSIM_EDITOR` (an editor
command, or `none`). An example meant for a higher level offers to open at
that level first. The files in `examples/` are never changed. **Examples → Start … over from the
original** puts the original back over a copy.

The **Level** menu (top right, remembered between runs) controls how much of
the app is shown. Students can start at level 1 and move up as the course
goes on:

| Level | Shows |
|---|---|
| 1 · Basics | Step instruction, Run, the registers and flags (in hex, decimal or signed), the disassembly, Explain this step and the serial console |
| 2 · Memory & I/O | + the Memory tab (a map of memory, the bytes coloured by what they are, the stack as boxes, and what each step read or wrote), the Pico board, Pico (USB serial) mode |
| 3 · Inside the CPU | + Step cycle, the CPU diagram and cycle inspector, MAR/MDR/IR, and the walkthrough beside the diagram (every step of the program, the current one marked, Go here to jump to any step, breakpoints), which replaces the disassembly with the Memory tab while the diagram is shown |
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

**Explain this step** (the fold-out panel at the top of the right-hand
column) explains every step as the student takes it: what the instruction
just did, with the real values, and what the next instruction will do, where
the program goes after it and why (from the shape of the code and the
comment on that line). The forecast is exact: `picosim/explain.py` runs the
next instruction on a scratch copy of the machine. **More detail** adds what
changed, how the instruction works, its encoding and, from level 3, its clock
cycles. While Run is on the panel is not rebuilt.

The guide (F1) lists only the topics for the current level. Pass
`--level=N` after Godot's `--` to override the level for one run, e.g. for
classroom demos or screenshots. It is written as the course's reference for
students who start from nothing: each level has a reading path on its
*Start here* page, lessons with values taken from the bundled examples, and
a glossary that grows with the level. Every page opens with a short version
(two paragraphs at most) and folds out the full details on demand. Click a
section heading in its topic list to fold that section away; the choice is
remembered.

The GUI is built to stay light on battery: Godot runs in low-processor mode
(it redraws only when something on screen changes), the backend sleeps until
the next clock cycle is due when Run has a set speed and sends only real
progress, panels skip updates that change nothing, the Pico board is drawn
once into a texture, and while Run is on the diagram's pulse is redrawn at
30 frames a second.

Raising the Level switches on what the new level adds (the Memory tab at 2,
the CPU diagram at 3 (with the heat overlay on it), the 2-stage pipeline at 4), shows a "New at Level N"
strip with a link to the matching lesson, and outlines the new controls with
a NEW tag for a few seconds (`gui/scripts/spotlight.gd`). Pass
`--demo-level-up N` to see it without saving the level.

- **Registers** (top left): R0–R12, SP, LR, PC, the NZCV flags and the
  datapath registers MAR, MDR, IR and IR2. Anything changed by the last step
  is shown in yellow. Type a value and press Enter to change a register, or
  click a flag to toggle it.
- **Memory** (bottom left): the *Disassembly* tab follows the PC; click the
  ● column to toggle a breakpoint. The *Memory* tab has a hex dump with
  go-to, byte editing and a stack view. At Levels 3 and 4, while the CPU
  diagram is shown, only the *Memory* tab is shown: the walkthrough beside
  the diagram lists the program instead, with the ▶ marker, the ● breakpoint
  column and Skip to main. Hiding the diagram brings the Disassembly back.
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

### Testing against a real RP2040

**Testing** (the mode menu's last entry, or `python3 -m picosim.hwtest`)
runs a program on a real RP2040 and in picosim at once, through a second
Pico running Raspberry Pi's debugprobe firmware wired to the target's SWD
pins. Step-by-step tests compare every register, flag and memory write
after every instruction; timing tests compare clock cycles measured with
SysTick against picosim's RP2040 model. The suite is in
[hwtests/](hwtests/); a stand-in emulator lets you try it without hardware.
See [docs/hardware-testing.md](docs/hardware-testing.md).

Every instruction takes several clock cycles, as described in the course
text's datapath chapter. See [docs/cycles.md](docs/cycles.md) for the count
and state sequence of each instruction.

Run the tests with `python3 -m unittest discover tests`.

