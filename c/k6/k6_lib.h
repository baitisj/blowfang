/* k6_lib: the K6's SPI module library (thin wrapper over c/common/mod_lib.h).
   Jeff approved it, 2026-11-08; docs/k6-modules.md.

   A RAM module (k6_mod.h) can be kept in the radio's SPI flash and run again later without
   sending it over the air each time. The library is 32 slots of 16 KB from 0x110000 to
   0x18FFFF, above sotto's codebooks (0x100000-0x10D320) and the calibration sector (0x10F000);
   12 slots until the release plan (Jeff 2026-11-13) gave it 20 more. 0x190000 up stays unused.

     slot n at KL_BASE + n * KL_SLOT_BYTES: the module image exactly as it arrived, header,
     code and SipHash tag (k6_mod.h), and 0xFF after it

   Slots by use (a plan for the tools, the library itself takes any image in any slot):
     0-8    modules
     9-11   EXPLORE's code pages (KM_F_PAGE, docs/k6-voice-explore.md)
     12     the station editor's code page (KM_F_PAGE)
     13-15  help card decks (KM_F_CARDS, the release plan's help cards)
     16-31  modules

   A module may carry its name (K6_MODULE_NAME in k6_mod.h): a 16-byte block, 'K' '6' 'N' 'M'
   and 12 characters (NUL padded), as the first bytes of its code. kl_info reads it with the
   header, so the module list can show names without loading anything.

   Install: a module sent with header flag KM_F_LIB (byte 13) and its slot in byte 15 is checked
   as any module is (km_packet: the tag under the radio's key, the serial), and then written to
   its slot instead of being run: kl_install erases the sectors the image needs, programs it a
   page at a time and reads it back. The only SPI writes are these, once per install, and never
   while keyed (the caller's flash calls refuse them). Nothing else of the radio's state goes
   to SPI: the message store never does (Jeff's rule).

   Packed slots (API level 15, KM_F_LZ4 below): the image as it arrived, packed; kl_info decodes
   just the name from its first bytes, and kl_load / kl_page read it to the top of the RAM they
   are given, check its tag, and unpack it in place there (k6_mod.h km_unpack), so a module
   loads from fewer SPI bytes and a slot holds more code than the RAM it runs in would take raw.

   Load: kl_load reads a slot into RAM (the shared block, as a module arriving over the air),
   checks the header and the tag again (so a slot that wore, or that something else wrote, is
   refused), and arms km_run, which then runs it as it runs any module. No serial check here:
   the serial guards against a replay over the air, and a slot was checked when it went in.

   SPDX-License-Identifier: MIT */
#ifndef K6_LIB_H
#define K6_LIB_H
#include <stdint.h>
#include "../common/mod_lib.h"
#include "k6_mod.h"
#ifdef __cplusplus
extern "C" {
#endif

#define KL_BASE 0x110000u
#define KL_SLOT_BYTES 0x4000u
#define KL_SLOTS 32
#define KL_END (KL_BASE + KL_SLOTS * KL_SLOT_BYTES)   /* 0x190000 */
#define KL_SECTOR 4096u
#define KL_PAGE 256u

#define KM_F_LIB   ML_F_LIB
#define KM_F_PAGE  ML_F_PAGE
#define KM_F_CARDS ML_F_CARDS
#define KM_F_LZ4   ML_F_LZ4
#define KL_NAME    ML_NAME

typedef ml_flash_t kl_flash_t;
typedef ml_info_t  kl_info_t;

enum {
  KL_EMPTY   = ML_EMPTY,
  KL_BAD_HDR = ML_BAD_HDR,
  KL_BAD_TAG = ML_BAD_TAG,
  KL_IO      = ML_IO,
  KL_VERIFY  = ML_VERIFY,
  KL_SLOT    = ML_SLOT
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
/* What slot n holds, from its header and the name block alone (no tag check). code_len: the
   code's length unpacked; packed: the packed body's length in the slot (0 for an image that
   isn't packed). name: the module's name (NUL padded; all NUL when it has none, or is a page or
   a deck). 0, or KL_* */
int kl_info(int slot, const kl_flash_t *fl, kl_info_t *out);

#ifdef __cplusplus
}
#endif
#endif
