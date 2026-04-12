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
picosim <file.s>           # run an assembly file
picosim --trace <file.s>   # run with instruction trace
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
