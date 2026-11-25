#include <stdio.h>
#include "pico/stdlib.h"
#include "kernels.h"
#include "rp2lab_io.h"

// Optional user-visible demonstration. No USB reset command, UART or GPIO.
int main(void) {
    stdio_init_all();
    const uint32_t io = rp2lab_io_selftest();
    const uint32_t packed = rp_floor2(0x8000ffffu);
    const uint32_t affine = rp_affine(0);
    for (;;) {
        printf("rp2lab io=0x%lx packed=0x%08lx affine(0)=0x%08lx\n",
               (unsigned long)io, (unsigned long)packed, (unsigned long)affine);
        sleep_ms(1000);
    }
}
