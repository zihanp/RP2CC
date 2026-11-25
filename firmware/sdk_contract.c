// Compile/link audit only. No function in this table is called by the demo.
// Force-retained by --capability-smoke so the link actually resolves the APIs.
#include "hardware/adc.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/exception.h"
#include "hardware/flash.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "hardware/interp.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
#include "hardware/pll.h"
#include "hardware/powman.h"
#include "hardware/pwm.h"
#include "hardware/sha256.h"
#include "hardware/spi.h"
#include "hardware/sync.h"
#include "hardware/ticks.h"
#include "hardware/timer.h"
#include "hardware/uart.h"
#include "hardware/vreg.h"
#include "hardware/watchdog.h"
#include "hardware/xip_cache.h"
#include "hardware/structs/hstx_ctrl.h"
#include "hardware/structs/hstx_fifo.h"
#include "hardware/structs/usb.h"
#include "hardware/structs/qmi.h"
#include "hardware/structs/trng.h"
#include "hardware/structs/otp.h"
#include "hardware/structs/accessctrl.h"
#include "hardware/structs/tmds_encode.h"
#include "pico/bootrom.h"
#include "pico/multicore.h"
#include "pico/rand.h"
#include "pico/unique_id.h"

typedef void (*probe_fn)(void);
#define BIND(f) ((probe_fn)(f))
struct rp2lab_sdk_contract {
    probe_fn functions[27];
    volatile const void *register_blocks[8];
};

const struct rp2lab_sdk_contract rp2lab_sdk_contract = {
    {
        BIND(adc_read), BIND(clock_get_hz), BIND(dma_channel_is_busy),
        BIND(exception_get_vtable_handler), BIND(flash_get_unique_id), BIND(gpio_get),
        BIND(i2c_get_read_available), BIND(interp_claim_lane), BIND(irq_is_enabled),
        BIND(pio_sm_is_rx_fifo_empty), BIND(pll_init), BIND(powman_timer_get_ms),
        BIND(pwm_get_counter), BIND(sha256_is_sum_valid), BIND(spi_is_busy),
        BIND(save_and_disable_interrupts), BIND(tick_is_running), BIND(time_us_64),
        BIND(uart_is_readable), BIND(vreg_get_voltage), BIND(watchdog_caused_reboot),
        BIND(xip_cache_invalidate_all), BIND(rom_get_sys_info), BIND(multicore_fifo_rvalid),
        BIND(get_rand_32), BIND(pico_get_unique_board_id), BIND(pio_get_dreq),
    },
    { hstx_ctrl_hw, hstx_fifo_hw, usb_hw, qmi_hw, trng_hw, otp_hw, accessctrl_hw, tmds_encode_hw },
};
