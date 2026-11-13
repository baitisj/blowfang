/* hello: the first RAM module for the UV-380's OpenRTX image (ortx_mod.h). It says where it runs,
   does the K6 hello's sum of squares, a hardware divide and a float sum, checks its scratch RAM,
   sleeps 20 ms (other threads run), reads the HR_C6000's register E0 and the RSSI, and returns
   0x55. Ctrl-C (or PTT) during the sleep ends it early with 0x5c.
   Built by `python -m modem.uv380mod` (clang -fropi). No libraries, no writable statics.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

int uv_main(const ortx_api_t *api) {
    char line[96];
    volatile uint32_t acc = 0, seven = 7;
    volatile float f = 0;
    uint32_t i, bytes;
    api->fmt(line, sizeof line, "hello world from RAM at 0x%08lx", (unsigned long)(uintptr_t)&uv_main);
    api->say(line);

    for (i = 1; i <= 10000; i++)
        acc += i * i;                       /* 333383335000 mod 2^32 */
    for (i = 1; i <= 100; i++)
        f += 0.5f * (float)i;               /* 2525 */
    api->fmt(line, sizeof line, "squares 0x%08lx div %lu float %lu", (unsigned long)acc,
             (unsigned long)(1000000u / seven), (unsigned long)f);
    api->say(line);

    uint32_t *s = (uint32_t *)api->scratch(&bytes), bad = 0;
    for (i = 0; i < bytes / 4; i++)
        s[i] = i * 2654435761u;
    for (i = 0; i < bytes / 4; i++)
        bad += s[i] != i * 2654435761u;
    api->fmt(line, sizeof line, "scratch %lu %s", (unsigned long)bytes, bad ? "BAD" : "ok");
    api->say(line);

    for (i = 0; i < 4; i++) {
        if (api->stop())
            return 0x5c;
        api->sleep_ms(5);
    }
    api->fmt(line, sizeof line, "c6000 E0=%02x rssi %ld dBm after %lu ms", api->c6000_read(0xE0),
             (long)api->rssi(), (unsigned long)api->ms());
    api->say(line);
    return 0x55;
}
