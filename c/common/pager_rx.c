/* pager_rx: see pager_rx.h. SPDX-License-Identifier: MIT */
#include "pager_rx.h"

#include <string.h>

void prx_init(prx_state *r, uint8_t *frames, uint8_t *have, unsigned max) {
    r->frames = frames;
    r->have = have;
    r->max = max;
    r->bad = 0;
    prx_reset(r);
}

void prx_reset(prx_state *r) {
    r->active = 0;
    r->magic = 0;
    r->msg_id = 0;
    r->total = 0;
    r->got = 0;
    r->packets = 0;
    memset(r->have, 0, r->max / 8u + 1u);
}

static int has(const prx_state *r, unsigned f) {
    return (r->have[f >> 3] >> (f & 7u)) & 1u;
}

int prx_packet(prx_state *r, const uint8_t *payload, unsigned n) {
    uint8_t p[PT_MAX_PAYLOAD];
    if (n != 64u && n != 128u) {
        r->bad++;
        return PRX_BAD;
    }
    memcpy(p, payload, n);
    pt_whiten(p, n);
    unsigned crc = (unsigned)p[n - 2] << 8 | p[n - 1];
    unsigned magic = p[0], hs = (unsigned)p[2] << 8 | p[3], total = (unsigned)p[4] << 8 | p[5];
    unsigned depth = 8u >> (hs >> 14), is_short = (hs >> 13) & 1u, seq = hs & 0x1FFFu;
    unsigned slots = n == 128u ? 20u : 8u, gf = slots * depth;
    if (pt_crc16(p, n - 2u) != crc || (magic & 0xF0u) != 0xA0u || total == 0 ||
        total >= 0xFFFFu || total > r->max) {
        r->bad++;
        return PRX_BAD;     /* parity packets are 0xB?: passed over here too */
    }
    if (!r->active || r->msg_id != p[1] || r->magic != magic || r->total != total) {
        prx_reset(r);
        r->active = 1;
        r->magic = (uint8_t)magic;
        r->msg_id = p[1];
        r->total = total;
    }
    /* pager_tx's frame_of: the short last group (its packets each take one of every m frames),
       or depth packets taking one of every depth frames */
    unsigned g = seq / depth, k = seq % depth, rem = total % gf;
    unsigned m = (rem + slots - 1u) / slots, first = total / gf * depth;
    unsigned fresh = 0;
    for (unsigned s = 0; s < slots; s++) {
        unsigned f = is_short && m ? g * gf + s * m + (seq - first) : g * gf + s * depth + k;
        if (is_short && (m == 0 || seq < first))
            break;          /* a header that contradicts itself */
        if (f >= total || has(r, f))
            continue;
        memcpy(r->frames + f * PT_FRAME_BYTES, p + PT_HDR + s * PT_FRAME_BYTES, PT_FRAME_BYTES);
        r->have[f >> 3] |= (uint8_t)(1u << (f & 7u));
        r->got++;
        fresh++;
    }
    r->packets++;
    if (!fresh)
        return PRX_OLD;
    return r->got >= r->total ? PRX_DONE : PRX_NEW;
}

unsigned prx_conceal(prx_state *r) {
    unsigned missing = 0, last = r->total;
    for (unsigned f = 0; f < r->total; f++) {
        if (has(r, f)) {
            last = f;
            continue;
        }
        missing++;
        unsigned src = last;
        if (src >= r->total) {          /* none before: the next one received */
            for (src = f + 1u; src < r->total && !has(r, src); src++) {
            }
        }
        if (src < r->total)
            memcpy(r->frames + f * PT_FRAME_BYTES, r->frames + src * PT_FRAME_BYTES, PT_FRAME_BYTES);
        else
            memset(r->frames + f * PT_FRAME_BYTES, 0, PT_FRAME_BYTES);
    }
    return missing;
}
