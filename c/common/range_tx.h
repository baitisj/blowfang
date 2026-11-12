/* PHY R, the K6 range mode's sender (docs/k6-range.md; modem/k6r.py has the spec and a
 * reference; the K6's receiver is c/k6/mod/rangerx.c), in portable C for radios that send it
 * themselves: the UV-380 first. Packets (K6 pager payloads, whitened, from any pt_source:
 * pager_tx.h) are coded with PHY G's K=7 rate-1/2 code (133, 171; 8 zero tail bits: 2064 coded
 * bits), interleaved in 24 rows of 86 and precoded, and come out as tone bits for ffsk_tx.h at
 * 1200 baud with mark 600 Hz (tone bit 1) and space 1200 Hz (tone bit 0):
 *
 *     rg_air a;  ffsk_tx m;
 *     rg_air_defaults(&a, pt_message_source, &msg);
 *     ffsk_init_src(&m, rg_air_next, &a, fs, RG_BAUD, RG_MARK_HZ, RG_SPACE_HZ, level);
 *
 * A burst in line bits: lead_bits of lead (tone bits 1 1 1 1 1 0 0 0 over and over, ending on
 * the pattern's end), then for each packet the sync word 1ACFFC1D (MSB first) and the packet's
 * 2064 coded bits (line bit i = coded bit 86 (i % 24) + i / 24), with gap_bits of lead before
 * each packet after the first; tail_bits of lead at the end. The tone bit sent is the line bit
 * XOR the line bit before it (the first: 1).
 *
 * No malloc, no OS, no floating point; one 128-byte packet and its 258 coded bytes at a time.
 * SPDX-License-Identifier: MIT */
#ifndef RANGE_TX_H
#define RANGE_TX_H

#include <stdint.h>

#include "pager_tx.h"

#define RG_BAUD 1200
#define RG_MARK_HZ 600
#define RG_SPACE_HZ 1200
#define RG_PAYLOAD 128
#define RG_CODED 2064
#define RG_SYNC 0x1ACFFC1Du

typedef struct {
    unsigned lead_bits, gap_bits, tail_bits;
    pt_source source;
    void *ctx;
    /* state */
    uint8_t buf[RG_PAYLOAD];
    uint8_t coded[RG_CODED / 8];
    unsigned pkt, phase, pos, row, col;
    int prev;                   /* the last line bit sent, -1 before the first */
} rg_air;

/* As modem.k6r's defaults: lead 600 bits (0.5 s), gaps 128, tail 32. */
void rg_air_defaults(rg_air *a, pt_source source, void *ctx);
void rg_air_start(rg_air *a);

/* The next line bit, or -1 when the burst is over. */
int rg_air_line_bit(rg_air *a);
/* The next tone bit (1: mark, 600 Hz), or -1: the bit source for ffsk_init_src. */
int rg_air_next(void *a);

/* Line bits in the whole burst for n packets (air time = bits / RG_BAUD). */
unsigned long rg_air_bits(const rg_air *a, unsigned n);

/* PHY G's code: 128 bytes -> 2064 coded bits, c[2n] (133) then c[2n+1] (171), MSB first in out. */
void rg_encode(const uint8_t *payload, uint8_t *out);

#endif
