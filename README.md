# picosim

An ARMv6-M (Cortex-M0+) simulator for learning ARM assembly.

---

## Installation

Install Python 3 and `arm-none-eabi-gcc`, then run:

```bash
pip install .
```

See [installation.md](installation.md) for platform-specific instructions.

---

## Usage

```bash
picosim <file.s>           # run interactively
picosim --run <file.s>     # run to completion
picosim --trace <file.s>   # run with instruction trace
picosim --uf2 <file.s>     # build a .uf2 for the Raspberry Pi Pico
picosim --flash <file.s>   # build and upload a .uf2 to the Pico
picosim --gui [file.s]     # open the graphical simulator (Godot 4)
picosim --cycle-table      # print per-instruction cycle counts
```

The simulator assembles and links your `.s` file automatically, then starts
execution.  When the program finishes it drops into an interactive prompt where
you can inspect registers and memory.

### Interactive commands

| Command | Description |
|---------|-------------|
| `s` | Step one instruction |
| `r` | Print registers and flags |
| `m <addr> [n]` | Dump memory at address (default 64 bytes) |
| `c` | Continue running until halt |
| `q` | Quit |
| `h` | Show help |

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

See [test_asm_files/echo_test.s](test_asm_files/echo_test.s) for a working example.

The build wraps your assembly in a small C stub that calls `stdio_init_all()`
before jumping to your code, so `putchar` and `getchar` work over USB serial
exactly as they do in the simulator.

The SDK is cloned automatically to `~/pico-sdk` on first use (requires `git`).
Subsequent builds reuse the cached SDK objects — only your assembly and the
final link step are redone.

> **Note:** if `main:` is your entry-point label the build renames it to
> `asm_main` automatically.  Alternatively, name it `asm_main:` in your source
> to make the intent explicit (as shown in the I/O example above).

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

This needs Godot 4 (`godot` on your PATH, or set `PICOSIM_GODOT` to the
binary). The Godot project lives in [gui/](gui/) and talks to a Python backend
([picosim/server.py](picosim/server.py)) over a local TCP socket.

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
  the on-board LED lights when GP25 is high, and the most recent I/O
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

| Key | Action |
|-----|--------|
| F11 | step one clock cycle |
| F10 | step one instruction (finishes the current one) |
| F5  | run / pause (the speed slider sets cycles per second) |
| Ctrl/Cmd+O | open a `.s` file |
| Ctrl/Cmd+R | reload (re-assemble and reset) |

Every instruction takes several clock cycles, as described in the course
text's datapath chapter. See [docs/cycles.md](docs/cycles.md) for the count
and state sequence of each instruction.

Run the tests with `python3 -m unittest discover tests`.

