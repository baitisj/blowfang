/* k6_lib: the K6's SPI module library (thin wrapper over c/common/mod_lib.c).
 * SPDX-License-Identifier: MIT */
#include "k6_lib.h"

static const ml_cfg_t K6_CFG = {
  KL_BASE,
  KL_SLOT_BYTES,
  KL_SECTOR,
  KL_PAGE,
  KL_SLOTS,
  {'K', '6', 'M'},
  KM_ABI,
  KM_LEVEL,
  {'K', '6', 'N', 'M'},
  km_tag_check,
  km_unpack
};

int kl_install(const uint8_t *img, const kl_flash_t *fl) {
  return ml_install(&K6_CFG, img, fl);
}

int kl_info(int slot, const kl_flash_t *fl, kl_info_t *out) {
  return ml_info(&K6_CFG, slot, fl, out);
}

int kl_load(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap) {
  int r = ml_load(&K6_CFG, slot, fl, dst, cap, 0);
  if (r > 0) km_arm(dst, 0);
  return r;
}

int kl_page(int slot, const kl_flash_t *fl, uint8_t *dst, unsigned cap) {
  return ml_load(&K6_CFG, slot, fl, dst, cap, 1);
}
