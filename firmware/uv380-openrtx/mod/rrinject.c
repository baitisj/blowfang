/* rrinject: rangerx's receiver (c/common/range_rx.c built as rangerx builds it) fed audio from the
   USB serial instead of the radio, to tell the receiver on the MCU from the audio it gets. The
   audio comes as lines "a SEQ hhhh hhhh ...": 4 hex digits a 16-bit sample (two's complement),
   the line's first two words skipped (a 4-digit SEQ is not a sample), so rxrec's lines can be
   sent back as logged. Ends
   after 3 s with nothing, a "q", Ctrl-C or RG_S. It never calls stop(), which would take bytes
   from USB serial past what the loader keeps for usb_read (256; the loader flashed on 11-13
   drops the rest): it reads the Ctrl-C byte itself.
     inject start fs=8000 state=NNNN
     r 3 t=12345 c=-118 y=31 e=41 ok=1 s=2 bits=NNNN    as rangerx, with the sync's soft value count
     inject end samples=N n=N ok=N
   Touches no hardware. Returns the packets that checked.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define RR_HIST 32
#define RR_XR 32
#include "../../../c/common/range_rx.c"

#ifndef RG_S
#define RG_S 600u
#endif
#define FS 8000u

static int hexv(int c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
           : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

int uv_main(const ortx_api_t *api) {
    char line[96];
    uint32_t bytes;
    rr_state *r = (rr_state *)api->scratch(&bytes);
    if (bytes < sizeof(rr_state))
        return -8;
    rr_init(r, FS);
    api->fmt(line, sizeof line, "inject start fs=%u state=%u", FS, (unsigned)sizeof(rr_state));
    api->say(line);
    uint8_t b[64];
    int16_t x[64];
    unsigned nx = 0, nd = 0, acc = 0, word = 0, npk = 0, nok = 0, quit = 0;
    uint32_t samples = 0, idle = api->ms();
    while (!quit && api->ms() < RG_S * 1000u) {
        unsigned n = api->usb_read(b, sizeof b);
        if (n == 0) {
            if (api->ms() - idle > 3000u)
                break;
            api->sleep_ms(1);
            continue;
        }
        idle = api->ms();
        for (unsigned i = 0; i < n && !quit; i++) {
            int c = b[i], v = hexv(c);
            if (c == 'q' || c == 3) {
                quit = 1;
                break;
            }
            if (v >= 0) {
                acc = (acc << 4 | (unsigned)v) & 0xFFFFu;
                nd++;
                continue;
            }
            if (nd) {                    /* a word ended: a sample unless it's "a" or SEQ */
                if (word++ > 1 && nd == 4u) {
                    x[nx++] = (int16_t)(uint16_t)acc;
                    samples++;
                }
                nd = 0, acc = 0;
            }
            if (c == '\n' || c == '\r')
                word = 0;
            if (nx == 64u) {
                unsigned off = 0, used;
                while (off < nx) {
                    if (rr_push(r, x + off, nx - off, &used)) {
                        npk++;
                        nok += (unsigned)r->ok;
                        api->fmt(line, sizeof line, "r %u t=%lu c=%d y=%d e=%d ok=%d s=%u bits=%lu",
                                 npk, (unsigned long)api->ms(), r->pcorr, r->ymean, r->errors, r->ok,
                                 rr_seq(r->payload), (unsigned long)r->psync);
                        api->say(line);
                    }
                    off += used;
                }
                nx = 0;
            }
        }
    }
    api->fmt(line, sizeof line, "inject end samples=%lu n=%u ok=%u", (unsigned long)samples, npk, nok);
    api->say(line);
    return (int)nok;
}
