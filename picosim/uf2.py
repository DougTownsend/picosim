"""
UF2 builder for the ARMv6-M simulator.

Compiles a user .s file (plus a C wrapper that initialises the Pico hardware)
into a .uf2 image that can be drag-dropped onto a Raspberry Pi Pico.

Usage (called from sim.py --uf2):
    from .uf2 import build_uf2
    build_uf2(s_file)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile


# ── SDK location ───────────────────────────────────────────────────────────────

PICO_SDK_URL = "https://github.com/raspberrypi/pico-sdk.git"


def _sdk_path():
    """
    Return the pico-sdk directory.

    Priority:
      1. PICO_SDK_PATH environment variable (standard Pico convention)
      2. ~/pico-sdk  (os.path.expanduser works on Linux, macOS, and Windows)
    """
    env = os.environ.get("PICO_SDK_PATH")
    if env:
        return os.path.abspath(env)
    return os.path.expanduser("~/pico-sdk")


def ensure_pico_sdk():
    """Clone the pico-sdk (with submodules) if it is not already present."""
    sdk = _sdk_path()
    if os.path.isdir(sdk):
        return sdk

    if shutil.which("git") is None:
        print("Error: git is not on PATH — needed to clone the pico-sdk.",
              file=sys.stderr)
        print("See installation.md for instructions.", file=sys.stderr)
        return None

    print(f"pico-sdk not found at {sdk}")
    print(f"Cloning {PICO_SDK_URL} ...")
    result = subprocess.run(
        ["git", "clone", "--recurse-submodules", PICO_SDK_URL, sdk],
        check=False,
    )
    if result.returncode != 0:
        print("Error: failed to clone pico-sdk.", file=sys.stderr)
        return None

    print("pico-sdk cloned successfully.")
    return sdk


# ── Tool finders ───────────────────────────────────────────────────────────────

def _cmake_exe():
    """
    Return the path to the cmake binary.

    Prefers the cmake installed by the 'cmake' pip package (which may not be
    on PATH) over any system cmake.
    """
    try:
        import cmake as cmake_pkg
        exe = os.path.join(cmake_pkg.CMAKE_BIN_DIR, "cmake")
        if os.path.isfile(exe):
            return exe
    except ImportError:
        pass
    return shutil.which("cmake")


def _ninja_exe():
    """
    Return the path to the ninja binary.

    Prefers the ninja installed by the 'ninja' pip package.
    """
    try:
        import ninja as ninja_pkg
        exe = os.path.join(ninja_pkg.BIN_DIR, "ninja")
        if os.path.isfile(exe):
            return exe
    except ImportError:
        pass
    return shutil.which("ninja")


# ── UF2 builder ────────────────────────────────────────────────────────────────

def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def build_uf2(s_file):
    """
    Build a .uf2 from *s_file* and place it alongside the source file.

    Steps:
      1. Ensure the pico-sdk is present.
      2. Create a temporary build directory in the cwd.
      3. Copy wrapper_main.c and CMakeLists.txt into it.
      4. Copy the assembly file, renaming the `main:` label to `asm_main:`
         and injecting `.global asm_main` so C's linker can see the symbol.
      5. Run cmake + ninja to produce firmware.uf2.
      6. Move firmware.uf2 next to the original .s file.
      7. Remove the temporary directory.
    """
    s_file = os.path.abspath(s_file)
    if not os.path.isfile(s_file):
        print(f"Error: assembly file not found: {s_file}", file=sys.stderr)
        return False

    sdk = ensure_pico_sdk()
    if sdk is None:
        return False

    cmake = _cmake_exe()
    ninja = _ninja_exe()
    for name, exe in (("cmake", cmake), ("ninja", ninja)):
        if exe is None:
            print(f"Error: '{name}' not found.", file=sys.stderr)
            print("Install it with:  pip install cmake ninja", file=sys.stderr)
            return False

    src_dir = _script_dir()
    wrapper_c  = os.path.join(src_dir, "wrapper_main.c")
    cmake_file = os.path.join(src_dir, "CMakeLists.txt")

    out_dir = os.path.dirname(s_file)
    base    = os.path.splitext(os.path.basename(s_file))[0]
    uf2_out = os.path.join(out_dir, base + ".uf2")

    tmp = tempfile.mkdtemp(dir=os.getcwd(), prefix="_picosim_build_")
    try:
        # Copy support files
        shutil.copy(wrapper_c,  tmp)
        shutil.copy(cmake_file, tmp)

        # Copy assembly, renaming `main:` label to `asm_main:` if needed,
        # and injecting `.global asm_main` so C's linker can see the symbol.
        asm_src = open(s_file).read()
        asm_src = re.sub(r'(?m)^(main)(:)', r'asm_main\2', asm_src)
        asm_src = re.sub(r'(?m)^(asm_main:)', r'.global asm_main\n\1', asm_src)
        asm_dst = os.path.join(tmp, "asm_main.s")
        with open(asm_dst, "w") as f:
            f.write(asm_src)

        # Configure
        env = os.environ.copy()
        env["PICO_SDK_PATH"] = sdk

        build_dir = os.path.join(tmp, "build")
        os.makedirs(build_dir)

        print("Configuring with cmake...")
        r = subprocess.run(
            [cmake, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", ".."],
            cwd=build_dir, env=env, check=False,
        )
        if r.returncode != 0:
            print("Error: cmake configuration failed.", file=sys.stderr)
            return False

        # Build
        print("Building with ninja...")
        r = subprocess.run(
            [ninja],
            cwd=build_dir, env=env, check=False,
        )
        if r.returncode != 0:
            print("Error: ninja build failed.", file=sys.stderr)
            return False

        # Locate and move the .uf2
        built_uf2 = os.path.join(build_dir, "firmware.uf2")
        if not os.path.isfile(built_uf2):
            print("Error: firmware.uf2 was not produced.", file=sys.stderr)
            return False

        shutil.move(built_uf2, uf2_out)
        print(f"Created {uf2_out}")
        return True

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
