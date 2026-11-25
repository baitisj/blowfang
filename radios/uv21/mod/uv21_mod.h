/* uv21_mod.h: what a UV-21 Pro RAM module gets (radios/uv21/fw/src/umod.rs).
   A module is C built by modem/uv21mod.py (clang, Cortex-M4 without FPU, -fropi: no writable
   statics), uploaded over the cable while SURF runs, run once, and dropped. It returns an int the
   console prints ("um end rrrrrrrr"); its lines go out with api->say.
   Every register write passes the firmware's receive-only filter. SPDX-License-Identifier: MIT */
#ifndef UV21_MOD_H
#define UV21_MOD_H
#include <stdint.h>

#define UM_ABI 1

typedef struct {
  uint32_t abi;                                 /* UM_ABI */
  uint16_t (*fd_read)(uint8_t reg);             /* BK4829 register */
  void (*fd_write)(uint8_t reg, uint16_t v);    /* (transmit state, PA bias and TX GPIOs dropped) */
  void (*tune)(uint32_t hz, int wide);          /* receive on hz, the front end's band with it */
  void (*delay_us)(uint32_t us);
  uint32_t (*ms)(void);
  void (*say)(const char *line);                /* a line on the serial console */
  int (*front_end)(int step);                   /* the receive band switch's mode (0 asks) */
} um_api_t;

/* the entry: ram is the zeroed rest of the 4 KB block, 8-aligned */
int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len);

/* the level the chip reads, dBm x 2 (REG_67: raw / 2 - 174) */
static inline int um_rssi2(const um_api_t *api) {
  return (int)(api->fd_read(0x67) & 0x1FF) - 348;
}

/* api->delay_us loads SysTick's 24-bit reload once, so at 60 MHz a single call waits at most
   2^24 cycles (about 279 ms) and a longer one wraps to a short wait; this one waits in 100 ms
   pieces (found 2026-11-25: 1 s waits in fmstock ran 161 ms) */
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
