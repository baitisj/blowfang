/* Host front end for range_tx + ffsk_tx, for tests/test_k6_range.py:
 *   range_tx_host tones IN.bin OUT.bin            the burst's tone bits, one a byte (1: 600 Hz)
 *   range_tx_host audio IN.bin OUT.raw [--fs N] [--level N]
 *                                                 FFSK 600/1200 Hz at 1200 baud, signed 16-bit LE
 * IN.bin: whitened 128-byte payloads back to back (modem.k6r.test_payloads).
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ffsk_tx.h"
#include "range_tx.h"

static uint8_t pl[128 * 4096];
static unsigned npl;

static unsigned source(void *ctx, unsigned i, uint8_t *out) {
    (void)ctx;
    if (i >= npl)
        return 0;
    memcpy(out, pl + 128 * i, 128);
    return 128;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s tones|audio IN.bin OUT [--fs N] [--level N]\n", argv[0]);
        return 2;
    }
    unsigned fs = 48000;
    int level = 16384;
    for (int i = 4; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--fs"))
            fs = (unsigned)atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--level"))
            level = atoi(argv[i + 1]);
    }
    FILE *f = fopen(argv[2], "rb");
    if (!f)
        return 1;
    npl = (unsigned)(fread(pl, 1, sizeof pl, f) / 128);
    fclose(f);
    FILE *o = fopen(argv[3], "wb");
    if (!o)
        return 1;
    rg_air a;
    rg_air_defaults(&a, source, 0);
    if (!strcmp(argv[1], "tones")) {
        int b;
        rg_air_start(&a);
        while ((b = rg_air_next(&a)) >= 0)
            fputc(b, o);
    } else {
        ffsk_tx m;
        int16_t buf[512];
        unsigned got;
        rg_air_start(&a);
        ffsk_init_src(&m, rg_air_next, &a, fs, RG_BAUD, RG_MARK_HZ, RG_SPACE_HZ, (int16_t)level);
        while ((got = ffsk_render(&m, buf, 512)) != 0)
            fwrite(buf, 2, got, o);
    }
    fclose(o);
    return 0;
}
