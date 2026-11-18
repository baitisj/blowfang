/* framers_host: m17_tx and ysf_tx on the host, for tests/test_uv380_framers.py, which checks every
   byte against modem/m17.py and modem/ysf.py. Bytes in on stdin, the transmission out on stdout.

     framers_host m17 sotto SRC DST REPEAT < SOTTO        tx_sotto (36-byte reads, as the chip)
     framers_host m17 voice SRC < C2                      tx_voice (16-byte stream payloads)
     framers_host m17 bert N                              tx_bert(N)
     framers_host m17 sms SRC TEXT                        tx_sms([TEXT])
     framers_host ysf sotto DEST SRC DGID < SOTTO         tx_sotto
     framers_host ysf datafr FN < 40 BYTES                one data FR comms frame (FT 7)
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "m17_tx.h"
#include "ysf_tx.h"

static uint8_t in[1u << 16];

static unsigned read_all(void) {
    size_t n = fread(in, 1, sizeof in, stdin);
    return (unsigned)n;
}

static void out(const uint8_t *b, unsigned n) {
    fwrite(b, 1, n, stdout);
}

static int m17(int argc, char **argv) {
    uint8_t f[M17T_FRAME];
    if (argc >= 6 && !strcmp(argv[2], "sotto")) {
        unsigned n = read_all() / M17T_SOTTO_SF;
        m17t_sotto t;
        if (m17t_sotto_begin(&t, in, n, argv[3], argv[4], atoi(argv[5])))
            return 1;
        uint8_t slot[36];
        unsigned got, total = 0;
        while ((got = m17t_sotto_read(&t, slot, sizeof slot)) > 0) {
            out(slot, got);
            total += got;
        }
        return total == m17t_sotto_bytes(&t) ? 0 : 3;
    }
    if (argc >= 4 && !strcmp(argv[2], "voice")) {
        unsigned n = read_all();
        while (n % 16u)
            in[n++] = 0;
        uint8_t l30[M17T_LSF];
        m17t_lsf(l30, argv[3], "", M17T_TYPE_STREAM_VOICE, NULL);
        m17t_fill(f, M17T_PRE_LSF);
        out(f, M17T_FRAME);
        m17t_frame_lsf(l30, f);
        out(f, M17T_FRAME);
        for (unsigned k = 0; k < n / 16u; k++) {
            m17t_frame_stream(l30, (uint16_t)k, in + 16u * k, k == n / 16u - 1u, f);
            out(f, M17T_FRAME);
        }
        m17t_eot(f);
        out(f, M17T_FRAME);
        return 0;
    }
    if (argc >= 4 && !strcmp(argv[2], "bert")) {
        uint16_t prbs = 1;
        m17t_fill(f, M17T_PRE_BERT);
        out(f, M17T_FRAME);
        for (int k = 0; k < atoi(argv[3]); k++) {
            m17t_frame_bert(&prbs, f);
            out(f, M17T_FRAME);
        }
        m17t_eot(f);
        out(f, M17T_FRAME);
        return 0;
    }
    if (argc >= 5 && !strcmp(argv[2], "sms")) {
        uint8_t l30[M17T_LSF], p[260];
        unsigned n = 0;
        p[n++] = 0x05;
        for (const char *s = argv[4]; *s && n < 250u; s++)
            p[n++] = (uint8_t)*s;
        p[n++] = 0;
        uint16_t c = m17t_crc(p, n);
        p[n++] = (uint8_t)(c >> 8);
        p[n++] = (uint8_t)c;
        m17t_lsf(l30, argv[3], "", M17T_TYPE_PACKET_DATA, NULL);
        m17t_fill(f, M17T_PRE_LSF);
        out(f, M17T_FRAME);
        m17t_frame_lsf(l30, f);
        out(f, M17T_FRAME);
        for (unsigned i = 0, k = 0; i < n; i += 25u, k++) {
            unsigned len = n - i < 25u ? n - i : 25u;
            int last = i + 25u >= n;
            m17t_frame_packet(p + i, len, last, last ? len : k, f);
            out(f, M17T_FRAME);
        }
        m17t_eot(f);
        out(f, M17T_FRAME);
        return 0;
    }
    return 2;
}

static int ysf(int argc, char **argv) {
    if (argc >= 6 && !strcmp(argv[2], "sotto")) {
        unsigned n = read_all() / YSFT_SOTTO_SF;
        ysft_sotto t;
        if (ysft_sotto_begin(&t, in, n, argv[3], argv[4], (uint8_t)atoi(argv[5])))
            return 1;
        uint8_t slot[36];
        unsigned got, total = 0;
        while ((got = ysft_sotto_read(&t, slot, sizeof slot)) > 0) {
            out(slot, got);
            total += got;
        }
        return total == ysft_sotto_bytes(&t) ? 0 : 3;
    }
    if (argc >= 4 && !strcmp(argv[2], "datafr")) {
        if (read_all() < 40u)
            return 1;
        ysft_fich fi;
        ysft_fich_default(&fi);
        fi.dt = YSFT_DT_DATA_FR;
        fi.ft = 7;
        fi.fn = (uint8_t)atoi(argv[3]);
        uint8_t f[YSFT_FRAME];
        ysft_frame_data_fr(&fi, in, in + 20, f);
        out(f, YSFT_FRAME);
        return 0;
    }
    return 2;
}

int main(int argc, char **argv) {
    if (argc >= 3 && !strcmp(argv[1], "m17"))
        return m17(argc, argv);
    if (argc >= 3 && !strcmp(argv[1], "ysf"))
        return ysf(argc, argv);
    fprintf(stderr, "usage: see framers_host.c\n");
    return 2;
}
