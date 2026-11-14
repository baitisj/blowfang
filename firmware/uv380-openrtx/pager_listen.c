/* pager_listen: see pager_listen.h. SPDX-License-Identifier: MIT */
#include "pager_listen.h"

#if defined(PLATFORM_MDUV3x0)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ortx_mod.h"
#include "pager_rx.h"
#include "rxaudio.h"

#define RR_HIST 32               /* 8 kHz: 2 bits are 13.3 samples */
#define RR_XR 32                 /* 8 kHz: the quadrature filter spans 31 samples */
#include "range_rx.c"

#define PL_FS 8000u
#define PL_GAP_MS 4000u          /* quiet this long after a packet: play what came, if enough */
#define PL_SAME_MS 60000u        /* a message played is not played again for this long */

static ortx_api_t plApi;
static rxa_state plRx;
static rr_state *plRr;
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
    plRr = (rr_state *)malloc(sizeof(rr_state));
    plFrames = (uint8_t *)malloc(max * PT_FRAME_BYTES + max / 8u + 1u);
    if (!plRr || !plFrames) {
        free(plRr);
        free(plFrames);
        plRr = 0;
        plFrames = 0;
        return -1;
    }
    plHave = plFrames + max * PT_FRAME_BYTES;
    prx_init(&plMsg, plFrames, plHave, max);
    rr_init(plRr, PL_FS);
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
        unsigned off = 0, used;
        while (off < got) {
            int pk = rr_push(plRr, x + off, got - off, &used);
            off += used;
            if (!pk || !plRr->ok)
                continue;
            int r = prx_packet(&plMsg, plRr->payload, RR_PAYLOAD);
            if (r == PRX_BAD)
                continue;
            if (plDoneValid && plMsg.msg_id == plDoneId && now - plDoneMs < PL_SAME_MS) {
                prx_reset(&plMsg);                      /* a repeat of what was played */
                continue;
            }
            plLast = now;
            if (r == PRX_DONE && plMsg.magic == PT_MAGIC_1200)
                return ready(now, line, len, "whole");
            snprintf(line, len, "heard pkt seq=%u id=%u frames=%u/%u", rr_seq(plRr->payload),
                     plMsg.msg_id, plMsg.got, plMsg.total);
            ret = PL_PACKET;
        }
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

void pager_listen_stop(void) {
    if (!plOn)
        return;
    rx_stop(&plApi, &plRx);
    free(plRr);
    free(plFrames);
    plRr = 0;
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

#endif
