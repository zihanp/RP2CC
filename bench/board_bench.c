/* RP2 research fixture: all code and the result buffer are in SRAM. No pins or flash writes. */
#include <stdint.h>
#include "pico/bootrom.h"
#include "hardware/clocks.h"
#include "hardware/sync.h"
#include "hardware/structs/sysinfo.h"
#include "rp2lab_io.h"
#include "bench_config.h"
#if LIB_PICO_STDIO_USB
#include "pico/stdio_usb.h"
#include "pico/stdio.h"
#include "pico/time.h"
#endif

typedef uint32_t (*kernel)(uint32_t);
#define PAIR(p) extern uint32_t p##_floor2(uint32_t), p##_affine(uint32_t)
PAIR(b2); PAIR(b3); PAIR(bs); PAIR(op); PAIR(hand);
extern uint32_t hand_identity(uint32_t);
static kernel const funs[] = {hand_identity, b2_floor2, b3_floor2, bs_floor2,
    op_floor2, hand_floor2, b2_affine, b3_affine, bs_affine, op_affine, hand_affine};
enum { ROUNDS=41, INNER=128, CASES=sizeof(funs)/sizeof(funs[0]), HEADER=16, VECTORS=1024 };
/* No ordinary static variable is allocated here. Host checks ELF segments before loading. */
static volatile uint32_t *const res=(volatile uint32_t *)(uintptr_t)0x20050000u;
#define REG32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define ST_CTRL REG32(0xe000e010u)
#define ST_LOAD REG32(0xe000e014u)
#define ST_VAL  REG32(0xe000e018u)
#define ST_MASK 0x00ffffffu
#if defined(__riscv)
static inline void fence(void) { __asm volatile ("fence rw,rw" ::: "memory"); }
static inline uint32_t cpu_id(void) { uint32_t x; __asm volatile ("csrr %0, mimpid" : "=r"(x)); return x; }
static inline uint32_t counter(void) { uint32_t x; __asm volatile ("csrr %0, mcycle" : "=r"(x)::"memory"); return x; }
static inline void counter_start(void) { __asm volatile ("csrci mcountinhibit, 1" ::: "memory"); }
static inline void counter_stop(void) { }
#define TIMER_KIND 2
#else
static inline void fence(void) { __asm volatile ("dsb\nisb" ::: "memory"); }
static inline uint32_t cpu_id(void) { return REG32(0xe000ed00u); }
static inline uint32_t counter(void) { return 0u-ST_VAL; }
static inline void counter_start(void) { ST_CTRL=0; ST_LOAD=ST_MASK; ST_VAL=0; ST_CTRL=5; }
static inline void counter_stop(void) { ST_CTRL=0; }
#define TIMER_KIND 1
#endif
static uint32_t step(uint32_t s) { return s*UINT32_C(1664525)+UINT32_C(1013904223); }
static uint32_t lane_ref(uint32_t u) {
    int32_t s=(int32_t)(u & 0xffffu); if (s>=32768) s-=65536;
    /* C truncates negative division toward zero; subtract one if there is a remainder. */
    return (uint32_t)(s/16 - (s<0 && s%16!=0)) & 0xffffu;
}
static uint32_t validate(void) {
    const uint32_t edges[]={0,0x80008000u,0x7fff7fffu,0x000f8001u,0xfff0ffffu,0xffffffffu,0x80007fffu,0x7fff8000u};
    uint32_t bad=0, x=0x5cab21adu;
    for (unsigned i=0;i<VECTORS;++i) {
        x=(i<sizeof(edges)/sizeof(edges[0]))?edges[i]:step(x);
        const uint32_t floor=lane_ref(x) | (lane_ref(x>>16)<<16);
        uint32_t affine=x; for (unsigned j=0;j<257;++j) affine=step(affine);
        for (unsigned k=1;k<CASES;++k) bad+=(funs[k](x)!=(k<6?floor:affine));
    }
    return bad;
}
/* Same indirect call, data dependency, seeds, timing edges and loop for every case. */
__attribute__((noinline,noclone))
static uint32_t sample(kernel fn,uint32_t seed,uint32_t *digest) {
    uint32_t x=seed; fence(); uint32_t start=counter();
    for (uint32_t i=0;i<INNER;++i) { x=fn(x^(UINT32_C(0x9e3779b9)+i)); }
    __asm volatile ("" : "+r"(x) :: "memory"); fence();
    uint32_t end=counter(); *digest=x;
    return (end-start)&ST_MASK;
}
__attribute__((used)) const uint32_t rp_bench_abi_1=1;
#if LIB_PICO_STDIO_USB
/* USB starts only after timing is finished. Useful when the boot ROM clears SRAM. */
static void publish(void) {
    static const char hex[]="0123456789abcdef";
    static char msg[(HEADER+2*ROUNDS*CASES)*8+32]; unsigned n=0;
    const char *p="RP2LAB1 "; while (*p) msg[n++]=*p++;
    for (int i=28;i>=0;i-=4) msg[n++]=hex[(RP_RUN_NONCE>>i)&15u];
    msg[n++]=' ';
    for (unsigned w=0; w<res[14]; ++w) {
        uint32_t word=res[w];
        for (unsigned b=0; b<4; ++b) { unsigned v=(word>>(b*8))&255u; msg[n++]=hex[v>>4]; msg[n++]=hex[v&15u]; }
    }
    msg[n++]='\n';
    if (!stdio_usb_init()) return;
    absolute_time_t end=make_timeout_time_ms(25000); absolute_time_t next=get_absolute_time();
    do {
        if (stdio_usb_connected() && time_reached(next)) {
            stdio_put_string(msg,(int)n,false,false); stdio_flush(); next=make_timeout_time_ms(350);
        }
        if (getchar_timeout_us(10000)=='K') break;
    } while (!time_reached(end));
    stdio_usb_deinit();
}
#endif
int main(void) {
    res[0]=0; res[1]=1; res[2]=RP_RUN_NONCE; res[3]=ROUNDS; res[4]=INNER; res[5]=CASES;
    res[6]=clock_get_hz(clk_sys); res[7]=rp2lab_io_selftest(); res[8]=sysinfo_hw->chip_id;
    res[9]=sysinfo_hw->package_sel; res[10]=cpu_id();
    res[11]=validate(); res[12]=VECTORS; res[13]=TIMER_KIND; res[14]=HEADER+2*ROUNDS*CASES;
    uint32_t flags=save_and_disable_interrupts();
    counter_start(); /* core clock, no timer IRQ */
    uint32_t tmp=0;
    for (unsigned k=0;k<CASES;++k) (void)sample(funs[k],0xfedcba98u,&tmp);
    for (unsigned r=0;r<ROUNDS;++r) {
        uint32_t seed=0x1234abcdu^(0x9e3779b9u*(r+1u));
        for (unsigned o=0;o<CASES;++o) {
            unsigned k=(r+o)%CASES, idx=HEADER+2*(r*CASES+k);
            res[idx]=sample(funs[k],seed,&tmp); res[idx+1]=tmp;
        }
    }
    counter_stop(); restore_interrupts(flags);
    uint32_t h=2166136261u;
    for (unsigned i=1;i<res[14];++i) if (i!=15) { h=(h^res[i])*16777619u; }
    res[15]=h; fence(); res[0]=0x31423252u; fence();
#if LIB_PICO_STDIO_USB
    publish();
#endif
    reset_usb_boot(0,0);  /* Return to the ROM with both default interfaces. */
}
