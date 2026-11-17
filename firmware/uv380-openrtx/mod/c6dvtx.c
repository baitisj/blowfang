/* c6dvtx: a sotto message out as a DMR voice call through the HR_C6000's own modem: 3 voice LC
   headers, then whole superframes of voice bursts A-F whose 216 payload bits each carry one
   c/common/dmr_sotto.h block under our own FEC (c/common/dmr_voice.h: K=7 rate 1/2, interleaved),
   then the terminator. One burst per 60 ms in direct mode: 9 payload bytes = sotto 1200 in real
   time, as c6dmrtx's data bursts. docs/uv380-c6000-modem.md, "Sotto in DMR voice bursts".

   What differs from c6dmrtx (whose keying, slot clock and register set-up this follows): the
   voice payload goes over the chip's voice SPI port (c6vspi.h: PB12-PB15, page 3), not U_SPI; the
   vocoder select 0x06 = 0x21 (voice over V_SPI under MCU control) and 0x22 = TX_ENC (encode start)
   for the call, 0x22 = 0x40 (encode stop) and 0x06 as found after; the LC (group call to TX_DST
   from TX_RADIO) sits in page 2 for the whole call: the chip codes the headers and the terminator
   from it, and the embedded LC in bursts B-E. Burst set-up per RadioFoundry modem-hrc6000.md 6.1
   and 6.3 (MIT): header 0x50 = 0x10, voice n (A..F = 0..5) 0x50 = 0x08 | n << 4, terminator 0x50
   = 0x20; 0x41 = 0x80 for our slot, 0 for the other.

   TX_SRC 0 (the default) sends a test message (superframes counting up), 1 the radio's message
   (what PTT and # use), 2 the raw test pattern dv_pn(k) in burst k, with no FEC: the host learns
   from it how page 3's bits land on the air (modem/dmrvoice.py learn_map). The bursts after the
   message's last block, up to a whole superframe, repeat its end block (a receiver ignores a
   repeat).

   TX_VIA picks the port the 27 payload bytes go through: 0 (the default) V_SPI (c6vspi.h), as
   OpenGD77's GD-77 driver hands its AMBE frames over (SPI1, page 3, reg 0, in the slot interrupt);
   1 U_SPI page 3 (c6spi.h C6_SOUND), the bus the chip's registers are on (OpenGD77's init fills
   page 3 that way); 2 both by turns, a superframe each, V_SPI first, so one call tells which
   the encoder takes. V1 (11-17, 0x22 = 0x80, V_SPI) and V1c (0x86, both) decoded once the
   capture's carrier was mixed to 0 Hz: V_SPI bit exact in plain order, U_SPI page 3 random.

   TX_DRY 1 (the default) does all of it with no keying (no PA, the AT1846S stays in receive): it
   proves the port, the slot clock and the burst set-up before anything goes on air. Wrappers with
   TX_DRY 0 transmit, at API level 4 only (ident first, transmit guard), on the radio's channel
   (api->tx_tone keys only 441.850 or 440.850 MHz): only with Jeff at the radio, or at 0 W.

   Lines: "cal I= Q= ...", the keying's lines (c6key.h), "dmr on", "tx enc= via= u= hdr=N voice=N/M term=N
   edges= first= rftx= 82|= late= restored at", "end". While keyed they are kept for the key-down.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "c6vspi.h"
#include "c6key.h"
#include "w25sec.h"
#include "../../../c/common/dmr_sotto.h"
#include "../../../c/common/dmr_voice.h"

#ifndef TX_DRY
#define TX_DRY 1
#endif
#ifndef TX_MS
#define TX_MS 4600u
#endif
#ifndef TX_SRC
#define TX_SRC 0
#endif
#ifndef TX_CC
#define TX_CC 1u
#endif
#ifndef TX_HDR
#define TX_HDR 3u
#endif
#ifndef TX_ENC
#define TX_ENC 0x86u            /* 0x22: vocoder encode start; 0x86 as RadioFoundry and OpenGD77 (facts
                                   only, GPL) write it ("Start Vocoder Encode, I2S mode"); V1 sent
                                   0x80 and the chip sent no voice bursts */
#endif
#ifndef TX_VIA
#define TX_VIA 0
#endif
#ifndef TX_I
#define TX_I 0u                 /* 0: from the calibration table */
#endif
#ifndef TX_Q
#define TX_Q 0u
#endif
#ifndef TX_ID
#define TX_ID 1u                /* the dmr_sotto message id */
#endif
#ifndef TX_DST
#define TX_DST 9u               /* talk group 9 (local) */
#endif
#ifndef TX_RADIO
#define TX_RADIO 1u             /* the LC's source ID */
#endif
#define TX_TEXT "AG7EW"
#define TX_FKHZ 441850u         /* the UV-380's bench channel: the calibration row */
#define TX_TAIL_MS 350u         /* registers back this long before the keying ends */
#define TX_BURSTS ((TX_MS - 300u - TX_TAIL_MS) / 60u)
#define TX_MAXV (((TX_BURSTS - TX_HDR - 1u) / 6u) * 6u)     /* voice bursts: whole superframes */

/* the test message: superframe k is bytes 6k..6k+5 counting up */
static unsigned test_msg(uint8_t *buf, unsigned nsf) {
    for (unsigned i = 0; i < nsf * DS_SF; i++)
        buf[i] = (uint8_t)i;
    return nsf * DS_SF;
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x01, 0x11, 0x1F, 0x20, 0x21, 0x45, 0x46, 0x60, 0x5F,
                                   0x81, 0x26, 0x50, 0x40, 0x41, 0x06, 0};
    char line[180];
    c6_saved sv;
    c6s_state ss;
    vs_port vp;
    uint32_t sz = 0;
    uint8_t *scratch = (uint8_t *)api->scratch(&sz);

    /* the message: at most what TX_MAXV bursts carry */
    unsigned textlen = sizeof TX_TEXT - 1;
    unsigned maxbytes = (TX_MAXV - 2u - ds_ntext(textlen)) * DS_PAYLOAD;
    maxbytes -= maxbytes % DS_SF;
    const uint8_t *msg = scratch;
    unsigned nbytes = 0, nblk;
    if (TX_SRC == 1) {
        nbytes = api->msg(&msg) * DS_SF;
    } else if (TX_SRC == 0) {
        if (sz < maxbytes)
            maxbytes = sz - sz % DS_SF;
        nbytes = test_msg(scratch, maxbytes / DS_SF);
    }
    if (nbytes > maxbytes)
        nbytes = maxbytes;
    nblk = TX_SRC == 2 ? TX_MAXV : ds_nblocks(nbytes, textlen);
    unsigned nvoice = (nblk + 5u) / 6u * 6u;
    if (nvoice > TX_MAXV)
        nvoice = TX_MAXV;

    unsigned ig = TX_I, qg = TX_Q;
    if (ig == 0 || qg == 0) {
        if (w25_dmr_pair(api, TX_FKHZ, &ig, &qg)) {
            api->say("err cal: DMR gains unreadable, nothing sent");
            return -1;
        }
    }
    api->fmt(line, sizeof line, "cal I=%02x Q=%02x (now 45=%02x 46=%02x 06=%02x) src %d: %u blocks in %u voice bursts, dry=%d",
             ig, qg, api->c6000_read(0x45), api->c6000_read(0x46), api->c6000_read(0x06), TX_SRC,
             nblk, nvoice, TX_DRY);
    api->say(line);
    if (ig < 0x08u || ig > 0xF8u || qg < 0x08u || qg > 0xF8u) {
        api->say("err cal: gains out of range, nothing sent");
        return -1;
    }
    api->fmt(line, sizeof line, "vspi pins PB12-15 modes=%02x", vs_modes());
    api->say(line);
    if (vs_open(&vp)) {
        api->say("err PB12-15 claimed: no voice port, nothing sent");
        return -1;
    }
    if (c6s_open(&ss)) {
        api->say("err irq: EXTI0-2 in use, no slot clock, nothing sent");
        vs_close(&vp);
        return -1;
    }

    uint32_t t_key = api->ms();
    c6k_notes q;
    q.n = 0;
    q.quiet = 0;
    if (!TX_DRY && c6k_key(api, &q, TX_MS, &t_key)) {
        c6s_close(&ss);
        vs_close(&vp);
        return -1;
    }
    uint32_t t_end = t_key + TX_MS - TX_TAIL_MS;

    c6_save(api, &sv, regs);
    api->c6000_write(0x60, 0x00);           /* the FM chain off: the DMR modem modulates */
    api->c6000_write(0x01, 0xF0);           /* two-point TX */
    api->c6000_write(0x45, (uint8_t)ig);
    api->c6000_write(0x46, (uint8_t)qg);
    api->c6000_write(0x10, 0x6E);           /* DMR, Tier II, layer 2 */
    api->c6000_write(0x11, 0x80);
    api->c6000_write(0x1F, (uint8_t)(TX_CC << 4));
    api->c6000_write(0x06, 0x21);           /* voice over V_SPI under MCU control */
    api->c6000_write(0x20, 0x00);           /* impolite: send whatever is on the channel */
    api->c6000_write(0x21, 0x02);           /* and clear the encode buffer */
    api->c6000_write(0x5F, 0xF0);
    api->c6000_write(0x81, 0x1F);
    api->c6000_write(0x83, 0xFF);
    api->c6000_write(0x41, 0x00);
    {
        /* the call's LC: group call (FLCO 0), FID 0, no options; the chip adds the RS parity */
        uint8_t lc[12] = {0, 0, 0, (uint8_t)(TX_DST >> 16), (uint8_t)(TX_DST >> 8), (uint8_t)TX_DST,
                          (uint8_t)(TX_RADIO >> 16), (uint8_t)(TX_RADIO >> 8), (uint8_t)TX_RADIO, 0, 0, 0};
        c6_write(api, C6_DATA, 0x00, lc, 12);
    }
    api->c6000_write(0x22, (uint8_t)TX_ENC);
    c6s_poll(&ss);
    api->c6000_write(0x40, 0xE3);           /* TX and RX, active timing */
    c6k_note(api, &q, "dmr on");

    unsigned edges = 0, rftx = 0, step = 0, hdr = 0, voice = 0, term = 0, late = 0, ours = 1, quiet = 0;
    unsigned via_u = 0;                         /* voice bursts sent through U_SPI page 3 */
    uint8_t f82 = 0;
    uint32_t t0 = api->ms(), first = 0, last_edge = 0;
    uint8_t blk[DS_BLOCK], pay[DV_BYTES];
    while (!api->stop()) {
        uint32_t now = api->ms();
        unsigned e = c6s_poll(&ss);
        rftx += (e & C6S_RFTX) != 0;
        uint8_t f = api->c6000_read(0x82);
        if (f) {
            f82 |= f;
            api->c6000_write(0x83, f);
        }
        if (e & C6S_SLOT) {
            if (edges == 0)
                first = now - t0;
            else if (now - last_edge > 33u)
                late++;
            edges++;
            last_edge = now;
            ours ^= 1u;
            if (!ours) {
                api->c6000_write(0x41, 0x00);
            } else if (step < TX_HDR + nvoice + 1u && now < t_end - 60u) {
                if (step < TX_HDR) {
                    api->c6000_write(0x50, 0x10);       /* voice LC header */
                    hdr++;
                } else if (step < TX_HDR + nvoice) {
                    unsigned k = step - TX_HDR;
                    if (TX_SRC == 2) {
                        dv_pn(k, pay);
                    } else {
                        ds_make(blk, k < nblk ? k : nblk - 1u, msg, nbytes, TX_ID, TX_TEXT, textlen);
                        dv_encode(blk, pay);
                    }
                    if (TX_VIA == 1 || (TX_VIA == 2 && (k / 6u) % 2u == 1u)) {
                        c6_write(api, C6_SOUND, 0x00, pay, DV_BYTES);
                        via_u++;
                    } else {
                        vs_write(pay);
                    }
                    api->c6000_write(0x50, (uint8_t)(0x08u | ((k % 6u) << 4)));
                    voice++;
                } else {
                    for (unsigned i = 0; i < DV_BYTES; i++)
                        pay[i] = 0;
                    if (TX_VIA == 1)
                        c6_write(api, C6_SOUND, 0x00, pay, DV_BYTES);
                    else
                        vs_write(pay);
                    api->c6000_write(0x50, 0x20);       /* terminator with LC */
                    term++;
                }
                api->c6000_write(0x41, 0x80);
                step++;
            } else {
                api->c6000_write(0x41, 0x00);
                if (++quiet >= 2)
                    break;                              /* the last burst is out */
            }
        }
        if (now - t0 > 400u && edges == 0) {
            c6k_note(api, &q, "err no slot edges on PC0: stopped");
            break;
        }
        if (now >= t_end)
            break;
        api->sleep_ms(1);
    }

    api->c6000_write(0x41, 0x00);
    api->c6000_write(0x22, 0x40);           /* encode stop */
    api->c6000_write(0x40, 0x43);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    c6s_close(&ss);
    vs_close(&vp);
    api->fmt(line, sizeof line, "tx enc=%02x via=%d u=%u hdr=%u voice=%u/%u term=%u edges=%u first=%lu ms rftx=%u 82|=%02x late=%u restored at %lu ms",
             (unsigned)TX_ENC, TX_VIA, via_u, hdr, voice, nvoice, term, edges, (unsigned long)first, rftx, f82, late,
             (unsigned long)(api->ms() - t_key));
    c6k_note(api, &q, line);
    if (!TX_DRY) {
        while (api->ms() - t_key < TX_MS + 200u && !api->stop())
            api->sleep_ms(10);
    }
    c6k_flush(api, &q, 2000u);
    api->say("end");
    return 0;
}
