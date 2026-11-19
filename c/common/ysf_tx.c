/* ysf_tx: YSF frames one at a time (ysf_tx.h). A port of modem/ysf.py's encoder.
   SPDX-License-Identifier: MIT */
#include "ysf_tx.h"

#define SYNC_BITS 40u
#define PAYLOAD_AT 240u                 /* sync + FICH */
#define GOLAY_POLY 0xC75u

static const uint8_t SYNC[5] = {0xD4, 0x71, 0xC9, 0x63, 0x4D};

static unsigned ysft_parity(unsigned v) {
    v ^= v >> 16;
    v ^= v >> 8;
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

static void put_bit(uint8_t *f, unsigned pos, unsigned bit) {
    uint8_t m = (uint8_t)(0x80u >> (pos % 8u));
    if (bit)
        f[pos / 8u] |= m;
    else
        f[pos / 8u] &= (uint8_t)~m;
}

static unsigned get_bit(const uint8_t *d, unsigned i) {
    return (d[i / 8u] >> (7u - i % 8u)) & 1u;
}

/* PN9 x^9 + x^5 + 1, LSB out: the next bit */
static unsigned pn9(unsigned *s) {
    unsigned b = *s & 1u;
    *s = (*s >> 1) | ((((*s >> 4) ^ *s) & 1u) << 8);
    return b;
}

uint16_t ysft_crc(const uint8_t *d, unsigned n) {
    uint16_t crc = 0;
    for (unsigned i = 0; i < n; i++) {
        crc ^= (uint16_t)(d[i] << 8);
        for (unsigned b = 0; b < 8u; b++)
            crc = (uint16_t)((crc & 0x8000u) ? ((unsigned)crc << 1) ^ 0x1021u : (unsigned)crc << 1);
    }
    return (uint16_t)(crc ^ 0xFFFFu);
}

static uint32_t golay24(unsigned d) {
    uint32_t r = (uint32_t)d << 11;
    for (unsigned i = 22; i >= 11u; i--)
        if (r & (1u << i))
            r ^= (uint32_t)GOLAY_POLY << (i - 11u);
    uint32_t c = (uint32_t)d << 11 | r;
    return c << 1 | ysft_parity(c);
}

/* nbytes of d + 4 flush bits through the K=5 code, dibit i (of cols x 20) to field bits
   (i % cols) * 40 + (i / cols) * 2 and + 1; field bit j lands at frame bit at(j) */
typedef unsigned (*field_at)(unsigned j, unsigned arg);

static void conv_field(uint8_t *f, const uint8_t *d, unsigned nbytes, unsigned cols,
                       field_at at, unsigned arg) {
    unsigned m = 0;
    for (unsigned i = 0; i < 8u * nbytes + 4u; i++) {
        unsigned b = i < 8u * nbytes ? get_bit(d, i) : 0u;
        m = ((m << 1) | b) & 0x1Fu;
        unsigned j = (i % cols) * 40u + (i / cols) * 2u;
        put_bit(f, at(j, arg), ysft_parity(m & 0x19u));
        put_bit(f, at(j + 1u, arg), ysft_parity(m & 0x17u));
    }
}

static unsigned at_fich(unsigned j, unsigned arg) {
    (void)arg;
    return SYNC_BITS + j;
}

/* CSD field bit j (0-359) of field arg (0: the first 72 bits of each section, 1: the last 72) */
static unsigned at_csd(unsigned j, unsigned arg) {
    return PAYLOAD_AT + (j / 72u) * 144u + arg * 72u + j % 72u;
}

void ysft_fich_default(ysft_fich *f) {
    uint8_t *p = (uint8_t *)f;
    for (unsigned i = 0; i < sizeof *f; i++)
        p[i] = 0;
    f->fi = YSFT_FI_COMMS;
    f->cs = 2;
    f->ft = 6;
    f->dt = YSFT_DT_VOICE_FR;
}

void ysft_fich_bytes(const ysft_fich *f, uint8_t out[4]) {
    out[0] = (uint8_t)((f->fi & 3u) << 6 | (f->cs & 3u) << 4 | (f->cm & 3u) << 2 | (f->bn & 3u));
    out[1] = (uint8_t)((f->bt & 3u) << 6 | (f->fn & 7u) << 3 | (f->ft & 7u));
    out[2] = (uint8_t)((f->sotto ? 0x80u : 0u) | (f->dev ? 0x40u : 0u) | (f->mr & 7u) << 3 |
                       (f->voip ? 4u : 0u) | (f->dt & 3u));
    out[3] = (uint8_t)((f->sql ? 0x80u : 0u) | (f->dgid & 0x7Fu));
}

static void frame_start(const ysft_fich *fi, uint8_t out[YSFT_FRAME]) {
    for (unsigned i = 0; i < YSFT_FRAME; i++)
        out[i] = 0;
    for (unsigned i = 0; i < 5u; i++)
        out[i] = SYNC[i];
    uint8_t raw[6], cw[12];
    ysft_fich_bytes(fi, raw);
    uint16_t c = ysft_crc(raw, 4);
    raw[4] = (uint8_t)(c >> 8);
    raw[5] = (uint8_t)c;
    for (unsigned k = 0; k < 4u; k++) {
        unsigned bit = 12u * k, d = 0;
        for (unsigned i = 0; i < 12u; i++)
            d = d << 1 | get_bit(raw, bit + i);
        uint32_t w = golay24(d);
        cw[3 * k] = (uint8_t)(w >> 16);
        cw[3 * k + 1] = (uint8_t)(w >> 8);
        cw[3 * k + 2] = (uint8_t)w;
    }
    conv_field(out, cw, 12, 5, at_fich, 0);
}

/* 20 bytes whitened (PN9 seeded 0x1C9), CRC-16 over the whitened bytes, coded into field */
static void csd(uint8_t *out, const uint8_t d20[20], unsigned field) {
    uint8_t w[22];
    unsigned s = 0x1C9u;
    for (unsigned i = 0; i < 20u; i++) {
        unsigned x = 0;
        for (unsigned b = 0; b < 8u; b++)
            x = x << 1 | pn9(&s);
        w[i] = (uint8_t)(d20[i] ^ x);
    }
    uint16_t c = ysft_crc(w, 20);
    w[20] = (uint8_t)(c >> 8);
    w[21] = (uint8_t)c;
    conv_field(out, w, 22, 9, at_csd, field);
}

static void callsign10(const char *s, uint8_t *out) {
    unsigned i = 0;
    for (; i < 10u && s[i]; i++)
        out[i] = (uint8_t)(s[i] >= 'a' && s[i] <= 'z' ? s[i] - 32 : s[i]);
    for (; i < 10u; i++)
        out[i] = ' ';
}

void ysft_frame_header(const ysft_fich *f, const char *dest, const char *src,
                       uint8_t out[YSFT_FRAME]) {
    uint8_t c1[20], c2[20];
    callsign10(dest, c1);
    callsign10(src, c1 + 10);
    for (unsigned i = 0; i < 20u; i++)
        c2[i] = ' ';
    frame_start(f, out);
    csd(out, c1, 0);
    csd(out, c2, 1);
}

void ysft_frame_data_fr(const ysft_fich *f, const uint8_t d1[20], const uint8_t d2[20],
                        uint8_t out[YSFT_FRAME]) {
    frame_start(f, out);
    csd(out, d1, 0);
    csd(out, d2, 1);
}

/* CRC-8 x^8 + x^2 + x + 1, initial 0, over 6 bytes */
static uint8_t crc8(const uint8_t *d) {
    unsigned c = 0;
    for (unsigned i = 0; i < 6u; i++) {
        c ^= d[i];
        for (unsigned b = 0; b < 8u; b++)
            c = (c & 0x80u) ? ((c << 1) ^ 0x07u) & 0xFFu : (c << 1) & 0xFFu;
    }
    return (uint8_t)c;
}

void ysft_frame_sotto(const ysft_fich *f, const uint8_t frames[30], uint8_t out[YSFT_FRAME]) {
    enum { NU = 280, PAY = 720 };
    uint8_t u[35];                      /* five (6 bytes + CRC-8) */
    for (unsigned k = 0; k < 5u; k++) {
        for (unsigned i = 0; i < 6u; i++)
            u[7u * k + i] = frames[6u * k + i];
        u[7u * k + 6u] = crc8(frames + 6u * k);
    }
    frame_start(f, out);
    /* tail-biting: the register starts as the last 6 bits went in */
    unsigned reg = 0;
    for (unsigned i = NU - 6u; i < NU; i++)
        reg = ((reg << 1) | get_bit(u, i)) & 0x7Fu;
    unsigned ci = 0;
    for (unsigned i = 0; i < NU; i++) {
        reg = ((reg << 1) | get_bit(u, i)) & 0x7Fu;
        unsigned g[3] = {ysft_parity(reg & 0133u), ysft_parity(reg & 0171u), ysft_parity(reg & 0165u)};
        unsigned n = (i % 7u) % 2u == 0 ? 3u : 2u;   /* g3 on rows 0, 2, 4, 6 of 7 */
        for (unsigned j = 0; j < n; j++, ci++)
            put_bit(out, PAYLOAD_AT + (ci % 24u) * 30u + ci / 24u, g[j]);
    }
    unsigned s = 0x1FFu;
    for (unsigned i = 0; i < PAY; i++)
        if (pn9(&s))
            out[(PAYLOAD_AT + i) / 8u] ^= (uint8_t)(0x80u >> ((PAYLOAD_AT + i) % 8u));
}

/* ---- a whole sotto transmission ---- */

static unsigned comms_frames(unsigned n) {
    return (n + YSFT_SOTTO_NF - 1u) / YSFT_SOTTO_NF;
}

int ysft_sotto_begin(ysft_sotto *t, const uint8_t *sf, unsigned n, const char *dest,
                     const char *src, uint8_t dgid) {
    if (n == 0)
        return -1;
    t->sf = sf;
    t->n = n;
    unsigned i = 0;
    for (; i < 10u && dest[i]; i++)
        t->dest[i] = dest[i];
    t->dest[i] = 0;
    for (i = 0; i < 10u && src[i]; i++)
        t->src[i] = src[i];
    t->src[i] = 0;
    t->dgid = dgid;
    t->unit = 0;
    t->units = 3u + comms_frames(n);
    t->pos = t->len = 0;
    return 0;
}

unsigned ysft_sotto_bytes(const ysft_sotto *t) {
    return YSFT_PRE_BYTES + (t->units - 1u) * YSFT_FRAME;
}

static void ysft_build(ysft_sotto *t) {
    unsigned u = t->unit, nc = t->units - 3u;
    ysft_fich f;
    ysft_fich_default(&f);
    f.sotto = 1;
    f.dgid = t->dgid;
    t->len = YSFT_FRAME;
    if (u == 0) {
        for (unsigned i = 0; i < YSFT_PRE_BYTES; i++)
            t->buf[i] = YSFT_PREAMBLE;
        t->len = YSFT_PRE_BYTES;
    } else if (u == 1u || u == nc + 2u) {
        f.fi = u == 1u ? YSFT_FI_HEADER : YSFT_FI_TERMINATOR;
        ysft_frame_header(&f, t->dest[0] ? t->dest : "ALL", t->src, t->buf);
    } else {
        unsigned k = u - 2u;
        uint8_t fr[30];
        for (unsigned j = 0; j < YSFT_SOTTO_NF; j++) {
            unsigned s = YSFT_SOTTO_NF * k + j;
            const uint8_t *p = t->sf + YSFT_SOTTO_SF * (s < t->n ? s : t->n - 1u);
            for (unsigned i = 0; i < YSFT_SOTTO_SF; i++)
                fr[YSFT_SOTTO_SF * j + i] = p[i];
        }
        f.fi = YSFT_FI_COMMS;
        f.fn = (uint8_t)(k % 7u);
        ysft_frame_sotto(&f, fr, t->buf);
    }
}

unsigned ysft_sotto_read(ysft_sotto *t, uint8_t *out, unsigned max) {
    unsigned got = 0;
    while (got < max) {
        if (t->pos >= t->len) {
            if (t->unit >= t->units)
                break;
            ysft_build(t);
            t->unit++;
            t->pos = 0;
        }
        out[got++] = t->buf[t->pos++];
    }
    return got;
}
