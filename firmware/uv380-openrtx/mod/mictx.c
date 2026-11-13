/* mictx: the keyed mic test (Jeff 11-13 20:16Z). Does the UV-380's microphone reach the MCU while
   it transmits? In receive no setting showed it (docs/uv380-characteristics.md): the mic may be
   powered or switched in only while keyed. The module captures I2S3 as the codec leaves it
   (rxaudio.h's rx_start_as_is: register 26 and PD9 untouched), asks for MT_MS of FM carrying the
   mic (tx_mic: as FM mode sends it, 1 W, 440.850 MHz only, then the Morse ident) and captures on
   through the key-up, the ident and after. hamlet records the IC-7100's receive of the same
   transmission: the mic as sent, to correlate the I2S3 capture with (tools/uv380_micprobe.py
   LOG --ref IC7100.wav).
   Lines: as micprobe ("set K ...", "a" lines, "got K ..."):
     set 0 rx t=N        MT_PRE ms before the request
     set 1 tx t=N        MT_MS + 1000 ms from the request (the mic keyed)
     set 2 after t=N     MT_POST ms: the ident and receive again
   Touches no register; the firmware keys and unkeys. Returns the tx_mic result (0 or -1).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "rxaudio.h"

#define UV_MODULE_LEVEL 2

#ifndef MT_MS
#define MT_MS 3000u
#endif
#define MT_PRE 1500u
#define MT_POST 7000u
#define RING 1024u
#define BLK 64u

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

/* ms of audio as "a" lines; *rd, *seq carried on; the "got" line */
static void grab(const ortx_api_t *api, rxa_state *s, char *line, unsigned k, uint32_t ms,
                 unsigned *rd, uint32_t *seq) {
    volatile int16_t *ring = s->buf;
    unsigned ov = 0, pk = 0, got = 0;
    uint32_t sq = 0, end = api->ms() + ms;
    while ((int32_t)(api->ms() - end) < 0) {
        unsigned wr = rx_wr(s), avail = (wr - *rd) & (RING - 1u);
        if (avail > RING - 128u) {
            ov++;
            *rd = (wr + RING - 2u) & ~1u & (RING - 1u);
            (*seq)++;
            continue;
        }
        if (avail < 2u * BLK) {
            api->sleep_ms(2);
            continue;
        }
        unsigned n = (unsigned)api->fmt(line, 24, "a %lu", (unsigned long)(*seq)++);
        for (unsigned i = 0; i < BLK; i++) {
            int v = ring[(*rd + 1u) & (RING - 1u)];
            *rd = (*rd + 2u) & (RING - 1u);
            unsigned a = (unsigned)(v < 0 ? -v : v);
            if (a > pk)
                pk = a;
            if (got < 9600u)
                sq += (uint32_t)(v * v) >> 12;
            got++;
            line[n++] = ' ';
            for (int b = 12; b >= 0; b -= 4)
                line[n++] = hexd(((uint16_t)v >> b) & 15u);
        }
        line[n++] = '\r';
        line[n++] = '\n';
        api->usb_write(line, n);
    }
    unsigned c = got < 9600u ? got : 9600u, ms2 = c ? sq / c : 0, r = 0;
    while ((r + 1u) * (r + 1u) <= (ms2 << 12) && r < 32767u)
        r++;
    api->fmt(line, 96, "got %u rms=%u pk=%u ov=%u n=%u", k, r, pk, ov, got);
    api->say(line);
}

int uv_main(const ortx_api_t *api) {
    char line[24 + BLK * 5u];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u)
        return -8;
    rxa_state s;
    unsigned align = rx_start_as_is(api, &s, (volatile int16_t *)mem, RING);
    api->fmt(line, sizeof line, "mictx start align=%u r26=%02x pd9=%u ms=%u", align, s.r26,
             (unsigned)s.mux, MT_MS);
    api->say(line);
    unsigned rd = rx_wr(&s) & ~1u;
    uint32_t seq = 0;

    api->fmt(line, sizeof line, "set 0 rx t=%lu", (unsigned long)api->ms());
    api->say(line);
    grab(api, &s, line, 0, MT_PRE, &rd, &seq);

    int r = api->tx_mic(MT_MS);
    api->fmt(line, sizeof line, "set 1 tx t=%lu req=%d", (unsigned long)api->ms(), r);
    api->say(line);
    grab(api, &s, line, 1, MT_MS + 1000u, &rd, &seq);

    api->fmt(line, sizeof line, "set 2 after t=%lu", (unsigned long)api->ms());
    api->say(line);
    grab(api, &s, line, 2, MT_POST, &rd, &seq);

    rx_stop(api, &s);
    api->fmt(line, sizeof line, "mictx end lines=%lu", (unsigned long)seq);
    api->say(line);
    return r;
}
