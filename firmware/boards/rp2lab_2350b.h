// Pin-neutral QFN80 RP2350B, 4 MiB external SPI NOR, 12 MHz reference.
#ifndef _BOARDS_RP2LAB_2350B_H
#define _BOARDS_RP2LAB_2350B_H
pico_board_cmake_set(PICO_PLATFORM, rp2350)
pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (4 * 1024 * 1024))
#define PICO_RP2350A 0
#define PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H 1
#define PICO_FLASH_SPI_CLKDIV 4
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (4 * 1024 * 1024)
#endif
pico_board_cmake_set_default(PICO_RP2350_A2_SUPPORTED, 1)
#define PICO_RP2350_A2_SUPPORTED 1
#endif
