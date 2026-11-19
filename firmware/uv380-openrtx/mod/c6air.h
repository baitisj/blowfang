/* c6air.h: the radio's sotto message out as M17, YSF (C4FM) or DMR through the shared HR_C6000
   core (c/common/c6_core.c) and the on-radio framers (c/common/m17_tx.c, ysf_tx.c, dmr_sotto.h +
   dmr_voice.h), the mode picked at run time: docs/uv380-multimode.md. One body for both users:
     - the c6sotto RAM modules (c6sotto.c: the mode a constant, so the other two drop out), proven
       on air 11-18 (job sotto3-air-1118);
     - the image (milestone 4, flash 1): PAGER mode's M17 / YSF / DMR sub-modes and the "air"
       command run it from flash with the image's own module API table (ortx_mod.c om_api).
   c6air_run(api, air, src, dry):
     air  C6AIR_M17 1: sotto 1200 stream (TYPE stream data, META "SOTTO 1200"), two superframes a
                       40 ms frame, at TX_DEV 123 % of the DMR pair (2400 Hz outer)
          C6AIR_YSF 2: the sotto softmodem's sotto VW frames, five superframes a 100 ms frame,
                       FICH sotto flag, at 140 % (2712 Hz measured 11-18)
          C6AIR_DMR 3: a group voice call to TG 9 whose bursts carry dmr_sotto blocks under the
                       voice FEC (c6dvtx's format), at the DMR pair itself
     src  1: the radio's message (api->msg: the last recording or message heard, what PTT
          sends); 0: a test message (superframe k = bytes 6k..6k+5 counting up)
     dry  1: everything with no keying
   The message is cut to what one key-up carries (tx_tone's 5 s): about 8 s of sotto as M17 or
   YSF, 4 s as DMR.

   Keying as every proven module: api->tx_tone (the Morse ident AG7EW first, the image's transmit
   guard, 441.850 or 440.850 MHz only, the channel's power), the engine's registers put back
   TX_TAIL_MS before the tone ends, nothing said over USB while keyed. The raw mode sits about
   6.7 kHz above the FM carrier: set the transmit trim first (FM trim - 6680) and back after.

   Lines: "sotto AIR sf=N/M first=HEX last=HEX bytes=B tx_ms=T cal I= Q= -> 45= 46= dry=", the
   keying's lines, "tx slots= bytes= edges= first= late= lost= chk= done=" (or for DMR "tx hdr=
   voice=/ term= ..."), "sotto done" or "sotto short", "end".

   Include ortx_mod.h, c6spi.h, c6slot.h, c6key.h, w25sec.h and c6vspi.h first. Header only, no
   writable statics. SPDX-License-Identifier: MIT */
#ifndef C6AIR_H
#define C6AIR_H

#include "../../../c/common/c6_core.c"
#include "../../../c/common/m17_tx.c"
#include "../../../c/common/ysf_tx.c"
#include "../../../c/common/dmr_sotto.h"
#include "../../../c/common/dmr_voice.h"
#if C6AIR_WITH_DSR             /* DSotto raw: RAM modules only so far (c6sotto_dsr*.c), not the image */
#include "../../../c/common/dsr_tx.c"
#endif
#include "c6bus_mod.h"

#define C6AIR_M17 1u
#define C6AIR_YSF 2u
#define C6AIR_DMR 3u
#define C6AIR_DSR 4u
#ifndef C6AIR_WITH_DSR
#define C6AIR_WITH_DSR 0
#endif

#ifndef TX_FKHZ
#define TX_FKHZ 441850u         /* the calibration pair's frequency: the UV-380's bench channel */
#endif
#define C6AIR_TAIL_MS 350u
#define C6AIR_MAX_MS 5000u      /* tx_tone's limit */
#define C6AIR_SF 6u
#define C6AIR_CALL "AG7EW"
#define C6AIR_DMR_MS 4600u      /* c6dvtx's key-up */
#define C6AIR_DMR_HDR 3u
#define C6AIR_DMR_BURSTS ((C6AIR_DMR_MS - 300u - C6AIR_TAIL_MS) / 60u)
#define C6AIR_DMR_MAXV (((C6AIR_DMR_BURSTS - C6AIR_DMR_HDR - 1u) / 6u) * 6u)

#define C6AIR_FN static __attribute__((unused))

/* the key-up for b bytes of 4FSK at 1200 bytes/s: settle, a slot to the first edge, the data,
   three pad slots, the tail */
C6AIR_FN uint32_t c6air_fsk4_ms(uint32_t b) {
    return C6K_SETTLE_MS + 30u + (b * 1000u + 1199u) / 1200u + 90u + C6AIR_TAIL_MS + 100u;
}

typedef struct {
    const uint8_t *msg;
    unsigned nbytes, nblk;
} c6air_dmr_src;

C6AIR_FN void c6air_dmr_voice(void *s, unsigned k, uint8_t pay[C6C_DV_BYTES]) {
    const c6air_dmr_src *d = s;
    uint8_t blk[DS_BLOCK];
    ds_make(blk, k < d->nblk ? k : d->nblk - 1u, d->msg, d->nbytes, 1u, C6AIR_CALL, sizeof C6AIR_CALL - 1u);
    dv_encode(blk, pay);
}

C6AIR_FN unsigned c6air_pull_ysf(void *s, uint8_t *o, unsigned n) {
    return ysft_sotto_read(s, o, n);
}

C6AIR_FN unsigned c6air_pull_m17(void *s, uint8_t *o, unsigned n) {
    return m17t_sotto_read(s, o, n);
}

#if C6AIR_WITH_DSR
C6AIR_FN unsigned c6air_pull_dsr(void *s, uint8_t *o, unsigned n) {
    return dsrt_msg_read(s, o, n);
}
#endif

/* 0x50 after a whole send, -1 refused before keying, -2 short */
C6AIR_FN int c6air_run(const ortx_api_t *api, unsigned air, unsigned src, unsigned dry) {
    static const char names[5][4] = {"?", "m17", "ysf", "dmr", "dsr"};
    char line[180];
    uint32_t sz = 0;
    uint8_t *scratch = (uint8_t *)api->scratch(&sz);
    const uint8_t *msg = scratch;
    unsigned nsf = 0, have;
    unsigned dev = air == C6AIR_DMR ? 100u : air == C6AIR_YSF ? 140u : 123u;
    if (air < C6AIR_M17 || air > (C6AIR_WITH_DSR ? C6AIR_DSR : C6AIR_DMR)) {
        api->say(C6AIR_WITH_DSR ? "err air: m17, ysf, dmr or dsr" : "err air: m17, ysf or dmr");
        return -1;
    }
    if (src) {
        nsf = api->msg(&msg);
    } else {
        nsf = sz / C6AIR_SF < 400u ? sz / C6AIR_SF : 400u;
        for (unsigned i = 0; i < nsf * C6AIR_SF; i++)
            scratch[i] = (uint8_t)i;
    }
    have = nsf;
    if (nsf == 0) {
        api->say("err no message (record one, or the test message)");
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
    uint8_t i45 = c6c_dev(ig, dev), q46 = c6c_dev(qg, dev);

    /* cut the message to one key-up */
    uint32_t tx_ms = 0, bytes = 0;
    c6air_dmr_src d = {msg, 0, 0};
    unsigned nvoice = 0;
    m17t_sotto ms;
    ysft_sotto ys;
#if C6AIR_WITH_DSR
    dsrt_msg ds;
#endif
    if (air == C6AIR_DMR) {
        unsigned textlen = sizeof C6AIR_CALL - 1u;
        unsigned maxbytes = (C6AIR_DMR_MAXV - 2u - ds_ntext(textlen)) * DS_PAYLOAD;
        maxbytes -= maxbytes % C6AIR_SF;
        if (nsf * C6AIR_SF > maxbytes)
            nsf = maxbytes / C6AIR_SF;
        d.nbytes = nsf * C6AIR_SF;
        d.nblk = ds_nblocks(d.nbytes, textlen);
        nvoice = (d.nblk + 5u) / 6u * 6u;
        if (nvoice > C6AIR_DMR_MAXV)
            nvoice = C6AIR_DMR_MAXV;
        tx_ms = C6AIR_DMR_MS;
        bytes = d.nblk * DS_BLOCK;
    } else {
        for (;;) {
            if (air == C6AIR_YSF) {
                ysft_sotto_begin(&ys, msg, nsf, "ALL", C6AIR_CALL, 0);
                bytes = ysft_sotto_bytes(&ys);
#if C6AIR_WITH_DSR
            } else if (air == C6AIR_DSR) {
                dsrt_msg_begin(&ds, DSRT_120, msg, nsf, C6AIR_CALL);
                bytes = dsrt_msg_bytes(&ds);
#endif
            } else {
                m17t_sotto_begin(&ms, msg, nsf, C6AIR_CALL, "", 0);
                bytes = m17t_sotto_bytes(&ms);
            }
            tx_ms = c6air_fsk4_ms(bytes);
            if (tx_ms <= C6AIR_MAX_MS || nsf <= 1u)
                break;
            nsf--;
        }
    }
    char h0[2 * C6AIR_SF + 1], h1[2 * C6AIR_SF + 1];
    c6_hex(h0, msg, C6AIR_SF);
    c6_hex(h1, msg + C6AIR_SF * (nsf - 1u), C6AIR_SF);
    api->fmt(line, sizeof line, "sotto %s sf=%u/%u first=%s last=%s bytes=%lu tx_ms=%lu cal I=%02x Q=%02x dev=%u%% -> 45=%02x 46=%02x src=%u dry=%u",
             names[air], nsf, have, h0, h1, (unsigned long)bytes, (unsigned long)tx_ms, ig, qg, dev, i45, q46,
             src, dry);
    api->say(line);

    c6s_state ss;
    vs_port vp;
    if (air == C6AIR_DMR && vs_open(&vp)) {
        api->say("err PB12-15 claimed: no voice port, nothing sent");
        return -1;
    }
    if (c6s_open(&ss)) {
        api->say("err irq: EXTI0-2 in use, no slot clock, nothing sent");
        if (air == C6AIR_DMR)
            vs_close(&vp);
        return -1;
    }
    c6bm_ctx cx = {api, &ss};
    c6c_bus b;
    c6bm_bus(&b, &cx, air == C6AIR_DMR);

    uint32_t t_key = api->ms();
    c6k_notes q;
    q.n = 0;
    q.quiet = 0;
    if (!dry && c6k_key(api, &q, tx_ms, &t_key)) {
        c6s_close(&ss);
        if (air == C6AIR_DMR)
            vs_close(&vp);
        return -1;
    }

    c6c_stats st;
    int r;
    if (air == C6AIR_DMR) {
        c6c_dmr p = {i45, q46, 1u, 0x86u, 9u, 1u, C6AIR_DMR_HDR, nvoice, t_key + tx_ms - C6AIR_TAIL_MS};
        r = c6c_dmr_tx(&b, &p, c6air_dmr_voice, &d, &st);
        c6s_close(&ss);
        vs_close(&vp);
        api->fmt(line, sizeof line, "tx hdr=%u voice=%u/%u term=%u edges=%u first=%lu ms rftx=%u 82|=%02x late=%u done=%d restored at %lu ms",
                 st.hdr, st.voice, nvoice, st.term, st.edges, (unsigned long)st.first_ms, st.rftx, st.f82,
                 st.late, st.done, (unsigned long)(api->ms() - t_key));
    } else {
        c6c_fsk4 p;
        p.i45 = i45;
        p.q46 = q46;
        p.mode = 0x66u;
        p.fill = 0x77u;
        if (air == C6AIR_YSF || air == C6AIR_DSR) {
            p.pad0 = p.pad1 = 0x77u;
        } else {
            p.pad0 = 0x55u;         /* M17 EOT */
            p.pad1 = 0x5Du;
        }
        p.half = 0;
        p.t_end = t_key + tx_ms - C6AIR_TAIL_MS;
        if (air == C6AIR_YSF)
            r = c6c_fsk4_tx(&b, &p, c6air_pull_ysf, &ys, &st);
#if C6AIR_WITH_DSR
        else if (air == C6AIR_DSR)
            r = c6c_fsk4_tx(&b, &p, c6air_pull_dsr, &ds, &st);
#endif
        else
            r = c6c_fsk4_tx(&b, &p, c6air_pull_m17, &ms, &st);
        c6s_close(&ss);
        api->fmt(line, sizeof line, "tx slots=%u bytes=%lu/%lu edges=%u first=%lu ms rftx=%u late=%u lost=%u chk=%u/%u spi=%u done=%d restored at %lu ms",
                 st.slots, (unsigned long)st.bytes, (unsigned long)bytes, st.edges, (unsigned long)st.first_ms,
                 st.rftx, st.late, st.lost, st.chk0, st.chk1, st.spi_err, st.done, (unsigned long)(api->ms() - t_key));
    }
    c6k_note(api, &q, line);
    if (!dry) {
        while (api->ms() - t_key < tx_ms + 200u && !api->stop())
            api->sleep_ms(10);
    }
    c6k_flush(api, &q, 2000u);
    api->say(r == 0 ? "sotto done" : "sotto short");
    return r == 0 ? 0x50 : -2;
}

#endif
