/* Host front end for range_rx, for tests/test_range_rx.py:
 *   range_rx_host IN.raw FS     signed 16-bit LE audio at FS; one line a packet:
 *   pkt BITS c=CORR y=YMEAN e=ERRORS ok=0|1 PAYLOAD-HEX
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>

#include "range_rx.h"

static rr_state r;

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s IN.raw FS\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f)
        return 1;
    rr_init(&r, (unsigned)atoi(argv[2]));
    int16_t buf[256];
    size_t got;
    while ((got = fread(buf, 2, 256, f)) != 0) {
        unsigned off = 0, used;
        while (off < got) {
            if (rr_push(&r, buf + off, (unsigned)got - off, &used)) {
                printf("pkt %lu c=%d y=%d e=%d ok=%d ", (unsigned long)r.psync, r.pcorr, r.ymean, r.errors,
                       r.ok);
                for (unsigned i = 0; i < RR_PAYLOAD; i++)
                    printf("%02x", r.payload[i]);
                printf("\n");
            }
            off += used;
        }
    }
    fclose(f);
    return 0;
}
