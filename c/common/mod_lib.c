/* mod_lib: portable SPI-flash module library (see mod_lib.h). SPDX-License-Identifier: MIT */
#include "mod_lib.h"
#include "lz4d.h"

static unsigned u16le(const uint8_t *p) { return (unsigned)p[0] | (unsigned)p[1] << 8; }

/* Unpacked code length: from header, or from the 2-byte prefix of an ML_F_LZ4 body. */
static unsigned raw_len(const uint8_t *a) {
  unsigned code = u16le(a + 4);
  if (!(a[13] & ML_F_LZ4)) return code;
  return code < 3 ? 0 : u16le(a + MC_HDR);
}

/* Header sanity check: returns total stored image length (packed if ML_F_LZ4), or 0. */
static unsigned hdr_len(const ml_cfg_t *cfg, const uint8_t *a) {
  unsigned code = u16le(a + 4), entry = u16le(a + 6), total = MC_HDR + code + MC_TAG;
  if (a[0] != (uint8_t)cfg->magic[0] || a[1] != (uint8_t)cfg->magic[1] ||
      a[2] != (uint8_t)cfg->magic[2] || a[3] != cfg->abi || a[12] > cfg->max_level ||
      !code || entry >= raw_len(a) || (entry & 1) || total > cfg->slot_bytes)
    return 0;
  return total;
}

int ml_install(const ml_cfg_t *cfg, const uint8_t *img, const ml_flash_t *fl) {
  unsigned total = hdr_len(cfg, img), slot = img[15], off, n;
  uint32_t base;
  uint8_t back[64];
  if (!total || !(img[13] & ML_F_LIB)) return ML_BAD_HDR;
  if (slot >= cfg->slots) return ML_SLOT;
  base = ml_slot_addr(cfg, (int)slot);
  /* only erase the sectors the image needs; a shorter image leaves 0xFF behind it */
  for (off = 0; off < total; off += cfg->sector_bytes)
    if (fl->erase(base + off)) return ML_IO;
  for (off = 0; off < total; off += n) {
    n = total - off < cfg->page_bytes ? total - off : cfg->page_bytes;
    if (fl->prog(base + off, img + off, n)) return ML_IO;
  }
  for (off = 0; off < total; off += n) {
    unsigned i;
    n = total - off < sizeof back ? total - off : (unsigned)sizeof back;
    if (fl->read(base + off, back, n)) return ML_IO;
    for (i = 0; i < n; i++)
      if (back[i] != img[off + i]) return ML_VERIFY;
  }
  return (int)total;
}

int ml_info(const ml_cfg_t *cfg, int slot, const ml_flash_t *fl, ml_info_t *out) {
  uint8_t h[MC_HDR + 32], c[4 + ML_NAME], *n = h + MC_HDR;
  unsigned i, blank = 1, named;
  if (slot < 0 || slot >= cfg->slots) return ML_SLOT;
  if (fl->read(ml_slot_addr(cfg, slot), h, sizeof h)) return ML_IO;
  for (i = 0; i < MC_HDR; i++) blank &= h[i] == 0xFF;
  if (blank) return ML_EMPTY;
  if (!hdr_len(cfg, h)) return ML_BAD_HDR;
  out->code_len = (uint16_t)raw_len(h);
  out->entry = (uint16_t)u16le(h + 6);
  out->packed = (uint16_t)(h[13] & ML_F_LZ4 ? u16le(h + 4) : 0);
  out->serial = (uint32_t)h[8] | (uint32_t)h[9] << 8 |
                (uint32_t)h[10] << 16 | (uint32_t)h[11] << 24;
  out->level = h[12];
  out->flags = h[13];
  if (out->packed) {
    /* decode the 16-byte name block from the start of the LZ4 stream */
    for (i = 0; i < sizeof c; i++) c[i] = 0;
    lz4d(h + MC_HDR + 2, sizeof h - MC_HDR - 2, c, sizeof c);
    n = c;
  }
  named = !(h[13] & (ML_F_PAGE | ML_F_CARDS)) && out->code_len >= 4 + ML_NAME &&
          n[0] == (uint8_t)cfg->name_magic[0] && n[1] == (uint8_t)cfg->name_magic[1] &&
          n[2] == (uint8_t)cfg->name_magic[2] && n[3] == (uint8_t)cfg->name_magic[3];
  for (i = 0; i < ML_NAME; i++) out->name[i] = named ? (char)n[4 + i] : 0;
  return 0;
}

int ml_load(const ml_cfg_t *cfg, int slot, const ml_flash_t *fl,
            uint8_t *dst, unsigned cap, unsigned is_page) {
  ml_info_t inf;
  unsigned total, code;
  uint8_t *src;
  int r = ml_info(cfg, slot, fl, &inf);
  if (r) return r;
  if (!(inf.flags & ML_F_PAGE) != !is_page || (inf.flags & ML_F_CARDS)) return ML_BAD_HDR;
  code = inf.packed ? inf.packed : inf.code_len;
  total = MC_HDR + code + MC_TAG;
  if (total > cap) return ML_BAD_HDR;
  /* read a packed image to the top of dst so it can unpack in place down to dst */
  src = dst + (inf.packed ? cap - total : 0);
  if (fl->read(ml_slot_addr(cfg, slot), src, total)) return ML_IO;
  if (hdr_len(cfg, src) != total || src[13] != inf.flags) return ML_BAD_HDR;
  if (!cfg->tag_check || cfg->tag_check(src, code)) return ML_BAD_TAG;
  if (inf.packed && (!cfg->unpack || cfg->unpack(dst, src, cap) < 0)) return ML_BAD_HDR;
  return inf.code_len;
}
