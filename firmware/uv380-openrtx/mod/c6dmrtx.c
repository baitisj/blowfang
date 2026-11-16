/* c6dmrtx: a sotto message out through the HR_C6000's own DMR modem: rate 1/2 data bursts (the
   chip's BPTC(196,96), slot type, sync), one every 60 ms in direct mode, 9 bytes of
   c/common/dmr_sotto.h payload each = sotto 1200 in real time. Step 3 of
   docs/uv380-c6000-modem.md.

   The image has no DMR transmit path, so the keying is borrowed: api->tx_tone keys FM at 1 W on
   440.850 MHz only (it refuses anywhere else) and sends the Morse ident after. Once the C6000's
   analog TX is up (0x60 bit 7), this switches the chip to DMR layer 2 with active timing
   (RadioFoundry modem-hrc6000.md 3.1, 6.1-6.2, MIT): two-point modulation with the radio's own
   DMR calibration pair (w25sec.h, read only; or TX_I / TX_Q), colour code TX_CC, impolite
   access, 0x40 = 0xE3. At every slot edge (c6slot.h, PC0) it sets up the slot after: ours gets
   the next block in TX RAM, 0x50 = data type, 0x41 = 0x80; the other 0x41 = 0. First TX_PRE CSBK
   preambles (receivers lock), then the message's blocks. Then every register goes back to what
   the image had, before the tone ends, so the ident goes out in FM as always.

   TX_DRY 1 (the default) does all of it with no keying: the chip frames bursts that go nowhere
   (no PA, the AT1846S stays in receive), which proves the slot clock, the RF TX interrupt and
   the TX start / end flags before anything goes on air. A wrapper with TX_DRY 0 transmits:
   only with Jeff at the radio (project rule: no UV-380 TX while he is away).

   TX_SRC 0 sends a test pattern (superframes counting up), 1 the radio's message (what PTT and
   # use: the last recording or the last message heard).

   Lines: "cal I= Q=", "key ok after N ms", "slot0 after N ms", "tx blocks=N/M edges= rftx=
   82|= late=", "end". TX_MS (<= 5000) is the whole keyed time.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "w25sec.h"
#include "../../../c/common/dmr_sotto.h"

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
#ifndef TX_PRE
#define TX_PRE 3u
#endif
#ifndef TX_I
#define TX_I 0u                 /* 0: from the calibration table */
#endif
#ifndef TX_Q
#define TX_Q 0u
#endif
#ifndef TX_ID
#define TX_ID 1u
#endif
#define TX_TEXT "AG7EW"
#define TX_FKHZ 440850u
#define TX_SETTLE_MS 80u        /* after 0x60 shows the analog TX: AT1846S TX + PA (OpenRTX 50 ms) */
#define TX_TAIL_MS 350u         /* registers back this long before the keying ends */
#define TX_MAXBLK ((TX_MS - 300u - TX_TAIL_MS) / 60u)

/* the test pattern: superframe k is bytes 6k..6k+5 counting up */
static unsigned test_msg(uint8_t *buf, unsigned nsf) {
    for (unsigned i = 0; i < nsf * DS_SF; i++)
        buf[i] = (uint8_t)i;
    return nsf * DS_SF;
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x01, 0x11, 0x1F, 0x20, 0x21, 0x45, 0x46, 0x60, 0x5F,
                                   0x81, 0x26, 0x50, 0x40, 0x41, 0};
    char line[180];
    c6_saved sv;
    c6s_state ss;
    uint32_t sz = 0;
    uint8_t *scratch = (uint8_t *)api->scratch(&sz);

    /* the message: at most what fits the keyed time */
    unsigned maxblk = TX_MAXBLK - TX_PRE, textlen = sizeof TX_TEXT - 1;
    unsigned maxbytes = (maxblk - 2u - ds_ntext(textlen)) * DS_PAYLOAD;
    maxbytes -= maxbytes % DS_SF;
    const uint8_t *msg;
    unsigned nbytes;
    if (TX_SRC == 1) {
        nbytes = api->msg(&msg) * DS_SF;
    } else {
        if (sz < maxbytes)
            maxbytes = sz - sz % DS_SF;
        nbytes = test_msg(scratch, maxbytes / DS_SF);
        msg = scratch;
    }
    if (nbytes > maxbytes)
        nbytes = maxbytes;
    unsigned nblk = ds_nblocks(nbytes, textlen);

    unsigned ig = TX_I, qg = TX_Q;
    if (ig == 0 || qg == 0) {
        if (w25_dmr_pair(api, TX_FKHZ, &ig, &qg)) {
            api->say("err cal: DMR gains unreadable, nothing sent");
            return -1;
        }
    }
    api->fmt(line, sizeof line, "cal I=%02x Q=%02x (now 45=%02x 46=%02x) msg %u superframes in %u blocks, dry=%d",
             ig, qg, api->c6000_read(0x45), api->c6000_read(0x46), nbytes / DS_SF, nblk, TX_DRY);
    api->say(line);
    if (ig < 0x08u || ig > 0xF8u || qg < 0x08u || qg > 0xF8u) {
        api->say("err cal: gains out of range, nothing sent");
        return -1;
    }
    if (c6s_open(&ss)) {
        api->say("err irq: EXTI0-2 in use, no slot clock, nothing sent");
        return -1;
    }

    uint32_t t_key = api->ms();
    if (!TX_DRY) {
        if (api->tx_tone(TX_MS, 1000)) {
            api->say("err tx_tone refused (not on 440.850 MHz, or TX off)");
            c6s_close(&ss);
            return -1;
        }
        while ((api->c6000_read(0x60) & 0x80u) == 0) {
            if (api->ms() - t_key > 400u || api->stop()) {
                api->say("err the keying never started");
                c6s_close(&ss);
                return -1;
            }
            api->sleep_ms(1);
        }
        api->sleep_ms(TX_SETTLE_MS);
        api->fmt(line, sizeof line, "key ok after %lu ms", (unsigned long)(api->ms() - t_key));
        api->say(line);
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
    api->c6000_write(0x20, 0x00);           /* impolite: send whatever is on the channel */
    api->c6000_write(0x21, 0x02);
    api->c6000_write(0x5F, 0xF0);
    api->c6000_write(0x81, 0x1F);
    api->c6000_write(0x83, 0xFF);
    api->c6000_write(0x41, 0x00);
    c6s_poll(&ss);
    api->c6000_write(0x40, 0xE3);           /* TX and RX, active timing */

    unsigned edges = 0, rftx = 0, sent = 0, late = 0, ours = 1, quiet = 0;
    uint8_t f82 = 0;
    uint32_t t0 = api->ms(), first = 0, last_edge = 0;
    uint8_t blk[DS_BLOCK];
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
            } else if (sent < TX_PRE + nblk && now < t_end - 60u) {
                if (sent < TX_PRE) {
                    uint8_t c[12] = {0x3D, 0x00, 0x80, 0, 0xFF, 0xFF, 0xFF, 0, 0, 0, 0, 0};
                    c[3] = (uint8_t)(TX_PRE - sent + nblk);
                    c6_write(api, C6_DATA, 0x00, c, 12);
                    api->c6000_write(0x50, 0x30);       /* CSBK: the chip adds its CRC */
                } else {
                    ds_make(blk, sent - TX_PRE, msg, nbytes, TX_ID, TX_TEXT, textlen);
                    c6_write(api, C6_DATA, 0x00, blk, DS_BLOCK);
                    api->c6000_write(0x50, 0x70);       /* rate 1/2 data */
                }
                api->c6000_write(0x41, 0x80);
                sent++;
            } else {
                api->c6000_write(0x41, 0x00);
                if (++quiet >= 2)
                    break;                              /* the last burst is out */
            }
        }
        if (now - t0 > 400u && edges == 0) {
            api->say("err no slot edges on PC0: stopped");
            break;
        }
        if (now >= t_end)
            break;
        api->sleep_ms(1);
    }

    api->c6000_write(0x41, 0x00);
    api->c6000_write(0x40, 0x43);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    c6s_close(&ss);
    api->fmt(line, sizeof line, "tx blocks=%u/%u edges=%u first=%lu ms rftx=%u 82|=%02x late=%u restored at %lu ms",
             sent, TX_PRE + nblk, edges, (unsigned long)first, rftx, f82, late,
             (unsigned long)(api->ms() - t_key));
    api->say(line);
    if (!TX_DRY) {
        while (api->ms() - t_key < TX_MS + 200u && !api->stop())
            api->sleep_ms(10);
    }
    api->say("end");
    return 0;
}
