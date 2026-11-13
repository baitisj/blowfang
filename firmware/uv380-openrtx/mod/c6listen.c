/* c6listen: listen on I2S3 with the receive audio routed to the C6000, for a tone run (the
   IC-7100's PSK31 "AG7EW TEST" on 440.850, then quiet). It sets what the probe's pass sets for
   receive and plain "cap" commands don't: register 26 = fd (the C6000's FM receive, OpenRTX's
   radio_enableAfOutput) and RX_AUDIO_MUX (PD9) high (the AT1846S's audio into the C6000), then
   for about 90 s, round after round: the RSSI and, at codec gain E1 0f (as OpenRTX sets it) and
   4f (about x32, from c6codec), captures in P32 and S16 with their first 64 samples. Everything
   goes back at the end, or on Ctrl-C or PTT. Reads and writes no RF register: nothing keys.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define GPIOD_ODR (*(volatile uint32_t *)0x40020C14u)
#define GPIOD_BSRR (*(volatile uint32_t *)0x40020C18u)
#define MUX_PIN 9u
#define RUN_MS 90000u

static const uint8_t GAINS[] = {
    0x0F,
    0x4F,
};

int uv_main(const ortx_api_t *api) {
    char line[64];
    uint8_t e1 = api->c6000_read(0xE1), r26 = api->c6000_read(0x26);
    uint32_t mux = (GPIOD_ODR >> MUX_PIN) & 1u;
    int rounds = 0;
    api->fmt(line, sizeof line, "c6listen was e1=%02x 26=%02x mux=%lu", e1, r26, (unsigned long)mux);
    api->say(line);
    api->c6000_write(0x26, 0xFD);
    GPIOD_BSRR = 1u << MUX_PIN;
    while (api->ms() < RUN_MS && !api->stop()) {
        for (unsigned g = 0; g < sizeof GAINS && !api->stop(); g++) {
            api->c6000_write(0xE1, GAINS[g]);
            api->sleep_ms(20);
            for (unsigned c = 0; c < 2; c++) {     /* no table of strings: -fropi can't move one */
                api->fmt(line, sizeof line, "round %d t=%lu rssi=%ld e1=%02x", rounds,
                         (unsigned long)api->ms(), (long)api->rssi(), GAINS[g]);
                api->say(line);
                api->probe(c ? "cap SPI3 s S16 8000" : "cap SPI3 s P32 8000");
                api->probe("raw a 64");
            }
        }
        rounds++;
    }
    api->c6000_write(0xE1, e1);
    api->c6000_write(0x26, r26);
    GPIOD_BSRR = mux ? 1u << MUX_PIN : 1u << (MUX_PIN + 16);
    api->say("c6listen restored");
    return rounds;
}
