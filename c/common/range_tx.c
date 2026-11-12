/* PHY R sender (range_tx.h). SPDX-License-Identifier: MIT */
#include "range_tx.h"

#include <string.h>

#define ROWS 24u
#define COLS 86u
#define LEAD_LEN 8u
static const uint8_t LEAD_TONES[LEAD_LEN] = {1, 1, 1, 1, 1, 0, 0, 0};

enum { R_LEAD, R_SYNC, R_DATA, R_GAP, R_TAIL, R_END };

static unsigned parity(unsigned v) {
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

void rg_encode(const uint8_t *payload, uint8_t *out) {
    unsigned reg = 0, n, j = 0;
    memset(out, 0, RG_CODED / 8);
    for (n = 0; n < RG_CODED / 2; n++) {
        unsigned bit = n < RG_PAYLOAD * 8 ? (payload[n >> 3] >> (7 - (n & 7))) & 1u : 0u;
        reg = ((reg << 1) | bit) & 127u;
        if (parity(reg & 0133u))
            out[j >> 3] |= (uint8_t)(0x80u >> (j & 7));
        j++;
        if (parity(reg & 0171u))
            out[j >> 3] |= (uint8_t)(0x80u >> (j & 7));
        j++;
    }
}

void rg_air_defaults(rg_air *a, pt_source source, void *ctx) {
    memset(a, 0, sizeof *a);
    a->lead_bits = 600;
    a->gap_bits = 128;
    a->tail_bits = 32;
    a->source = source;
    a->ctx = ctx;
}

void rg_air_start(rg_air *a) {
    a->pkt = 0;
    a->phase = R_LEAD;
    a->pos = 0;
    a->prev = -1;
}

/* the tone bit at place pos of a lead of n bits (it ends on the pattern's end) */
static unsigned lead_tone(unsigned pos, unsigned n) {
    return LEAD_TONES[(pos + LEAD_LEN - n % LEAD_LEN) % LEAD_LEN];
}

/* a lead's line bit from its tone bit (the first lead's first line bit: the tone bit itself) */
static int from_tone(const rg_air *a, unsigned tone) {
    return (int)((a->prev < 0 ? 0u : (unsigned)a->prev) ^ tone);
}

static int next_packet(rg_air *a) {
    unsigned len = a->source(a->ctx, a->pkt, a->buf);
    if (len != RG_PAYLOAD)
        return 0;
    rg_encode(a->buf, a->coded);
    return 1;
}

int rg_air_line_bit(rg_air *a) {
    for (;;) {
        switch (a->phase) {
        case R_LEAD:
        case R_GAP:
        case R_TAIL: {
            unsigned n = a->phase == R_LEAD ? a->lead_bits : a->phase == R_GAP ? a->gap_bits : a->tail_bits;
            if (a->pos < n)
                return from_tone(a, lead_tone(a->pos++, n));
            if (a->phase == R_TAIL) {
                a->phase = R_END;
                break;
            }
            a->pos = 0;
            a->phase = next_packet(a) ? R_SYNC : R_TAIL;
            if (a->phase == R_TAIL && a->pkt == 0)
                a->phase = R_END;      /* nothing to send: no burst past the lead */
            break;
        }
        case R_SYNC:
            if (a->pos < 32)
                return (int)((RG_SYNC >> (31 - a->pos++)) & 1u);
            a->phase = R_DATA;
            a->pos = 0;
            a->row = 0;
            a->col = 0;
            break;
        case R_DATA:
            if (a->pos < RG_CODED) {
                unsigned j = a->row * COLS + a->col;
                a->pos++;
                if (++a->row == ROWS) {
                    a->row = 0;
                    a->col++;
                }
                return (int)((a->coded[j >> 3] >> (7 - (j & 7))) & 1u);
            }
            a->pkt++;
            a->pos = 0;
            a->phase = R_GAP;
            {
                /* look ahead: no next packet means the tail, not a gap */
                uint8_t tmp[RG_PAYLOAD];
                if (a->source(a->ctx, a->pkt, tmp) != RG_PAYLOAD)
                    a->phase = R_TAIL;
            }
            break;
        default:
            return -1;
        }
    }
}

int rg_air_next(void *ctx) {
    rg_air *a = (rg_air *)ctx;
    int l = rg_air_line_bit(a), t;
    if (l < 0)
        return -1;
    t = a->prev < 0 ? 1 : (l ^ a->prev);
    a->prev = l;
    return t;
}

unsigned long rg_air_bits(const rg_air *a, unsigned n) {
    if (!n)
        return a->lead_bits;
    return a->lead_bits + a->tail_bits + (unsigned long)n * (32 + RG_CODED) +
           (unsigned long)(n - 1) * a->gap_bits;
}
