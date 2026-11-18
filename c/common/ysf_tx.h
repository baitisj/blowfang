/* ysf_tx: Yaesu System Fusion (YSF, C4FM) frames, built on the radio one 120-byte frame at a
   time, for the UV-380's HR_C6000 physical layer mode (docs/uv380-multimode.md). A C port of
   the encoder half of modem/ysf.py, byte for byte the same (tests/test_uv380_framers.py checks both
   framers): the chip sends these bytes as 4FSK unchanged (dibit 01 +3, 00 +1, 10 -1, 11 -3).

   A frame is 100 ms = 960 bits: sync D4 71 C9 63 4D, the FICH (200 bits: 4 bytes + CRC-16 as
   four Golay(24,12) words, K=5 rate 1/2 code, 5 x 20 dibit interleaver), the payload (720 bits
   in five 144-bit sections). Payloads here: header and terminator (two CSD fields: dest + src,
   then spaces), data full rate (two 20-byte DCH fields), and the sotto softmodem's "sotto VW"
   voice payload (sotto docs/softmodem.md: five 48-bit frames, each + CRC-8, tail-biting K=7
   (133, 171, 165) punctured to rate 7/18, spread, PN9 seeded 0x1FF; FICH DT voice FR with the
   provisional sotto flag, byte 2 bit 7). On the UV-380 the five frames are sotto 1200
   superframes. Nothing here allocates; the largest buffer on the stack is 36 bytes.
   SPDX-License-Identifier: MIT */
#ifndef YSF_TX_H
#define YSF_TX_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define YSFT_FRAME 120u                 /* bytes a frame: 100 ms, 480 symbols */
#define YSFT_PREAMBLE 0x77u             /* 24 bytes of it before the header */
#define YSFT_PRE_BYTES 24u
#define YSFT_FI_HEADER 0u
#define YSFT_FI_COMMS 1u
#define YSFT_FI_TERMINATOR 2u
#define YSFT_DT_VD1 0u
#define YSFT_DT_DATA_FR 1u
#define YSFT_DT_VD2 2u
#define YSFT_DT_VOICE_FR 3u
#define YSFT_SOTTO_NF 5u                /* sotto frames in a YSF frame */
#define YSFT_SOTTO_SF 6u                /* bytes each (48 bits) */

typedef struct {
    uint8_t fi, cs, cm, bn, bt, fn, ft, dev, mr, voip, dt, sql, dgid, sotto;
} ysft_fich;

/* modem/ysf.py's FICH defaults: comms, cs 2, ft 6, voice FR, the rest 0 */
void ysft_fich_default(ysft_fich *f);
void ysft_fich_bytes(const ysft_fich *f, uint8_t out[4]);

/* CRC-16 CCITT, initial 0, inverted (the FICH's and the CSD's) */
uint16_t ysft_crc(const uint8_t *d, unsigned n);

/* a header or terminator frame (f->fi says which): CSD1 = dest + src (10 characters each,
   upper case, space padded), CSD2 = 20 spaces */
void ysft_frame_header(const ysft_fich *f, const char *dest, const char *src,
                       uint8_t out[YSFT_FRAME]);
/* a data full rate frame: two 20-byte DCH fields */
void ysft_frame_data_fr(const ysft_fich *f, const uint8_t d1[20], const uint8_t d2[20],
                        uint8_t out[YSFT_FRAME]);
/* a sotto VW frame: five 6-byte frames (30 bytes) */
void ysft_frame_sotto(const ysft_fich *f, const uint8_t frames[30], uint8_t out[YSFT_FRAME]);

/* A whole sotto transmission, pulled a slot at a time: 24 bytes of 0x77, header, comms frames
   FN 0-6 of five superframes each (the last superframe repeats to fill the last frame),
   terminator (modem/ysf.py tx_sotto). The superframes must stay put until it ends. */
typedef struct {
    const uint8_t *sf;
    unsigned n;
    char dest[11], src[11];
    uint8_t dgid;
    unsigned unit, units;               /* 0: preamble, 1: header, comms..., terminator */
    uint8_t buf[YSFT_FRAME];
    unsigned pos, len;
} ysft_sotto;

/* 0, or -1 (no superframes) */
int ysft_sotto_begin(ysft_sotto *t, const uint8_t *sf, unsigned n, const char *dest,
                     const char *src, uint8_t dgid);
unsigned ysft_sotto_read(ysft_sotto *t, uint8_t *out, unsigned max);
unsigned ysft_sotto_bytes(const ysft_sotto *t);

#ifdef __cplusplus
}
#endif

#endif
