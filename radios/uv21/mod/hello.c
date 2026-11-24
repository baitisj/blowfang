/* hello: the smallest UV-21 module. Says hello and the RF chip's ID, returns it. MIT */
#include "uv21_mod.h"

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  char line[40];
  char *p = um_put(line, "hello uv21 rf=");
  uint16_t id = api->fd_read(0x00);
  p = um_hex(p, id, 4);
  p = um_put(p, " ram=");
  um_hex(p, ram_len, 4);
  (void)ram;
  api->say(line);
  return id;
}
