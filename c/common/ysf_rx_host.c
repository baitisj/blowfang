/* Host front end for ysf_rx, for tests/test_uv380_ysfrx.py:
 *   ysf_rx_host IN.raw [invert]   signed 16-bit LE audio at 8 kHz; one line a frame:
 *   f AT fi=FI dt=DT fn=FN/FT sotto=0|1 fich=0|1 sc=SCORE g=GAIN m=METRIC ok=HEX [DATA-HEX]
 *   end frames=N lost=N bad=N
 *   ysf_rx_host --sizeof          the receiver's and a frame's bytes (a module keeps both in scratch)
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <string.h>

#include "ysf_rx.h"

static ysf_rx r;

int main(int argc, char **argv) {
    static const unsigned len[] = {0, 40, 40, 30};
    if (argc < 2) {
        fprintf(stderr, "usage: %s IN.raw [invert]\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "--sizeof") == 0) {
        printf("ysf_rx=%u ysf_frame=%u\n", (unsigned)sizeof(ysf_rx), (unsigned)sizeof(ysf_frame));
        return 0;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f)
        return 1;
    int inv = argc > 2 && strcmp(argv[2], "invert") == 0;
    ysf_init(&r);
    int16_t buf[256];
    size_t got;
    ysf_frame fr;
    while ((got = fread(buf, 2, 256, f)) != 0) {
        for (size_t i = 0; i < got; i++) {
            int16_t s = inv ? (int16_t)(buf[i] == -32768 ? 32767 : -buf[i]) : buf[i];
            if (ysf_push(&r, s, &fr)) {
                printf("f %lu fi=%u dt=%u fn=%u/%u sotto=%u fich=%d sc=%.3f g=%.1f m=%.3f ok=%x ",
                       (unsigned long)fr.at, fr.fi, fr.dt, fr.fn, fr.ft, fr.sotto, fr.fich_ok,
                       (double)fr.score, (double)fr.gain, (double)fr.metric, fr.ok);
                for (unsigned k = 0; k < len[fr.pay]; k++)
                    printf("%02x", fr.data[k]);
                printf("\n");
            }
        }
    }
    printf("end frames=%lu lost=%lu bad=%lu\n", (unsigned long)r.frames, (unsigned long)r.lost,
           (unsigned long)r.bad);
    fclose(f);
    return 0;
}
