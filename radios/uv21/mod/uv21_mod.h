/* uv21_mod.h: what a UV-21 Pro RAM module gets (radios/uv21/fw/src/umod.rs).
   A module is C built by modem/uv21mod.py (clang, Cortex-M4 without FPU, -fropi: no writable
   statics), uploaded over the cable while SURF runs, run once, and dropped. It returns an int the
   console prints ("um end rrrrrrrr"); its lines go out with api->say.
   Every register write passes the firmware's receive-only filter. SPDX-License-Identifier: MIT */
#ifndef UV21_MOD_H
#define UV21_MOD_H
#include <stdint.h>
#include "../../../c/common/radio_api.h"

#define UM_ABI 2
#define UM_LEVEL 1

typedef struct um_api {
  /* The shared BK48xx/FD6818 prefix (c/common/radio_api.h), UM_ABI 2:
       abi, level   UM_ABI, UM_LEVEL
       say          a line on the serial console
       ticks        uptime in 100 us ticks, 32 bits (the K6's are 16: portable code masks)
       fd_read      BK4829 register
       fd_write     always 0: the transmit state, PA bias and TX GPIOs are dropped from the write
       delay_us     busy wait (see um_wait_us below for waits over 279 ms)
       tune         receive on hz, the front end's band with it
       rx_hz        SURF's centre, where it listens again once the module returns
       ms           ms since boot */
  RADIO_API_PREFIX_FIELDS;
  int (*front_end)(int step);                   /* the receive band switch's mode (0 asks) */
} um_api_t;

/* the entry: ram is the zeroed rest of the 12 KB arena, 8-aligned */
int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len);

/* the level the chip reads, dBm x 2 (REG_67: raw / 2 - 174) */
static inline int um_rssi2(const um_api_t *api) {
  return radio_rssi2((const radio_api_t *)api);
}

/* api->delay_us loads SysTick's 24-bit reload once, so a single call waits at most 2^24 cycles
   (about 139 ms at the port's 120 MHz, 279 ms at 60) and a longer one wraps to a short wait; this
   one waits in 100 ms pieces (found 2026-11-25: 1 s waits in fmstock ran 161 ms) */
static inline void um_wait_us(const um_api_t *api, uint32_t us) {
  for (; us > 100000u; us -= 100000u) api->delay_us(100000u);
  api->delay_us(us);
}

/* text without a libc */
static inline char *um_put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  *p = 0;
  return p;
}

static inline char *um_hex(char *p, uint32_t v, int digits) {
  while (digits--) *p++ = "0123456789abcdef"[(v >> (4 * digits)) & 15];
  *p = 0;
  return p;
}

static inline char *um_int(char *p, int32_t v) {
  char t[12];
  int n = 0;
  uint32_t u = v < 0 ? (uint32_t)-v : (uint32_t)v;
  if (v < 0) *p++ = '-';
  do t[n++] = (char)('0' + u % 10); while ((u /= 10) && n < 11);
  while (n) *p++ = t[--n];
  *p = 0;
  return p;
}

/* half-dB units as "-87.5" */
static inline char *um_half_db(char *p, int v2) {
  if (v2 < 0) *p++ = '-', v2 = -v2;
  p = um_int(p, v2 / 2);
  *p++ = '.';
  *p++ = v2 & 1 ? '5' : '0';
  *p = 0;
  return p;
}

#endif
