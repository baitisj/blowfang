/* dmr_voice.h: a dmr_sotto block (c/common/dmr_sotto.h, 12 bytes) as the 27-byte payload of one DMR
   voice burst, for the UV-380's HR_C6000 (docs/uv380-c6000-modem.md, "Sotto in DMR voice
   bursts"). The reference is modem/dmrvoice.py (with the soft Viterbi decoder); tests check the two
   agree byte for byte (tests/test_uv380_dmrvoice.py).

   A voice burst carries 216 payload bits (108, the sync or EMB, 108), which a DMR radio fills with
   three 72-bit AMBE+2 frames. The chip takes them as 27 bytes over its voice SPI port (page 3) and
   sends them as they are: the vocoder's FEC is the vocoder's business, so these 216 bits are ours.
   Here they carry one block under our own FEC:
     the 96 block bits, MSB first, then 6 zero tail bits: 102 bits
     a K=7 rate 1/2 convolutional code (generators 0171, 0133 octal, input into bit 0): 204 bits
     interleaved: coded bit j goes to payload bit (j * 61) mod 216; the 12 payload bits no coded
       bit reaches are 0
     payload bit p is bit 7 - (p mod 8) of byte p / 8 (MSB first)
   One block per burst, one burst per 60 ms in direct mode: 9 payload bytes per 60 ms = 1200 bit/s,
   sotto 1200 in real time, as the data path (rate 1/2 data bursts) carries it.

   dv_pn: the raw test pattern for learning how the chip maps page 3's bits onto the air (a PN9
   sequence per burst, seeded from the burst number), sent with no FEC.

   Header only, no statics, no tables (a RAM module is position-independent).
   SPDX-License-Identifier: MIT */
#ifndef DMR_VOICE_H
#define DMR_VOICE_H

#include <stdint.h>

#define DV_FN static __attribute__((unused))
#define DV_BYTES 27u
#define DV_BITS 216u
#define DV_INFO 96u
#define DV_STEPS 102u           /* info + tail */
#define DV_CODED 204u
#define DV_SPREAD 61u           /* coprime with 216 */
#define DV_G1 0x79u             /* 0171 */
#define DV_G2 0x5Bu             /* 0133 */

DV_FN unsigned dv_parity(unsigned v) {
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

/* payload bit p set */
DV_FN void dv_set(uint8_t *out, unsigned p) {
    out[p >> 3] = (uint8_t)(out[p >> 3] | (0x80u >> (p & 7u)));
}

/* the 12-byte block blk -> the 27-byte burst payload out */
DV_FN void dv_encode(const uint8_t *blk, uint8_t *out) {
    for (unsigned i = 0; i < DV_BYTES; i++)
        out[i] = 0;
    unsigned reg = 0;
    for (unsigned i = 0; i < DV_STEPS; i++) {
        unsigned b = i < DV_INFO ? (blk[i >> 3] >> (7u - (i & 7u))) & 1u : 0u;
        reg = ((reg << 1) | b) & 0x7Fu;
        unsigned j = 2u * i;
        if (dv_parity(reg & DV_G1))
            dv_set(out, (j * DV_SPREAD) % DV_BITS);
        if (dv_parity(reg & DV_G2))
            dv_set(out, ((j + 1u) * DV_SPREAD) % DV_BITS);
    }
}

/* burst k's raw test pattern: 216 bits of PN9 (x^9 + x^5 + 1) from seed (k * 97) mod 511 + 1 */
DV_FN void dv_pn(unsigned k, uint8_t *out) {
    unsigned s = (k * 97u) % 511u + 1u;
    for (unsigned i = 0; i < DV_BYTES; i++)
        out[i] = 0;
    for (unsigned p = 0; p < DV_BITS; p++) {
        unsigned b = ((s >> 8) ^ (s >> 4)) & 1u;
        s = ((s << 1) | b) & 0x1FFu;
        if (b)
            dv_set(out, p);
    }
}

#endif
