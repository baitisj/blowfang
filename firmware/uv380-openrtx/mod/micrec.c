/* micrec: the UV-380's microphone into the sotto 1200 encoder, with the USB cable out. The TYT
   cable plugs into the side accessory jack, and a plug there switches the internal mic out (Jeff
   11-14 02:47Z: FM carried his voice only with the cable unplugged), so every mic probe so far
   ran deaf. The module is loaded with the cable in, then works with it out:

     1. "micrec ready": unplug the cable within MR_WAIT s and talk until it's plugged back in.
     2. Each input setting below for MR_SET ms (the first 150 ms dropped): its level and peak.
     3. The loudest setting (if it beats the mic-off control by 2x, else setting 1), with a gain
        that brings it to about rms 3000, into enc_begin/enc_push/enc_end for MR_S s: the
        recording becomes the message (PTT sends it, # plays it).
     4. The codec put back. It waits up to MR_BACK s for any byte from USB (the cable back in and
        the host saying hello), then reports, so nothing is written while the cable is out.

   Settings (I2S3 as rxaudio.h reads it; PD9 low, so the receiver's audio isn't routed in):
     0  26=7d, mic preamp off                     the control
     1  26=7d, mic on                             micprobe's best room pickup (cable in, weak)
     2  26=bd, mic on                             the other one
     3  26=7d, mic on, E0 8b E2 06 E4 4a          OpenGD77's DMR codec (mic stage 2 +30 dB)
     4  26=7d, mic on, E0 c1                      OpenRTX's FM mic
     5  26 as found, mic on

   Lines (after the cable is back):
     mod say micrec set K rms=N pk=N n=N           per setting (16-bit scale, mean removed; n: samples)
     mod say micrec use K gain=G                   the setting recorded, its gain
     mod say micrec enc frames=N ov=N pushed=N ms=N  ov: ring overruns (the encoder fell behind)
     mod say m IDX hhhhhhhhhhhh ...                the message, as usbenc prints it
   It never checks api->stop(): pulling the cable out makes it 1 (11-14 03:04Z: every phase ended
   at once and nothing was recorded), so it runs its fixed course, about MR_WAIT + 19 + MR_S s,
   then waits for the hello. Receive only: touches no RF register. Puts back 26, E0, E2, E4, PD9, PA13, SPI3 and the DMA
   stream. Returns the superframes, or -1 (no memory for the encoder), -8 (scratch too small).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "rxaudio.h"

#define UV_MODULE_LEVEL 2

#ifndef MR_WAIT
#define MR_WAIT 20u
#endif
#ifndef MR_SET
#define MR_SET 3000u
#endif
#ifndef MR_S
#define MR_S 15u
#endif
#ifndef MR_BACK
#define MR_BACK 900u
#endif
#define RING 4096u                  /* halfwords: 2048 samples, 256 ms */
#define NSET 6u
#define MIC_PIN 13u                 /* MIC_PWR, GPIOA 13 */

typedef struct {
    rxa_state rs;
    uint8_t r26, e0, e2, e4;
    uint32_t pa_moder, pa_odr;
    unsigned rd, ov;
} mr_t;

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

static unsigned isqrt(uint32_t v) {
    uint32_t r = 0, b = 1u << 30;
    while (b > v)
        b >>= 2;
    while (b) {
        if (v >= r + b) {
            v -= r + b;
            r = (r >> 1) + b;
        } else {
            r >>= 1;
        }
        b >>= 2;
    }
    return (unsigned)r;
}

static void mic(unsigned on) {
    RXA_REG(RXA_GPIOA) = (RXA_REG(RXA_GPIOA) & ~(3u << (2 * MIC_PIN))) | (1u << (2 * MIC_PIN));
    RXA_REG(RXA_GPIOA + 0x18u) = on ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
}

static void codec(const ortx_api_t *api, const mr_t *m, unsigned k) {
    api->c6000_write(0xE0, k == 3u ? 0x8B : k == 4u ? 0xC1 : m->e0);
    api->c6000_write(0xE2, k == 3u ? 0x06 : m->e2);
    api->c6000_write(0xE4, k == 3u ? 0x4A : m->e4);
    api->c6000_write(0x26, k == 2u ? 0xBD : k == 5u ? m->r26 : 0x7D);
    mic(k != 0u);
}

/* up to n samples (every other halfword) from the ring into x; counts overruns */
static unsigned take(mr_t *m, int16_t *x, unsigned n) {
    unsigned wr = rx_wr(&m->rs), avail = (wr - m->rd) & (RING - 1u);
    if (avail > RING - 256u) {
        m->ov++;
        m->rd = (wr + RING - 2u) & ~1u & (RING - 1u);
        return 0;
    }
    unsigned got = 0;
    while (got < n && avail >= 2u) {
        x[got++] = m->rs.buf[(m->rd + 1u) & (RING - 1u)];
        m->rd = (m->rd + 2u) & (RING - 1u);
        avail -= 2u;
    }
    return got;
}

/* ms of audio: rms with the mean removed, peak */
static void level(const ortx_api_t *api, mr_t *m, uint32_t ms, unsigned *rms, unsigned *pk,
                  unsigned *cnt) {
    int16_t x[64];
    int32_t sum = 0;
    uint32_t sq = 0, n = 0, end;
    unsigned p = 0;
    api->sleep_ms(150);
    m->rd = rx_wr(&m->rs) & ~1u;
    end = api->ms() + ms;
    while ((int32_t)(api->ms() - end) < 0) {
        unsigned g = take(m, x, 64u);
        if (!g) {
            api->sleep_ms(4);
            continue;
        }
        for (unsigned i = 0; i < g; i++) {
            int v = x[i];
            unsigned a = (unsigned)(v < 0 ? -v : v);
            p = a > p ? a : p;
            if (n < 65536u) {
                sum += v;
                sq += (uint32_t)(v * v) >> 8;
                n++;
            }
        }
    }
    int32_t mean = n ? sum / (int32_t)n : 0;
    uint32_t var = n ? sq / n : 0, mm = (uint32_t)(mean * mean) >> 8;
    *rms = isqrt((var > mm ? var - mm : 0u) << 8);
    *pk = p;
    *cnt = n;
}

int uv_main(const ortx_api_t *api) {
    char line[24 + 12 * 13];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u)
        return -8;
    mr_t m;
    api->memset(&m, 0, sizeof m);
    m.r26 = api->c6000_read(0x26), m.e0 = api->c6000_read(0xE0);
    m.e2 = api->c6000_read(0xE2), m.e4 = api->c6000_read(0xE4);
    m.pa_moder = RXA_REG(RXA_GPIOA), m.pa_odr = RXA_REG(RXA_GPIOA + 0x14u);
    unsigned align = rx_start_as_is(api, &m.rs, (volatile int16_t *)mem, RING);
    uint32_t pd9 = (RXA_REG(RXA_GPIOD + 0x14u) >> RXA_MUX_PIN) & 1u;
    RXA_REG(RXA_GPIOD + 0x18u) = 1u << (RXA_MUX_PIN + 16);          /* PD9 low */
    api->fmt(line, sizeof line, "micrec ready align=%u: unplug the cable within %u s and talk",
             align, MR_WAIT);
    api->say(line);
    for (uint32_t t = api->ms(); api->ms() - t < 1000u * MR_WAIT;)
        api->sleep_ms(50);

    unsigned rms[NSET], pk[NSET], cnt[NSET], best = 1;
    for (unsigned k = 0; k < NSET; k++) {
        codec(api, &m, k);
        level(api, &m, MR_SET, &rms[k], &pk[k], &cnt[k]);
        if (k && rms[k] > rms[best])
            best = k;
    }
    if (rms[best] < 2u * rms[0])
        best = 1;
    unsigned r = rms[best] ? rms[best] : 1u, gain = 3000u / r;
    gain = gain < 1u ? 1u : gain > 64u ? 64u : gain;

    int frames = -1;
    unsigned pushed = 0;
    uint32_t t0 = api->ms();
    if (api->enc_begin() == 0) {
        codec(api, &m, best);
        api->sleep_ms(150);
        m.rd = rx_wr(&m.rs) & ~1u;
        int16_t x[64];
        uint32_t end = api->ms() + 1000u * MR_S;
        while ((int32_t)(api->ms() - end) < 0) {
            unsigned g = take(&m, x, 64u);
            if (!g) {
                api->sleep_ms(2);
                continue;
            }
            for (unsigned i = 0; i < g; i++) {
                int32_t v = (int32_t)x[i] * (int32_t)gain;
                x[i] = (int16_t)(v > 32767 ? 32767 : v < -32767 ? -32767 : v);
            }
            if (api->enc_push(x, g) < 0)
                break;
            pushed += g;
        }
        frames = api->enc_end();
    }
    uint32_t took = api->ms() - t0;

    rx_stop(api, &m.rs);
    api->c6000_write(0x26, m.r26);
    api->c6000_write(0xE0, m.e0);
    api->c6000_write(0xE2, m.e2);
    api->c6000_write(0xE4, m.e4);
    RXA_REG(RXA_GPIOA + 0x18u) = (m.pa_odr >> MIC_PIN) & 1u ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
    RXA_REG(RXA_GPIOA) = m.pa_moder;
    RXA_REG(RXA_GPIOD + 0x18u) = pd9 ? 1u << RXA_MUX_PIN : 1u << (RXA_MUX_PIN + 16);

    /* the cable back in: wait for the host's hello before writing anything */
    uint8_t b[16];
    for (uint32_t t = api->ms(); api->ms() - t < 1000u * MR_BACK;) {
        if (api->usb_read(b, sizeof b))
            break;
        api->sleep_ms(100);
    }
    for (unsigned k = 0; k < NSET; k++) {
        api->fmt(line, sizeof line, "micrec set %u rms=%u pk=%u n=%u", k, rms[k], pk[k], cnt[k]);
        api->say(line);
    }
    api->fmt(line, sizeof line, "micrec use %u gain=%u", best, gain);
    api->say(line);
    api->fmt(line, sizeof line, "micrec enc frames=%d ov=%u pushed=%u ms=%lu", frames, m.ov,
             pushed, (unsigned long)took);
    api->say(line);
    if (frames < 0) {
        api->say("micrec enc failed: no memory for the encoder");
        return -1;
    }
    if (frames == 0)
        return 0;                   /* msg() would be the built-in message */
    const uint8_t *msg;
    unsigned nf = api->msg(&msg);
    for (unsigned f = 0; msg && f < nf; f += 12u) {
        unsigned k = (unsigned)api->fmt(line, sizeof line, "m %u", f);
        for (unsigned j = f; j < nf && j < f + 12u; j++) {
            line[k++] = ' ';
            for (unsigned q = 0; q < 6u; q++) {
                line[k++] = hexd(msg[6u * j + q] >> 4);
                line[k++] = hexd(msg[6u * j + q] & 15u);
            }
        }
        line[k] = 0;
        api->say(line);
    }
    return frames;
}
