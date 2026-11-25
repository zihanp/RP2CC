/* A fixed, unflashed XIP fixture: keep all byte getters live in the linked ELF.
 * No kernel call, so section GC isolates the pool layout under study. */
#include <stdint.h>
#include "kernels.h"
#include "pico/stdlib.h"
volatile uint32_t rp_footprint_index;
volatile uint32_t rp_footprint_result;
int main(void) {
    uint32_t i=rp_footprint_index;
    rp_footprint_result=rp_rom_ramp(i)+rp_rom_middle(i)+rp_rom_tail(i)+
                        rp_rom_sweep(i)+rp_rom_sync(i)+rp_rom_empty(i);
    for (;;) tight_loop_contents();
}
