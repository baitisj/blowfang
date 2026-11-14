/* Host front end for fsk_rx, for tests/test_fsk_rx.py:
 *   fsk_rx_host IN.raw      signed 16-bit LE audio at 8 kHz; one line a packet:
 *   pkt POS len=N ok=0|1 e=EYE PAYLOAD-HEX
 * SPDX-License-Identifier: MIT */
#include <stdio.h>

#include "fsk_rx.h"

static fr_state r;

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s IN.raw\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f)
        return 1;
    fr_init(&r);
    int16_t buf[256];
    size_t got;
    while ((got = fread(buf, 2, 256, f)) != 0) {
        unsigned off = 0, used;
        while (off < got) {
            if (fr_push(&r, buf + off, (unsigned)got - off, &used)) {
                printf("pkt %lu len=%u ok=%d e=%d ", (unsigned long)r.psync, r.len, r.ok, r.pe);
                for (unsigned i = 0; i < r.len; i++)
                    printf("%02x", r.payload[i]);
                printf("\n");
            }
            off += used;
        }
    }
    fclose(f);
    return 0;
}
