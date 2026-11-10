/* hello: the first UV-380 RAM module (the K6's c/k6/mod/hello.c, for the M4F). It says where it
   runs, does the K6 hello's sum of squares, a hardware divide and a float sum, checks its
   scratch RAM, writes 100 ms of a 1200 Hz tone with a float oscillator to the audio path, and
   returns 0x55. Built by `python -m modem.uv380mod` (clang -fropi: code and constants reached
   PC-relative). No libraries, no writable statics.
   SPDX-License-Identifier: MIT */
#include "uv_api.h"

#define FS 48000
#define TONE_N (FS / 10)
#define BLOCK 240

static char *put(char *p, const char *s) {
    while (*s)
        *p++ = *s++;
    return p;
}

static char *hex(char *p, uint32_t v, int digits) {
    const char *x = "0123456789abcdef";
    while (digits--)
        *p++ = x[(v >> (4 * digits)) & 15];
    return p;
}

static char *dec(char *p, uint32_t v) {
    char d[10];
    int n = 0;
    do
        d[n++] = (char)('0' + v % 10);     /* udiv: the M4 has it, the K6's M0 doesn't */
    while (v /= 10);
    while (n)
        *p++ = d[--n];
    return p;
}

int uv_main(const uv380_api_t *api) {
    char line[64], *p;
    volatile uint32_t acc = 0, seven = 7;
    volatile float f = 0;
    uint32_t i, bytes;
    p = put(line, "hello world from RAM at 0x");
    p = hex(p, (uint32_t)(uintptr_t)&uv_main, 8);
    *p = 0;
    api->say(line);

    for (i = 1; i <= 10000; i++)
        acc += i * i;                      /* 333383335000 mod 2^32 */
    for (i = 1; i <= 100; i++)
        f += 0.5f * (float)i;              /* 2525 */
    p = put(line, "squares 0x");
    p = hex(p, acc, 8);
    p = put(p, " div ");
    p = dec(p, 1000000u / seven);
    p = put(p, " float ");
    p = dec(p, (uint32_t)f);
    *p = 0;
    api->say(line);

    uint32_t *s = (uint32_t *)api->scratch(&bytes), bad = 0;
    for (i = 0; i < bytes / 4; i++)
        s[i] = i * 2654435761u;
    for (i = 0; i < bytes / 4; i++)
        bad += s[i] != i * 2654435761u;
    p = put(line, "scratch ");
    p = dec(p, bytes);
    p = put(p, bad ? " BAD" : " ok");
    *p = 0;
    api->say(line);

    /* y[n] = 2 cos(w) y[n-1] - y[n-2], w = 2 pi 1200 / 48000 */
    const float c2 = 2.0f * 0.98768834f;
    float y1 = 0.0f, y2 = -0.15643447f;
    int16_t buf[BLOCK];
    api->led(1, 0);
    api->audio_start(FS);
    for (uint32_t n = 0; n < TONE_N; n += BLOCK) {
        for (i = 0; i < BLOCK; i++) {
            float y = c2 * y1 - y2;
            y2 = y1, y1 = y;
            buf[i] = (int16_t)(y * 16000.0f);
        }
        api->audio_write(buf, BLOCK);
    }
    api->audio_stop();
    p = put(line, "tone 1200 Hz ");
    p = dec(p, TONE_N);
    p = put(p, " samples in ");
    p = dec(p, api->ms());
    p = put(p, " ms");
    *p = 0;
    api->say(line);
    return 0x55;
}
