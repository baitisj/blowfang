/* Host front end for m17_rx, for tests/test_uv380_m17rx.py:
 *   m17_rx_host IN.raw [invert]   signed 16-bit LE audio at 8 kHz; one line a frame:
 *   f AT KIND ok=0|1 sc=SCORE g=GAIN m=METRIC DATA-HEX [src=CALL dst=CALL]
 *   end frames=N lost=N
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "m17_rx.h"

static m17_rx r;

int main(int argc, char **argv) {
    static const char *kinds[] = {"lsf", "stream", "packet", "bert", "eot"};
    static const unsigned len[] = {30, 18, 26, 25, 0};
    if (argc < 2) {
        fprintf(stderr, "usage: %s IN.raw [invert]\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f)
        return 1;
    int inv = argc > 2 && strcmp(argv[2], "invert") == 0;
    m17_init(&r);
    int16_t buf[256];
    size_t got;
    m17_frame fr;
    while ((got = fread(buf, 2, 256, f)) != 0) {
        for (size_t i = 0; i < got; i++) {
            int16_t s = inv ? (int16_t)(buf[i] == -32768 ? 32767 : -buf[i]) : buf[i];
            if (m17_push(&r, s, &fr)) {
                printf("f %lu %s ok=%d sc=%.3f g=%.1f m=%.3f ", (unsigned long)fr.at, kinds[fr.kind], fr.ok,
                       (double)fr.score, (double)fr.gain, (double)fr.metric);
                for (unsigned k = 0; k < len[fr.kind]; k++)
                    printf("%02x", fr.data[k]);
                if (fr.kind == 0) {
                    char a[10], b[10];
                    m17_callsign(fr.data + 6, a);
                    m17_callsign(fr.data, b);
                    printf(" src=%s dst=%s", a, b);
                }
                printf("\n");
            }
        }
    }
    printf("end frames=%lu lost=%lu\n", (unsigned long)r.frames, (unsigned long)r.lost);
    fclose(f);
    return 0;
}
