/* Host front end for pager_rx, for tests/test_pager_rx.py:
 *   pager_rx_host IN.bin N OUT.sot [--conceal]
 * IN.bin: payloads of N bytes (64 or 128) back to back, whitened as on air, in the order they
 * came. One line a payload, "p I r=R got=G total=T id=ID", then
 * "msg magic=M id=ID total=T got=G packets=P bad=B missing=K"; OUT.sot gets the message's frames
 * (with --conceal, the missing ones filled as prx_conceal does; else zeros).
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pager_rx.h"

#define MAXF 4096u
static uint8_t frames[MAXF * PT_FRAME_BYTES];
static uint8_t have[MAXF / 8u + 1u];

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s IN.bin N OUT.sot [--conceal]\n", argv[0]);
        return 2;
    }
    unsigned n = (unsigned)atoi(argv[2]);
    int conceal = argc > 4 && !strcmp(argv[4], "--conceal");
    FILE *f = fopen(argv[1], "rb");
    if (!f) {
        perror(argv[1]);
        return 1;
    }
    static prx_state r;
    prx_init(&r, frames, have, MAXF);
    uint8_t p[PT_MAX_PAYLOAD];
    for (unsigned i = 0; n <= sizeof p && fread(p, 1, n, f) == n; i++) {
        int v = prx_packet(&r, p, n);
        printf("p %u r=%d got=%u total=%u id=%u\n", i, v, r.got, r.total, r.msg_id);
    }
    fclose(f);
    unsigned missing = r.total - r.got;
    if (conceal)
        prx_conceal(&r);
    printf("msg magic=%u id=%u total=%u got=%u packets=%u bad=%u missing=%u\n", r.magic, r.msg_id,
           r.total, r.got, r.packets, r.bad, missing);
    FILE *o = fopen(argv[3], "wb");
    if (!o) {
        perror(argv[3]);
        return 1;
    }
    fwrite(frames, PT_FRAME_BYTES, r.total, o);
    fclose(o);
    return 0;
}
