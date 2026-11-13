/* rangerx: PHY R (docs/k6-range.md) received by the UV-380 with no hardware mod. The FM
   receiver's audio reaches the MCU through the HR_C6000's codec on I2S3 (rxaudio.h: the route,
   SPI3 as an I2S slave in 32-bit channels, DMA1 stream 0 into a ring in scratch RAM, every other
   halfword the audio). The module follows the ring and hands the audio to c/common/range_rx.c:
   integrate and dump on the bit edges with a 16-phase clock, on the audio turned to follow the
   tones' phase walk (the K6 sender's clocks), the sync, PHY G's Viterbi, the
   pager CRC.

   Lines:
     range start fs=8000 ring=1024 state=NNNN free=NNN align=1
                    align: the tries it took to start the capture in step with the codec's
                    frames (rxaudio.h; 0: none looked right, expect nothing)
                    (the codec's frame rate is 8000 to rxrec's count: 48000 halfwords in 3 s)
     r 3 t=12345 c=-118 y=31 e=41 ok=1 s=2 r=-84
                    a packet: its number, ms at its end, the sync's correlation (128 from clean
                    soft values; its sign: the burst's way up), the mean |soft value| x 10 (40
                    clean), the line bits whose sign the decoded packet's own code disagrees with
                    (of 2064), CRC right, seq (meaningless unless ok=1), RSSI (dBm) at its end
     range end n=12 ok=11 ov=0 worst=9 in=4800000 s=600
                    packets, CRC right, ring overruns (audio lost), the longest gap between reads
                    of the ring (ms; 64 or more: it may have gone round unseen), samples to the
                    receiver (8000 a second), seconds listened
   Listens RG_S seconds (Ctrl-C or PTT end it sooner); receive only: touches no RF register.
   Puts back register 26, PD9, the pins, SPI3 and the DMA stream as it found them.
   Returns the packets that checked, or -8 (scratch too small).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "rxaudio.h"

#define RR_HIST 32               /* 8 kHz: 2 bits are 13.3 samples */
#define RR_XR 32                 /* 8 kHz: the quadrature filter spans 31 samples */
#include "../../../c/common/range_rx.c"

#ifndef RG_S
#define RG_S 600u
#endif
#define RING 1024u               /* halfwords: 64 ms of audio */
#define FS 8000u

#ifdef RANGERX_TAP
/* rangetap.c: what goes to the receiver, to the USB serial too, as rxrec's "a SEQ hhhh ..." */
static void tap(const ortx_api_t *api, const int16_t *x, unsigned n, uint32_t seq) {
    char t[24 + 64 * 5];
    unsigned k = (unsigned)api->fmt(t, sizeof t, "a %lu", (unsigned long)seq);
    for (unsigned i = 0; i < n; i++) {
        t[k++] = ' ';
        for (int b = 12; b >= 0; b -= 4) {
            unsigned d = ((uint16_t)x[i] >> b) & 15u;
            t[k++] = (char)(d < 10u ? '0' + d : 'a' + d - 10u);
        }
    }
    t[k++] = '\r';
    t[k++] = '\n';
    api->usb_write(t, k);
}
#endif

int uv_main(const ortx_api_t *api) {
    char line[96];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    unsigned need = (unsigned)sizeof(rr_state) + RING * 2u;
    if (bytes < need) {
        api->fmt(line, sizeof line, "range scratch %lu < %u", (unsigned long)bytes, need);
        api->say(line);
        return -8;
    }
    rr_state *r = (rr_state *)mem;
    volatile int16_t *ring = (volatile int16_t *)(mem + sizeof(rr_state));
    rxa_state rs;
    unsigned align = rx_start(api, &rs, ring, RING);
    rr_init(r, FS);
    api->fmt(line, sizeof line, "range start fs=%u ring=%u state=%u free=%lu align=%u", FS, RING,
             (unsigned)sizeof(rr_state), (unsigned long)(bytes - need), align);
    api->say(line);

    unsigned rd = rx_wr(&rs) & ~1u, npk = 0, nok = 0, ov = 0;
    int16_t x[64];
    uint32_t seen = api->ms();
    unsigned worst = 0;
#ifdef RANGERX_TAP
    uint32_t tseq = 0;
#endif
    while (api->ms() < RG_S * 1000u && !api->stop()) {
        unsigned wr = rx_wr(&rs), avail = (wr - rd + RING) % RING;
        uint32_t now = api->ms(), gap = now - seen;
        seen = now;
        if (gap > worst)
            worst = gap;                /* 64 ms or more: the ring may have gone round unseen */
        if (avail > RING - 64u) {
            ov++;                       /* the ring went round: skip to what's fresh */
            rd = (wr + RING - 2u) & ~1u & (RING - 1u);
            continue;
        }
        if (avail < 2u) {
            api->sleep_ms(4);
            continue;
        }
        unsigned n = 0;
        while (avail >= 2u && n < 64u) {
            x[n++] = ring[(rd + 1u) % RING];    /* every other halfword: the audio at 8 kHz */
            rd = (rd + 2u) % RING;
            avail -= 2u;
        }
#ifdef RANGERX_TAP
        tap(api, x, n, tseq++);
#endif
        unsigned off = 0, used;
        while (off < n) {
            if (rr_push(r, x + off, n - off, &used)) {
                npk++;
                nok += (unsigned)r->ok;
                api->fmt(line, sizeof line, "r %u t=%lu c=%d y=%d e=%d ok=%d s=%u r=%ld", npk,
                         (unsigned long)api->ms(), r->pcorr, r->ymean, r->errors, r->ok,
                         rr_seq(r->payload), (long)api->rssi());
                api->say(line);
            }
            off += used;
        }
    }

    rx_stop(api, &rs);
    api->fmt(line, sizeof line, "range end n=%u ok=%u ov=%u worst=%u in=%lu s=%lu", npk, nok, ov,
             worst, (unsigned long)r->n, (unsigned long)(api->ms() / 1000u));
    api->say(line);
    return (int)nok;
}
