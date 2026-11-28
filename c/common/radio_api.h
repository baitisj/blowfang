/* radio_api.h: shared C radio-API prefix for BK48xx / FD6818 RAM modules.
 *
 * Both the Baofeng K6 (c/k6/k6_mod.h: k6_api_t) and the UV-21 Pro (radios/uv21/mod/uv21_mod.h:
 * um_api_t) begin with RADIO_API_PREFIX_FIELDS in the exact same field order and types, so
 * portable BK48xx / FD6818 helper routines can accept `const radio_api_t *` and run unchanged
 * on either radio.
 *
 * K6's existing ABI-1 binary layout is preserved verbatim (zero breakage for signed K6 modules).
 * UV-21 Pro adopts this prefix at UM_ABI 2.
 *
 * What portable code may count on, on both radios:
 *   abi, level  each radio's own numbers (K6: KM_ABI 1, KM_LEVEL 17; UV-21: UM_ABI 2, UM_LEVEL 1).
 *               A level on one radio says nothing about the other, so portable code doesn't test
 *               it: the radio's own module builder already refuses a module its image can't run.
 *   ticks       100 us ticks. The K6 gives 16 bits (wraps every 6.5536 s), the UV-21 32: portable
 *               code takes (uint16_t)(t1 - t0) and measures under 6.5 s with it (ms for longer).
 *   fd_write    receive only on both. The K6 refuses its state, power, PA and band registers
 *               (0x30 0x33 0x36 0x37) and returns -1; the UV-21's driver masks the transmitter's
 *               enables out of every write and returns 0. Portable code doesn't rely on the -1.
 *   tune        receive on hz, the band switch with it; the way to move.
 *   rx_hz       the frequency the radio listens on when the module returns (the K6's pager
 *               frequency, the UV-21's SURF centre); tune doesn't change it.
 *   ms          ms since boot (UV-21) or module start (K6): only differences mean anything.
 *
 * SPDX-License-Identifier: MIT */
#ifndef RADIO_API_H
#define RADIO_API_H
#include <stdint.h>

#define RADIO_API_PREFIX_FIELDS                              \
  uint16_t abi, level;                                       \
  void (*say)(const char *s);                                \
  uint32_t (*ticks)(void);                                   \
  uint16_t (*fd_read)(uint8_t reg);                          \
  int (*fd_write)(uint8_t reg, uint16_t value);              \
  void (*delay_us)(uint32_t us);                             \
  void (*tune)(uint32_t hz, int wide);                       \
  uint32_t (*rx_hz)(void);                                   \
  uint32_t (*ms)(void)

typedef struct radio_api {
  RADIO_API_PREFIX_FIELDS;
} radio_api_t;

/* REG 0x67 bits 8:0 = RSSI in half-dB steps above -174 dBm (BK4829 / FD6818) */
static inline int radio_rssi2(const radio_api_t *api) {
  return (int)(api->fd_read(0x67) & 0x1FF) - 348;
}

#endif
