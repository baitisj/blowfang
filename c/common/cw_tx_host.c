/* cw_tx_host TEXT OUT.raw [fs] [wpm] [tone] [level]: Morse ident as signed 16-bit LE samples.
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>

#include "cw_tx.h"

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s TEXT OUT.raw [fs] [wpm] [tone] [level]\n", argv[0]);
        return 2;
    }
    unsigned fs = argc > 3 ? (unsigned)atoi(argv[3]) : 48000, wpm = argc > 4 ? (unsigned)atoi(argv[4]) : 20;
    unsigned tone = argc > 5 ? (unsigned)atoi(argv[5]) : 800;
    int level = argc > 6 ? atoi(argv[6]) : 13107;
    FILE *o = fopen(argv[2], "wb");
    if (!o) {
        perror(argv[2]);
        return 1;
    }
    cw_tx c;
    int16_t buf[1024];
    unsigned n;
    cw_init(&c, argv[1], fs, wpm, tone, (int16_t)level);
    while ((n = cw_render(&c, buf, 1024)) != 0)
        fwrite(buf, 2, n, o);
    fclose(o);
    return 0;
}
