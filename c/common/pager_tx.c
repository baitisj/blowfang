/* K6 voice-pager packets, transmit side (pager_tx.h). Mirrors modem/pager.py Profile.pack and
 * Air.burst byte for byte; tests/test_uv380_pager.py checks that.
 * SPDX-License-Identifier: MIT */
#include "pager_tx.h"

#include <string.h>

static const uint8_t SYNC[4] = {0x07, 0x6B, 0x3E, 0x36};

unsigned pt_crc16(const uint8_t *p, unsigned n) {
    unsigned crc = 0xFFFF;
    while (n--) {
        crc ^= (unsigned)*p++ << 8;
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
    }
    return crc;
}

uint32_t pt_crc32(const uint8_t *p, unsigned n) {
    uint32_t crc = 0xFFFFFFFFu;
    while (n--) {
        crc ^= *p++;
        for (int b = 0; b < 8; b++)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1)));
    }
    return ~crc;
}

void pt_whiten(uint8_t *p, unsigned n) {
    unsigned s = 0x1FF;
    for (unsigned i = 0; i < n; i++) {
        p[i] ^= (uint8_t)s;
        for (int b = 0; b < 8; b++)
            s = (s >> 1) | (((s ^ (s >> 5)) & 1) << 8);
    }
}

uint8_t pt_msg_id(const uint8_t *frames, unsigned total) {
    return (uint8_t)pt_crc32(frames, total * PT_FRAME_BYTES);
}

static unsigned depth_code(unsigned d) {
    return d == 8 ? 0 : d == 4 ? 1 : d == 2 ? 2 : 3;
}

static unsigned group_frames(const pt_profile *pr) {
    return (unsigned)pr->slots * pr->depth;
}

/* The short last group: its first seq (*first) and packet count; 0 when the layout is plain. */
static unsigned last_group(const pt_profile *pr, unsigned total, unsigned *first) {
    if (!pr->short_last)
        return 0;
    unsigned gf = group_frames(pr), g = total / gf, rem = total % gf;
    unsigned m = (rem + pr->slots - 1) / pr->slots;
    if (m == 0 || m >= pr->depth)
        return 0;
    *first = g * pr->depth;
    return m;
}

unsigned pt_n_packets(const pt_profile *pr, unsigned total) {
    unsigned first = 0, m = last_group(pr, total, &first);
    unsigned n = m ? first + m : (total + group_frames(pr) - 1) / group_frames(pr) * pr->depth;
    return (total > 0xFFFF || n > 0x2000) ? 0 : n;
}

unsigned pt_air_packets(const pt_profile *pr, unsigned total) {
    unsigned n = pt_n_packets(pr, total);
    return n + (pr->parity ? (n + pr->depth - 1) / pr->depth : 0);
}

/* frame index carried in slot s of data packet seq */
static unsigned frame_of(const pt_profile *pr, unsigned total, unsigned seq, unsigned s) {
    unsigned g = seq / pr->depth, k = seq % pr->depth, first = 0;
    unsigned m = last_group(pr, total, &first);
    if (m && seq >= first)
        return g * group_frames(pr) + s * m + (seq - first);
    return g * group_frames(pr) + s * pr->depth + k;
}

static void header(const pt_profile *pr, uint8_t *p, uint8_t magic, uint8_t msg_id, unsigned field,
                   unsigned total) {
    unsigned hs = depth_code(pr->depth) << 14 | field;
    p[0] = magic;
    p[1] = msg_id;
    p[2] = (uint8_t)(hs >> 8);
    p[3] = (uint8_t)hs;
    p[4] = (uint8_t)(total >> 8);
    p[5] = (uint8_t)total;
}

/* data packet seq, unwhitened and without its CRC: header, frames, zero padding */
static void body(const pt_profile *pr, uint8_t msg_id, const uint8_t *frames, unsigned total,
                 unsigned seq, uint8_t *p) {
    unsigned first = 0, m = last_group(pr, total, &first);
    unsigned is_short = m && seq >= first;
    memset(p, 0, pr->payload);
    header(pr, p, pr->magic, msg_id, is_short << 13 | seq, total);
    for (unsigned s = 0; s < pr->slots; s++) {
        unsigned f = frame_of(pr, total, seq, s);
        if (f < total)
            memcpy(p + PT_HDR + s * PT_FRAME_BYTES, frames + f * PT_FRAME_BYTES, PT_FRAME_BYTES);
    }
}

static unsigned seal(const pt_profile *pr, uint8_t *p) {
    unsigned n = pr->payload, crc = pt_crc16(p, n - 2);
    p[n - 2] = (uint8_t)(crc >> 8);
    p[n - 1] = (uint8_t)crc;
    pt_whiten(p, n);
    return n;
}

unsigned pt_packet(const pt_profile *pr, uint8_t msg_id, const uint8_t *frames, unsigned total,
                   unsigned i, uint8_t *out) {
    unsigned n = pt_n_packets(pr, total);
    if (n == 0 || i >= pt_air_packets(pr, total) || pr->payload > PT_MAX_PAYLOAD)
        return 0;
    if (!pr->parity) {
        body(pr, msg_id, frames, total, i, out);
        return seal(pr, out);
    }
    unsigned per = pr->depth + 1u, g = i / per, r = i % per;
    unsigned cnt = n - g * pr->depth < pr->depth ? n - g * pr->depth : pr->depth;
    if (r < cnt) {
        body(pr, msg_id, frames, total, g * pr->depth + r, out);
        return seal(pr, out);
    }
    /* the parity packet of group g: XOR of bytes 6 .. payload-3 of its data packets */
    uint8_t tmp[PT_MAX_PAYLOAD];
    unsigned first = 0, m = last_group(pr, total, &first);
    unsigned last_seq = g * pr->depth + cnt - 1, is_short = m && last_seq >= first;
    memset(out, 0, pr->payload);
    for (unsigned k = 0; k < cnt; k++) {
        body(pr, msg_id, frames, total, g * pr->depth + k, tmp);
        for (unsigned b = PT_HDR; b < pr->payload - 2u; b++)
            out[b] ^= tmp[b];
    }
    header(pr, out, (uint8_t)(pr->magic | PT_PARITY), msg_id, is_short << 13 | g, total);
    return seal(pr, out);
}

/* ---- air framing ------------------------------------------------------------------------- */

static unsigned bytes_for_ms(unsigned ms, unsigned baud) {
    return (ms * baud + 7999u) / 8000u;              /* ceil(ms / 1000 * baud / 8) */
}

void pt_air_defaults(pt_air *a, unsigned payload, unsigned baud, pt_source source, void *ctx) {
    memset(a, 0, sizeof *a);
    a->lead_bytes = bytes_for_ms(150, baud);
    a->first_pre = 16;
    a->later_pre = payload > 64 ? (baud >= 1800 ? 12 : 24) : 16;
    a->extra_second = bytes_for_ms(50, baud);
    unsigned t = bytes_for_ms(300, baud);
    a->tail_bytes = t > 2 ? t : 2;
    a->source = source;
    a->ctx = ctx;
}

enum { A_LEAD, A_PRE, A_SYNC, A_PAYLOAD, A_TAIL, A_END };

void pt_air_start(pt_air *a) {
    a->pkt = 0;
    a->phase = A_LEAD;
    a->pos = 0;
    a->bit = 8;
}

static unsigned pre_bytes(const pt_air *a) {
    if (a->pkt == 0)
        return a->first_pre;
    return a->later_pre + (a->pkt == 1 ? a->extra_second : 0);
}

/* the next byte on air, or -1 at the end */
static int next_byte(pt_air *a) {
    for (;;) {
        switch (a->phase) {
        case A_LEAD:
            if (a->pos < a->lead_bytes) {
                a->pos++;
                return 0x55;
            }
            a->len = a->source(a->ctx, a->pkt, a->buf);
            a->phase = a->len ? A_PRE : A_TAIL;
            a->pos = 0;
            break;
        case A_PRE:
            if (a->pos < pre_bytes(a)) {
                a->pos++;
                return 0x55;
            }
            a->phase = A_SYNC;
            a->pos = 0;
            break;
        case A_SYNC:
            if (a->pos < 4)
                return SYNC[a->pos++];
            a->phase = A_PAYLOAD;
            a->pos = 0;
            break;
        case A_PAYLOAD:
            if (a->pos < a->len)
                return a->buf[a->pos++];
            a->pkt++;
            a->len = a->source(a->ctx, a->pkt, a->buf);
            a->phase = a->len ? A_PRE : A_TAIL;
            a->pos = 0;
            break;
        case A_TAIL:
            if (a->pos < a->tail_bytes) {
                a->pos++;
                return 0x55;
            }
            a->phase = A_END;
            break;
        default:
            return -1;
        }
    }
}

int pt_air_bit(pt_air *a) {
    if (a->bit >= 8) {
        int b = next_byte(a);
        if (b < 0)
            return -1;
        a->byte = (unsigned)b;
        a->bit = 0;
    }
    return (int)((a->byte >> (7 - a->bit++)) & 1);
}

unsigned long pt_air_bits(const pt_air *a, unsigned n, unsigned payload) {
    unsigned long bytes = a->lead_bytes + a->tail_bytes + (unsigned long)n * (4 + payload);
    if (n > 0)
        bytes += a->first_pre;
    if (n > 1)
        bytes += (unsigned long)(n - 1) * a->later_pre + a->extra_second;
    return bytes * 8;
}

unsigned pt_message_source(void *ctx, unsigned i, uint8_t *out) {
    const pt_message *m = (const pt_message *)ctx;
    unsigned n = pt_air_packets(m->pr, m->total);
    if (n == 0 || i >= n * m->passes)
        return 0;
    return pt_packet(m->pr, m->msg_id, m->frames, m->total, i % n, out);
}
