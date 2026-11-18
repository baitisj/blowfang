/* c6sotto: the radio's sotto message out as M17, YSF (C4FM) or DMR, through the shared HR_C6000
   core (c/common/c6_core.c) and the on-radio framers (c/common/m17_tx.c, ysf_tx.c, dmr_sotto.h +
   dmr_voice.h): step 3 of docs/uv380-multimode.md, run as a RAM module before any of it goes in
   flash. The same core and framers then go into the image unchanged; only the bus differs
   (mod/c6bus_mod.h here).

   SOTTO_AIR picks the mode:
     1 M17   sotto 1200 stream (TYPE stream data, META "SOTTO 1200"), two superframes a 40 ms
             frame, the physical layer mode at TX_DEV 123 % of the DMR pair (2400 Hz outer)
     2 YSF   the sotto softmodem's sotto VW frames, five superframes a 100 ms frame, FICH sotto
             flag, at 140 % (2712 Hz measured 11-18)
     3 DMR   a group voice call to TG 9 whose bursts carry dmr_sotto blocks under the voice FEC
             (c6dvtx's format, 60/60 blocks 11-17), at the DMR pair itself
   SOTTO_SRC 1 (the default) sends the radio's message (api->msg: the last recording or message
   heard, what PTT sends); 0 a test message (superframe k = bytes 6k..6k+5 counting up). The
   message is cut to what one key-up carries (tx_tone's 5 s): about 8 s of sotto as M17 or YSF,
   4 s as DMR. TX_DRY 1 (the default) runs everything with no keying.

   Keying as every proven module: api->tx_tone (the Morse ident AG7EW first, the image's transmit
   guard, 441.850 or 440.850 MHz only, the channel's power: the bench runs at power 0), the
   engine's registers put back TX_TAIL_MS before the tone ends, nothing said over USB while keyed.
   The raw mode sits about 6.7 kHz above the FM carrier: the bench sets the transmit trim first
   (uv380.cmd "trim", FM trim - 6680) and back after, as for c6m17tx and c6ysftx.

   Lines: "sotto AIR sf=N/M first=HEX last=HEX bytes=B tx_ms=T cal I= Q= -> 45= 46= dry=" (the
   first and last superframe sent, to check a capture's decode against), the keying's lines, "tx
   slots= bytes= edges= first= late= lost= chk= done=" (or for DMR "tx hdr= voice=/ term= ..."),
   "sotto done" or "sotto short", "end".
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "c6key.h"
#include "w25sec.h"
#include "../../../c/common/c6_core.c"

#ifndef SOTTO_AIR
#define SOTTO_AIR 1
#endif
#ifndef SOTTO_SRC
#define SOTTO_SRC 1
#endif
#ifndef TX_DRY
#define TX_DRY 1
#endif
#ifndef TX_FKHZ
#define TX_FKHZ 441850u         /* the calibration pair's frequency: the UV-380's bench channel */
#endif
#define TX_TAIL_MS 350u
#define TX_MAX_MS 5000u         /* tx_tone's limit */
#define SF 6u
#define SRC_CALL "AG7EW"

#if SOTTO_AIR == 3
#include "c6vspi.h"
#include "../../../c/common/dmr_sotto.h"
#include "../../../c/common/dmr_voice.h"
#define AIR_NAME "dmr"
#define TX_DEV 100u
#define DMR_MS 4600u            /* c6dvtx's key-up */
#define DMR_HDR 3u
#define DMR_BURSTS ((DMR_MS - 300u - TX_TAIL_MS) / 60u)
#define DMR_MAXV (((DMR_BURSTS - DMR_HDR - 1u) / 6u) * 6u)
#elif SOTTO_AIR == 2
#define C6BUS_NO_VSPI
#include "../../../c/common/ysf_tx.c"
#define AIR_NAME "ysf"
#define TX_DEV 140u
#else
#define C6BUS_NO_VSPI
#include "../../../c/common/m17_tx.c"
#define AIR_NAME "m17"
#define TX_DEV 123u
#endif
#include "c6bus_mod.h"

#if SOTTO_AIR != 3
/* the key-up for b bytes of 4FSK at 1200 bytes/s: settle, a slot to the first edge, the data,
   three pad slots, the tail */
static uint32_t fsk4_ms(uint32_t b) {
    return C6K_SETTLE_MS + 30u + (b * 1000u + 1199u) / 1200u + 90u + TX_TAIL_MS + 100u;
}
#endif

#if SOTTO_AIR == 3
typedef struct {
    const uint8_t *msg;
    unsigned nbytes, nblk;
} dmr_src;

static void dmr_voice(void *s, unsigned k, uint8_t pay[C6C_DV_BYTES]) {
    const dmr_src *d = s;
    uint8_t blk[DS_BLOCK];
    ds_make(blk, k < d->nblk ? k : d->nblk - 1u, d->msg, d->nbytes, 1u, SRC_CALL, sizeof SRC_CALL - 1u);
    dv_encode(blk, pay);
}
#elif SOTTO_AIR == 2
static unsigned pull(void *s, uint8_t *o, unsigned n) {
    return ysft_sotto_read(s, o, n);
}
#else
static unsigned pull(void *s, uint8_t *o, unsigned n) {
    return m17t_sotto_read(s, o, n);
}
#endif

int uv_main(const ortx_api_t *api) {
    char line[180];
    uint32_t sz = 0;
    uint8_t *scratch = (uint8_t *)api->scratch(&sz);
    const uint8_t *msg = scratch;
    unsigned nsf = 0, have;
    if (SOTTO_SRC == 1) {
        nsf = api->msg(&msg);
    } else {
        nsf = sz / SF < 400u ? sz / SF : 400u;
        for (unsigned i = 0; i < nsf * SF; i++)
            scratch[i] = (uint8_t)i;
    }
    have = nsf;
    if (nsf == 0) {
        api->say("err no message (record one, or SOTTO_SRC 0)");
        return -1;
    }

    unsigned ig = 0, qg = 0;
    if (w25_dmr_pair(api, TX_FKHZ, &ig, &qg)) {
        api->say("err cal: DMR gains unreadable, nothing sent");
        return -1;
    }
    if (ig < 0x08u || ig > 0xF8u || qg < 0x08u || qg > 0xF8u) {
        api->say("err cal: gains out of range, nothing sent");
        return -1;
    }
    uint8_t i45 = c6c_dev(ig, TX_DEV), q46 = c6c_dev(qg, TX_DEV);

    /* cut the message to one key-up */
    uint32_t tx_ms, bytes;
#if SOTTO_AIR == 3
    unsigned textlen = sizeof SRC_CALL - 1u;
    unsigned maxbytes = (DMR_MAXV - 2u - ds_ntext(textlen)) * DS_PAYLOAD;
    maxbytes -= maxbytes % SF;
    if (nsf * SF > maxbytes)
        nsf = maxbytes / SF;
    dmr_src d = {msg, nsf * SF, ds_nblocks(nsf * SF, textlen)};
    unsigned nvoice = (d.nblk + 5u) / 6u * 6u;
    if (nvoice > DMR_MAXV)
        nvoice = DMR_MAXV;
    tx_ms = DMR_MS;
    bytes = d.nblk * DS_BLOCK;
#elif SOTTO_AIR == 2
    ysft_sotto s;
    for (;;) {
        ysft_sotto_begin(&s, msg, nsf, "ALL", SRC_CALL, 0);
        bytes = ysft_sotto_bytes(&s);
        tx_ms = fsk4_ms(bytes);
        if (tx_ms <= TX_MAX_MS || nsf <= 1u)
            break;
        nsf--;
    }
#else
    m17t_sotto s;
    for (;;) {
        m17t_sotto_begin(&s, msg, nsf, SRC_CALL, "", 0);
        bytes = m17t_sotto_bytes(&s);
        tx_ms = fsk4_ms(bytes);
        if (tx_ms <= TX_MAX_MS || nsf <= 1u)
            break;
        nsf--;
    }
#endif
    char h0[2 * SF + 1], h1[2 * SF + 1];
    c6_hex(h0, msg, SF);
    c6_hex(h1, msg + SF * (nsf - 1u), SF);
    api->fmt(line, sizeof line, "sotto %s sf=%u/%u first=%s last=%s bytes=%lu tx_ms=%lu cal I=%02x Q=%02x dev=%u%% -> 45=%02x 46=%02x src=%d dry=%d",
             AIR_NAME, nsf, have, h0, h1, (unsigned long)bytes, (unsigned long)tx_ms, ig, qg, TX_DEV, i45, q46,
             SOTTO_SRC, TX_DRY);
    api->say(line);

    c6s_state ss;
#if SOTTO_AIR == 3
    vs_port vp;
    if (vs_open(&vp)) {
        api->say("err PB12-15 claimed: no voice port, nothing sent");
        return -1;
    }
#endif
    if (c6s_open(&ss)) {
        api->say("err irq: EXTI0-2 in use, no slot clock, nothing sent");
#if SOTTO_AIR == 3
        vs_close(&vp);
#endif
        return -1;
    }
    c6bm_ctx cx = {api, &ss};
    c6c_bus b;
    c6bm_bus(&b, &cx, 1);

    uint32_t t_key = api->ms();
    c6k_notes q;
    q.n = 0;
    q.quiet = 0;
    if (!TX_DRY && c6k_key(api, &q, tx_ms, &t_key)) {
        c6s_close(&ss);
#if SOTTO_AIR == 3
        vs_close(&vp);
#endif
        return -1;
    }

    c6c_stats st;
    int r;
#if SOTTO_AIR == 3
    c6c_dmr p = {i45, q46, 1u, 0x86u, 9u, 1u, DMR_HDR, nvoice, t_key + tx_ms - TX_TAIL_MS};
    r = c6c_dmr_tx(&b, &p, dmr_voice, &d, &st);
    c6s_close(&ss);
    vs_close(&vp);
    api->fmt(line, sizeof line, "tx hdr=%u voice=%u/%u term=%u edges=%u first=%lu ms rftx=%u 82|=%02x late=%u done=%d restored at %lu ms",
             st.hdr, st.voice, nvoice, st.term, st.edges, (unsigned long)st.first_ms, st.rftx, st.f82,
             st.late, st.done, (unsigned long)(api->ms() - t_key));
#else
    c6c_fsk4 p;
    p.i45 = i45;
    p.q46 = q46;
    p.mode = 0x66u;
    p.fill = 0x77u;
#if SOTTO_AIR == 2
    p.pad0 = p.pad1 = 0x77u;
#else
    p.pad0 = 0x55u;             /* M17 EOT */
    p.pad1 = 0x5Du;
#endif
    p.half = 0;
    p.t_end = t_key + tx_ms - TX_TAIL_MS;
    r = c6c_fsk4_tx(&b, &p, pull, &s, &st);
    c6s_close(&ss);
    api->fmt(line, sizeof line, "tx slots=%u bytes=%lu/%lu edges=%u first=%lu ms rftx=%u late=%u lost=%u chk=%u/%u spi=%u done=%d restored at %lu ms",
             st.slots, (unsigned long)st.bytes, (unsigned long)bytes, st.edges, (unsigned long)st.first_ms,
             st.rftx, st.late, st.lost, st.chk0, st.chk1, st.spi_err, st.done, (unsigned long)(api->ms() - t_key));
#endif
    c6k_note(api, &q, line);
    if (!TX_DRY) {
        while (api->ms() - t_key < tx_ms + 200u && !api->stop())
            api->sleep_ms(10);
    }
    c6k_flush(api, &q, 2000u);
    api->say(r == 0 ? "sotto done" : "sotto short");
    api->say("end");
    return r == 0 ? 0x50 : -2;
}
