/* Differential executable: unoptimized and optimized generated C are separate TUs.
   The reference uses mathematical signed floor, not the generated bit tricks. */
#include "kernels.h"
#include <stdint.h>
#include <stdio.h>

extern uint32_t b_rp_floor2(uint32_t), b_rp_affine(uint32_t), b_rp_calibrated(uint32_t);
extern uint32_t b_rp_restore(uint32_t, uint32_t);
extern uint32_t b_rp_rom_ramp(uint32_t), b_rp_rom_middle(uint32_t), b_rp_rom_tail(uint32_t);
extern uint32_t b_rp_rom_sweep(uint32_t), b_rp_rom_sync(uint32_t), b_rp_rom_empty(uint32_t);

static uint32_t scalar(uint32_t u) {
    uint32_t lo = u & 0xffffu;
    int32_t s = (int32_t)lo - (lo >= 0x8000u ? 65536 : 0);
    int32_t q = s >= 0 ? s / 16 : -((15 - s) / 16);
    return (uint32_t)q & 0xffffu;
}
static int floor_ok(uint32_t x) {
    uint32_t expected = scalar(x) | (scalar(x >> 16) << 16);
    return rp_floor2(x) == expected && b_rp_floor2(x) == expected;
}
static uint32_t lcg257(uint32_t x) {
    for (unsigned i = 0; i != 257; ++i) x = UINT32_C(1013904223) + x * UINT32_C(1664525);
    return x;
}

int main(void) {
    for (uint32_t i = 0; i < 65536; ++i) {
        uint32_t p = (i * 32771u + 0xabd1u) & 0xffffu;
        if (!floor_ok((p << 16) | i) || !floor_ok((i << 16) | p)) return 1;
    }
    const uint32_t edges[] = {0u,1u,15u,16u,17u,0x7fefu,0x7fffu,0x8000u,0x8001u,0xffefu,0xfff0u,0xffffu};
    for (unsigned i = 0; i < 12; ++i) for (unsigned j = 0; j < 12; ++j)
        if (!floor_ok(edges[i] | (edges[j] << 16))) return 2;
    uint32_t seed = UINT32_C(0x6d2b79f5);
    for (uint32_t i = 0; i < 4096; ++i) {
        seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
        uint32_t x = i == 0 ? 0u : i == 1 ? UINT32_MAX : seed;
        uint32_t expected = lcg257(x);
        if (rp_affine(x) != expected || b_rp_affine(x) != expected) return 3;
        expected = (x * 17u + 32u) ^ (x >> 16);
        if (rp_calibrated(x) != expected || b_rp_calibrated(x) != expected) return 4;
        if (rp_restore(x, ~x) != x || b_rp_restore(x, ~x) != x) return 5;
    }
    static const uint8_t sync[] = {0xa5, 0, 0xff, 0x5a};
    for (uint32_t j = 0; j <= 257; ++j) {
        uint32_t i = j == 257 ? UINT32_MAX : j;
        const uint32_t expect[] = {i < 64 ? i : 0u, i < 32 ? 16u + i : 0u,
                                    i < 16 ? 48u + i : 0u, i < 32 ? 48u + i : 0u,
                                    i < 4 ? sync[i] : 0u, 0u};
        const uint32_t opt[] = {rp_rom_ramp(i), rp_rom_middle(i), rp_rom_tail(i), rp_rom_sweep(i), rp_rom_sync(i), rp_rom_empty(i)};
        const uint32_t base[] = {b_rp_rom_ramp(i), b_rp_rom_middle(i), b_rp_rom_tail(i), b_rp_rom_sweep(i), b_rp_rom_sync(i), b_rp_rom_empty(i)};
        for (unsigned k = 0; k < 6; ++k) if (opt[k] != expect[k] || base[k] != expect[k]) return 6;
    }
    puts("{\"floor_word_cases\":131216,\"unique_values_per_lane\":65536,\"affine_cases\":4096,\"calibrated_cases\":4096,\"restore_cases\":4096,\"blob_value_cases\":1548,\"compiled_variants\":2}");
    return 0;
}
