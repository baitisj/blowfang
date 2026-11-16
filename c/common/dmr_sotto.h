/* dmr_sotto.h: sotto messages as DMR rate 1/2 data blocks, for the UV-380's HR_C6000 modem
   (docs/uv380-c6000-modem.md). The format and its reference are modem/dmrsotto.py; tests check
   the two agree byte for byte (tests/test_dmr_sotto.py).

   Block (12 bytes, one rate 1/2 burst, 60 ms apart in direct mode = 1200 bit/s of payload):
     byte 0      kind (bits 7-5: 0 data, 1 head, 2 text, 3 end), sequence number (bits 4-0)
     bytes 1-9   payload
     bytes 10-11 CRC-16-CCITT (0x1021, init 0xFFFF) of bytes 0-9, big endian
   Message: head (id, codec, superframes (2 bytes), text blocks, 0 0 0 0), text blocks, data
   blocks (the superframes back to back, zero padded), end (id, bytes (2 bytes)).

   Header only, no statics, no tables: block i is built on the fly from the message, so a RAM
   module needs no buffer for the coded message.
   SPDX-License-Identifier: MIT */
#ifndef DMR_SOTTO_H
#define DMR_SOTTO_H

#include <stdint.h>

#define DS_FN static __attribute__((unused))
#define DS_DATA 0u
#define DS_HEAD 1u
#define DS_TEXT 2u
#define DS_END 3u
#define DS_PAYLOAD 9u
#define DS_BLOCK 12u
#define DS_SF 6u
#define DS_SOTTO1200 0x12u

DS_FN uint16_t ds_crc16(const uint8_t *b, unsigned n) {
    uint16_t c = 0xFFFFu;
    for (unsigned i = 0; i < n; i++) {
        c ^= (uint16_t)(b[i] << 8);
        for (unsigned k = 0; k < 8; k++)
            c = (c & 0x8000u) ? (uint16_t)((c << 1) ^ 0x1021u) : (uint16_t)(c << 1);
    }
    return c;
}

/* one block: kind, seq, n payload bytes (the rest zero) */
DS_FN void ds_block(uint8_t *out, unsigned kind, unsigned seq, const uint8_t *p, unsigned n) {
    out[0] = (uint8_t)(((kind & 7u) << 5) | (seq & 31u));
    for (unsigned i = 0; i < DS_PAYLOAD; i++)
        out[1 + i] = i < n ? p[i] : 0;
    uint16_t c = ds_crc16(out, 10);
    out[10] = (uint8_t)(c >> 8);
    out[11] = (uint8_t)c;
}

DS_FN unsigned ds_ntext(unsigned textlen) {
    return textlen ? (textlen + DS_PAYLOAD - 1) / DS_PAYLOAD : 1u;
}

/* blocks in a message of nbytes superframe bytes with textlen bytes of ident */
DS_FN unsigned ds_nblocks(unsigned nbytes, unsigned textlen) {
    return 2u + ds_ntext(textlen) + (nbytes + DS_PAYLOAD - 1) / DS_PAYLOAD;
}

/* block i of the message (msg: nbytes, a multiple of 6; text: the ident) into out */
DS_FN void ds_make(uint8_t *out, unsigned i, const uint8_t *msg, unsigned nbytes, unsigned id,
                   const char *text, unsigned textlen) {
    unsigned nt = ds_ntext(textlen), nd = (nbytes + DS_PAYLOAD - 1) / DS_PAYLOAD;
    uint8_t p[DS_PAYLOAD];
    if (i == 0) {
        unsigned nsf = nbytes / DS_SF;
        p[0] = (uint8_t)id, p[1] = DS_SOTTO1200, p[2] = (uint8_t)(nsf >> 8), p[3] = (uint8_t)nsf;
        p[4] = (uint8_t)nt;
        ds_block(out, DS_HEAD, 0, p, 5);
    } else if (i <= nt) {
        unsigned o = (i - 1) * DS_PAYLOAD, n = 0;
        for (; n < DS_PAYLOAD && o + n < textlen; n++)
            p[n] = (uint8_t)text[o + n];
        ds_block(out, DS_TEXT, i, p, n);
    } else if (i <= nt + nd) {
        unsigned o = (i - 1 - nt) * DS_PAYLOAD;
        unsigned n = nbytes - o < DS_PAYLOAD ? nbytes - o : DS_PAYLOAD;
        ds_block(out, DS_DATA, i, msg + o, n);
    } else {
        p[0] = (uint8_t)id, p[1] = (uint8_t)(nbytes >> 8), p[2] = (uint8_t)nbytes;
        ds_block(out, DS_END, i, p, 3);
    }
}

/* a received block: its kind, or -1 (CRC fails) */
DS_FN int ds_check(const uint8_t *b) {
    if (ds_crc16(b, 10) != (uint16_t)((b[10] << 8) | b[11]))
        return -1;
    return b[0] >> 5;
}

#endif
