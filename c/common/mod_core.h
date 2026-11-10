/* mod_core: check and run a signed, position-independent RAM module on any Cortex-M radio.
 *
 * The K6's module image (c/k6/k6_mod.h, docs/k6-modules.md) without the K6's packet layer, so
 * another radio can take modules too. The image is
 *
 *   0  magic[3], ABI      three bytes naming the radio's API table ("K6M" the K6, "U3M" the UV-380),
 *                         then the ABI major (1)
 *   4  code length        u16, little-endian
 *   6  entry              u16, byte offset of the entry function in the code (even)
 *   8  serial             u32, must be newer than the last module this boot accepted
 *   12 API level, flags   u8 level (at most the radio's), u8 flags (must be 0 here), u8 telemetry
 *                         parts, u8 library slot
 *   16 code               position-independent Thumb (clang -fropi), no data or bss
 *   16 + code length      SipHash-2-4 tag (8 bytes, little-endian) of bytes 0 .. 15 + code length
 *                         under the radio's 16-byte module key
 *
 * The magic is under the tag, so a module signed for one radio's table never runs against
 * another's, even under the same key. A radio builds with -DMC_KEY=0x12,0x34,... (16 bytes), or
 * without it and has `python -m modem.k6 keyimage` write the key over the placeholder in the
 * built image, exactly as a K6 image (the placeholder text is the K6's). Until then every module
 * is refused with MC_NO_KEY.
 *
 * The module runs once on the caller's stack, as int entry(const <radio>_api_t *api).
 * SPDX-License-Identifier: MIT */
#ifndef MOD_CORE_H
#define MOD_CORE_H
#include <stdint.h>

#define MC_HDR 16
#define MC_TAG 8
#define MC_ABI 1

enum {
    MC_READY = 3,                       /* checks: mc_run it */
    MC_BAD_HDR = -3,                    /* magic, ABI, API level, flags, entry or length wrong */
    MC_BAD_TAG = -4,                    /* the SipHash tag doesn't check: not signed with our key */
    MC_NO_KEY = -5,                     /* this image has no key: modules are off */
    MC_OLD = -6                         /* serial not newer than the last one run (a replay) */
};

/* the image a[0 .. n-1] (n: bytes held, at least the whole image): is it a module for this radio
   (magic, API level at most level)? MC_READY arms mc_run. */
int mc_check(const uint8_t *a, unsigned n, const char magic[3], unsigned level);
/* After MC_READY: run it once and return what it returned (-100 if none is armed). */
int mc_run(const uint8_t *a, const void *api);
uint32_t mc_serial(void);               /* of the last module accepted this boot, 0 if none */
/* SipHash-2-4 (Aumasson and Bernstein) */
uint64_t mc_siphash(const uint8_t key[16], const uint8_t *m, unsigned n);

#endif
