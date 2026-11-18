/* m17_tx: M17 frames one at a time (m17_tx.h). A port of modem/m17.py's encoder.
   SPDX-License-Identifier: MIT */
#include "m17_tx.h"

#define NBITS 368u

static const uint8_t DECORR[46] = {
    0xd6, 0xb5, 0xe2, 0x30, 0x82, 0xff, 0x84, 0x62, 0xba, 0x4e, 0x96, 0x90,
    0xd8, 0x98, 0xdd, 0x5d, 0x0c, 0xc8, 0x52, 0x43, 0x91, 0x1d, 0xf8, 0x6e,
    0x68, 0x2f, 0x35, 0xda, 0x14, 0xea, 0xcd, 0x76, 0x19, 0x8d, 0xd5, 0x80,
    0xd1, 0x33, 0x87, 0x13, 0x57, 0x18, 0x2d, 0x29, 0x78, 0xc3};
static const uint16_t GOLAY[12] = {0x8eb, 0x93e, 0xa97, 0xdc6, 0x367, 0x6cd,
                                   0xd99, 0x3da, 0x7b4, 0xf68, 0x63b, 0xc75};
static const char CALL[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/.";

/* puncture patterns as bit masks, bit i = position i of the period */
typedef struct {
    uint32_t lo, hi;                    /* up to 64 positions */
    unsigned len;
} punct;

/* P1: [1,1,0,1] x 15 + [1] (61); P2: 11 ones, a zero (12); P3: 7 ones, a zero (8) */
static const punct P1 = {0xBBBBBBBBu, 0x1BBBBBBBu, 61u};
static const punct P2 = {0x7FFu, 0, 12u};
static const punct P3 = {0x7Fu, 0, 8u};

static int keep(const punct *p, unsigned i) {
    unsigned k = i % p->len;
    return k < 32u ? (int)((p->lo >> k) & 1u) : (int)((p->hi >> (k - 32u)) & 1u);
}

static unsigned parity(unsigned v) {
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

uint16_t m17t_crc(const uint8_t *d, unsigned n) {
    uint16_t crc = 0xFFFFu;
    for (unsigned i = 0; i < n; i++) {
        crc ^= (uint16_t)(d[i] << 8);
        for (unsigned b = 0; b < 8u; b++)
            crc = (uint16_t)((crc & 0x8000u) ? ((unsigned)crc << 1) ^ 0x5935u : (unsigned)crc << 1);
    }
    return crc;
}

int m17t_callsign(const char *s, uint8_t out[6]) {
    unsigned n = 0;
    while (s[n])
        n++;
    for (unsigned i = 0; i < 6u; i++)
        out[i] = 0xFFu;
    if (n == 0)
        return 0;
    char u[10];
    if (n > 9u)
        return -1;
    for (unsigned i = 0; i < n; i++)
        u[i] = (char)(s[i] >= 'a' && s[i] <= 'z' ? s[i] - 32 : s[i]);
    if ((n == 3u && u[0] == 'A' && u[1] == 'L' && u[2] == 'L') ||
        (n == 4u && u[0] == '@' && u[1] == 'A' && u[2] == 'L' && u[3] == 'L'))
        return 0;
    uint64_t v = 0;
    for (unsigned i = n; i-- > 0;) {
        unsigned c = 0;
        while (CALL[c] && CALL[c] != u[i])
            c++;
        if (!CALL[c])
            return -1;
        v = v * 40u + c;
    }
    for (unsigned i = 0; i < 6u; i++)
        out[i] = (uint8_t)(v >> (8u * (5u - i)));
    return 0;
}

void m17t_lsf(uint8_t l30[M17T_LSF], const char *src, const char *dst, uint16_t type,
              const uint8_t *meta) {
    m17t_callsign(dst, l30);
    m17t_callsign(src, l30 + 6);
    l30[12] = (uint8_t)(type >> 8);
    l30[13] = (uint8_t)type;
    for (unsigned i = 0; i < 14u; i++)
        l30[14 + i] = meta ? meta[i] : 0;
    uint16_t c = m17t_crc(l30, 28);
    l30[28] = (uint8_t)(c >> 8);
    l30[29] = (uint8_t)c;
}

void m17t_fill(uint8_t out[M17T_FRAME], uint8_t b) {
    for (unsigned i = 0; i < M17T_FRAME; i++)
        out[i] = b;
}

void m17t_eot(uint8_t out[M17T_FRAME]) {
    for (unsigned i = 0; i < M17T_FRAME; i += 2u) {
        out[i] = 0x55u;
        out[i + 1u] = 0x5Du;
    }
}

/* the 368 bits go straight to their interleaved places in out + 2 */
typedef struct {
    uint8_t *f;                         /* the frame; bits from byte 2 */
    unsigned k;                         /* bits placed */
} placer;

static void place(placer *p, unsigned bit) {
    if (p->k >= NBITS)
        return;
    uint32_t k = p->k++;
    uint32_t j = (45u * k + 92u * k * k) % NBITS;
    if (bit)
        p->f[2u + j / 8u] |= (uint8_t)(0x80u >> (j % 8u));
}

static void start(uint8_t out[M17T_FRAME], uint8_t s0, uint8_t s1, placer *p) {
    for (unsigned i = 0; i < M17T_FRAME; i++)
        out[i] = 0;
    out[0] = s0;
    out[1] = s1;
    p->f = out;
    p->k = 0;
}

static void finish(uint8_t out[M17T_FRAME]) {
    for (unsigned i = 0; i < 46u; i++)
        out[2u + i] ^= DECORR[i];
}

/* nbits of data (MSB first from d), then 4 flush bits, coded and punctured into p */
typedef struct {
    unsigned m, i;                      /* the shift register, coded bits so far */
} conv;

static void conv_bit(conv *c, placer *p, const punct *pp, unsigned b) {
    c->m = ((c->m << 1) | b) & 0x1Fu;
    if (keep(pp, c->i++))
        place(p, parity(c->m & 0x19u));
    if (keep(pp, c->i++))
        place(p, parity(c->m & 0x17u));
}

static void conv_bytes(placer *p, const punct *pp, const uint8_t *d, unsigned nbytes) {
    conv c = {0, 0};
    for (unsigned i = 0; i < 8u * nbytes; i++)
        conv_bit(&c, p, pp, (d[i / 8u] >> (7u - i % 8u)) & 1u);
    for (unsigned i = 0; i < 4u; i++)
        conv_bit(&c, p, pp, 0);
}

void m17t_frame_lsf(const uint8_t l30[M17T_LSF], uint8_t out[M17T_FRAME]) {
    placer p;
    start(out, 0x55u, 0xF7u, &p);
    conv_bytes(&p, &P1, l30, M17T_LSF);
    finish(out);
}

static uint32_t golay(unsigned d12) {
    uint32_t c = 0;
    for (unsigned i = 0; i < 12u; i++)
        if ((d12 >> i) & 1u)
            c ^= GOLAY[i];
    return (uint32_t)d12 << 12 | c;
}

void m17t_frame_stream(const uint8_t l30[M17T_LSF], uint16_t fn, const uint8_t pay[16], int last,
                       uint8_t out[M17T_FRAME]) {
    placer p;
    start(out, 0xFFu, 0x5Du, &p);
    unsigned idx = (fn & 0x7FFFu) % 6u;
    const uint8_t *c = l30 + 5u * idx;
    unsigned blk[4] = {(unsigned)c[0] << 4 | c[1] >> 4, (c[1] & 0xFu) << 8 | c[2],
                       (unsigned)c[3] << 4 | c[4] >> 4, (c[4] & 0xFu) << 8 | idx << 5};
    for (unsigned b = 0; b < 4u; b++) {
        uint32_t w = golay(blk[b]);
        for (unsigned i = 0; i < 24u; i++)
            place(&p, (w >> (23u - i)) & 1u);
    }
    uint8_t d[18];
    uint16_t h = (uint16_t)((fn & 0x7FFFu) | (last ? 0x8000u : 0u));
    d[0] = (uint8_t)(h >> 8);
    d[1] = (uint8_t)h;
    for (unsigned i = 0; i < 16u; i++)
        d[2 + i] = pay[i];
    conv_bytes(&p, &P2, d, sizeof d);
    finish(out);
}

void m17t_frame_packet(const uint8_t *chunk, unsigned n, int eof, unsigned counter,
                       uint8_t out[M17T_FRAME]) {
    placer p;
    uint8_t d[26];
    for (unsigned i = 0; i < 25u; i++)
        d[i] = i < n ? chunk[i] : 0;
    d[25] = (uint8_t)((eof ? 0x80u : 0u) | (counter & 0x1Fu) << 2);
    start(out, 0x75u, 0xFFu, &p);
    conv_bytes(&p, &P3, d, sizeof d);
    finish(out);
}

void m17t_frame_bert(uint16_t *prbs, uint8_t out[M17T_FRAME]) {
    placer p;
    conv c = {0, 0};
    start(out, 0xDFu, 0x55u, &p);
    unsigned s = *prbs;
    for (unsigned i = 0; i < 197u; i++) {
        unsigned b = ((s >> 8) ^ (s >> 4)) & 1u;
        s = ((s << 1) | b) & 0x1FFu;
        conv_bit(&c, &p, &P2, b);
    }
    for (unsigned i = 0; i < 4u; i++)
        conv_bit(&c, &p, &P2, 0);
    *prbs = (uint16_t)s;
    finish(out);
}

void m17t_sotto_payload(const uint8_t a[6], const uint8_t b[6], int repeat, uint8_t out[16]) {
    for (unsigned i = 0; i < 6u; i++) {
        out[i] = a[i];
        out[6u + i] = b[i];
    }
    uint16_t c = m17t_crc(out, 12);
    out[12] = (uint8_t)(c >> 8);
    out[13] = (uint8_t)c;
    out[14] = (uint8_t)(M17T_SOTTO_RATE_1200 | (repeat ? M17T_SOTTO_REPEAT : 0u));
    out[15] = 0;
}

/* frames: preamble, LSF, the stream frames, EOT */
static unsigned stream_frames(unsigned n, unsigned repeat) {
    return repeat ? n : (n + 1u) / 2u;
}

int m17t_sotto_begin(m17t_sotto *t, const uint8_t *sf, unsigned n, const char *src,
                     const char *dst, int repeat) {
    static const uint8_t META[14] = {'S', 'O', 'T', 'T', 'O', ' ', '1', '2', '0', '0', 0, 0, 0, 0};
    uint8_t tmp[6];
    if (n == 0 || m17t_callsign(src, tmp) || m17t_callsign(dst, tmp))
        return -1;
    t->sf = sf;
    t->n = n;
    t->repeat = repeat ? 1u : 0u;
    m17t_lsf(t->l30, src, dst, M17T_TYPE_STREAM_DATA, META);
    t->frame = 0;
    t->frames = 3u + stream_frames(n, t->repeat);
    t->pos = M17T_FRAME;
    return 0;
}

unsigned m17t_sotto_bytes(const m17t_sotto *t) {
    return t->frames * M17T_FRAME;
}

static const uint8_t *sf_at(const m17t_sotto *t, unsigned k) {
    return t->sf + M17T_SOTTO_SF * (k < t->n ? k : t->n - 1u);
}

static void build(m17t_sotto *t) {
    unsigned f = t->frame, ns = t->frames - 3u;
    if (f == 0)
        m17t_fill(t->buf, M17T_PRE_LSF);
    else if (f == 1u)
        m17t_frame_lsf(t->l30, t->buf);
    else if (f - 2u < ns) {
        unsigned k = f - 2u;
        uint8_t pay[16];
        if (t->repeat)
            m17t_sotto_payload(sf_at(t, k), sf_at(t, k ? k - 1u : 0), 1, pay);
        else
            m17t_sotto_payload(sf_at(t, 2u * k), sf_at(t, 2u * k + 1u), 0, pay);
        m17t_frame_stream(t->l30, (uint16_t)k, pay, k == ns - 1u, t->buf);
    } else
        m17t_eot(t->buf);
}

unsigned m17t_sotto_read(m17t_sotto *t, uint8_t *out, unsigned max) {
    unsigned got = 0;
    while (got < max) {
        if (t->pos >= M17T_FRAME) {
            if (t->frame >= t->frames)
                break;
            build(t);
            t->frame++;
            t->pos = 0;
        }
        out[got++] = t->buf[t->pos++];
    }
    return got;
}
