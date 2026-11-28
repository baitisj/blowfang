/* scrgrab: the UV-380's screen on USB serial, in the shared screen-grab lines (c/common/scrgrab.h:
   RGB565 rows, 8-row bands packed with LZ4, CRC-32), from OpenRTX's framebuffer (ortx_mod.h
   screen, level 6): what the UI drew last, which the panel is sent from. About 40 KB of pixels;
   the bands pack to a few KB. tools/screen_grab.py turns the "mod say scr ..." lines into a PNG.

     python3 -m modem.uv380mod firmware/uv380-openrtx/mod/scrgrab.c scrgrab.u3r --key-file KEY
     python3 tools/uv380_i2sprobe.py mod /dev/ttyACM0 scrgrab.u3r > grab.log
     python3 tools/screen_grab.py grab.log -o uv380.png

   The UI thread may be drawing while this reads: run it twice if a grab looks torn.
   SPDX-License-Identifier: MIT */
#define UV_MODULE_LEVEL 6
#define SCR_HEX_LINE 128                  /* say() takes 200 characters */
#include "ortx_mod.h"
#include "../../../c/common/scrgrab.c"

#define SG_BAND 8

int uv_main(const ortx_api_t *api) {
  unsigned w = 0, h = 0;
  uint32_t room = 0;
  const uint16_t *fb = api->level >= 6 ? api->screen(&w, &h) : 0;
  if (!fb || !w || !h) {
    api->say("scrgrab: this image has no framebuffer call (level 6)");
    return -1;
  }
  uint8_t *ram = (uint8_t *)api->scratch(&room);
  unsigned rb = 2 * w;
  uint8_t *row = ram;
  uint8_t *band = row + rb;
  uint8_t *pack = band + SG_BAND * rb;
  uint16_t *tab = (uint16_t *)(pack + SG_BAND * rb);
  if (!ram || room < (uint32_t)((uint8_t *)(tab + LZ4C_TAB) - ram)) {
    api->say("scrgrab: not enough scratch");
    return -2;
  }
  scr_t s;
  s.say = api->say;
  s.band = band;
  s.pack = pack;
  s.tab = tab;
  scr_begin(&s, (int)w, (int)h, SCR_RGB565, rb, SG_BAND);
  for (unsigned y = 0; y < h; y++) {
    for (unsigned x = 0; x < w; x++) {
      uint16_t c = fb[y * w + x];
      row[2 * x] = (uint8_t)(c >> 8);       /* big-endian, as the format (and an ST77xx) has it */
      row[2 * x + 1] = (uint8_t)c;
    }
    scr_row(&s, row);
  }
  scr_end(&s);
  return (int)h;
}
