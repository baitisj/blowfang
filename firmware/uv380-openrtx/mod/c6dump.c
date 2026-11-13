/* c6dump: every HR_C6000 configuration register (00-FF), 16 a line, then the RSSI. Reading only:
   a picture of the codec's set-up (audio routing, I2S) to compare between modes.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

int uv_main(const ortx_api_t *api) {
    char line[80];
    for (unsigned r = 0; r < 256; r += 16) {
        int n = api->fmt(line, sizeof line, "c6 %02x:", r);
        for (unsigned i = 0; i < 16; i++)
            n += api->fmt(line + n, sizeof line - n, " %02x", api->c6000_read((uint8_t)(r + i)));
        api->say(line);
    }
    api->fmt(line, sizeof line, "rssi %ld dBm", (long)api->rssi());
    api->say(line);
    return 0;
}
