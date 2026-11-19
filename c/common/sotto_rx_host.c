/* Host front end for sotto_rx, for tests/test_uv380_sottorx.py:
 *   sotto_rx_host m17|ysf IN.raw [invert]   signed 16-bit LE audio at 8 kHz, through m17_rx or
 *   ysf_rx and sotto_rx; a message is handed over when it ends (the last frame, an EOT, a
 *   terminator) or at the end of the input:
 *   msg n=N got=G missing=M bad=B over=O ended=0|1 HEX     the message after srx_conceal
 *   end frames=N
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <string.h>

#include "sotto_rx.h"

#define MAXSF 1000u

static union {
    m17_rx m;
    ysf_rx y;
} rx;
static uint8_t frames[MAXSF * SRX_SF], have[MAXSF / 8u + 1u];

static void emit(srx_state *s) {
    if (!s->got)
        return;
    unsigned miss = srx_conceal(s);
    printf("msg n=%u got=%u missing=%u bad=%u over=%u ended=%d ", s->n, s->got, miss, s->bad, s->over, s->ended);
    for (unsigned i = 0; i < s->n * SRX_SF; i++)
        printf("%02x", frames[i]);
    printf("\n");
    srx_init(s, frames, have, MAXSF);
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s m17|ysf IN.raw [invert]\n", argv[0]);
        return 2;
    }
    int ysf = strcmp(argv[1], "ysf") == 0;
    FILE *f = fopen(argv[2], "rb");
    if (!f)
        return 1;
    int inv = argc > 3 && strcmp(argv[3], "invert") == 0;
    srx_state s;
    srx_init(&s, frames, have, MAXSF);
    if (ysf)
        ysf_init(&rx.y);
    else
        m17_init(&rx.m);
    int16_t buf[256];
    size_t got;
    unsigned long nf = 0;
    m17_frame mf;
    ysf_frame yf;
    while ((got = fread(buf, 2, 256, f)) != 0) {
        for (size_t i = 0; i < got; i++) {
            int16_t v = inv ? (int16_t)(buf[i] == -32768 ? 32767 : -buf[i]) : buf[i];
            if (ysf ? ysf_push(&rx.y, v, &yf) : m17_push(&rx.m, v, &mf)) {
                nf++;
                if (ysf)
                    srx_ysf(&s, &yf);
                else
                    srx_m17(&s, &mf);
                if (s.ended)
                    emit(&s);
            }
        }
    }
    emit(&s);
    printf("end frames=%lu\n", nf);
    fclose(f);
    return 0;
}
