"""Install Raspberry Pi's pinned Windows Hazard3 GCC 16 archive into this project.

Verifies the entire official release-asset SHA-256. No device or system installation.
An installed risc-v toolchain can instead be passed to build_firmware.py --toolchain.
"""
from fetch_arm_windows import DEPS, install

NAME = "riscv-toolchain-16-x64-win.zip"
URL = "https://github.com/raspberrypi/pico-sdk-tools/releases/download/v2.3.1-0/" + NAME
SIZE = 152335247
SHA256 = "bff41ef9d213b351e00a7d8e0a0a147e9e8cf3d270db5fa854435f149e65c471"

if __name__ == "__main__":
    install(NAME, URL, SHA256, SIZE, "riscv", DEPS/"riscv", __doc__)
