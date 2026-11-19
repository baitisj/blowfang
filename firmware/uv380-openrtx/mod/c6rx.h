/* c6rx.h: M17 or YSF receive on the unmodified UV-380, the mode picked at run time, and the sotto
   message put back together from what it hears (docs/uv380-multimode.md, milestone 5). One body
   for both users, as c6air.h is for transmit:
     - the c6sottorx RAM modules (c6sottorx.c), to prove it on the bench first;
     - the image (c6rx_img.c): PAGER mode's M17 and YSF sub-modes listen while idle and play what
       they hear, as RNG and FSK do.
   The tap is set up exactly as c6m17rx and c6ysfrx (both proven on air 11-18): the AT1846S in its
   DMR receive set (at1846.h), RX_AUDIO_MUX (PD9) high, the C6000 in FM receive (0x10 = 0x80,
   0x60 = 0x00, 0x26 = 0xFD), 0x34 = 0x1C (flat 10 Hz-3.4 kHz, DC coupled, read +3 negative),
   0x81 |= 0x02; on 0x82 bit 1 the complete half is the one whose last sample changed. All of it
   is put back when c6rx_listen returns. Receive only: no RF out.

   c6rx_listen(api, air, st, max_ms, out, outmax): listens until a message has ended (its last
   frame, an EOT or a terminator, or C6RX_QUIET_MS with no new superframe after the first), then
   copies it into out (superframes not heard filled from the one before) and returns its
   superframes; 0 when api->stop() or max_ms came first with nothing heard; -1 if st is too
   small. Lines:
     rx AIR msg sf=N got=G missing=M bad=B over=O end=1|0 src=CALL first=HEX last=HEX rssi=R
     rx AIR st halves= gaps= frames= lost=                once a second, with C6RX_ST 1
     rx AIR f T ...                                       each frame, with C6RX_FRAMES 1
   Include ortx_mod.h, c6spi.h and at1846.h first. Header only, no writable statics.
   SPDX-License-Identifier: MIT */
#ifndef C6RX_H
#define C6RX_H

/* which receivers: both (the image), or one (a module, which has room for one) */
#ifndef C6RX_HAS_M17
#define C6RX_HAS_M17 1
#endif
#ifndef C6RX_HAS_YSF
#define C6RX_HAS_YSF 1
#endif
#define M17_INVERT 1
#define YSF_INVERT 1
#if C6RX_HAS_M17
#include "../../../c/common/m17_rx.c"
#else
#include "../../../c/common/m17_rx.h"
#endif
#if C6RX_HAS_YSF
#include "../../../c/common/ysf_rx.c"
#else
#include "../../../c/common/ysf_rx.h"
#endif
#include "../../../c/common/sotto_rx.c"

#define C6RX_M17 1u
#define C6RX_YSF 2u
#ifndef C6RX_MAX
#define C6RX_MAX 750u                   /* superframes: 30 s, the image's REC_MAX */
#endif
#ifndef C6RX_QUIET_MS
#define C6RX_QUIET_MS 1500u             /* no new superframe this long after the first: the end */
#endif
#ifndef C6RX_ST
#define C6RX_ST 0
#endif
#ifndef C6RX_FRAMES
#define C6RX_FRAMES 0
#endif
#define C6RX_V34 0x1Cu
#define C6RX_GPIOD 0x40020C00u
#define C6RX_MUX 9u
#define C6RX_HALF0 0x030u
#define C6RX_HALF1 0x230u
#define C6RX_BLK 64u
#define C6RX_FN static __attribute__((unused))

typedef struct {
    union {
#if C6RX_HAS_M17
        m17_rx m;
#endif
#if C6RX_HAS_YSF
        ysf_rx y;
#endif
    } rx;
    union {
        m17_frame m;
        ysf_frame y;
    } fr;
    srx_state s;
    unsigned air;
    uint32_t last_new;                  /* ms of the last new superframe */
    char src[12];
    uint8_t have[C6RX_MAX / 8u + 1u];
    uint8_t frames[C6RX_MAX * SRX_SF];
} c6rx_state;

C6RX_FN unsigned c6rx_rd16(const ortx_api_t *api, unsigned a) {
    uint8_t b[2];
    if (c6_read(api, C6_DATA, a, b, 2))
        return 0x10000u;                /* port busy: never equals a sample */
    return ((unsigned)b[0] << 8) | b[1];
}

C6RX_FN void c6rx_hex(char *o, const uint8_t *d, unsigned n) {
    for (unsigned i = 0; i < n; i++) {
        unsigned h = d[i] >> 4, l = d[i] & 15u;
        o[2 * i] = (char)(h < 10u ? '0' + h : 'a' + h - 10u);
        o[2 * i + 1] = (char)(l < 10u ? '0' + l : 'a' + l - 10u);
    }
    o[2 * n] = 0;
}

C6RX_FN const char *c6rx_name(unsigned air) {
    return air == C6RX_M17 ? "m17" : "ysf";
}

/* a frame: into the message; the sender's callsign from an LSF or a YSF header */
C6RX_FN void c6rx_frame(const ortx_api_t *api, c6rx_state *c, uint32_t now) {
    int got = 0;
    if (c->air == C6RX_M17) {
#if C6RX_HAS_M17
        const m17_frame *f = &c->fr.m;
        if (f->kind == M17_LSF && f->ok)
            m17_callsign(f->data + 6, c->src);
        got = srx_m17(&c->s, f);
#endif
    } else {
#if C6RX_HAS_YSF
        const ysf_frame *f = &c->fr.y;
        if (f->fi == YSF_FI_HEADER && (f->ok & 1u)) {
            unsigned n = 10;
            while (n && f->data[10u + n - 1u] == ' ')
                n--;
            for (unsigned i = 0; i < n; i++)
                c->src[i] = (f->data[10u + i] >= 32u && f->data[10u + i] < 127u) ? (char)f->data[10u + i] : '?';
            c->src[n] = 0;
        }
        got = srx_ysf(&c->s, f);
#endif
    }
    if (got > 0)
        c->last_new = now;
    if (C6RX_FRAMES) {
        char line[96];
        api->fmt(line, sizeof line, "rx %s f %lu new=%d got=%u n=%u bad=%u end=%d", c6rx_name(c->air),
                 (unsigned long)now, got, c->s.got, c->s.n, c->s.bad, c->s.ended);
        api->say(line);
    }
}

/* one sample to the receiver in use; 1 when a frame came */
C6RX_FN int c6rx_push(c6rx_state *c, int16_t s) {
#if C6RX_HAS_M17
    if (c->air == C6RX_M17)
        return m17_push(&c->rx.m, s, &c->fr.m);
#endif
#if C6RX_HAS_YSF
    if (c->air == C6RX_YSF)
        return ysf_push(&c->rx.y, s, &c->fr.y);
#endif
    (void)s;
    return 0;
}

C6RX_FN uint32_t c6rx_count(const c6rx_state *c, int lost) {
#if C6RX_HAS_M17
    if (c->air == C6RX_M17)
        return lost ? c->rx.m.lost : c->rx.m.frames;
#endif
#if C6RX_HAS_YSF
    if (c->air == C6RX_YSF)
        return lost ? c->rx.y.lost : c->rx.y.frames;
#endif
    (void)lost;
    return 0;
}

/* one complete half: four 64-sample blocks to the receiver */
C6RX_FN void c6rx_feed(const ortx_api_t *api, c6rx_state *c, unsigned base, uint32_t now) {
    uint8_t b[2 * C6RX_BLK];
    for (unsigned k = 0; k < 4u; k++) {
        if (c6_read(api, C6_DATA, base + k * 2u * C6RX_BLK, b, sizeof b))
            continue;
        for (unsigned i = 0; i < C6RX_BLK; i++) {
            int16_t s = (int16_t)(((unsigned)b[2 * i] << 8) | b[2 * i + 1]);
            if (c6rx_push(c, s))
                c6rx_frame(api, c, now);
        }
    }
}

C6RX_FN int c6rx_listen(const ortx_api_t *api, unsigned air, c6rx_state *c, uint32_t st_bytes, uint32_t max_ms,
                        uint8_t *out, unsigned outmax) {
    static const uint8_t regs[] = {0x10, 0x60, 0x26, 0x81, 0x34, 0x36, 0xE0, 0};
    char line[200];
    if (st_bytes < sizeof(c6rx_state)) {
        api->fmt(line, sizeof line, "rx err scratch=%lu need=%u", (unsigned long)st_bytes,
                 (unsigned)sizeof(c6rx_state));
        api->say(line);
        return -1;
    }
    c->air = air;
    if (!((air == C6RX_M17 && C6RX_HAS_M17) || (air == C6RX_YSF && C6RX_HAS_YSF))) {
        api->say("rx err: this build has no receiver for that mode");
        return -1;
    }
#if C6RX_HAS_M17
    if (air == C6RX_M17)
        m17_init(&c->rx.m);
#endif
#if C6RX_HAS_YSF
    if (air == C6RX_YSF)
        ysf_init(&c->rx.y);
#endif
    srx_init(&c->s, c->frames, c->have, C6RX_MAX);
    c->src[0] = '?';
    c->src[1] = 0;
    c->last_new = 0;

    c6_saved sv;
    at_saved as;
    as.ok = 0;
    c6_save(api, &sv, regs);
    uint32_t mux = (C6_REG(C6RX_GPIOD + 0x14u) >> C6RX_MUX) & 1u;
    C6_REG(C6RX_GPIOD + 0x18u) = 1u << C6RX_MUX;
    at_dmr_rx(api, &as, 0);
    uint8_t m81 = api->c6000_read(0x81);
    api->c6000_write(0x10, 0x80);
    api->c6000_write(0x60, 0x00);
    api->c6000_write(0x26, 0xFD);
    api->c6000_write(0x81, (uint8_t)(m81 | 0x02u));
    api->c6000_write(0x34, C6RX_V34);
    api->c6000_write(0x83, 0xFF);

    uint32_t t0 = api->ms(), last_st = 0;
    unsigned halves = 0, gaps = 0, last = 2, pe[2];
    int32_t rssi = 0;
    pe[0] = c6rx_rd16(api, C6RX_HALF0 + 510u), pe[1] = c6rx_rd16(api, C6RX_HALF1 + 510u);
    while (!api->stop()) {
        uint32_t t = api->ms() - t0;
        if (c->s.got && (c->s.ended || t - c->last_new >= C6RX_QUIET_MS))
            break;
        if (!c->s.got && t >= max_ms)
            break;
        uint8_t f = api->c6000_read(0x82);
        if (f)
            api->c6000_write(0x83, f);
        if (f & 0x02u) {
            unsigned se0 = c6rx_rd16(api, C6RX_HALF0 + 510u), se1 = c6rx_rd16(api, C6RX_HALF1 + 510u);
            unsigned c0 = se0 != pe[0], c1 = se1 != pe[1];
            int done = c0 != c1 ? (c0 ? 0 : 1) : (last < 2u ? (int)(1u - last) : 0);
            pe[0] = se0, pe[1] = se1;
            if ((unsigned)done == last)
                gaps++;
            last = (unsigned)done;
            c6rx_feed(api, c, done ? C6RX_HALF1 : C6RX_HALF0, t);
            halves++;
            if (c->s.got == 1u && rssi == 0)
                rssi = api->rssi();
        }
        if (C6RX_ST && t - last_st >= 1000u) {
            last_st = t;
            api->fmt(line, sizeof line, "rx %s st %lu halves=%u gaps=%u frames=%lu lost=%lu", c6rx_name(c->air),
                     (unsigned long)t, halves, gaps,
                     (unsigned long)c6rx_count(c, 0), (unsigned long)c6rx_count(c, 1));
            api->say(line);
        }
        api->sleep_ms(1);
    }

    api->c6000_write(0x81, m81);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    at_restore(api, &as);
    C6_REG(C6RX_GPIOD + 0x18u) = mux ? (1u << C6RX_MUX) : (1u << (C6RX_MUX + 16u));

    if (!c->s.got)
        return 0;
    unsigned missing = srx_conceal(&c->s), n = c->s.n;
    char h0[16], h1[16];
    c6rx_hex(h0, c->frames, SRX_SF);
    c6rx_hex(h1, c->frames + (n - 1u) * SRX_SF, SRX_SF);
    api->fmt(line, sizeof line, "rx %s msg sf=%u got=%u missing=%u bad=%u over=%u end=%d src=%s first=%s last=%s "
             "rssi=%ld gaps=%u", c6rx_name(c->air), n, c->s.got, missing, c->s.bad, c->s.over, c->s.ended, c->src,
             h0, h1, (long)rssi, gaps);
    api->say(line);
    if (out) {
        if (n > outmax)
            n = outmax;
        api->memcpy(out, c->frames, n * SRX_SF);
    }
    return (int)n;
}

#endif
