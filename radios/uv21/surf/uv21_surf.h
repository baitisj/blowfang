/* uv21_surf.h: what the UV-21 firmware gives SURF (radios/uv21/surf/uv21_surf.c).
   The firmware (Rust, radios/uv21/fw/src/surf.rs) fills one of these and calls uv21_surf();
   a simulator can fill it instead. SPDX-License-Identifier: MIT */
#ifndef UV21_SURF_H
#define UV21_SURF_H
#include <stdint.h>

/* key codes uv_api_t.key returns: digits, '*', '#', and
   'U' / 'D' up / down (on the press, then repeats while held), 'M' MENU (the ring's centre),
   'X' EXIT, 'V' VFO/MR, 'W' A/B, 'A' the top side key, 'N' the middle one, 'B' the bottom one;
   a long press of a key that has one is its code | 0x80 */
#define UV_LONG 0x80

typedef struct {
  uint16_t (*fd_read)(uint8_t reg);             /* BK4829 register */
  void (*fd_write)(uint8_t reg, uint16_t v);    /* (the firmware drops any transmit state) */
  void (*tune)(uint32_t hz, int wide);          /* receive on hz, the front end's band with it */
  void (*delay_us)(uint32_t us);
  uint32_t (*ms)(void);
  int (*key)(void);                             /* the next key event, 0 for none */
  void (*speaker)(int on);
  void (*say)(const char *line);                /* a line on the serial console */
  void (*fill)(int x, int y, int w, int h, uint16_t rgb565);
  /* text at x, top row y: font 0 the 5x5 subpixel caps (4 px a character, 5 rows), 1 the
     6x10; returns its width in pixels */
  int (*text)(int x, int y, const char *s, uint16_t fg, uint16_t bg, int font);
  int (*poll)(void);                            /* the firmware's housekeeping; nonzero: leave */
  void (*wake)(uint32_t hz, int dbm, int floor);   /* the CW wake heard */
  /* the receive front end's way of switching bands (port 12, while its polarity is unknown):
     steps it by `step` (0 asks) and returns the one in use, 0 to 3 (fw board/rxpath.rs) */
  int (*front_end)(int step);
  uint32_t wake_hz;                             /* where the wake beacon is (0: no wake check) */
  uint32_t start_hz;                            /* where SURF starts */
} uv_api_t;

/* SURF until EXIT (or poll asks); returns where the middle (the listening frequency) was */
uint32_t uv21_surf(uv_api_t *api, void *state, uint32_t room);

/* the state uv21_surf needs in `state` (the caller's RAM, 8-aligned) */
uint32_t uv21_surf_state_size(void);

#endif
