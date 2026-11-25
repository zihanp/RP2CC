/* Independent expert controls for GCC on Arm. The signed shift is arithmetic on this target. */
#include <stdint.h>
#include "bench_config.h"
#pragma GCC optimize ("O3")
uint32_t hand_identity(uint32_t x) { return x; }
uint32_t hand_floor2(uint32_t x) {
    uint16_t lo=(uint16_t)(((int16_t)(uint16_t)x)>>4);
    uint16_t hi=(uint16_t)(((int16_t)(uint16_t)(x>>16))>>4);
    return (uint32_t)lo | ((uint32_t)hi<<16);
}
uint32_t hand_affine(uint32_t x) { return RP_HAND_A*x + RP_HAND_B; }
