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
#define OM_LEVEL 5                      /* 2: sotto 1200 encoder, the message, send (11-13);
                                           3: tx_tone (11-14);
                                           4: tx_mic and tx_tone send the ident first, under
                                              the image's transmit guard (11-16);
                                           5: Codec2 3200 to the speaker (11-18) */
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

    /* level 2: sotto 1200 (8 kHz, the fixed-point encoder) on the radio, and the message PTT
       sends. The encoder runs in a thread of its own (its stack is bigger than the module's);
       enc_push hands it 320 samples at a time and waits while it is behind. */
    int (*enc_begin)(void);             /* a new recording: 0, or -1 (no memory for it) */
    int (*enc_push)(const int16_t *x, unsigned n);  /* 8 kHz audio: superframes so far, -1 full */
    int (*enc_end)(void);               /* the rest, flushed; the recording becomes the message
                                           PTT sends and # plays: its superframes, or -1 */
    unsigned (*msg)(const uint8_t **bytes);         /* the message: superframes (6 bytes each) */
    void (*preview)(void);              /* the # key: the message on the speaker */
    /* send the message as a PTT press would (PHY R in the range build, then the Morse ident),
       once the module has ended; only on 441.850 or 440.850 MHz (the bench channels): 0, or -1 (another frequency, TX off) */
    int (*tx_request)(void);
    /* the keyed mic test: ms (at most 5000) of FM carrying the microphone as FM mode sends it
       (MIC_PWR on, the C6000's mic input), at 1 W whatever the channel says; only on
       441.850 or 440.850 MHz. Level 4 images send the Morse ident first, unkey for 100 ms, then key the
       test (level 2-3 images: the test within about 30 ms, the ident after). The module runs on
       meanwhile, so it can capture I2S3 (rxaudio.h's rx_start_as_is: register 26 and PD9 left
       alone). Under the transmit guard (level 4): a key-up still up 1.5 s past its length, or a
       frozen radio, resets the radio about 2 s later. 0, or -1 (refused) */
    int (*tx_mic)(unsigned ms);
    /* level 3: the keyed line-in tone: ms (at most 5000) of FM carrying an hz (100-3000) tone
       from the MCU through the line in, as every send's audio goes, at 1 W; only on
       441.850 or 440.850 MHz. Ident first and the transmit guard as tx_mic (level 4). The module runs on
       meanwhile, and may switch the C6000's transmit source (E0) and capture I2S3: 0, or -1
       (refused) */
    int (*tx_tone)(unsigned ms, unsigned hz);
    /* level 5: Codec2 3200 on the speaker through OpenRTX's own decoder thread, as its M17
       mode plays a call (docs/uv380-m17-rx.md). c2_start opens the MCU -> speaker path (the
       C6000's DAC) and starts the decoder: 0, or -1 (no path, or no memory for the thread).
       c2_push queues one 8-byte frame (20 ms) without waiting: 0, or -1 (the queue, 4 frames,
       is full: push it again later; or not started). The decoder plays silence when the queue
       runs dry. c2_stop waits up to 1 s for the queue to play out, then stops the decoder and
       releases the path; the loader also calls it when a module returns. Check api->level >= 5
       before calling these: older images' tables end at tx_tone. */
    int (*c2_start)(void);
    int (*c2_push)(const uint8_t *frame);
    void (*c2_stop)(void);
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
    /* level 2 (NULL: the calls return -1, 0 or nothing) */
    int (*enc_begin)(void);
    int (*enc_push)(const int16_t *x, unsigned n);
    int (*enc_end)(void);
    unsigned (*msg)(const uint8_t **bytes);
    void (*preview)(void);
    int (*tx_request)(void);
    int (*tx_mic)(unsigned ms);
    /* level 3 (NULL: returns -1) */
    int (*tx_tone)(unsigned ms, unsigned hz);
    /* level 5 (NULL: c2_start and c2_push return -1) */
    int (*c2_start)(void);
    int (*c2_push)(const uint8_t *frame);
    void (*c2_stop)(void);
} om_host;

/* One "mod ..." command; args is what follows "mod". Returns 0, or -1 after an "err" line. */
int om_command(const om_host *h, const char *args);
uint8_t *om_arena(void);                /* NULL if the heap can't spare it */
/* The image's own use of the module table (docs/uv380-multimode.md milestone 4): h for the
   calls that follow, the clock from now, Ctrl-C cleared. Not for modules. */
const ortx_api_t *om_api(const om_host *h);
/* c6air_img.c: mod/c6air.h's c6air_run from flash (PAGER's M17, YSF and DMR sends, "air") */
int c6air_send(const ortx_api_t *api, unsigned air, unsigned src, unsigned dry);
/* No module upload under way ("mod begin" with no "mod run" yet): the image may use the arena */
int om_idle(void);
/* c6rx_img.c: mod/c6rx.h's c6rx_listen from flash (PAGER's M17 and YSF receive, air 1 or 2): a
   heard message's superframes into out (at most max), 0 when stop() or max_ms came first, -1 if
   the arena is too small */
int c6rx_hear(const ortx_api_t *api, unsigned air, uint32_t max_ms, uint8_t *out, unsigned max);
/* the message c6rx_hear last heard (it stays in the scratch until the arena's next use): its
   superframes into out, at most max; 0 if there is none */
int c6rx_take(const ortx_api_t *api, uint8_t *out, unsigned max);

#ifdef __cplusplus
}
#endif

#endif
