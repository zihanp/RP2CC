#ifndef RP2LAB_IO_H
#define RP2LAB_IO_H
#include <stdint.h>
// Use on an otherwise idle board during startup, before launching core 1.
// Result bits: 0/1/2 = PIO0/1/2 FIFO echo, bit 3 = exact 32-word SRAM DMA copy.
uint32_t rp2lab_io_selftest(void);
#endif
