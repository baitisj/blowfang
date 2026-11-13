/* RAM modules for the UV-380's OpenRTX image: load signed code over USB serial and run it, with no
 * reflash and no bootloader keys.
 *
 * The image (c/common/mod_core.h) is the K6's and the bare-metal UV-380's: header, position-
 * independent Thumb-2 for the Cortex-M4F (clang -fropi, hard float), SipHash-2-4 tag under the
 * module key (~/.config/sotto/k6-mod.key). The magic is "U3R": this table is not the bare-metal
 * port's ("U3M", radios/uv380/src/uv_api.h), so a module built for one never runs against the other.
 * Build and sign one with
 *
 *     python -m modem.uv380mod MODULE.c MODULE.u3r --key-file ~/.config/sotto/k6-mod.key
 *
 * (a source that includes ortx_mod.h is built for this table) and send it with
 *
 *     python3 tools/uv380_i2sprobe.py mod /dev/ttyACM0 MODULE.u3r
 *
 * The loader is in the probe image (BLOWFANG_I2SPROBE=1), whose thread reads USB serial. The module
 * runs once, in that thread, as
 *
 *     int uv_main(const ortx_api_t *api);
 *
 * while OpenRTX's own threads go on (UI, RTX, audio): sleep_ms yields to them, a busy loop starves
 * them. Stack: about 5 KB; big buffers go in api->scratch. The arena is taken from the heap, in
 * the 128 KB RAM (never CCM, which can neither run code nor take DMA), at the first "mod begin",
 * so scratch can be a DMA buffer and images that never load a module pay nothing. No libraries,
 * no writable statics; api->fmt is snprintf (no float).
 *
 * A module stops when stop() says so: PTT, the speaker preview, leaving PAGER mode, or a Ctrl-C
 * byte (0x03) from USB serial, which hamlet sends to end a module that runs too long.
 *
 * USB serial commands (the probe's channel, i2sprobe.h; each ends in "ok ..." or "err ..."):
 *   mod info                   arena address and size (taking it now; 0: no heap for it), bytes
 *                              loaded, key or not, last serial
 *   mod begin N                a new image of N bytes (at most OM_ARENA)
 *   mod hex OFF HEX            bytes at offset OFF (hex) of the image, up to OM_HEX_MAX a line
 *   mod run                    check the whole image (magic, level, tag, serial) and run it:
 *                              its say lines ("mod say ..."), then "ok mod run ret=R ms=T"
 *
 * The table is append-only within ABI major 1: a new call goes at the end with a new level.
 * SPDX-License-Identifier: MIT */
#ifndef ORTX_MOD_H
#define ORTX_MOD_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OM_MAGIC "U3R"
#define OM_LEVEL 1
#define OM_ARENA 16384                  /* module image plus scratch (half if the heap is short) */
#define OM_HEX_MAX 128                  /* image bytes in one "mod hex" line */
#define OM_LINE 320                     /* the longest command line the image reads */

typedef struct ortx_api {
    uint16_t abi, level;                /* MC_ABI, OM_LEVEL */
    void (*say)(const char *s);         /* a line "mod say <s>" (up to 200 chars) */
    int (*fmt)(char *buf, unsigned size, const char *f, ...);   /* snprintf, no float */
    uint32_t (*ms)(void);               /* ms since the module started */
    void (*sleep_ms)(uint32_t ms);      /* yields to OpenRTX's threads */
    int (*stop)(void);                  /* 1: end now (PTT, preview, mode change, Ctrl-C) */
    void *(*scratch)(uint32_t *bytes);  /* the arena after the image, 8-byte aligned, DMA-able */
    uint8_t (*c6000_read)(uint8_t reg); /* HR_C6000 configuration registers */
    void (*c6000_write)(uint8_t reg, uint8_t val);
    unsigned (*usb_read)(void *buf, unsigned n);        /* what came from USB serial, no wait */
    void (*usb_write)(const void *buf, unsigned n);     /* raw bytes to USB serial */
    /* PC3 (AIN_RTX) through OpenRTX's input stream: n samples (at most 2048) at fs */
    unsigned (*adc_capture)(int16_t *buf, unsigned n, unsigned fs);
    int32_t (*rssi)(void);              /* dBm, from the AT1846S */
    void *(*memcpy)(void *d, const void *s, unsigned n);
    void *(*memset)(void *d, int c, unsigned n);
    /* one I2S probe command line (i2sprobe.h: "c6w E0 c8", "cap SPI3 s S16 8000", "raw a 64",
       ...), its lines to USB serial as if hamlet had sent it: 0, or -1 after an "err" line.
       A module can sweep codec settings and capture after each with no reflash. */
    int (*probe)(const char *line);
} ortx_api_t;

/* the image's side: what the loader needs from OpenRTX (all of it, or NULL where noted) */
typedef struct {
    void (*line)(const char *s);                /* one line out, no newline */
    void (*write)(const void *b, unsigned n);   /* raw bytes out */
    unsigned (*read)(void *b, unsigned n);      /* bytes in, no wait */
    uint32_t (*now_ms)(void);
    int (*stop)(void);                          /* PTT, preview, mode change */
    void (*sleep_ms)(uint32_t ms);
    uint8_t (*c6000_read)(uint8_t reg);         /* NULL: reads 0 */
    void (*c6000_write)(uint8_t reg, uint8_t val);  /* NULL: does nothing */
    unsigned (*adc_capture)(int16_t *buf, unsigned n, unsigned fs);     /* NULL: 0 samples */
    int32_t (*rssi)(void);                      /* NULL: -999 */
    int (*probe)(const char *line);             /* NULL: says "err probe: not here", -1 */
} om_host;

/* One "mod ..." command; args is what follows "mod". Returns 0, or -1 after an "err" line. */
int om_command(const om_host *h, const char *args);
uint8_t *om_arena(void);                /* NULL if the heap can't spare it */

#ifdef __cplusplus
}
#endif

#endif
