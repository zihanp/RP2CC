cmake_minimum_required(VERSION 3.19)
list(APPEND PICO_BOARD_HEADER_DIRS "${CMAKE_CURRENT_LIST_DIR}/boards")
include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")
project(rp2lab C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
pico_sdk_init()
add_executable(rp_lab "${RP2_APP}" "${RP2_GENERATED}/kernels.c" rp2lab_io.c ${RP2_EXTRA_SOURCES})
get_filename_component(APP_DIR "${RP2_APP}" DIRECTORY)
target_include_directories(rp_lab PRIVATE "${RP2_GENERATED}" "${CMAKE_CURRENT_LIST_DIR}" "${APP_DIR}")
pico_generate_pio_header(rp_lab "${CMAKE_CURRENT_LIST_DIR}/echo.pio")
foreach(pio_source IN LISTS RP2_EXTRA_PIO)
    pico_generate_pio_header(rp_lab "${pio_source}")
endforeach()
# The SDK lib list is explicit. Optional libraries are selected by the caller.
target_link_libraries(rp_lab PRIVATE pico_stdlib pico_bootrom hardware_pio hardware_dma
    hardware_sync hardware_clocks ${RP2_EXTRA_LIBS})
if(RP2_CAPS)
    target_sources(rp_lab PRIVATE sdk_contract.c)
    target_link_libraries(rp_lab PRIVATE hardware_adc hardware_gpio hardware_uart hardware_spi hardware_i2c
        hardware_pwm hardware_timer hardware_watchdog hardware_irq hardware_interp hardware_clocks
        hardware_pll hardware_vreg hardware_powman hardware_ticks hardware_xip_cache hardware_flash
        hardware_sha256 hardware_exception hardware_sync pico_multicore pico_rand pico_unique_id)
    target_link_options(rp_lab PRIVATE "LINKER:--undefined=rp2lab_sdk_contract")
endif()
if(RP2_RAM)
    pico_set_binary_type(rp_lab no_flash)
else()
    # SDK's default E10 marker is at the end of a 16 MiB address window.
    # Place it in the last sector of the selected, actually addressable flash.
    set_property(TARGET rp_lab PROPERTY PICOTOOL_EXTRA_UF2_ARGS "--abs-block;${RP2_E10_ADDR}")
endif()
pico_enable_stdio_uart(rp_lab 0)
if(RP2_USB_STDIO)
    if(NOT TARGET pico_stdio_usb)
        message(FATAL_ERROR "USB CDC requested; the SDK's pinned TinyUSB submodule is missing")
    endif()
    target_compile_definitions(rp_lab PRIVATE PICO_STDIO_USB_ENABLE_RESET_VIA_BAUD_RATE=0
        PICO_STDIO_USB_ENABLE_RESET_VIA_VENDOR_INTERFACE=0 PICO_STDIO_USB_CONNECT_WAIT_TIMEOUT_MS=0)
    pico_enable_stdio_usb(rp_lab 1)
else()
    pico_enable_stdio_usb(rp_lab 0)
endif()
pico_add_extra_outputs(rp_lab)
