// RP2354A QFN60 with 2 MiB in-package SPI NOR. Assume a 12 MHz reference crystal.
#ifndef _BOARDS_RP2LAB_2354A_H
#define _BOARDS_RP2LAB_2354A_H
pico_board_cmake_set(PICO_PLATFORM, rp2350)
pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (2 * 1024 * 1024))
#define PICO_RP2350A 1
#define PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H 1
#define PICO_FLASH_SPI_CLKDIV 4
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)
#endif
pico_board_cmake_set_default(PICO_RP2350_A2_SUPPORTED, 1)
#define PICO_RP2350_A2_SUPPORTED 1
#endif
