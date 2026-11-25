#include "rp2lab_io.h"
#include "hardware/dma.h"
#include "hardware/pio.h"
#include "hardware/sync.h"
#include "echo.pio.h"

enum { SPINS = 100000, DMA_WORDS = 32 };

static bool fifo_echo(PIO pio, uint32_t salt) {
    int sm = pio_claim_unused_sm(pio, false);
    if (sm < 0) return false;
    int offset = pio_add_program(pio, &rp2lab_echo_program);
    if (offset < 0) {
        pio_sm_unclaim(pio, (uint)sm);
        return false;
    }
    pio_sm_config cfg = rp2lab_echo_program_get_default_config((uint)offset);
    bool ok = pio_sm_init(pio, (uint)sm, (uint)offset, &cfg) == 0;
    if (ok) pio_sm_set_enabled(pio, (uint)sm, true);
    for (uint32_t i = 0; ok && i < 8; ++i) {
        uint32_t v = (0x5555aaaau ^ salt) + 0x9e3779b9u * i;
        // At most one outstanding word: there must be space in the TX FIFO.
        if (pio_sm_is_tx_fifo_full(pio, (uint)sm)) { ok = false; break; }
        pio_sm_put(pio, (uint)sm, v);
        uint32_t spins = 0;
        while (pio_sm_is_rx_fifo_empty(pio, (uint)sm) && spins < SPINS) ++spins;
        if (pio_sm_is_rx_fifo_empty(pio, (uint)sm)) { ok = false; break; }
        ok = pio_sm_get(pio, (uint)sm) == v;
    }
    pio_sm_set_enabled(pio, (uint)sm, false);
    pio_sm_clear_fifos(pio, (uint)sm);
    pio_remove_program(pio, &rp2lab_echo_program, (uint)offset);
    pio_sm_unclaim(pio, (uint)sm);
    return ok;
}

// Static SRAM buffers remain valid even if a faulty channel does not finish.
static uint32_t dma_src[DMA_WORDS];
static volatile uint32_t dma_dst[DMA_WORDS];
static bool dma_echo(void) {
    int ch = dma_claim_unused_channel(false);
    if (ch < 0) return false;
    for (uint32_t i = 0; i < DMA_WORDS; ++i) {
        dma_src[i] = 0x1234abcdu ^ (i * 0x9e3779b9u);
        dma_dst[i] = ~dma_src[i];
    }
    dma_channel_config cfg = dma_channel_get_default_config((uint)ch);
    channel_config_set_transfer_data_size(&cfg, DMA_SIZE_32);
    channel_config_set_read_increment(&cfg, true);
    channel_config_set_write_increment(&cfg, true);
    __dmb();
    dma_channel_configure((uint)ch, &cfg, dma_dst, dma_src, DMA_WORDS, true);
    uint32_t spins = 0;
    while (dma_channel_is_busy((uint)ch) && spins < SPINS) ++spins;
    if (dma_channel_is_busy((uint)ch)) return false; // quarantine; never release a busy channel
    __dmb();
    bool ok = !(dma_channel_hw_addr((uint)ch)->ctrl_trig & DMA_CH0_CTRL_TRIG_AHB_ERROR_BITS);
    for (uint32_t i = 0; i < DMA_WORDS; ++i) if (dma_dst[i] != dma_src[i]) ok = false;
    dma_hw->intr = 1u << (uint)ch;  // discard this channel's completion only
    dma_channel_unclaim((uint)ch);
    return ok;
}

uint32_t rp2lab_io_selftest(void) {
    uint32_t result = 0;
    for (uint k = 0; k < NUM_PIOS; ++k)
        if (fifo_echo(pio_get_instance(k), k)) result |= 1u << k;
    if (dma_echo()) result |= 8u;
    return result;
}
