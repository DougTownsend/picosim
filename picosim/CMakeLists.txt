cmake_minimum_required(VERSION 3.13)

# pico_sdk_import.cmake is distributed with the SDK.
# PICO_SDK_PATH must be set in the environment (uf2.py sets it before invoking cmake).
include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(firmware C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

add_executable(firmware
    wrapper_main.c
    asm_main.s
)

target_link_libraries(firmware
    pico_stdlib
)

# Route stdio over USB; disable UART stdio
pico_enable_stdio_usb(firmware 1)
pico_enable_stdio_uart(firmware 0)

# Generate .uf2, .bin, .hex, .map alongside the ELF
pico_add_extra_outputs(firmware)
