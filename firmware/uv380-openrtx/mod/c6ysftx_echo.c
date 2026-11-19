/* c6ysftx ON AIR, the echo plan: a parrot for the FTM-300D (Jeff 11-19: "key the FTM-300D and
   have our message echoed back to the radio"). It listens on the UV-380's own FM tap (no mod,
   as c6ysfrx, proven 108/108 frames 11-18) for a C4FM call, keeps each frame as the 120 bytes it
   came as (c/common/ysf_echo.c: a DN call's AMBE voice, DCH and FICH, no vocoder needed), and when
   the call ends (its terminator, or ECHO_QUIET_MS with no frame) sends the same frames back:
   the Morse ident AG7EW, then the call through the C6000's physical layer mode at YSF's level
   (TX_DEV 140 %, 2712 Hz outer), on the UV-380's channel (441.850 or 440.850 MHz; api->tx_tone
   refuses any other) at the channel's power (the bench: power 0). The FTM should play the caller's
   own voice and show the caller's callsign. It waits ECHO_S seconds for calls, echoes up to
   ECHO_MAX of them, and stops early for Ctrl-C.

   What one key-up and the module arena hold: the frames that fit both (a DN frame is 100 ms; the
   arena's room comes after the module's code and the receiver's state, about 2 s). The rest of a
   longer call is not kept. No voice is ever said over USB: the lines give counts, the frames'
   kinds and the caller's callsign only (the bench's logs go to the public jobs branch).

   The raw mode sits about 6.7 kHz above the FM carrier: the bench sets the transmit trim first
   (uv380.cmd "trim -580", FM trim - 6680) and back after, as for c6ysftx's other plans. That
   trim moves only the transmitter; the tap listens on the channel itself.

   Lines: "echo armed s= max=N frames (T ms) cal I= Q= -> 45= 46=", then per call "echo heard
   n= dropped= hdr= term= voice= other= gaps= ended= src= rx_frames= lost=", the keying's lines,
   "echo tx slots= bytes=/ late= lost= done=", "echo sent" or "echo short"; at the end "end
   echoes=N".
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the ident first and the transmit guard */
#define C6_BITWAIT 3u               /* the tap's 512 bytes a half at about 2 MHz, as c6m17rx */
#include "c6spi.h"
#include "c6slot.h"
#include "c6key.h"
#include "w25sec.h"
#include "at1846.h"
#include "c6vspi.h"                /* c6bus_mod.h names its port (unused: no DMR here) */
#define YSF_INVERT 1                /* the tap reads +3 negative */
#define YSF_RX_NO_SOTTO 1           /* FICH and CSD only: the sotto VW decoder's 2.6 KB not needed */
#include "../../../c/common/ysf_rx.c"
#include "../../../c/common/ysf_echo.c"
#include "../../../c/common/c6_core.c"
#include "c6bus_mod.h"

#ifndef ECHO_S
#define ECHO_S 900u                 /* how long it waits for calls */
#endif
#ifndef ECHO_MAX
#define ECHO_MAX 3u                 /* calls echoed, then it ends */
#endif
#define ECHO_QUIET_MS 500u          /* no frame this long after the first: the call is over */
#define ECHO_GAP_MS 400u            /* after the call, before keying: the FTM back in receive */
#define TX_DEV 140u
#define TX_FKHZ 441850u
#define TX_TAIL_MS 350u
#define TX_MAX_MS 5000u             /* tx_tone's limit */
#define PRE_BYTES 24u               /* 0x77 before the first frame, as ysf_tx sends */
#define V34 0x1Cu
#define GPIOD 0x40020C00u
#define MUX 9u
#define HALF0 0x030u
#define HALF1 0x230u
#define BLK 64u

typedef struct {
    ysf_rx rx;
    ysf_frame f;
    ye_state e;
    uint32_t last_ms;
    uint8_t frames[];
} echo_state;

typedef struct {
    const uint8_t *frames;
    unsigned n, at;                 /* bytes given so far, preamble included */
} echo_src;

static uint32_t echo_ms(uint32_t bytes) {
    return C6K_SETTLE_MS + 30u + (bytes * 1000u + 1199u) / 1200u + 90u + TX_TAIL_MS + 100u;
}

static unsigned echo_pull(void *s, uint8_t *o, unsigned max) {
    echo_src *p = s;
    unsigned total = PRE_BYTES + p->n * YE_FRAME, k = 0;
    while (k < max && p->at < total) {
        o[k++] = p->at < PRE_BYTES ? 0x77u : p->frames[p->at - PRE_BYTES];
        p->at++;
    }
    return k;
}

static unsigned rd16(const ortx_api_t *api, unsigned a) {
    uint8_t b[2];
    if (c6_read(api, C6_DATA, a, b, 2))
        return 0x10000u;
    return ((unsigned)b[0] << 8) | b[1];
}

/* one call: frames into st->e until it ends; 0 when the time ran out or stop() with none */
static unsigned echo_listen(const ortx_api_t *api, echo_state *st, unsigned maxf, uint32_t until) {
    static const uint8_t regs[] = {0x10, 0x60, 0x26, 0x81, 0x34, 0x36, 0xE0, 0};
    ysf_init(&st->rx);
    ye_init(&st->e, st->frames, maxf);
    c6_saved sv;
    at_saved as;
    as.ok = 0;
    c6_save(api, &sv, regs);
    uint32_t mux = (C6_REG(GPIOD + 0x14u) >> MUX) & 1u;
    C6_REG(GPIOD + 0x18u) = 1u << MUX;
    at_dmr_rx(api, &as, 0);
    uint8_t m81 = api->c6000_read(0x81);
    api->c6000_write(0x10, 0x80);
    api->c6000_write(0x60, 0x00);
    api->c6000_write(0x26, 0xFD);
    api->c6000_write(0x81, (uint8_t)(m81 | 0x02u));
    api->c6000_write(0x34, V34);
    api->c6000_write(0x83, 0xFF);
    unsigned last = 2, pe[2];
    pe[0] = rd16(api, HALF0 + 510u), pe[1] = rd16(api, HALF1 + 510u);
    while (!api->stop()) {
        uint32_t t = api->ms();
        if (st->e.n || st->e.dropped) {
            if (st->e.ended || t - st->last_ms >= ECHO_QUIET_MS)
                break;
        } else if ((int32_t)(t - until) >= 0) {
            break;
        }
        uint8_t fl = api->c6000_read(0x82);
        if (fl)
            api->c6000_write(0x83, fl);
        if (fl & 0x02u) {
            unsigned se0 = rd16(api, HALF0 + 510u), se1 = rd16(api, HALF1 + 510u);
            unsigned c0 = se0 != pe[0], c1 = se1 != pe[1];
            int done = c0 != c1 ? (c0 ? 0 : 1) : (last < 2u ? (int)(1u - last) : 0);
            pe[0] = se0, pe[1] = se1;
            last = (unsigned)done;
            uint8_t b[2 * BLK];
            unsigned base = done ? HALF1 : HALF0;
            for (unsigned k = 0; k < 4u; k++) {
                if (c6_read(api, C6_DATA, base + k * 2u * BLK, b, sizeof b))
                    continue;
                for (unsigned i = 0; i < BLK; i++) {
                    int16_t s = (int16_t)(((unsigned)b[2 * i] << 8) | b[2 * i + 1]);
                    if (ysf_push(&st->rx, s, &st->f)) {
                        ye_put(&st->e, &st->rx, &st->f);
                        st->last_ms = api->ms();
                    }
                }
            }
        }
        api->sleep_ms(1);
    }
    api->c6000_write(0x81, m81);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    at_restore(api, &as);
    C6_REG(GPIOD + 0x18u) = mux ? (1u << MUX) : (1u << (MUX + 16u));
    return st->e.n;
}

/* the kept frames back out: 0 sent whole, -1 refused, -2 short */
static int echo_send(const ortx_api_t *api, echo_state *st, uint8_t i45, uint8_t q46) {
    char line[160];
    uint32_t bytes = PRE_BYTES + st->e.n * YE_FRAME, tx_ms = echo_ms(bytes);
    c6s_state ss;
    if (c6s_open(&ss)) {
        api->say("err irq: EXTI0-2 in use, no slot clock, nothing sent");
        return -1;
    }
    c6bm_ctx cx = {api, &ss};
    c6c_bus b;
    c6bm_bus(&b, &cx, 0);
    uint32_t t_key = api->ms();
    c6k_notes q;
    q.n = 0;
    q.quiet = 0;
    if (c6k_key(api, &q, tx_ms, &t_key)) {
        c6s_close(&ss);
        return -1;
    }
    c6c_fsk4 p;
    p.i45 = i45;
    p.q46 = q46;
    p.mode = 0x66u;
    p.fill = 0x77u;
    p.pad0 = p.pad1 = 0x77u;
    p.half = 0;
    p.t_end = t_key + tx_ms - TX_TAIL_MS;
    echo_src src = {st->frames, st->e.n, 0};
    c6c_stats s;
    int r = c6c_fsk4_tx(&b, &p, echo_pull, &src, &s);
    c6s_close(&ss);
    api->fmt(line, sizeof line, "echo tx slots=%u bytes=%lu/%lu edges=%u late=%u lost=%u chk=%u/%u done=%d restored at %lu ms",
             s.slots, (unsigned long)s.bytes, (unsigned long)bytes, s.edges, s.late, s.lost, s.chk0, s.chk1, s.done,
             (unsigned long)(api->ms() - t_key));
    c6k_note(api, &q, line);
    while (api->ms() - t_key < tx_ms + 200u && !api->stop())
        api->sleep_ms(10);
    c6k_flush(api, &q, 2000u);
    return r == 0 ? 0 : -2;
}

int uv_main(const ortx_api_t *api) {
    char line[200];
    uint32_t sz = 0;
    echo_state *st = (echo_state *)api->scratch(&sz);
    if (sz < sizeof(echo_state) + YE_FRAME) {
        api->fmt(line, sizeof line, "err scratch=%lu: need %u and a frame", (unsigned long)sz, (unsigned)sizeof(echo_state));
        api->say(line);
        return -1;
    }
    unsigned maxf = (unsigned)((sz - sizeof(echo_state)) / YE_FRAME);
    while (maxf > 1u && echo_ms(PRE_BYTES + maxf * YE_FRAME) > TX_MAX_MS)
        maxf--;
    unsigned ig = 0, qg = 0;
    if (w25_dmr_pair(api, TX_FKHZ, &ig, &qg) || ig < 0x08u || ig > 0xF8u || qg < 0x08u || qg > 0xF8u) {
        api->say("err cal: DMR gains unreadable or out of range, nothing armed");
        return -1;
    }
    uint8_t i45 = c6c_dev(ig, TX_DEV), q46 = c6c_dev(qg, TX_DEV);
    api->fmt(line, sizeof line, "echo armed s=%u max=%u frames (%u ms) scratch=%lu state=%u cal I=%02x Q=%02x -> 45=%02x 46=%02x",
             ECHO_S, maxf, maxf * 100u, (unsigned long)sz, (unsigned)sizeof(echo_state), ig, qg, i45, q46);
    api->say(line);
    uint32_t until = api->ms() + ECHO_S * 1000u;
    unsigned echoes = 0;
    while (echoes < ECHO_MAX && !api->stop() && (int32_t)(api->ms() - until) < 0) {
        st->last_ms = api->ms();
        if (echo_listen(api, st, maxf, until) == 0)
            continue;
        const ye_state *e = &st->e;
        api->fmt(line, sizeof line, "echo heard n=%u dropped=%u hdr=%u term=%u voice=%u other=%u gaps=%u ended=%d src=%s rx_frames=%lu lost=%lu bad=%lu",
                 e->n, e->dropped, e->hdr, e->term, e->voice, e->other, e->gaps, e->ended, e->src,
                 (unsigned long)st->rx.frames, (unsigned long)st->rx.lost, (unsigned long)st->rx.bad);
        api->say(line);
        uint32_t t0 = api->ms();
        while (api->ms() - t0 < ECHO_GAP_MS && !api->stop())
            api->sleep_ms(10);
        if (api->stop())
            break;
        int r = echo_send(api, st, i45, q46);
        if (r == -1)
            break;
        api->say(r == 0 ? "echo sent" : "echo short");
        echoes++;
    }
    api->fmt(line, sizeof line, "end echoes=%u", echoes);
    api->say(line);
    return (int)echoes;
}
