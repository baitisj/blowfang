/* apiprobe: the shared module API head (c/common/radio_api.h) on the UV-21, through a
   const radio_api_t * as a portable helper would take it. One line:
     api 2/1 hz=HOME moved=D t16=N ms=N rssi=R        (under the console's 64 characters)
   abi/level, HOME rx_hz before the module tunes, D how far rx_hz moved after it tunes 100 kHz up
   (0: tune doesn't move it), t16 and ms the 100 us ticks and ms over a 100 ms wait (about 1000 and
   100), rssi the level back on home. Returns 0x55 when abi, home and away, t16 and ms agree, else
   -1. The radio listens on home again after (SURF starts on its centre). MIT */
#include "uv21_mod.h"

static int probe(const radio_api_t *r, char *line) {
  uint32_t home = r->rx_hz();
  r->tune(home + 100000u, 1);
  uint32_t away = r->rx_hz();
  r->tune(home, 1);
  uint32_t t0 = r->ticks(), m0 = r->ms();
  for (int i = 0; i < 4; i++) r->delay_us(25000u);
  uint32_t t16 = (uint16_t)(r->ticks() - t0), dms = r->ms() - m0;
  int rssi2 = radio_rssi2(r);
  char *p = um_put(line, "api ");
  p = um_int(p, r->abi);
  p = um_put(p, "/");
  p = um_int(p, r->level);
  p = um_put(p, " hz=");
  p = um_int(p, (int32_t)home);
  p = um_put(p, " moved=");
  p = um_int(p, (int32_t)(away - home));
  p = um_put(p, " t16=");
  p = um_int(p, (int32_t)t16);
  p = um_put(p, " ms=");
  p = um_int(p, (int32_t)dms);
  p = um_put(p, " rssi=");
  um_half_db(p, rssi2);
  r->say(line);
  return r->abi == UM_ABI && home && away == home && t16 >= 900 && t16 <= 1100 && dms >= 90 &&
                 dms <= 110
             ? 0x55
             : -1;
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  char line[64];
  (void)ram;
  (void)ram_len;
  return probe((const radio_api_t *)api, line);
}
