/* c6phy_fsk.h: plain 2FSK at 2400 or 1200 baud out of an HR_C6000's physical layer mode, for the
   UV-380 (firmware/uv380-openrtx/mod/c6phytx.c). The reference is modem/c6phy.py; a test checks
   the two agree byte for byte (tests/test_uv380_c6phy.py).

   In the physical layer mode (0x10 = 0x66) the chip sends TX RAM as 4FSK at 4800 symbols a
   second, 36 bytes a 30 ms slot, MSB first, a dibit a symbol (DMR's mapping: 01 +3, 00 +1,
   10 -1, 11 -3; outer deviation +-1944 Hz), RRC shaped, with no framing of its own
   (RadioFoundry docs/spec/modem-hrc6000.md 9.1-9.2, MIT; RadioFoundryC4FM sends whole C4FM calls
   this way on an HR_C7000: docs/uv380-c4fm-foundry.md). Each of our bits as two equal outer
   symbols is plain FSK at 2400 baud, +-1944 Hz: bit 1 = +3 +3 = nibble 0x5, bit 0 = -3 -3 =
   nibble 0xF, nine data bytes a slot. Four symbols a bit is 1200 baud (a byte 0x55 or 0xFF a
   bit, 4.5 data bytes a slot). The K6's chip FSK read 2400 baud perfectly from a flat FM
   transmitter (docs/k6-fsk24.md run 3, 234/234), and two-point modulation is flat to DC.

   What goes out is c/k6/mod/fsk24rx.c's test stream (modem/fsk24.py, pager.Air framing): a lead of
   0x55 bytes, then per packet 0x55 x CF_PRE (and CF_EXTRA more before the second, the K6 re-arms
   late after the first), the sync 07 6B 3E 36 and the 64-byte test packet (0xF2, variant,
   sequence, PN9, CRC-16/CCITT-FALSE), and 0x55 after the last packet for as long as slots are
   asked for (the bit clock never sees a gap).

   Polarity: which way the UV-380 swings for +3 is not known yet. pol 0 sends bit 1 as +3, pol 1
   as -3 (every byte of the packet's frame inverted, its variant byte var0 + 1), pol 2 alternates
   packet by packet (even 0, odd 1), so one key-up shows which way reads on the K6.

   Header only, no statics, no pointer tables (RAM modules are position independent).
   SPDX-License-Identifier: MIT */
#ifndef C6PHY_FSK_H
#define C6PHY_FSK_H

#include <stdint.h>

#define CF_FN static __attribute__((unused))
#define CF_SLOT 36u             /* TX RAM bytes a 30 ms slot */
#define CF_NB 64u               /* the test packet */
#define CF_PN_FIRST 3u
#define CF_PN 59u
#define CF_MAGIC 0xF2u
#define CF_PRE 16u              /* 0x55 bytes before each packet */
#define CF_SYNC 0x076B3E36u
#define CF_FRAME (CF_PRE + 4u + CF_NB)
#define CF_FILL 0x55u
#define CF_VAR2400 20u          /* modem/fsk24.py variants 20/21 (2400), 22/23 (1200) */
#define CF_VAR1200 22u

typedef struct {
    uint16_t baud;              /* 2400 or 1200 */
    uint8_t pol;                /* 0, 1 or 2 (alternate) */
    uint8_t var0, seq0;
    uint16_t npk;               /* packets, then fill */
    uint16_t lead, extra;       /* 0x55 bytes before the first packet's preamble / the second's */
    uint32_t i;                 /* data bytes so far */
    uint16_t k, at;             /* packet now, byte within its frame */
    uint8_t pk[CF_NB];
    uint8_t raw[8];             /* the expanded byte's TX RAM bytes not yet sent */
    uint8_t nraw, rawat;
} cf_gen;

/* 0x55 bytes for ms at baud, rounded up (pager.Air's ceil(ms / 1000 * baud / 8)) */
CF_FN unsigned cf_bytes_ms(unsigned ms, unsigned baud) {
    return (ms * baud + 7999u) / 8000u;
}

CF_FN unsigned cf_pol(const cf_gen *g, unsigned k) {
    return g->pol == 2u ? (k & 1u) : g->pol;
}

CF_FN uint16_t cf_crc16(const uint8_t *b, unsigned n) {
    uint16_t c = 0xFFFFu;
    for (unsigned i = 0; i < n; i++) {
        c ^= (uint16_t)(b[i] << 8);
        for (unsigned k = 0; k < 8; k++)
            c = (c & 0x8000u) ? (uint16_t)((c << 1) ^ 0x1021u) : (uint16_t)(c << 1);
    }
    return c;
}

/* fsk24's test packet: 0xF2, variant, sequence, PN9 (x^9 + x^5 + 1 from 0x1FF, the low byte of
   the state a byte), CRC-16/CCITT-FALSE big endian */
CF_FN void cf_packet(uint8_t *p, unsigned variant, unsigned seq) {
    unsigned st = 0x1FFu;
    p[0] = CF_MAGIC;
    p[1] = (uint8_t)variant;
    p[2] = (uint8_t)seq;
    for (unsigned i = 0; i < CF_PN; i++) {
        p[CF_PN_FIRST + i] = (uint8_t)st;
        for (unsigned k = 0; k < 8; k++)
            st = (st >> 1) | (((st ^ (st >> 5)) & 1u) << 8);
    }
    uint16_t c = cf_crc16(p, CF_NB - 2u);
    p[CF_NB - 2u] = (uint8_t)(c >> 8);
    p[CF_NB - 1u] = (uint8_t)c;
}

/* baud 2400 or 1200, pol 0-2, npk packets; lead 150 ms and 50 ms extra (pager.Air's defaults) */
CF_FN void cf_init(cf_gen *g, unsigned baud, unsigned pol, unsigned npk, unsigned seq0) {
    g->baud = (uint16_t)(baud == 1200u ? 1200u : 2400u);
    g->pol = (uint8_t)(pol > 2u ? 2u : pol);
    g->var0 = (uint8_t)(g->baud == 1200u ? CF_VAR1200 : CF_VAR2400);
    g->seq0 = (uint8_t)seq0;
    g->npk = (uint16_t)npk;
    g->lead = (uint16_t)cf_bytes_ms(150u, g->baud);
    g->extra = (uint16_t)cf_bytes_ms(50u, g->baud);
    g->i = 0;
    g->k = 0;
    g->at = 0;
    g->nraw = 0;
    g->rawat = 0;
}

/* packets that fit in ms of slots, the lead, the extra and 4 bytes of tail included */
CF_FN unsigned cf_fit(unsigned ms, unsigned baud) {
    unsigned b = ms * baud / 8000u, over = cf_bytes_ms(150u, baud) + cf_bytes_ms(50u, baud) + 4u;
    return b > over ? (b - over) / CF_FRAME : 0u;
}

/* the next data byte of the stream as it goes on air (inverted for a pol 1 packet) */
CF_FN uint8_t cf_next(cf_gen *g) {
    uint8_t b;
    unsigned k = g->k;
    if (g->i < g->lead || k >= g->npk) {
        b = CF_FILL;
        if (k >= g->npk && k)
            k--;                                /* the tail keeps the last packet's polarity */
    } else {
        unsigned pre = CF_PRE + (k == 1u ? g->extra : 0u), at = g->at;
        if (at == 0u)
            cf_packet(g->pk, g->var0 + cf_pol(g, k), (unsigned)g->seq0 + k);
        if (at < pre)
            b = CF_FILL;
        else if (at < pre + 4u)
            b = (uint8_t)(CF_SYNC >> (8u * (3u - (at - pre))));
        else
            b = g->pk[at - pre - 4u];
        if (++g->at == pre + 4u + CF_NB) {
            g->at = 0;
            g->k++;
        }
    }
    g->i++;
    return cf_pol(g, k) ? (uint8_t)~b : b;
}

/* one data byte as TX RAM bytes (2400: 4, 1200: 8) into out; returns how many */
CF_FN unsigned cf_expand(uint8_t d, unsigned baud, uint8_t *out) {
    if (baud == 1200u) {
        for (unsigned j = 0; j < 8u; j++)
            out[j] = (d >> (7u - j)) & 1u ? 0x55u : 0xFFu;
        return 8u;
    }
    for (unsigned j = 0; j < 4u; j++) {
        unsigned hi = (d >> (7u - 2u * j)) & 1u, lo = (d >> (6u - 2u * j)) & 1u;
        out[j] = (uint8_t)(((hi ? 0x5u : 0xFu) << 4) | (lo ? 0x5u : 0xFu));
    }
    return 4u;
}

/* the next slot's 36 TX RAM bytes */
CF_FN void cf_slot(cf_gen *g, uint8_t *out) {
    for (unsigned n = 0; n < CF_SLOT; n++) {
        if (g->rawat >= g->nraw) {
            g->nraw = (uint8_t)cf_expand(cf_next(g), g->baud, g->raw);
            g->rawat = 0;
        }
        out[n] = g->raw[g->rawat++];
    }
}

/* 1: every packet's bytes are in the slots made so far (what follows is fill) */
CF_FN int cf_done(const cf_gen *g) {
    return g->k >= g->npk;
}

/* 36 bytes of fill (0x55 at pol, before any packet): what TX RAM holds before the first slot */
CF_FN void cf_fill(uint8_t *out, unsigned baud, unsigned pol) {
    uint8_t r[8];
    unsigned n = cf_expand(pol == 1u ? (uint8_t)~CF_FILL : CF_FILL, baud, r);
    for (unsigned i = 0; i < CF_SLOT; i++)
        out[i] = r[i % n];
}

#endif
