/* Host front end for ysf_echo, for tests/test_uv380_ysfecho.py:
 *   ysf_echo_host IN.raw [invert]   signed 16-bit LE audio at 8 kHz through ysf_rx and ysf_echo
 *   call n= dropped= hdr= term= voice= other= gaps= ended= src=   then one line of hex a frame
 * A call ends at its terminator, or at the end of the input. SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ysf_echo.h"

#define MAXF 200u

static ysf_rx rx;
static uint8_t frames[MAXF * YE_FRAME];

static void emit(ye_state *e) {
    if (!e->n)
        return;
    printf("call n=%u dropped=%u hdr=%u term=%u voice=%u other=%u gaps=%u ended=%d src=%s\n", e->n, e->dropped,
           e->hdr, e->term, e->voice, e->other, e->gaps, e->ended, e->src);
    for (unsigned k = 0; k < e->n; k++) {
        for (unsigned i = 0; i < YE_FRAME; i++)
            printf("%02x", e->frames[k * YE_FRAME + i]);
        printf("\n");
    }
    ye_init(e, frames, MAXF);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s IN.raw [invert] [max]\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f)
        return 1;
    int inv = argc > 2 && strcmp(argv[2], "invert") == 0;
    unsigned max = argc > 3 ? (unsigned)atoi(argv[3]) : MAXF;
    if (max > MAXF)
        max = MAXF;
    ye_state e;
    ye_init(&e, frames, max);
    ysf_init(&rx);
    int16_t buf[256];
    size_t got;
    ysf_frame yf;
    while ((got = fread(buf, 2, 256, f)) != 0) {
        for (size_t i = 0; i < got; i++) {
            int16_t v = inv ? (int16_t)(buf[i] == -32768 ? 32767 : -buf[i]) : buf[i];
            if (ysf_push(&rx, v, &yf)) {
                ye_put(&e, &rx, &yf);
                if (e.ended) {
                    emit(&e);
                    e.max = max;
                }
            }
        }
    }
    emit(&e);
    fclose(f);
    return 0;
}
