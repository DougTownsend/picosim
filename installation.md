# Installation

The simulator requires Python 3, a C++ compiler (to build its core), the ARM cross-compiler toolchain, and a few Python packages (installed automatically). The graphical simulator also needs Godot 4. [DEPENDENCIES.md](DEPENDENCIES.md) says what each one is for.

---

## 1. Python 3 (3.8 or newer)

**Linux**
Most distributions include Python 3. If not:
```bash
# Debian / Ubuntu
sudo apt install python3 python3-pip

# Fedora / RHEL
sudo dnf install python3

# Arch
sudo pacman -S python
```

**macOS**
```bash
brew install python3
```
Or download the installer from [python.org](https://www.python.org/downloads/).

**Windows**
Download the installer from [python.org](https://www.python.org/downloads/).
During installation, check **"Add Python to PATH"**.

---

## 2. A C++ compiler

`pip install` compiles the simulator's core (C++17).

**Linux (Debian / Ubuntu)**
```bash
sudo apt install build-essential python3-dev
```

**Linux (Fedora / RHEL)**
```bash
sudo dnf install gcc-c++ python3-devel
```

**macOS**
```bash
xcode-select --install
```

**Windows**
Install the [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with the "Desktop development with C++" workload.

---

## 3. Install the simulator

From the root of the repository:

```bash
pip install -e .
```

This builds the C++ core, installs the `picosim` command and pulls in the required Python packages (`pyelftools`, `capstone`, `pyserial`, `cmake`, `ninja`). The `-e` (editable install) matters: the graphical simulator and the example programs are read from this folder, so keep it where it is.

On Linux you may need `pip3` instead of `pip`. If you change `picosim/picosim_core.cpp`, run the command again to rebuild the core.

---

## 4. Git

Git is required only when using `--uf2` or `--flash` (to clone the pico-sdk on first use).

**Linux**
```bash
# Debian / Ubuntu
sudo apt install git

# Fedora / RHEL
sudo dnf install git

# Arch
sudo pacman -S git
```

**macOS**
```bash
brew install git
```
Or install Xcode Command Line Tools, which includes git:
```bash
xcode-select --install
```

**Windows**
```powershell
winget install --id Git.Git -e --source winget
```
After installation open a new terminal so the updated PATH takes effect.
Alternatively download the installer from [git-scm.com](https://git-scm.com/download/win).

---

## 5. arm-none-eabi-gcc

This is the ARM cross-compiler that assembles and links your `.s` files. For `--uf2` / `--flash` it must include the C library (newlib), which the Pico SDK links against; the packages below do, except Homebrew's plain `arm-none-eabi-gcc` formula — on macOS use the cask shown.

**Linux (Debian / Ubuntu)**
```bash
sudo apt install gcc-arm-none-eabi
```

**Linux (Fedora / RHEL)**
```bash
sudo dnf install arm-none-eabi-gcc arm-none-eabi-binutils
```

**Linux (Arch)**
```bash
sudo pacman -S arm-none-eabi-gcc
```

**macOS**
```bash
brew install --cask gcc-arm-embedded
```
This installs ARM's official binary distribution via Homebrew Cask.
Homebrew itself can be installed from [brew.sh](https://brew.sh).

**Windows**
1. Download the installer from [ARM's toolchain page](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads).
   Choose the **arm-none-eabi** variant for your machine (usually the `x86_64-mingw-w64` package).
2. Run the installer.
3. On the final screen, check **"Add path to environment variable"** so the tools are on your PATH.
4. Open a new Command Prompt and verify:
   ```
   arm-none-eabi-gcc --version
   ```

---

## 6. Godot 4 (for the graphical simulator)

`picosim --gui` runs the GUI in [Godot 4](https://godotengine.org/download) (4.7 is tested).

**macOS**
```bash
brew install --cask godot
```

**Linux / Windows**
Download Godot from [godotengine.org](https://godotengine.org/download) and put the `godot` executable on your PATH, or set the `PICOSIM_GODOT` environment variable to its full path.

---

## Quick check

After installing everything, run these commands to confirm:

```bash
python3 --version              # Python 3.8 or newer
picosim --help                 # simulator installed and on PATH
arm-none-eabi-gcc --version    # cross-compiler found
```

To use the `--flash` option (and the GUI's Flash button) without holding BOOTSEL, install `picotool` as well:

```bash
brew install picotool          # macOS
```

On Linux and Windows, see the [picotool README](https://github.com/raspberrypi/picotool). Without picotool, picosim copies the `.uf2` to the Pico's `RPI-RP2` drive (hold BOOTSEL while plugging it in).

Run the test suite with `python3 -m unittest discover tests`.
