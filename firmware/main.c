#include <stdint.h>
#include "kernels.h"
#include "rp2lab_io.h"
#include "pico/stdlib.h"

// Visible in ELF for a debugger, without requiring any GPIO or USB console.
volatile struct { uint32_t magic, io_pass_bits, packed, affine, checks; } rp2lab_status;
int main(void) {
    const uint32_t x = 0x8000ffffu; // -32768 and -1, floor division is lane-wise
    uint32_t ref = x;
    for (uint i = 0; i < 257; ++i) ref = 1664525u * ref + 1013904223u;
    rp2lab_status.io_pass_bits = rp2lab_io_selftest();
    rp2lab_status.packed = rp_floor2(x);
    rp2lab_status.affine = rp_affine(x);
    rp2lab_status.checks = (rp2lab_status.packed == 0xf800ffffu)
                            | ((rp2lab_status.affine == ref) << 1);
    rp2lab_status.magic = 0x5250324cu;
    for (;;) tight_loop_contents();
}
