/* k6_lib: the K6's SPI module library (thin wrapper over c/common/mod_lib.h).
   Jeff approved it, 2026-11-08; docs/k6-modules.md.

   A RAM module (k6_mod.h) can be kept in the radio's SPI flash and run again later without
   sending it over the air each time. The library is 32 slots of 16 KB from 0x110000 to
   0x18FFFF, above sotto's codebooks (0x100000-0x10D320) and the calibration sector (0x10F000);
   12 slots until the release plan (Jeff 2026-11-13) gave it 20 more. 0x190000 up stays unused.

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

int kl_install(const uint8_t *img, const kl_flash_t *fl);
int kl_load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap);
int kl_page(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap);
int kl_info(int slot, const kl_flash_t *fl, kl_info_t *out);

#ifdef __cplusplus
}
#endif
#endif
