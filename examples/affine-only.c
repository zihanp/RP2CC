/* Pin-neutral integration example. These volatile accesses belong to the application,
 * not the mathematical IR; a debugger can set input or inspect output if desired. */
#include <stdint.h>
#include "kernels.h"

volatile uint32_t rp2cc_example_input;
volatile uint32_t rp2cc_example_output;

int main(void) {
    for (;;) {
        uint32_t x = rp2cc_example_input;
        rp2cc_example_output = rp_orbit(x);
    }
}
