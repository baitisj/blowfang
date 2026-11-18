/* tests/c6m17rx_spk_host.c: c6m17rx's frame and speaker logic (report(), speaker()) on the host, fed a
   tap capture (s16 LE, 8 kHz, as the radio reads it), with a model of OpenRTX's Codec2 decoder:
   a 4-frame queue that takes one frame (or plays silence) every 20 ms once started. The C6000
   and AT1846S code is compiled but never called. Lines: the module's say lines, "T silent" for
   each 20 ms the decoder had nothing ("T underrun" when that happened mid-call, the module not
   yet ending it), "end pushes= played= silent_while_on= underruns=".
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#define uv_main static __attribute__((unused)) uv_main_host
#include "c6m17rx.c"

static uint32_t now_ms;
static int on, q, played, silent, pushes, underruns;
static m17_mod m;
static uint32_t next_take;
static void say(const char *s) { printf("%lu %s\n", (unsigned long)now_ms, s); }
static int fmtf(char *b, unsigned n, const char *f, ...) { va_list a; va_start(a, f); int r = vsnprintf(b, n, f, a); va_end(a); return r; }
static uint32_t ms(void) { return now_ms; }
static void *mcpy(void *d, const void *s, unsigned n) { return memcpy(d, s, n); }
static int c2s(void) { on = 1; q = 0; next_take = now_ms + 20; return 0; }
static int c2p(const uint8_t *f) { (void)f; if (!on || q >= 4) return -1; q++; pushes++; return 0; }
static void c2x(void) { while (q) { q--; played++; } on = 0; }
static void tick(void) {          /* the decoder: one frame (or silence) every 20 ms */
    while (on && now_ms >= next_take) {
        if (q) { q--; played++; } else {
            silent++;
            underruns += m.spk == 2;
            printf("%lu %s\n", (unsigned long)now_ms, m.spk == 2 ? "underrun" : "silent");
        }
        next_take += 20;
    }
}

int main(int argc, char **argv) {
    ortx_api_t api;
    memset(&api, 0, sizeof api);
    api.level = 5; api.say = say; api.fmt = fmtf; api.ms = ms; api.memcpy = mcpy;
    api.c2_start = c2s; api.c2_push = c2p; api.c2_stop = c2x;
    m17_init(&m.rx);
    m.spk = 0;
    FILE *f = fopen(argv[1], "rb");
    int16_t half[256];
    while (fread(half, 2, 256, f) == 256) {        /* a half every 32 ms, as the radio takes them */
        for (unsigned i = 0; i < 256; i++)
            if (m17_push(&m.rx, half[i], &m.fr))
                report(&api, &m);
        for (unsigned k = 0; k < 32; k++) {        /* the module loop: 1 ms sleeps */
            if (m.spk > 0)
                speaker(&api, &m);
            now_ms++;
            tick();
        }
    }
    printf("end pushes=%d played=%d silent_while_on=%d underruns=%d\n", pushes, played, silent, underruns);
    return 0;
}
