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
```

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
