/* Host front end for pager_tx + ffsk_tx, for tests/test_uv380_pager.py:
 *   pager_tx_host packets IN.sot OUT.bin [options]   whitened payloads, back to back
 *   pager_tx_host bits    IN.sot OUT.bin [options]   the burst as bytes (pt_air, MSB first)
 *   pager_tx_host audio   IN.sot OUT.raw [options]   FFSK, signed 16-bit little-endian mono
 * options: --profile L|XL  --magic N  --depth N  --parity  --plain (no short last group)
 *          --msg-id N  --passes N  --baud N  --fs N  --level N  --mark N --space N
 * SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ffsk_tx.h"
#include "pager_tx.h"

static uint8_t frames[6 * 65536];

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s packets|bits|audio IN.sot OUT [options]\n", argv[0]);
        return 2;
    }
    pt_profile pr = PT_PROFILE_XL(PT_MAGIC_1200);
    int msg_id = -1;
    unsigned passes = 1, baud = 1800, fs = 48000, mark = 0, space = 0;
    int level = 16384;
    for (int i = 4; i < argc; i++) {
        const char *o = argv[i], *v = i + 1 < argc ? argv[i + 1] : "0";
        if (!strcmp(o, "--profile")) {
            int xl = !strcmp(v, "XL");
            pr.payload = xl ? 128 : 64;
            pr.slots = xl ? 20 : 8;
            i++;
        } else if (!strcmp(o, "--magic")) {
            pr.magic = (uint8_t)strtoul(v, 0, 0);
            i++;
        } else if (!strcmp(o, "--depth")) {
            pr.depth = (uint8_t)atoi(v);
            i++;
        } else if (!strcmp(o, "--parity")) {
            pr.parity = 1;
        } else if (!strcmp(o, "--plain")) {
            pr.short_last = 0;
        } else if (!strcmp(o, "--msg-id")) {
            msg_id = (int)strtol(v, 0, 0);
            i++;
        } else if (!strcmp(o, "--passes")) {
            passes = (unsigned)atoi(v);
            i++;
        } else if (!strcmp(o, "--baud")) {
            baud = (unsigned)atoi(v);
            i++;
        } else if (!strcmp(o, "--fs")) {
            fs = (unsigned)atoi(v);
            i++;
        } else if (!strcmp(o, "--level")) {
            level = atoi(v);
            i++;
        } else if (!strcmp(o, "--mark")) {
            mark = (unsigned)atoi(v);
            i++;
        } else if (!strcmp(o, "--space")) {
            space = (unsigned)atoi(v);
            i++;
        } else {
            fprintf(stderr, "unknown option %s\n", o);
            return 2;
        }
    }
    FILE *f = fopen(argv[2], "rb");
    if (!f) {
        perror(argv[2]);
        return 1;
    }
    size_t nb = fread(frames, 1, sizeof frames, f);
    fclose(f);
    if (nb % 6) {
        fprintf(stderr, "a .sot file is a whole number of 6-byte frames\n");
        return 1;
    }
    unsigned total = (unsigned)(nb / 6);
    pt_message m = {&pr, frames, total, passes, (uint8_t)(msg_id < 0 ? pt_msg_id(frames, total) : msg_id)};
    if (pt_n_packets(&pr, total) == 0 && total) {
        fprintf(stderr, "message too long for the format\n");
        return 1;
    }
    FILE *o = fopen(argv[3], "wb");
    if (!o) {
        perror(argv[3]);
        return 1;
    }
    static pt_air air;
    pt_air_defaults(&air, pr.payload, baud, pt_message_source, &m);
    if (!strcmp(argv[1], "packets")) {
        uint8_t p[PT_MAX_PAYLOAD];
        unsigned n;
        for (unsigned i = 0; (n = pt_message_source(&m, i, p)) != 0; i++)
            fwrite(p, 1, n, o);
    } else if (!strcmp(argv[1], "bits")) {
        pt_air_start(&air);
        int b;
        unsigned byte = 0, k = 0;
        while ((b = pt_air_bit(&air)) >= 0) {
            byte = byte << 1 | (unsigned)b;
            if (++k == 8) {
                fputc((int)byte, o);
                byte = 0;
                k = 0;
            }
        }
    } else {
        static ffsk_tx mod;
        int16_t buf[1024];
        unsigned n;
        ffsk_init(&mod, &air, fs, baud, mark, space, (int16_t)level);
        while ((n = ffsk_render(&mod, buf, 1024)) != 0)
            fwrite(buf, 2, n, o);
    }
    fclose(o);
    return 0;
}
