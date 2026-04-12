#include "pico/stdlib.h"

/*
 * asm_main is defined in the user's assembly file.
 * The picosim build process renames the assembly `main:` label to `asm_main`
 * so it can be called from here after hardware initialisation.
 */
extern int asm_main(void);

int main(void) {
    stdio_init_all();   /* initialise USB serial and UART */
    return asm_main();
}
