# Installation

The simulator requires Python 3, the ARM cross-compiler toolchain, and a couple of Python packages (installed automatically).

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

## 2. Install the simulator

From the root of the repository:

```bash
pip install .
```

This installs the `picosim` command and automatically pulls in the required Python packages (`pyelftools` and `capstone`).

On Linux you may need `pip3` instead of `pip`.  
For an editable/development install where source changes take effect immediately:

```bash
pip install -e .
```

---

## 3. Git

Git is required only when using `--uf2` (to clone the pico-sdk on first use).

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

## 4. arm-none-eabi-gcc

This is the ARM cross-compiler that assembles and links your `.s` files.

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

## Quick check

After installing everything, run these commands to confirm:

```bash
python3 --version              # Python 3.8 or newer
picosim --help                 # simulator installed and on PATH
arm-none-eabi-gcc --version    # cross-compiler found
```
