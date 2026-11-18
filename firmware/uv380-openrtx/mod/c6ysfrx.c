/* c6ysfrx: YSF (C4FM) receive on the unmodified UV-380, on the radio (docs/uv380-multimode.md
   milestone 5, as a RAM module before any of it goes in flash). Receive only (no RF out). The
   HR_C6000's 8 kHz FM tap, set up exactly as c6m17rx (0x34 = 0x1C, flat 10 Hz-3.4 kHz, DC
   coupled, read +3 negative), feeds c/common/ysf_rx.c, and what it decodes goes to the USB serial:
   header and terminator callsigns, the sotto softmodem's sotto VW superframes, data full rate
   blocks, DN voice frames' FICH. Its state (about 5 KB) lives in scratch.

   Set-up, all of it put back at the end (or at PTT, mode change, Ctrl-C): the AT1846S in its DMR
   receive set (at1846.h), RX_AUDIO_MUX (PD9) high, the C6000 in FM receive (0x10 = 0x80,
   0x60 = 0x00, 0x26 = 0xFD), 0x34 = YSFRX_V34, 0x81 |= 0x02. Halves as c6m17rx: on 0x82 bit 1
   the complete half is the one whose last sample changed since the last flag.

   Lines ("mod say ..."; T is ms from the first sample the receiver had, AT/24):
     ysf start s=150 v34=1c at=ok scratch=N need=N tap=0
     f T hdr ok=3 dst=ALL src=AG7EW sc= g= m=           ok: bit 0 CSD1's CRC, bit 1 CSD2's
     f T vw fn=0/6 fich=1 ok=1f sf=HEX(30 bytes) sc= g= m=   ok: superframe k's CRC-8 in bit k;
                                                         fich=0: read on with the FICH lost
     f T dfr fn=1/7 ok=3 d=TEXT|TEXT sc= g= m=           data FR: DCH1 | DCH2 as text
     f T dn fn=3/7 dgid=0 sc= g= m=                      DN voice (AMBE): the FICH only
     f T term ok=3 dst= src=
     st T halves= gaps= frames= lost= bad= rssi=         once a second
     end halves= gaps= frames= lost= bad= hdr= vw= dfr= dn= term= sf=GOOD/ALL first=HEX last=HEX
   sc and m are thousandths (sync score, FICH Viterbi disagreement); g is tap units per level;
   first and last are the first and last superframes with a good CRC-8 (to check against what was
   sent). YSFRX_TAP 1 (c6ysfrx_tap.c) also sends each half as c6fmtap's four "a" lines, so the
   host receiver (c/common/ysf_rx_host) can be run on exactly what the radio had.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "at1846.h"

#define YSF_INVERT 1
#include "../../../c/common/ysf_rx.c"

#ifndef YSFRX_S
#define YSFRX_S 150u
#endif
#ifndef YSFRX_V34
#define YSFRX_V34 0x1C
#endif
#ifndef YSFRX_TAP
#define YSFRX_TAP 0
#endif

#define GPIOD 0x40020C00u
#define MUX 9u
#define HALF0 0x030u
#define HALF1 0x230u
#define BLK 64u

typedef struct {
    ysf_rx rx;
    ysf_frame fr;
    unsigned n_hdr, n_vw, n_dfr, n_dn, n_term;
    unsigned sf_ok, sf_all;
    uint8_t first[6], last[6];
} ysf_mod;

static unsigned rd16(const ortx_api_t *api, unsigned a) {
    uint8_t b[2];
    if (c6_read(api, C6_DATA, a, b, 2))
        return 0x10000u;                 /* port busy: never equals a sample */
    return ((unsigned)b[0] << 8) | b[1];
}

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

static unsigned milli(float v) {
    return v <= 0.0f ? 0u : (unsigned)(v * 1000.0f + 0.5f);
}

static unsigned put_hex(char *o, const uint8_t *d, unsigned n) {
    for (unsigned i = 0; i < n; i++) {
        o[2 * i] = hexd(d[i] >> 4);
        o[2 * i + 1] = hexd(d[i] & 15u);
    }
    o[2 * n] = 0;
    return 2u * n;
}

/* n bytes as printable text, trailing spaces off */
static unsigned put_text(char *o, const uint8_t *d, unsigned n) {
    while (n && d[n - 1u] == ' ')
        n--;
    for (unsigned i = 0; i < n; i++)
        o[i] = (d[i] >= 32u && d[i] < 127u) ? (char)d[i] : '?';
    o[n] = 0;
    return n;
}

static void report(const ortx_api_t *api, ysf_mod *m) {
    ysf_frame *f = &m->fr;
    char line[200], a[64], b[24];
    unsigned long t = (unsigned long)(f->at / 24u);
    unsigned sc = milli(f->score), g = f->gain > 0.0f ? (unsigned)f->gain : 0u, q = milli(f->metric);
    if (f->fi == YSF_FI_HEADER || f->fi == YSF_FI_TERM) {
        put_text(a, f->data, 10);
        put_text(b, f->data + 10, 10);
        if (f->fi == YSF_FI_HEADER)
            m->n_hdr++;
        else
            m->n_term++;
        api->fmt(line, sizeof line, "f %lu %s ok=%u dst=%s src=%s sc=%u g=%u m=%u", t,
                 f->fi == YSF_FI_HEADER ? "hdr" : "term", f->ok, (f->ok & 1u) ? a : "?", (f->ok & 1u) ? b : "?",
                 sc, g, q);
    } else if (f->pay == YSF_PAY_SOTTO) {
        char h[64];
        m->n_vw++;
        for (unsigned k = 0; k < 5u; k++) {
            m->sf_all++;
            if (!((f->ok >> k) & 1u))
                continue;
            if (!m->sf_ok)
                api->memcpy(m->first, f->data + 6u * k, 6);
            api->memcpy(m->last, f->data + 6u * k, 6);
            m->sf_ok++;
        }
        put_hex(h, f->data, 30);
        api->fmt(line, sizeof line, "f %lu vw fn=%u/%u fich=%d ok=%x sf=%s sc=%u g=%u m=%u", t, f->fn, f->ft,
                 f->fich_ok, f->ok, h, sc, g, q);
    } else if (f->pay == YSF_PAY_DATA_FR) {
        char d[48];
        unsigned n = put_text(d, f->data, 20);
        d[n++] = '|';
        put_text(d + n, f->data + 20, 20);
        m->n_dfr++;
        api->fmt(line, sizeof line, "f %lu dfr fn=%u/%u ok=%u d=%s sc=%u g=%u m=%u", t, f->fn, f->ft, f->ok, d,
                 sc, g, q);
    } else {
        m->n_dn++;
        api->fmt(line, sizeof line, "f %lu %s fn=%u/%u dgid=%u fich=%02x%02x%02x%02x sc=%u g=%u m=%u", t,
                 f->dt == YSF_DT_VD2 ? "dn" : "fich", f->fn, f->ft, f->dgid, f->fich[0], f->fich[1], f->fich[2],
                 f->fich[3], sc, g, q);
    }
    api->say(line);
}

/* one complete half: to the receiver (and as "a" lines with YSFRX_TAP) */
static void feed(const ortx_api_t *api, ysf_mod *m, unsigned base, uint32_t *seq) {
    uint8_t b[2 * BLK];
    for (unsigned k = 0; k < 4u; k++) {
        if (c6_read(api, C6_DATA, base + k * 2u * BLK, b, sizeof b)) {
            (*seq)++;
            continue;
        }
        if (YSFRX_TAP) {
            char line[24 + BLK * 5u];
            unsigned n = (unsigned)api->fmt(line, sizeof line, "a %lu", (unsigned long)*seq);
            for (unsigned i = 0; i < BLK; i++) {
                unsigned v = ((unsigned)b[2 * i] << 8) | b[2 * i + 1];
                line[n++] = ' ';
                for (int s = 12; s >= 0; s -= 4)
                    line[n++] = hexd((v >> s) & 15u);
            }
            line[n++] = '\r';
            line[n++] = '\n';
            api->usb_write(line, n);
        }
        (*seq)++;
        for (unsigned i = 0; i < BLK; i++) {
            int16_t s = (int16_t)(((unsigned)b[2 * i] << 8) | b[2 * i + 1]);
            if (ysf_push(&m->rx, s, &m->fr))
                report(api, m);
        }
    }
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x60, 0x26, 0x81, 0x34, 0x36, 0xE0, 0};
    char line[200];
    uint32_t sz = 0;
    ysf_mod *m = (ysf_mod *)api->scratch(&sz);
    if (sz < sizeof(ysf_mod)) {
        api->fmt(line, sizeof line, "ysf err scratch=%lu need=%u", (unsigned long)sz, (unsigned)sizeof(ysf_mod));
        api->say(line);
        return -1;
    }
    uint8_t *z = (uint8_t *)m;
    for (unsigned i = 0; i < sizeof(ysf_mod); i++)
        z[i] = 0;
    ysf_init(&m->rx);

    c6_saved sv;
    at_saved as;
    as.ok = 0;
    c6_save(api, &sv, regs);
    uint32_t mux = (C6_REG(GPIOD + 0x14u) >> MUX) & 1u;
    C6_REG(GPIOD + 0x18u) = 1u << MUX;
    int atr = at_dmr_rx(api, &as, 0);
    uint8_t m81 = api->c6000_read(0x81);
    api->c6000_write(0x10, 0x80);
    api->c6000_write(0x60, 0x00);
    api->c6000_write(0x26, 0xFD);
    api->c6000_write(0x81, (uint8_t)(m81 | 0x02u));
    api->c6000_write(0x34, YSFRX_V34);
    api->c6000_write(0x83, 0xFF);
    api->fmt(line, sizeof line, "ysf start s=%u v34=%02x at=%s scratch=%lu need=%u tap=%d", YSFRX_S, YSFRX_V34,
             atr ? "err" : "ok", (unsigned long)sz, (unsigned)sizeof(ysf_mod), YSFRX_TAP);
    api->say(line);

    uint32_t seq = 0, t0 = api->ms(), last_st = 0;
    unsigned halves = 0, gaps = 0, last = 2, pe[2];
    pe[0] = rd16(api, HALF0 + 510u), pe[1] = rd16(api, HALF1 + 510u);
    while (api->ms() - t0 < YSFRX_S * 1000u && !api->stop()) {
        uint32_t t = api->ms() - t0;
        uint8_t f = api->c6000_read(0x82);
        if (f)
            api->c6000_write(0x83, f);
        if (f & 0x02u) {
            unsigned se0 = rd16(api, HALF0 + 510u), se1 = rd16(api, HALF1 + 510u);
            unsigned c0 = se0 != pe[0], c1 = se1 != pe[1];
            int done = c0 != c1 ? (c0 ? 0 : 1) : (last < 2u ? (int)(1u - last) : 0);
            pe[0] = se0, pe[1] = se1;
            if ((unsigned)done == last) {
                gaps++;
                seq += 4u;
            }
            last = (unsigned)done;
            feed(api, m, done ? HALF1 : HALF0, &seq);
            halves++;
        }
        if (t - last_st >= 1000u) {
            last_st = t;
            api->fmt(line, sizeof line, "st %lu halves=%u gaps=%u frames=%lu lost=%lu bad=%lu rssi=%ld",
                     (unsigned long)t, halves, gaps, (unsigned long)m->rx.frames, (unsigned long)m->rx.lost,
                     (unsigned long)m->rx.bad, (long)api->rssi());
            api->say(line);
        }
        api->sleep_ms(1);
    }

    api->c6000_write(0x81, m81);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    at_restore(api, &as);
    C6_REG(GPIOD + 0x18u) = mux ? (1u << MUX) : (1u << (MUX + 16u));
    char h0[16], h1[16];
    put_hex(h0, m->first, 6);
    put_hex(h1, m->last, 6);
    api->fmt(line, sizeof line, "end halves=%u gaps=%u frames=%lu lost=%lu bad=%lu hdr=%u vw=%u dfr=%u dn=%u term=%u "
             "sf=%u/%u first=%s last=%s", halves, gaps, (unsigned long)m->rx.frames, (unsigned long)m->rx.lost,
             (unsigned long)m->rx.bad, m->n_hdr, m->n_vw, m->n_dfr, m->n_dn, m->n_term, m->sf_ok, m->sf_all,
             h0, h1);
    api->say(line);
    return 0;
}
