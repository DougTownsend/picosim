# Dependencies

Everything picosim needs, and what each part is for. Step-by-step install
commands for Linux, macOS and Windows are in [installation.md](installation.md).

## To run the simulator

| Dependency | Used for | How it is installed |
|------------|----------|---------------------|
| Python 3.8 or newer | the simulator, its command line and the GUI backend | python.org, or your package manager |
| A C++17 compiler | building the simulator's C++ core (`picosim/picosim_core.cpp`) during `pip install` | Xcode Command Line Tools (macOS), `build-essential` (Debian/Ubuntu), Visual Studio Build Tools (Windows) |
| `pybind11`, `setuptools` | building the C++ core | fetched automatically by `pip install` |
| `pyelftools` | loading the compiled ELF program | installed automatically by `pip install` |
| `capstone` | disassembling instructions for the listings and traces | installed automatically |
| `pyserial` | talking to a real Pico over USB serial (GUI) | installed automatically |
| `cmake`, `ninja` | building `.uf2` firmware with the Pico SDK (`--uf2`, `--flash`, the GUI's Flash) | installed automatically |
| `arm-none-eabi-gcc` | assembling and linking your `.s` files | see installation.md |

## Optional

| Dependency | Needed for |
|------------|------------|
| Godot 4 (4.7 tested) | the graphical simulator, `picosim --gui`; put `godot` on your PATH or set `PICOSIM_GODOT` |
| git | `--uf2` / `--flash`: the Pico SDK is cloned to `~/pico-sdk` on first use (or set `PICO_SDK_PATH`) |
| An ARM toolchain **with newlib** | `--uf2` / `--flash`: the Pico SDK links against the C library. The Arm GNU Toolchain has it (`brew install --cask gcc-arm-embedded` on macOS); Homebrew's plain `arm-none-eabi-gcc` formula does not, but it is fine for the simulator itself |
| `picotool` | `--flash` and the GUI's Flash: uploads without holding BOOTSEL. Without it, picosim copies the `.uf2` to a mounted `RPI-RP2` drive instead |

## Quick check

```bash
python3 --version                                   # 3.8 or newer
python3 -c "import picosim._picosim_core, elftools, capstone, serial; print('packages OK')"
arm-none-eabi-gcc --version
godot --version                                     # only for --gui
```
