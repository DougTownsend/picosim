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


def _cache_dir():
    """
    Persistent build cache directory.  Reused across invocations so that
    cmake/ninja only recompile what changed (primarily asm_main.s and the
    final link), avoiding repeated picotool downloads and SDK recompilation.

    Uses the platform-appropriate cache location:
      Linux   ~/.cache/picosim
      macOS   ~/Library/Caches/picosim
      Windows %LOCALAPPDATA%\\picosim
    """
    try:
        from platformdirs import user_cache_dir
        return os.path.join(user_cache_dir("picosim"), "build")
    except ImportError:
        return os.path.expanduser("~/.cache/picosim/build")


def _write_if_changed(path, content):
    """Write *content* to *path* only when the file is missing or differs.

    Preserving the mtime when the content is unchanged prevents cmake and
    ninja from treating the file as modified and triggering a full rebuild.
    """
    if isinstance(content, str):
        content = content.encode()
    if os.path.isfile(path) and open(path, "rb").read() == content:
        return  # identical — leave the mtime alone
    with open(path, "wb") as f:
        f.write(content)


def build_uf2(s_file):
    """
    Build a .uf2 from *s_file* and place it alongside the source file.

    The build tree is kept at ~/.cache/picosim/build between runs.  cmake and
    ninja track dependencies, so only asm_main.s and the final link are redone
    on subsequent calls — the SDK objects and picotool download are cached.
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

    src_dir  = _script_dir()
    cache    = _cache_dir()
    src_root = cache          # CMakeLists.txt / wrapper_main.c / asm_main.s live here
    build_dir = os.path.join(cache, "build")
    os.makedirs(build_dir, exist_ok=True)

    wrapper_c  = os.path.join(src_dir, "wrapper_main.c")
    cmake_file = os.path.join(src_dir, "CMakeLists.txt")

    out_dir = os.path.dirname(s_file)
    base    = os.path.splitext(os.path.basename(s_file))[0]
    uf2_out = os.path.join(out_dir, base + ".uf2")

    # Sync support files into the cache source root — only write when content
    # changed so cmake/ninja don't see spurious mtime updates.
    _write_if_changed(os.path.join(src_root, "CMakeLists.txt"),
                      open(cmake_file, "rb").read())
    _write_if_changed(os.path.join(src_root, "wrapper_main.c"),
                      open(wrapper_c,  "rb").read())

    # Write the processed assembly — rename `main:` → `asm_main:` and inject
    # `.global asm_main` so C's linker can see the symbol across object files.
    asm_src = open(s_file).read()
    asm_src = re.sub(r'(?m)^(main)(:)', r'asm_main\2', asm_src)
    asm_src = re.sub(r'(?m)^(asm_main:)', r'.global asm_main\n\1', asm_src)
    _write_if_changed(os.path.join(src_root, "asm_main.s"), asm_src)

    env = os.environ.copy()
    env["PICO_SDK_PATH"] = sdk

    # Configure only if no cmake cache exists yet
    if not os.path.isfile(os.path.join(build_dir, "CMakeCache.txt")):
        print("Configuring with cmake...")
        r = subprocess.run(
            [cmake, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", src_root],
            cwd=build_dir, env=env, check=False,
        )
        if r.returncode != 0:
            print("Error: cmake configuration failed.", file=sys.stderr)
            return False

    # Build (ninja rebuilds only what changed)
    print("Building with ninja...")
    r = subprocess.run(
        [ninja],
        cwd=build_dir, env=env, check=False,
    )
    if r.returncode != 0:
        print("Error: ninja build failed.", file=sys.stderr)
        return False

    # Copy the .uf2 to the output location
    built_uf2 = os.path.join(build_dir, "firmware.uf2")
    if not os.path.isfile(built_uf2):
        print("Error: firmware.uf2 was not produced.", file=sys.stderr)
        return False

    shutil.copy2(built_uf2, uf2_out)
    print(f"Created {uf2_out}")
    return True
