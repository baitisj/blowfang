/* mod_lib: portable SPI-flash module library over mod_core.h and lz4d.h.
 *
 * Stores signed RAM modules (header + code + SipHash tag, optionally LZ4-packed) in
 * fixed-size slots in external SPI NOR flash so any Cortex-M radio (K6, UV-21 Pro,
 * UV-380, ...) can install, inspect, and reload modules without hardcoding flash
 * geometry, image magic, or ABI level.
 *
 * Slot n lives at cfg->base + n * cfg->slot_bytes and holds the exact module image
 * as received, followed by erased 0xFF bytes.
 *
 * SPDX-License-Identifier: MIT */
#ifndef MOD_LIB_H
#define MOD_LIB_H
#include <stdint.h>
#include "mod_core.h"
#ifdef __cplusplus
extern "C" {
#endif

/* header byte 13 (flags) */
#define ML_F_LIB   0x01  /* install into the library (slot in byte 15) instead of running */
#define ML_F_PAGE  0x02  /* fixed-address code page, never run as a standalone module */
#define ML_F_CARDS 0x04  /* help-card deck, never loaded/run as code */
#define ML_F_LZ4   0x08  /* body is LZ4-packed (unpacked length in first 2 bytes of body) */

/* length of the NUL-padded module name following cfg->name_magic[4] */
#define ML_NAME 12

/* SPI flash operations: return 0 on success, non-zero on error/refusal. */
typedef struct {
  int (*read)(uint32_t addr, void *buf, uint32_t n);
  int (*prog)(uint32_t addr, const void *buf, uint32_t n);
  int (*erase)(uint32_t addr);
} ml_flash_t;

/* Per-radio library geometry, header parameters, and verification/unpack hooks. */
typedef struct {
  uint32_t base;          /* SPI flash base address of slot 0 */
  uint32_t slot_bytes;    /* bytes per slot (e.g. 0x4000) */
  uint32_t sector_bytes;  /* erase sector size (e.g. 4096) */
  uint32_t page_bytes;    /* program page size (e.g. 256) */
  uint8_t slots;          /* number of slots */
  char magic[3];          /* 3-byte header magic (e.g. "K6M") */
  uint8_t abi;            /* expected ABI major (header byte 3) */
  uint8_t max_level;      /* maximum accepted API level (header byte 12) */
  char name_magic[4];     /* 4-byte name-block magic (e.g. "K6NM") */
  int (*tag_check)(const uint8_t *img, unsigned code_len);       /* 0 ok, non-zero bad */
  int (*unpack)(uint8_t *dst, const uint8_t *src, unsigned cap); /* >=0 ok, <0 bad */
} ml_cfg_t;

enum {
  ML_EMPTY   = -1, /* the slot is erased */
  ML_BAD_HDR = -2, /* not a valid module image, or too large for slot / RAM */
  ML_BAD_TAG = -3, /* SipHash tag check failed (or no key configured) */
  ML_IO      = -4, /* a flash operation failed or was refused */
  ML_VERIFY  = -5, /* read-back after programming differed from written bytes */
  ML_SLOT    = -6  /* slot index out of range */
};

typedef struct {
  uint16_t code_len, entry;
  uint32_t serial;
  uint8_t level, flags;
  char name[ML_NAME];
  uint16_t packed;
} ml_info_t;

static inline uint32_t ml_slot_addr(const ml_cfg_t *cfg, int slot) {
  return cfg->base + (uint32_t)slot * cfg->slot_bytes;
}

/* Write the checked image at img (flag ML_F_LIB, slot in byte 15) to flash and verify.
 * Returns total image length in bytes, or ML_*. */
int ml_install(const ml_cfg_t *cfg, const uint8_t *img, const ml_flash_t *fl);

/* Inspect slot header and optional name block without checking the tag.
 * Returns 0 on success, or ML_*. */
int ml_info(const ml_cfg_t *cfg, int slot, const ml_flash_t *fl, ml_info_t *out);

/* Read slot into dst (cap bytes), verify header and tag, and unpack if ML_F_LZ4.
 * is_page=0 loads a runnable module (refuses ML_F_PAGE); is_page=1 loads a code page
 * (refuses non-pages). Always refuses ML_F_CARDS.
 * Returns unpacked code length (>0), or ML_*. */
int ml_load(const ml_cfg_t *cfg, int slot, const ml_flash_t *fl,
            uint8_t *dst, unsigned cap, unsigned is_page);

#ifdef __cplusplus
}
#endif
#endif
