/* pager_rec: see pager_rec.h. SPDX-License-Identifier: MIT */
#include "pager_rec.h"

#if defined(PLATFORM_MDUV3x0)
#include "ortx_mod.h"
#include "rxaudio.h"

static ortx_api_t prApi;
static rxa_state prState;
static unsigned prRd, prOv;
static int prOn;

unsigned pager_rec_start(const pr_host *h, int16_t *ring, unsigned n) {
    if (prOn)
        pager_rec_stop();
    prApi.c6000_read = h->c6r;
    prApi.c6000_write = h->c6w;
    prApi.sleep_ms = h->sleep_ms;
    for (unsigned i = 0; i < n; i++)
        ring[i] = 0;
    unsigned tries = rx_start_as_is(&prApi, &prState, (volatile int16_t *)ring, n);
    prRd = rx_wr(&prState) & ~1u;
    prOv = 0;
    prOn = 1;
    return tries;
}

unsigned pager_rec_take(int16_t *x, unsigned n) {
    if (!prOn)
        return 0;
    unsigned m = prState.ring - 1u, wr = rx_wr(&prState), avail = (wr - prRd) & m;
    if (avail > prState.ring - 256u) {
        prOv++;
        prRd = (wr + prState.ring - 2u) & ~1u & m;
        return 0;
    }
    unsigned got = 0;
    while (got < n && avail >= 2u) {
        x[got++] = prState.buf[(prRd + 1u) & m];   /* each sample comes twice: take one */
        prRd = (prRd + 2u) & m;
        avail -= 2u;
    }
    return got;
}

unsigned pager_rec_overruns(void) {
    return prOv;
}

void pager_rec_stop(void) {
    if (!prOn)
        return;
    rx_stop(&prApi, &prState);
    prOn = 0;
}

#else

unsigned pager_rec_start(const pr_host *h, int16_t *ring, unsigned n) {
    (void)h;
    (void)ring;
    (void)n;
    return 0;
}

unsigned pager_rec_take(int16_t *x, unsigned n) {
    (void)x;
    (void)n;
    return 0;
}

unsigned pager_rec_overruns(void) {
    return 0;
}

void pager_rec_stop(void) {
}

#endif
