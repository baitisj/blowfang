/* UV-380 app, first milestone: send a sotto message as K6 voice-pager packets in FFSK
 * (1200/1800 Hz, 1800 baud, XL), the format the K6's pager receives (modem/pager.py), then
 * the station ident in Morse (AG7EW, 800 Hz, 20 wpm) on the same carrier, as modem.k6 tx does.
 *
 * QEMU build: the message comes from msg.sot on the host and the audio goes to audio.raw, so
 * tests/test_uv380_emu.py can check that the K6's receiver model decodes it.
 * Radio build: the message is the built-in test pattern, and nothing is sent: rf.h has no
 * driver yet, so rf_tx_begin fails and the app only blinks the red LED.
 *
 * SPDX-License-Identifier: MIT */
#include <stdint.h>

#include "../../../c/common/cw_tx.h"
#include "../../../c/common/ffsk_tx.h"
#include "../../../c/common/pager_tx.h"
#include "board.h"
#include "rf.h"

#define FS 48000
#define BAUD 1800
#define TX_HZ 440850000u                /* 70 cm, the project's test frequency */
#define CALLSIGN "AG7EW"                /* every test transmission carries the ident */

static uint8_t msg[6 * 1500];           /* up to 60 s of sotto 1200 */
static int16_t buf[480];
static pt_air air;
static ffsk_tx mod;
static cw_tx cw;

static unsigned test_pattern(uint8_t *m) {
    for (unsigned i = 0; i < 6 * 100; i++)
        m[i] = (uint8_t)(i * 37u + 11u);
    return 100;
}

int main(void) {
    board_init();
    int n = host_read_file("msg.sot", msg, sizeof msg);
    unsigned total = n > 0 ? (unsigned)n / 6 : test_pattern(msg);
    static const pt_profile pr = PT_PROFILE_XL(PT_MAGIC_1200);
    pt_message m = {&pr, msg, total, 1, pt_msg_id(msg, total)};
#ifndef BOARD_QEMU
    if (rf_tx_begin(TX_HZ) != 0) {
        for (;;) {                      /* no RF driver yet: blink red, send nothing */
            board_led(0, (board_ms() / 250) & 1);
        }
    }
#endif
    pt_air_defaults(&air, pr.payload, BAUD, pt_message_source, &m);
    ffsk_init(&mod, &air, FS, BAUD, 0, 0, 16000);
    audio_start(FS);
    unsigned got;
    while ((got = ffsk_render(&mod, buf, sizeof buf / sizeof buf[0])) != 0)
        audio_write(buf, got);
    cw_init(&cw, CALLSIGN, FS, 20, 800, 13107);
    while ((got = cw_render(&cw, buf, sizeof buf / sizeof buf[0])) != 0)
        audio_write(buf, got);
    audio_stop();
    rf_tx_end();
    host_log("uv380: sent\n");
    host_exit(0);
    return 0;
}
