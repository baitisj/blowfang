/* k6_lib: the K6's SPI module library (see k6_lib.h). SPDX-License-Identifier: MIT */
#include "k6_lib.h"
#include "lz4d.h"

static unsigned u16le(const uint8_t *p) { return (unsigned)p[0] | (unsigned)p[1] << 8; }

/* the code length unpacked: the header's, or a packed image's (KM_F_LZ4) from its body */
static unsigned raw_len(const uint8_t *a) {
  unsigned code = u16le(a + 4);
  if (!(a[13] & KM_F_LZ4)) return code;
  return code < 3 ? 0 : u16le(a + KM_HDR);
}

/* the header's own checks (k6_mod.c check() without the transfer): the image's length as it
   is kept (packed, for a packed one), or 0 */
static unsigned hdr_len(const uint8_t *a) {
  unsigned code = u16le(a + 4), entry = u16le(a + 6), total = KM_HDR + code + KM_TAG;
  if (a[0] != 'K' || a[1] != '6' || a[2] != 'M' || a[3] != KM_ABI || a[12] > KM_LEVEL || !code ||
      entry >= raw_len(a) || (entry & 1) || total > KL_SLOT_BYTES)
    return 0;
  return total;
}

int kl_install(const uint8_t *img, const kl_flash_t *fl) {
  unsigned total = hdr_len(img), slot = img[15], off, n;
  uint32_t base;
  uint8_t back[64];
  if (!total || !(img[13] & KM_F_LIB)) return KL_BAD_HDR;
  if (slot >= KL_SLOTS) return KL_SLOT;
  base = KL_SLOT_ADDR(slot);
  /* only the sectors the image needs; a shorter image leaves 0xFF behind it in the last one */
  for (off = 0; off < total; off += KL_SECTOR)
    if (fl->erase(base + off)) return KL_IO;
  for (off = 0; off < total; off += n) {
    n = total - off < KL_PAGE ? total - off : KL_PAGE;
    if (fl->prog(base + off, img + off, n)) return KL_IO;
  }
  for (off = 0; off < total; off += n) {
    unsigned i;
    n = total - off < sizeof back ? total - off : (unsigned)sizeof back;
    if (fl->read(base + off, back, n)) return KL_IO;
    for (i = 0; i < n; i++)
      if (back[i] != img[off + i]) return KL_VERIFY;
  }
  return (int)total;
}

int kl_info(int slot, const kl_flash_t *fl, kl_info_t *out) {
  /* the header and the code's first bytes: the name block, or the packed bytes it decodes from */
  uint8_t h[KM_HDR + 32], c[4 + KL_NAME], *n = h + KM_HDR;
  unsigned i, blank = 1, named;
  if (slot < 0 || slot >= KL_SLOTS) return KL_SLOT;
  if (fl->read(KL_SLOT_ADDR(slot), h, sizeof h)) return KL_IO;
  for (i = 0; i < KM_HDR; i++) blank &= h[i] == 0xFF;
  if (blank) return KL_EMPTY;
  if (!hdr_len(h)) return KL_BAD_HDR;
  out->code_len = (uint16_t)raw_len(h), out->entry = (uint16_t)u16le(h + 6);
  out->packed = (uint16_t)(h[13] & KM_F_LZ4 ? u16le(h + 4) : 0);
  out->serial = (uint32_t)h[8] | (uint32_t)h[9] << 8 | (uint32_t)h[10] << 16 | (uint32_t)h[11] << 24;
  out->level = h[12], out->flags = h[13];
  if (out->packed) {
    /* the name's 16 bytes decode from the block's first ones (lz4d keeps what it wrote) */
    for (i = 0; i < sizeof c; i++) c[i] = 0;
    lz4d(h + KM_HDR + 2, sizeof h - KM_HDR - 2, c, sizeof c);
    n = c;
  }
  /* the name block: a module's (not a page's or a deck's), whole inside its code */
  named = !(h[13] & (KM_F_PAGE | KM_F_CARDS)) && out->code_len >= 4 + KL_NAME && n[0] == 'K' &&
          n[1] == '6' && n[2] == 'N' && n[3] == 'M';
  for (i = 0; i < KL_NAME; i++) out->name[i] = named ? (char)n[4 + i] : 0;
  return 0;
}

/* read slot n into dst and check its header and tag: the code length, or KL_* */
static int load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap, unsigned page) {
  kl_info_t inf;
  unsigned total, code;
  uint8_t *src;
  int r = kl_info(slot, fl, &inf);
  if (r) return r;
  if (!(inf.flags & KM_F_PAGE) != !page || (inf.flags & KM_F_CARDS)) return KL_BAD_HDR;
  code = inf.packed ? inf.packed : inf.code_len;
  total = KM_HDR + code + KM_TAG;
  if (total > cap) return KL_BAD_HDR;
  /* a packed image is read to the top of the RAM given, and unpacks from there down to dst */
  src = dst + (inf.packed ? cap - total : 0);
  if (fl->read(KL_SLOT_ADDR(slot), src, total)) return KL_IO;
  if (hdr_len(src) != total || src[13] != inf.flags) return KL_BAD_HDR;   /* changed between the two reads */
  if (km_tag_check(src, code)) return KL_BAD_TAG;
  if (inf.packed && km_unpack(dst, src, cap) < 0) return KL_BAD_HDR;
  return inf.code_len;
}

int kl_load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap) {
  int r = load(slot, fl, dst, cap, 0);
  if (r > 0) km_arm(dst, 0);
  return r;
}

int kl_page(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap) {
  return load(slot, fl, dst, cap, 1);
}
