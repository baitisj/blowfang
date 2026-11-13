/* rxrec: the UV-380's receive audio to the USB serial, to see what a receiver on it (rangerx)
   gets. The same route and I2S capture as rangerx (rxaudio.h). Lines:
     rxrec start ring=1024 align=1      tries to start in step with the codec's frames (0: none)
     mod say rate hw=NNNNN ms=3000      halfwords the DMA wrote in 3 s (2 a sample if the audio
                                        comes twice, so fs = hw / 2 / 3)
     mod say raw 0 h0 h1 ... h31        64 halfwords in a row as the DMA wrote them (two lines)
     a SEQ hhhh hhhh ...                64 samples (every other halfword, as rangerx reads them),
                                        hex, SEQ counting lines from 0 (a gap: samples lost)
     mod say stat t=10000 ov=0 r=-84    each 10 s: ms, ring overruns, RSSI
     mod say rxrec end lines=N ov=N s=N
   Listens RG_S seconds (Ctrl-C or PTT end it sooner); receive only: touches no RF register.
   tools/uv380_i2sprobe.py audio LOG OUT.raw turns the "a" lines into 16-bit audio.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "rxaudio.h"

#ifndef RG_S
#define RG_S 180u
#endif
#define RING 1024u
#define BLK 64u

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

int uv_main(const ortx_api_t *api) {
    char line[16 + BLK * 5u];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u)
        return -8;
    volatile int16_t *ring = (volatile int16_t *)mem;
    rxa_state s;
    unsigned align = rx_start(api, &s, ring, RING);
    api->fmt(line, sizeof line, "rxrec start ring=%u align=%u", RING, align);
    api->say(line);

    uint32_t took, hw = rx_count(api, &s, 3000u, &took);
    api->fmt(line, sizeof line, "rate hw=%lu ms=%lu", (unsigned long)hw, (unsigned long)took);
    api->say(line);
    unsigned w0 = rx_wr(&s);
    api->sleep_ms(10);          /* 64 more halfwords in, well inside the ring */
    for (unsigned h = 0; h < 2u; h++) {
        unsigned n = (unsigned)api->fmt(line, sizeof line, "raw %u", h);
        for (unsigned i = 0; i < 32u; i++) {
            uint16_t v = (uint16_t)ring[(w0 + 32u * h + i) & (RING - 1u)];
            line[n++] = ' ';
            for (int k = 12; k >= 0; k -= 4)
                line[n++] = hexd((v >> k) & 15u);
        }
        line[n] = 0;
        api->say(line);
    }

    unsigned rd = rx_wr(&s) & ~1u, ov = 0;
    uint32_t seq = 0, t_stat = api->ms() + 10000u;
    while (api->ms() < RG_S * 1000u && !api->stop()) {
        unsigned wr = rx_wr(&s), avail = (wr - rd) & (RING - 1u);
        if (avail > RING - 128u) {
            ov++;
            rd = (wr + RING - 2u) & ~1u & (RING - 1u);
            seq++;              /* a gap the host sees */
            continue;
        }
        if (avail < 2u * BLK) {
            api->sleep_ms(2);
            continue;
        }
        unsigned n = (unsigned)api->fmt(line, sizeof line, "a %lu", (unsigned long)seq++);
        for (unsigned i = 0; i < BLK; i++) {
            uint16_t v = (uint16_t)ring[(rd + 1u) & (RING - 1u)];
            rd = (rd + 2u) & (RING - 1u);
            line[n++] = ' ';
            for (int k = 12; k >= 0; k -= 4)
                line[n++] = hexd((v >> k) & 15u);
        }
        line[n++] = '\r';
        line[n++] = '\n';
        api->usb_write(line, n);
        if ((int32_t)(api->ms() - t_stat) >= 0) {
            t_stat += 10000u;
            api->fmt(line, sizeof line, "stat t=%lu ov=%u r=%ld", (unsigned long)api->ms(), ov,
                     (long)api->rssi());
            api->say(line);
        }
    }
    rx_stop(api, &s);
    api->fmt(line, sizeof line, "rxrec end lines=%lu ov=%u s=%lu", (unsigned long)seq, ov,
             (unsigned long)(api->ms() / 1000u));
    api->say(line);
    return (int)ov;
}
