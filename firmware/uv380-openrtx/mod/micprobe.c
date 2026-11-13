/* micprobe: looks for a setting that puts the UV-380's microphone on I2S3 with no hardware mod.
   The stock firmware does DMR this way: the mic goes into the HR_C6000's codec ADC, and the MCU
   (or the C6000's vocoder) takes it from there. rxaudio.h already reads that ADC on I2S3 with the
   FM receiver's audio routed in (register 26 = fd, RX_AUDIO_MUX PD9 high); this module powers the
   mic preamp (MIC_PWR, PA13, as OpenRTX does for its mic paths) and tries the input settings one
   at a time, streaming each setting's audio to the USB serial so the host can tell which one
   carries sound played into the mic (tools/uv380_micprobe.py: level, voice-band share and the
   correlation with a reference recording of what was played).

   Settings, each held MP_MS ms (the first 100 ms after a change are dropped):
     0  as found: register 26, PD9 and MIC_PWR as they were
     1  26=fd PD9 high, mic on        the receive route (reference: receiver audio)
     2  26=fd PD9 low,  mic on
     3  26 as found, PD9 low,  mic on
     4  26 as found, PD9 high, mic on
     then on setting 2: each bit of E0, E2, E4, E5 flipped (the codec block; E1 is the known gain,
     E3 partly status), then each bit of register 26 flipped. Never B9 (c6sweep: it sleeps).
   After each flip the register is written back and register 00 must still read its start value,
   or the module says STOP and ends (the chip stopped answering: power-cycle).

   Lines:
     mod say micprobe reg00=5a r26=NN e0..e5=NN NN NN NN NN NN pa13=M/O align=1
     mod say set K r26=NN pd9=P mic=M REG=NN       before each setting's audio
     a SEQ hhhh ...                                64 samples a line, as rxrec (8 kHz)
     mod say got K rms=N zc=N pk=N ov=N al=N        its level, zero crossings a second, peak,
                                                    ring overruns, realign tries (0: in step)
     mod say micprobe end sets=N
   Receive only: touches no RF register. Puts back 26, E0-E5, PD9, PA13, the pins, SPI3 and the
   DMA stream as it found them. Returns the settings done, negative if it stopped early.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "rxaudio.h"

#ifndef MP_MS
#define MP_MS 1200u
#endif
#define RING 1024u
#define BLK 64u
#define MIC_PIN 13u                 /* MIC_PWR, GPIOA 13 (OpenRTX MD-UV3x0 pinmap) */

static const uint8_t CODEC[] = {
    0xE0,
    0xE2,
    0xE4,
    0xE5,
};

typedef struct {
    rxa_state rs;
    uint8_t id, r26, e[6];
    uint32_t pa_moder, pa_odr;
    unsigned seq, k;
} mp_t;

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

static void pd9(unsigned hi) {
    RXA_REG(RXA_GPIOD + 0x18u) = hi ? 1u << RXA_MUX_PIN : 1u << (RXA_MUX_PIN + 16);
}

/* back in step with the codec's frames if a setting knocked it out: tries (0: it was in step) */
static unsigned realign(const ortx_api_t *api, mp_t *m) {
    if (rxa_aligned(&m->rs, rx_wr(&m->rs)))
        return 0;
    for (unsigned k = 0; k < 16u; k++) {
        rxa_i2s_on((k & 1u) ^ 1u);
        api->sleep_ms(15);
        if (rxa_aligned(&m->rs, rx_wr(&m->rs)))
            return k + 1u;
    }
    return 99u;
}

/* MP_MS of audio as "a" lines, then the "got" line */
static void grab(const ortx_api_t *api, mp_t *m, char *line, unsigned al) {
    volatile int16_t *ring = m->rs.buf;
    api->sleep_ms(100);
    unsigned rd = rx_wr(&m->rs) & ~1u, ov = 0, zc = 0, pk = 0, got = 0;
    uint32_t sq = 0, end = api->ms() + MP_MS;
    int prev = 0;
    while ((int32_t)(api->ms() - end) < 0 && !api->stop()) {
        unsigned wr = rx_wr(&m->rs), avail = (wr - rd) & (RING - 1u);
        if (avail > RING - 128u) {
            ov++;
            rd = (wr + RING - 2u) & ~1u & (RING - 1u);
            m->seq++;
            continue;
        }
        if (avail < 2u * BLK) {
            api->sleep_ms(2);
            continue;
        }
        unsigned n = (unsigned)api->fmt(line, 24, "a %u", m->seq++);
        for (unsigned i = 0; i < BLK; i++) {
            int v = ring[(rd + 1u) & (RING - 1u)];
            rd = (rd + 2u) & (RING - 1u);
            unsigned a = (unsigned)(v < 0 ? -v : v);
            if (a > pk)
                pk = a;
            if ((v < 0) != (prev < 0))
                zc++;
            prev = v;
            if (got < 9600u)
                sq += (uint32_t)(v * v) >> 12;
            got++;
            line[n++] = ' ';
            for (int k = 12; k >= 0; k -= 4)
                line[n++] = hexd(((uint16_t)v >> k) & 15u);
        }
        line[n++] = '\r';
        line[n++] = '\n';
        api->usb_write(line, n);
    }
    unsigned cnt = got < 9600u ? got : 9600u;
    unsigned rms = cnt ? isqrt((sq / cnt) << 12) : 0;
    api->fmt(line, 96, "got %u rms=%u zc=%u pk=%u ov=%u al=%u", m->k, rms,
             got ? (unsigned)((uint32_t)zc * 8000u / got) : 0u, pk, ov, al);
    api->say(line);
}

/* one setting: 26, PD9, MIC_PWR, and reg = val (reg 0: none); then its audio */
static void one(const ortx_api_t *api, mp_t *m, char *line, uint8_t r26, unsigned p9, unsigned mc,
                uint8_t reg, uint8_t val) {
    api->c6000_write(0x26, r26);
    pd9(p9);
    if (mc != 2u)
        mic(mc);
    if (reg)
        api->c6000_write(reg, val);
    api->fmt(line, 96, "set %u r26=%02x pd9=%u mic=%u %02x=%02x", m->k, r26, p9, mc, reg, val);
    api->say(line);
    api->sleep_ms(20);
    grab(api, m, line, realign(api, m));
    m->k++;
}

static int alive(const ortx_api_t *api, mp_t *m, char *line, uint8_t reg) {
    uint8_t now = api->c6000_read(0x00);
    if (now == m->id)
        return 1;
    api->fmt(line, 96, "STOP after %02x: reg00 %02x (want %02x)", reg, now, m->id);
    api->say(line);
    return 0;
}

int uv_main(const ortx_api_t *api) {
    char line[24 + BLK * 5u];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u)
        return -8;
    mp_t m;
    m.id = api->c6000_read(0x00);
    if (m.id == 0x00) {
        api->say("the C6000 doesn't answer: power-cycle first");
        return -1000;
    }
    for (unsigned i = 0; i < 6u; i++)
        m.e[i] = api->c6000_read((uint8_t)(0xE0u + i));
    m.pa_moder = RXA_REG(RXA_GPIOA);
    m.pa_odr = RXA_REG(RXA_GPIOA + 0x14u);
    unsigned align = rx_start(api, &m.rs, (volatile int16_t *)mem, RING);
    m.r26 = m.rs.r26;
    m.seq = 0, m.k = 0;
    api->fmt(line, sizeof line,
             "micprobe reg00=%02x r26=%02x e0..e5=%02x %02x %02x %02x %02x %02x pa13=%lu/%lu pd9=%lu "
             "align=%u",
             m.id, m.r26, m.e[0], m.e[1], m.e[2], m.e[3], m.e[4], m.e[5],
             (unsigned long)((m.pa_moder >> (2 * MIC_PIN)) & 3u),
             (unsigned long)((m.pa_odr >> MIC_PIN) & 1u), (unsigned long)m.rs.mux, align);
    api->say(line);

    int ok = 1;
    one(api, &m, line, m.r26, m.rs.mux, 2u, 0, 0);     /* 0: as found (mic pin untouched) */
    one(api, &m, line, 0xFD, 1u, 1u, 0, 0);
    one(api, &m, line, 0xFD, 0u, 1u, 0, 0);
    one(api, &m, line, m.r26, 0u, 1u, 0, 0);
    one(api, &m, line, m.r26, 1u, 1u, 0, 0);
    for (unsigned r = 0; ok && r < sizeof CODEC && !api->stop(); r++) {
        uint8_t was = m.e[CODEC[r] - 0xE0u];
        for (unsigned b = 0; ok && b < 8u && !api->stop(); b++) {
            one(api, &m, line, 0xFD, 0u, 1u, CODEC[r], (uint8_t)(was ^ (1u << b)));
            api->c6000_write(CODEC[r], was);
            api->sleep_ms(5);
            ok = alive(api, &m, line, CODEC[r]);
        }
    }
    for (unsigned b = 0; ok && b < 8u && !api->stop(); b++) {
        one(api, &m, line, (uint8_t)(0xFDu ^ (1u << b)), 0u, 1u, 0, 0);
        api->c6000_write(0x26, 0xFD);
        api->sleep_ms(5);
        ok = alive(api, &m, line, 0x26);
    }

    for (unsigned i = 0; i < 6u; i++)
        if (i != 3u)
            api->c6000_write((uint8_t)(0xE0u + i), m.e[i]);
    rx_stop(api, &m.rs);         /* 26 and PD9 as found */
    RXA_REG(RXA_GPIOA + 0x18u) =
        (m.pa_odr >> MIC_PIN) & 1u ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
    RXA_REG(RXA_GPIOA) = m.pa_moder;
    api->fmt(line, sizeof line, "micprobe end sets=%u", m.k);
    api->say(line);
    return ok ? (int)m.k : -(int)m.k;
}
