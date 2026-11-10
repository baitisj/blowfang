/* k6_lib: the K6's SPI module library (Jeff approved it, 2026-11-08; docs/k6-modules.md).

   A RAM module (k6_mod.h) can be kept in the radio's SPI flash and run again later without
   sending it over the air each time. The library is 12 slots of 16 KB from 0x110000 to
   0x13FFFF, above sotto's codebooks (0x100000-0x10D320) and the calibration sector (0x10F000):

     slot n at KL_BASE + n * KL_SLOT_BYTES: the module image exactly as it arrived, header,
     code and SipHash tag (k6_mod.h), and 0xFF after it

   Install: a module sent with header flag KM_F_LIB (byte 13) and its slot in byte 15 is checked
   as any module is (km_packet: the tag under the radio's key, the serial), and then written to
   its slot instead of being run: kl_install erases the sectors the image needs, programs it a
   page at a time and reads it back. The only SPI writes are these, once per install, and never
   while keyed (the caller's flash calls refuse them). Nothing else of the radio's state goes
   to SPI: the message store never does (Jeff's rule).

   Load: kl_load reads a slot into RAM (the shared block, as a module arriving over the air),
   checks the header and the tag again (so a slot that wore, or that something else wrote, is
   refused), and arms km_run, which then runs it as it runs any module. No serial check here:
   the serial guards against a replay over the air, and a slot was checked when it went in.

   SPDX-License-Identifier: MIT */
#ifndef K6_LIB_H
#define K6_LIB_H
#include <stdint.h>
#include "k6_mod.h"
#ifdef __cplusplus
extern "C" {
#endif

#define KL_BASE 0x110000u
#define KL_SLOT_BYTES 0x4000u
#define KL_SLOTS 12
#define KL_END (KL_BASE + KL_SLOTS * KL_SLOT_BYTES)   /* 0x140000 */
#define KL_SECTOR 4096u
#define KL_PAGE 256u

/* header byte 13 (flags): install into the library, slot in byte 15, instead of running */
#define KM_F_LIB 0x01
/* header byte 13 (flags), with KM_F_LIB: a code page (kl_page), never run as a module: code
   linked at a fixed RAM address against one image, which calls into it (docs/k6-voice-explore.md) */
#define KM_F_PAGE 0x02

/* The SPI flash, as the core gives it: read anywhere in the library, program up to KL_PAGE
   bytes inside one page, erase one KL_SECTOR sector. 0, or -1 (refused: keyed, out of range). */
typedef struct {
  int (*read)(uint32_t addr, void *buf, uint32_t n);
  int (*prog)(uint32_t addr, const void *buf, uint32_t n);
  int (*erase)(uint32_t addr);
} kl_flash_t;

enum {
  KL_EMPTY = -1,     /* the slot is erased */
  KL_BAD_HDR = -2,   /* not a module image, or longer than the slot / the RAM given */
  KL_BAD_TAG = -3,   /* the tag doesn't check under this radio's key (or it has none) */
  KL_IO = -4,        /* a flash call refused */
  KL_VERIFY = -5,    /* read back differs from what was written */
  KL_SLOT = -6       /* no such slot */
};

/* Slot n's address */
#define KL_SLOT_ADDR(n) (KL_BASE + (uint32_t)(n) * KL_SLOT_BYTES)

/* Write the checked image at img (KM_READY from km_packet, flag KM_F_LIB) to the slot in its
   byte 15. The image's length, or KL_*. */
int kl_install(const uint8_t *img, const kl_flash_t *fl);
/* Read slot n into dst (cap bytes) and check it: the code length and arm km_run, or KL_*.
   A page (KM_F_PAGE) is refused: KL_BAD_HDR. */
int kl_load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap);
/* Read the page in slot n into dst (cap bytes) and check it as kl_load does, without arming
   km_run: the code length (its code at dst + KM_HDR), or KL_* (KL_BAD_HDR: not a page). The
   caller checks that the page was linked against it (the page's last bytes). */
int kl_page(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap);
/* What slot n holds, from its header alone (no tag check): */
typedef struct {
  uint16_t code_len, entry;
  uint32_t serial;
  uint8_t level, flags;
} kl_info_t;
int kl_info(int slot, const kl_flash_t *fl, kl_info_t *out);   /* 0, or KL_* */

#ifdef __cplusplus
}
#endif
#endif
