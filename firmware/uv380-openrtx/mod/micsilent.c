/* micsilent: can the UV-380 record its mic WITHOUT transmitting? (Jeff 11-14 15:06Z: "There's no
   reason for the radio to transmit while the button is pressed".) So far the mic reached I2S3 (the
   C6000's codec input) only while the radio was keyed: OpenRTX's FM transmit starts the C6000's
   analog transmit (startAnalogTx: E0 c1, then 0x60 = 0x80) AND keys the AT1846S and the PA. This
   module does only the C6000 half: the AT1846S stays in receive and the PA stays off (the module
   touches no RF register and no PA pin), so nothing goes on air. In receive I2S3 carries the
   receiver, so PD9 (RX_AUDIO_MUX) goes low first, as micrec does.

   Load it with the cable in. The green LED blinks: unplug the cable at the radio's side jack.
   The unplug counts as a PTT press in PAGER mode (it sends the stored message, about 11 s), so
   the LED stays off for MS_TXWAIT; then it goes on: TALK without stopping until it goes out
   (6 settings x MS_SET), then plug the cable back in. The report waits for the host's hello.

   Settings (each MS_SET ms, the first 150 ms dropped; rms with the mean removed, peak):
     0  as found (receive), preamp on
     1  E0 c1 (OpenRTX's FM mic), receive, preamp on
     2  C6000 analog transmit, mic (E0 c1 ... 60=80), AT1846S in receive, preamp on
     3  the same with E0 c3 (mic and line in)
     4  setting 2 with the preamp off: the control (what isn't the mic)
     5  as found, preamp off
   The C6000's registers (E0 E5 25 A1 C2 34 3E 60) and PD9 are put back as found; the mic preamp
   (PA13) is left on, as PAGER mode keeps it. Lines:
     mod say micsilent set K rms=N pk=N n=N
     mod say micsilent mic=YES|no (setting 2 against 4)
   If setting 2 carries the mic (rms over 3x setting 4's), MS_REC s more of it are recorded at
   setting 2 into the sotto 1200 encoder (auto gain), and the message is printed as usbenc prints it
   (m IDX hex...), so it can be decoded and heard. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "rxaudio.h"

#ifndef MS_WAIT
#define MS_WAIT 120u
#endif
#ifndef MS_TXWAIT
#define MS_TXWAIT 25000u
#endif
#ifndef MS_SET
#define MS_SET 3000u
#endif
#ifndef MS_REC
#define MS_REC 6u
#endif
#ifndef MS_BACK
#define MS_BACK 3600u
#endif
#define RING 4096u
#define NSET 6u
#define MIC_PIN 13u
#define GPIOE 0x40021000u
#define LED_PIN 0u
#define OTG_DSTS 0x50000808u

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

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

static void mic(unsigned on) {
    RXA_REG(RXA_GPIOA) = (RXA_REG(RXA_GPIOA) & ~(3u << (2 * MIC_PIN))) | (1u << (2 * MIC_PIN));
    RXA_REG(RXA_GPIOA + 0x18u) = on ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
}

static void led(unsigned on) {
    RXA_REG(GPIOE + 0x18u) = on ? 1u << LED_PIN : 1u << (LED_PIN + 16);
}

static int unplugged(const ortx_api_t *api) {
    return api->stop() || (RXA_REG(OTG_DSTS) & 1u);
}

/* up to n samples (every other halfword) from the ring */
static unsigned take(rxa_state *rs, unsigned *rd, int16_t *x, unsigned n) {
    unsigned wr = rx_wr(rs), avail = (wr - *rd) & (RING - 1u);
    if (avail > RING - 256u) {
        *rd = (wr + RING - 2u) & ~1u & (RING - 1u);
        return 0;
    }
    unsigned got = 0;
    while (got < n && avail >= 2u) {
        x[got++] = rs->buf[(*rd + 1u) & (RING - 1u)];
        *rd = (*rd + 2u) & (RING - 1u);
        avail -= 2u;
    }
    return got;
}

static void level(const ortx_api_t *api, rxa_state *rs, unsigned *rms, unsigned *pk,
                  unsigned *cnt) {
    int16_t x[64];
    int32_t sum = 0;
    uint32_t sq = 0, n = 0;
    unsigned p = 0, rd;
    api->sleep_ms(150);
    rd = rx_wr(rs) & ~1u;
    for (uint32_t end = api->ms() + MS_SET; (int32_t)(api->ms() - end) < 0;) {
        unsigned g = take(rs, &rd, x, 64u);
        if (!g) {
            api->sleep_ms(4);
            continue;
        }
        for (unsigned i = 0; i < g && n < 65536u; i++, n++) {
            int v = x[i];
            unsigned a = (unsigned)(v < 0 ? -v : v);
            p = a > p ? a : p;
            sum += v;
            sq += (uint32_t)(v * v) >> 8;
        }
    }
    int32_t mean = n ? sum / (int32_t)n : 0;
    uint32_t var = n ? sq / n : 0, mm = (uint32_t)(mean * mean) >> 8;
    *rms = isqrt((var > mm ? var - mm : 0u) << 8);
    *pk = p;
    *cnt = n;
}

/* the C6000 half of OpenRTX's startAnalogTx (HR_Cx000.cpp), no AT1846S, no PA */
static void c6tx(const ortx_api_t *api, uint8_t e0) {
    api->c6000_write(0xE0, e0);
    api->c6000_write(0xC2, 0x00);
    api->c6000_write(0xE5, 0x1A);
    api->c6000_write(0x25, 0x0E);
    api->c6000_write(0xA1, 0x80);
    api->c6000_write(0x34, 0x30);   /* pre-emphasis, 25 kHz */
    api->c6000_write(0x3E, 0x08);
    api->c6000_write(0x60, 0x80);   /* start analog transmission */
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[8] = {0xE0, 0xE5, 0x25, 0xA1, 0xC2, 0x34, 0x3E, 0x60};
    char line[24 + 12 * 13];
    uint8_t was[8];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u)
        return -8;
    for (unsigned i = 0; i < 8u; i++)
        was[i] = api->c6000_read(regs[i]);
    uint32_t pd9 = (RXA_REG(RXA_GPIOD + 0x14u) >> RXA_MUX_PIN) & 1u;
    uint32_t e_moder = RXA_REG(GPIOE), e_odr = RXA_REG(GPIOE + 0x14u);
    rxa_state rs;
    unsigned align = rx_start_as_is(api, &rs, (volatile int16_t *)mem, RING);
    RXA_REG(GPIOE) = (e_moder & ~(3u << (2 * LED_PIN))) | (1u << (2 * LED_PIN));
    api->fmt(line, sizeof line, "micsilent ready align=%u: unplug the cable when the green LED blinks",
             align);
    api->say(line);
    int out = 0;
    for (uint32_t t = api->ms(); api->ms() - t < 1000u * MS_WAIT && !(out = unplugged(api));) {
        led(((api->ms() - t) / 250u) & 1u);
        api->sleep_ms(20);
    }
    led(0);
    unsigned rms[NSET], pk[NSET], cnt[NSET];
    api->memset(rms, 0, sizeof rms);
    api->memset(pk, 0, sizeof pk);
    api->memset(cnt, 0, sizeof cnt);
    int frames = 0;
    if (out) {
        mic(1);
        api->sleep_ms(MS_TXWAIT);   /* the unplug's own PTT send, and the preamp settles */
        RXA_REG(RXA_GPIOD + 0x18u) = 1u << (RXA_MUX_PIN + 16);     /* PD9 low */
        led(1);                     /* talk */
        for (unsigned k = 0; k < NSET; k++) {
            if (k == 1u)
                api->c6000_write(0xE0, 0xC1);
            if (k == 2u || k == 4u)
                c6tx(api, 0xC1);
            if (k == 3u)
                api->c6000_write(0xE0, 0xC3);
            if (k == 4u)
                mic(0);
            if (k == 5u) {
                for (unsigned i = 8u; i-- > 0;)
                    api->c6000_write(regs[i], was[i]);
            }
            level(api, &rs, &rms[k], &pk[k], &cnt[k]);
        }
        mic(1);
        if (rms[2] > 3u * rms[4] && api->enc_begin() == 0) {
            c6tx(api, 0xC1);
            api->sleep_ms(1000);    /* the preamp back on: a short surge */
            unsigned r = rms[2] ? rms[2] : 1u, gain = 3000u / r;
            if (pk[2] && gain * pk[2] > 28000u)
                gain = 28000u / pk[2];
            gain = gain < 1u ? 1u : gain > 64u ? 64u : gain;
            unsigned rd = rx_wr(&rs) & ~1u;
            int16_t x[64];
            for (uint32_t end = api->ms() + 1000u * MS_REC; (int32_t)(api->ms() - end) < 0;) {
                unsigned g = take(&rs, &rd, x, 64u);
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
            }
            frames = api->enc_end();
        }
    }
    for (unsigned i = 8u; i-- > 0;)
        api->c6000_write(regs[i], was[i]);
    RXA_REG(RXA_GPIOD + 0x18u) = pd9 ? 1u << RXA_MUX_PIN : 1u << (RXA_MUX_PIN + 16);
    rx_stop(api, &rs);
    RXA_REG(GPIOE + 0x18u) = (e_odr >> LED_PIN) & 1u ? 1u << LED_PIN : 1u << (LED_PIN + 16);
    RXA_REG(GPIOE) = e_moder;

    uint8_t b[16];
    for (uint32_t t = api->ms(); out && api->ms() - t < 1000u * MS_BACK;) {
        if (api->usb_read(b, sizeof b))
            break;
        api->sleep_ms(100);
    }
    if (!out)
        api->say("micsilent: no unplug seen, nothing measured");
    for (unsigned k = 0; k < NSET; k++) {
        api->fmt(line, sizeof line, "micsilent set %u rms=%u pk=%u n=%u", k, rms[k], pk[k], cnt[k]);
        api->say(line);
    }
    api->say(rms[2] > 3u * rms[4] ? "micsilent mic=YES" : "micsilent mic=no");
    if (frames <= 0)
        return frames;
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
