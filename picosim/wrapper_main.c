#include <stdio.h>
#include "pico/stdlib.h"
#include "pico/multicore.h"
#include "pico/stdio_usb.h"
#include "hardware/structs/sio.h"

/*
 * asm_main is defined in the user's assembly file.
 * The picosim build process renames the assembly `main:` label to `asm_main`
 * so it can be called from here after hardware initialisation.
 *
 * When asm_main returns, the Pico keeps running instead of exiting: USB
 * serial stays up, so the console still shows the program's last output and
 * the next Flash can reboot the board into BOOTSEL by itself (picotool does
 * that over USB, which an exited program has already shut down).
 */
extern int asm_main(void);

/*
 * Live pins for picosim's board drawing.  The RP2040's second core (core 1)
 * reads every pin's level (SIO GPIO_IN) and direction (GPIO_OE) about 50
 * times a second, and sends them over USB serial whenever they change (and
 * once a second anyway) as a frame
 *     \x02 P <level, 8 hex digits> , <output-enable, 8 hex digits> \x03
 * picosim takes these frames out of the console text and draws the board
 * from them.  Core 1 only reads the pins, so the student's program (on
 * core 0) runs exactly as before.  stdio's lock keeps a frame in one piece
 * even when the program prints at the same time.
 */
static void report_pins(void) {
    uint32_t last_in = 0, last_oe = 0;
    bool sent = false;
    absolute_time_t next = get_absolute_time();
    while (true) {
        uint32_t in = sio_hw->gpio_in, oe = sio_hw->gpio_oe;
        if (!sent || in != last_in || oe != last_oe || time_reached(next)) {
            if (stdio_usb_connected()) {
                printf("\x02P%08lx,%08lx\x03", (unsigned long)in, (unsigned long)oe);
                sent = true;
            }
            last_in = in;
            last_oe = oe;
            next = make_timeout_time_ms(1000);
        }
        sleep_ms(20);
    }
}

int main(void) {
    stdio_init_all();   /* initialise USB serial */
    multicore_launch_core1(report_pins);
    asm_main();
    stdio_flush();
    while (true) {
        tight_loop_contents();   /* finished: idle, USB still serviced */
    }
}
