/* dsr_tx: see dsr_tx.h. SPDX-License-Identifier: MIT */
#include "dsr_tx.h"

/* no libc (RAM modules build -nostdlib): these stand in for memset / memcpy */
static void dsrt_set(uint8_t *d, uint8_t v, unsigned n) {
    for (unsigned i = 0; i < n; i++)
        d[i] = v;
}

static void dsrt_cpy(uint8_t *d, const uint8_t *s, unsigned n) {
    for (unsigned i = 0; i < n; i++)
        d[i] = s[i];
}

/* no writable statics and no tables of pointers: a RAM module (-fropi) may #include this file */
static const uint8_t DSRT_POLYS[6] = {0105, 0127, 0157, 0167, 0113, 0173};
static const uint8_t DSRT_SYNC40[4] = {0xFF, 0x75, 0xF5, 0xD5};
static const uint8_t DSRT_SYNC120[6] = {0x7F, 0xF5, 0x7D, 0x77, 0x5D, 0x5F};
static const char DSRT_CHARS[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/.";

static uint16_t dsrt_crc(const uint8_t *d, unsigned n) {
    uint16_t c = 0xFFFF;
    for (unsigned i = 0; i < n; i++) {
        c ^= (uint16_t)(d[i] << 8);
        for (int k = 0; k < 8; k++)
            c = (c & 0x8000u) ? (uint16_t)((c << 1) ^ 0x5935u) : (uint16_t)(c << 1);
    }
    return c;
}

static int dsrt_callsign(const char *s, uint8_t out[6]) {
    unsigned n = 0;
    uint64_t v = 0;
    while (s[n])
        n++;
    if (n == 0 || n > 9) {
        dsrt_set(out, 0xFF, 6);
        return n ? -1 : 0;
    }
    for (unsigned i = n; i-- > 0;) {
        char c = s[i];
        if (c >= 'a' && c <= 'z')
            c = (char)(c - 32);
        unsigned k = 0;
        while (DSRT_CHARS[k] && DSRT_CHARS[k] != c)
            k++;
        if (!DSRT_CHARS[k]) {
            dsrt_set(out, 0xFF, 6);
            return -1;
        }
        v = v * 40u + k;
    }
    for (int i = 5; i >= 0; i--) {
        out[i] = (uint8_t)v;
        v >>= 8;
    }
    return 0;
}

static unsigned dsrt_par(unsigned x) {
    x ^= x >> 4;
    x ^= x >> 2;
    x ^= x >> 1;
    return x & 1u;
}

int dsrt_init(dsrt *t, unsigned fmt, const char *call) {
    if (fmt == DSRT_40) {
        t->nsf = 1;
        t->rate = 4;
        t->nsync = 4;
        t->frame = 48;
        t->a = 117;
    } else if (fmt == DSRT_120) {
        t->nsf = 3;
        t->rate = 6;
        t->nsync = 6;
        t->frame = 144;
        t->a = 205;
    } else {
        return -1;
    }
    t->fmt = (uint8_t)fmt;
    t->ninfo = (uint16_t)(8u * (3u + DSRT_SF * t->nsf + 2u));
    t->ncoded = (uint16_t)(t->ninfo * t->rate);
    t->counter = 0;
    return dsrt_callsign(call, t->call);
}

void dsrt_frame(dsrt *t, unsigned type, const uint8_t *sf, uint8_t *out) {
    uint8_t info[3u + 3u * DSRT_SF + 2u];
    unsigned nb = 3u + DSRT_SF * t->nsf, c = t->counter;
    unsigned ch = c & 3u;
    unsigned meta = (unsigned)(((uint32_t)t->call[ch * 3u / 2u] << 8 | t->call[ch * 3u / 2u + 1u]) >>
                               ((ch & 1u) ? 0 : 4)) & 0xFFFu;
    info[0] = (uint8_t)((type & 0xFu) << 4 | (c >> 4 & 0xFu));
    info[1] = (uint8_t)((c & 0xFu) << 4 | meta >> 8);
    info[2] = (uint8_t)meta;
    for (unsigned i = 0; i < DSRT_SF * t->nsf; i++)
        info[3u + i] = sf ? sf[i] : 0;
    uint16_t crc = dsrt_crc(info, nb);
    info[nb] = (uint8_t)(crc >> 8);
    info[nb + 1u] = (uint8_t)crc;

    const uint8_t *sync = t->fmt == DSRT_40 ? DSRT_SYNC40 : DSRT_SYNC120;
    dsrt_cpy(out, sync, t->nsync);
    uint8_t *air = out + t->nsync;
    dsrt_set(air, 0, t->ncoded / 8u);

    /* tail-biting: the register starts as the last six info bits */
    unsigned n = t->ninfo, s = 0;
    for (unsigned i = n - 6u; i < n; i++)
        s = ((s << 1) | (info[i >> 3] >> (7u - (i & 7u)) & 1u)) & 63u;
    unsigned pos = 0, N = t->ncoded;
    for (unsigned i = 0; i < n; i++) {
        unsigned m = ((s << 1) | (info[i >> 3] >> (7u - (i & 7u)) & 1u)) & 0x7Fu;
        for (unsigned r = 0; r < t->rate; r++) {
            if (dsrt_par(m & DSRT_POLYS[r]))
                air[pos >> 3] |= (uint8_t)(0x80u >> (pos & 7u));
            pos += t->a;
            if (pos >= N)
                pos -= N;
        }
        s = m & 63u;
    }
    /* PN9 whitening, x^9 + x^5 + 1, seed 0x1FF, LSB out first */
    unsigned p = 0x1FFu;
    for (unsigned i = 0; i < N; i++) {
        if (p & 1u)
            air[i >> 3] ^= (uint8_t)(0x80u >> (i & 7u));
        unsigned b = ((p >> 8) ^ (p >> 4)) & 1u;
        p = ((p << 1) | b) & 0x1FFu;
    }
    t->counter = (uint8_t)(c + 1u);
}

int dsrt_msg_begin(dsrt_msg *m, unsigned fmt, const uint8_t *sf, unsigned n, const char *call) {
    if (n == 0 || dsrt_init(&m->t, fmt, call))
        return -1;
    m->sf = sf;
    m->n = n;
    m->frames = (n + m->t.nsf - 1u) / m->t.nsf;
    m->frame = 0;
    dsrt_set(m->buf, 0x77, DSRT_PRE);
    m->len = DSRT_PRE;
    m->pos = 0;
    m->stage = 0;
    return 0;
}

unsigned dsrt_msg_bytes(const dsrt_msg *m) {
    return DSRT_PRE + (m->frames + 1u) * m->t.frame + DSRT_POST;
}

static void dsrt_msg_next(dsrt_msg *m) {
    if (m->stage == 0 || (m->stage == 1 && m->frame <= m->frames)) {
        m->stage = 1;
        if (m->frame < m->frames) {
            uint8_t sfs[3u * DSRT_SF];
            for (unsigned j = 0; j < m->t.nsf; j++) {
                unsigned k = m->frame * m->t.nsf + j;
                dsrt_cpy(sfs + DSRT_SF * j, m->sf + DSRT_SF * (k < m->n ? k : m->n - 1u), DSRT_SF);
            }
            dsrt_frame(&m->t, DSRT_T_SOTTO1200, sfs, m->buf);
        } else {
            dsrt_frame(&m->t, DSRT_T_END, 0, m->buf);
        }
        m->frame++;
        m->len = m->t.frame;
    } else if (m->stage == 1) {
        m->stage = 2;
        m->buf[0] = m->buf[1] = 0x77;
        m->len = DSRT_POST;
    } else {
        m->stage = 3;
        m->len = 0;
    }
    m->pos = 0;
}

unsigned dsrt_msg_read(dsrt_msg *m, uint8_t *out, unsigned max) {
    unsigned got = 0;
    while (got < max && m->stage < 3) {
        if (m->pos >= m->len) {
            dsrt_msg_next(m);
            continue;
        }
        out[got++] = m->buf[m->pos++];
    }
    return got;
}
