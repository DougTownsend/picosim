"""
Build the picosim C++ extension (_picosim_core) using pybind11.
Invoked automatically by `pip install .` via setuptools.
"""
from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext = Pybind11Extension(
    "picosim._picosim_core",
    sources=["picosim/picosim_core.cpp"],
    cxx_std=17,
    extra_compile_args=["-O3", "-DNDEBUG"],
)

setup(
    ext_modules=[ext],
    cmdclass={"build_ext": build_ext},
)
