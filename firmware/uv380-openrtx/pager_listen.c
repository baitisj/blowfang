/* pager_listen: see pager_listen.h. SPDX-License-Identifier: MIT */
#include "pager_listen.h"

#if defined(PLATFORM_MDUV3x0)
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "ortx_mod.h"
#include "pager_rx.h"
#include "rxaudio.h"

#define RR_HIST 32               /* 8 kHz: 2 bits are 13.3 samples */
#define RR_XR 32                 /* 8 kHz: the quadrature filter spans 31 samples */
#include "range_rx.c"
#include "fsk_rx.c"                 /* PHY F (Jeff 11-14: "As well as receiving FSK") */

/* The receiver's 14.5 KB come from the sotto 1200 encoder's static arena (sotto8k_enc_arena.h,
   25.6 KB in CCM), which is idle whenever the receiver runs (it stops for FUNC and every
   module), not from the heap: after a recording (the encoder thread's 16 KB stack) and a preview
   the heap was too broken up for it, and the first image (8b87511) stopped listening for good
   (11-14 15:45-15:52Z: three echoes on air, none played). */
void *sotto8k_enc_calloc(size_t n, size_t size);
void sotto8k_enc_free(void *p);

#define PL_FS 8000u
#define PL_GAP_MS 4000u          /* quiet this long after a packet: play what came, if enough */
#define PL_SAME_MS 60000u        /* a message played is not played again for this long */

static ortx_api_t plApi;
static rxa_state plRx;
static rr_state *plRr;
static fr_state *plFr;
static uint8_t *plFrames, *plHave;
static prx_state plMsg;
static unsigned plRd, plOn, plReady;
static uint32_t plLast;          /* ms at the last packet that checked */
/* kept across stop and start: the last message played */
static int plDoneValid;
static uint8_t plDoneId;
static uint32_t plDoneMs;

int pager_listen_start(const pr_host *h, int16_t *ring, unsigned n, unsigned max) {
    if (plOn)
        pager_listen_stop();
    plRr = (rr_state *)sotto8k_enc_calloc(1, sizeof(rr_state));
    plFr = (fr_state *)sotto8k_enc_calloc(1, sizeof(fr_state));
    plFrames = (uint8_t *)sotto8k_enc_calloc(1, max * PT_FRAME_BYTES + max / 8u + 1u);
    if (!plRr || !plFr || !plFrames) {
        sotto8k_enc_free(plRr);
        sotto8k_enc_free(plFr);
        sotto8k_enc_free(plFrames);
        plRr = 0;
        plFr = 0;
        plFrames = 0;
        return -1;
    }
    plHave = plFrames + max * PT_FRAME_BYTES;
    prx_init(&plMsg, plFrames, plHave, max);
    rr_init(plRr, PL_FS);
    fr_init(plFr);
    plApi.c6000_read = h->c6r;
    plApi.c6000_write = h->c6w;
    plApi.sleep_ms = h->sleep_ms;
    for (unsigned i = 0; i < n; i++)
        ring[i] = 0;
    rx_start(&plApi, &plRx, (volatile int16_t *)ring, n);
    plRd = rx_wr(&plRx) & ~1u;
    plReady = 0;
    plOn = 1;
    return 0;
}

static int ready(uint32_t now, char *line, unsigned len, const char *why) {
    plReady = 1;
    plDoneValid = 1;
    plDoneId = plMsg.msg_id;
    plDoneMs = now;
    snprintf(line, len, "heard %s id=%u frames=%u/%u packets=%u", why, plMsg.msg_id, plMsg.got,
             plMsg.total, plMsg.packets);
    return PL_READY;
}

int pager_listen_poll(uint32_t now, char *line, unsigned len) {
    if (!plOn || plReady)
        return PL_NONE;
    int16_t x[64];
    int ret = PL_NONE;
    for (unsigned rounds = 0; rounds < 32u; rounds++) {
        unsigned m = plRx.ring - 1u, wr = rx_wr(&plRx), avail = (wr - plRd) & m;
        if (avail > plRx.ring - 128u) {
            plRd = (wr + plRx.ring - 2u) & ~1u & m;     /* went round: skip to what's fresh */
            break;
        }
        if (avail < 2u)
            break;
        unsigned got = 0;
        while (got < 64u && avail >= 2u) {
            x[got++] = plRx.buf[(plRd + 1u) & m];       /* each sample comes twice: take one */
            plRd = (plRd + 2u) & m;
            avail -= 2u;
        }
        /* the same audio to both receivers: PHY R (range) and PHY F (FSK) */
        for (unsigned phy = 0; phy < 2u; phy++) {
            unsigned off = 0, used;
            while (off < got) {
                const uint8_t *pl;
                unsigned n;
                if (phy == 0) {
                    int pk = rr_push(plRr, x + off, got - off, &used);
                    off += used;
                    if (!pk || !plRr->ok)
                        continue;
                    pl = plRr->payload;
                    n = RR_PAYLOAD;
                } else {
                    int pk = fr_push(plFr, x + off, got - off, &used);
                    off += used;
                    if (!pk || !plFr->ok)
                        continue;
                    pl = plFr->payload;
                    n = plFr->len;
                }
                if (plReady)
                    continue;                           /* one message at a time */
                int r = prx_packet(&plMsg, pl, n);
                if (r == PRX_BAD)
                    continue;
                if (plDoneValid && plMsg.msg_id == plDoneId && now - plDoneMs < PL_SAME_MS) {
                    prx_reset(&plMsg);                  /* a repeat of what was played */
                    continue;
                }
                plLast = now;
                if (r == PRX_DONE && plMsg.magic == PT_MAGIC_1200) {
                    ready(now, line, len, phy ? "whole fsk" : "whole");
                    ret = PL_READY;
                    continue;
                }
                snprintf(line, len, "heard %s seq=%u id=%u frames=%u/%u", phy ? "fsk" : "pkt",
                         rr_seq(pl), plMsg.msg_id, plMsg.got, plMsg.total);
                ret = PL_PACKET;
            }
        }
        if (ret == PL_READY)
            return PL_READY;
    }
    if (ret == PL_NONE && plMsg.active && now - plLast > PL_GAP_MS) {
        if (plMsg.magic == PT_MAGIC_1200 && 4u * plMsg.got >= 3u * plMsg.total)
            return ready(now, line, len, "part");
        snprintf(line, len, "heard dropped id=%u frames=%u/%u", plMsg.msg_id, plMsg.got,
                 plMsg.total);
        prx_reset(&plMsg);
        return PL_PACKET;
    }
    return ret;
}

unsigned pager_listen_take(uint8_t *out, unsigned max) {
    if (!plOn || !plReady)
        return 0;
    prx_conceal(&plMsg);
    unsigned n = plMsg.total < max ? plMsg.total : max;
    memcpy(out, plFrames, n * PT_FRAME_BYTES);
    prx_reset(&plMsg);
    plReady = 0;
    return n;
}

int pager_listen_busy(void) {
    if (!plOn || plReady)
        return 0;
    return (plRr->collecting && plRr->best >= RR_SYNC_STRONG) ||
           (plFr->collecting && plFr->serr == 0) || plMsg.active;
}

void pager_listen_stop(void) {
    if (!plOn)
        return;
    rx_stop(&plApi, &plRx);
    sotto8k_enc_free(plFrames);
    sotto8k_enc_free(plFr);
    sotto8k_enc_free(plRr);
    plRr = 0;
    plFr = 0;
    plFrames = 0;
    plOn = 0;
}

#else

int pager_listen_start(const pr_host *h, int16_t *ring, unsigned n, unsigned max) {
    (void)h;
    (void)ring;
    (void)n;
    (void)max;
    return -1;
}

int pager_listen_poll(uint32_t now, char *line, unsigned len) {
    (void)now;
    (void)line;
    (void)len;
    return PL_NONE;
}

unsigned pager_listen_take(uint8_t *out, unsigned max) {
    (void)out;
    (void)max;
    return 0;
}

void pager_listen_stop(void) {
}

int pager_listen_busy(void) {
    return 0;
}

#endif
