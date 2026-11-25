#include <stdint.h>
#include "hardware/pio.h"
#include "kernels.h"          /* RP2CC output from pairs.rpm */
#include "fifo_invert.pio.h"  /* pioasm output from project.json's pio[] */

/* Hand-calculated expectations, independent of both the kernel and the PIO. */
static const struct { uint32_t input, scaled, inverted; } samples[] = {
    {0x8000ffffu, 0xf800ffffu, 0x07ff0000u}, /* -32768, -1 */
    {0x7fff0010u, 0x07ff0001u, 0xf800fffeu}, /*  32767, 16 */
    {0x000fffefu, 0x0000fffeu, 0xffff0001u}, /*     15, -17 */
    {0xfff0000fu, 0xffff0000u, 0x0000ffffu}, /*    -16, 15 */
    {0x00000000u, 0x00000000u, 0xffffffffu}
};

enum result { RUNNING=1, PASS, NO_PIO, INIT_ERROR, CPU_MISMATCH, TX_FULL, RX_TIMEOUT, RX_MISMATCH };
volatile struct { uint32_t state, completed, input, cpu_word, rx_word; } pio_math_status;

static bool receive_with_limit(PIO pio, uint sm, uint32_t *word) {
    for (uint poll = 0; poll < 100000; ++poll) {
        if (!pio_sm_is_rx_fifo_empty(pio, sm)) {
            *word = pio_sm_get(pio, sm);
            return true;
        }
    }
    return false;
}

static enum result run_example(void) {
    PIO pio; uint sm, offset;
    if (!pio_claim_free_sm_and_add_program(&fifo_invert_program, &pio, &sm, &offset)) return NO_PIO;
    pio_sm_config cfg = fifo_invert_program_get_default_config(offset);
    enum result result = INIT_ERROR;
    if (pio_sm_init(pio, sm, offset, &cfg) == 0) {
        pio_sm_set_enabled(pio, sm, true);
        result = PASS;
        for (uint i = 0; i < sizeof(samples) / sizeof(samples[0]); ++i) {
            uint32_t input = samples[i].input;
            uint32_t scaled = rp_scale_pair(input);
            pio_math_status.input = input; pio_math_status.cpu_word = scaled;
            if (scaled != samples[i].scaled) { result = CPU_MISMATCH; break; }
            /* We own this state machine and have at most one outstanding word. */
            if (pio_sm_is_tx_fifo_full(pio, sm)) { result = TX_FULL; break; }
            pio_sm_put(pio, sm, scaled);
            uint32_t rx;
            if (!receive_with_limit(pio, sm, &rx)) { result = RX_TIMEOUT; break; }
            pio_math_status.rx_word = rx;
            if (rx != samples[i].inverted) { result = RX_MISMATCH; break; }
            pio_math_status.completed = i + 1;
        }
    }
    pio_sm_set_enabled(pio, sm, false);
    pio_sm_clear_fifos(pio, sm);
    pio_remove_program_and_unclaim_sm(&fifo_invert_program, pio, sm, offset);
    return result;
}

int main(void) {
    pio_math_status.state = RUNNING;
    enum result result = run_example();
    pio_math_status.state = result;  /* Publish terminal status after cleanup. */
    return result == PASS ? 0 : (int)result;
}
