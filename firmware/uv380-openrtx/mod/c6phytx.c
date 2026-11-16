/* c6phytx: plain 2FSK at 2400 (or 1200) baud straight out of the HR_C6000's physical layer mode,
   for the K6's chip FSK receiver (c/k6/mod/fsk24rx_uv.c). No audio path at all: the MCU's PWM,
   the line in, the C6000's pre-emphasis and its 300 Hz cut (why the K6 read none of the UV-380's
   FFSK on 11-14) are all bypassed. Experiment 3 of docs/uv380-c4fm-foundry.md; the stream and
   the symbol packing are c/common/c6phy_fsk.h.

   The mode (RadioFoundry docs/spec/modem-hrc6000.md 9.1-9.2, MIT): 0x10 = 0x66 (physical layer,
   LayerMode 00) sends TX RAM page 2 as given, 288 bits a 30 ms slot, from 0x00-0x23 and
   0x24-0x47 in alternate slots, each latched at its slot start. Both halves get the next slot's
   bytes at every timeslot interrupt (PC0, c6slot.h), then 0x41 = 0x80, so every slot carries
   ours and the carrier never runs dry of symbols: RadioFoundryC4FM keeps an HR_C7000 keyed for
   whole C4FM calls this way, with the idle pattern when its queue is empty. Here the queue
   never empties: after the last packet the stream is 0x55, so the K6's bit clock sees no gap.

   The keying is borrowed, as c6dmrtx does (its notes apply): api->tx_tone keys FM on 440.850 MHz
   only, at the channel's power (the bench sets "power 0" and "zero 2": the PA on with its APC at
   0), the Morse ident first under the image's transmit guard (API level 4: a key-up that outlives
   its length by 1.5 s resets the radio). Once the tone's own key-up shows in 0x60 the module
   puts the chip in two-point modulation with the radio's DMR calibration pair (scaled by TX_DEV
   percent: the deviation; C4FM wants 139), physical layer mode, active timing (0x40 = 0xE3), and
   feeds the slots. Every register goes back TX_TAIL_MS before the tone ends, and the tone's last
   moments are the plain 1 kHz line-in tone again.

   TX_DRY 1 (the default) runs all of it with no keying: the chip clocks slots and takes TX RAM
   with no PA and the AT1846S in receive. A wrapper with TX_DRY 0 transmits (c6phytx_live.c:
   2400 baud; c6phytx_live1200.c). Only with Jeff around (no UV-380 TX while he is away).

   TX_BAUD 2400 or 1200; TX_POL 0 (bit 1 = +3), 1 (bit 1 = -3), 2 (alternate packet by packet:
   variant 20/22 for polarity 0, 21/23 for 1, so one key-up says which way the K6 reads);
   TX_MODE 0x66 (slotted physical layer; 0x02, continuous, is untested on a C6000); TX_SEQ the
   first sequence number (so runs can be told apart in the K6's log).

   Lines: "cal", "tx_tone ok", "ident keyed after", "ident done after", "key ok after", "phy on",
   "chk" (TX RAM read back through page 6 after the first slot's write: both halves as written),
   "tx slots= pk= edges= first= rftx= late= lost= restored at", "end". From tx_tone to the
   key-down the lines are kept and said after it (a say can block while keyed: 11-16, 1 W).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "w25sec.h"
#include "../../../c/common/c6phy_fsk.h"

#ifndef TX_DRY
#define TX_DRY 1
#endif
#ifndef TX_MS
#define TX_MS 4600u
#endif
#ifndef TX_BAUD
#define TX_BAUD 2400u
#endif
#ifndef TX_POL
#define TX_POL 2u
#endif
#ifndef TX_DEV
#define TX_DEV 100u             /* percent of the DMR calibration pair */
#endif
#ifndef TX_MODE
#define TX_MODE 0x66u
#endif
#ifndef TX_SEQ
#define TX_SEQ 0u
#endif
/* TX_WRITE 1 (default): each slot edge writes the next slot's bytes to ONE half, the one the
   chip isn't sending, halves alternating edge by edge; TX_HALF is the half written at the first
   edge. 11-16 run 3: TX_HALF 0 sent every packet right (Pluto 26/26 at 2400, 12/12 at 1200,
   0 bits wrong; the K6 likewise), TX_HALF 1 none. TX_WRITE 0 writes BOTH halves at every edge
   (RadioFoundry's way): run 2, where each slot's first 5 bytes (~4 ms) went out from the slot
   before, because the chip had already taken them from the half the write landed in. The
   radio's transmit frequency needs its own trim in this mode: the FSK sits ~6.7 kHz above the FM
   (bench: "trim" 5745 - 6680). */
#ifndef TX_WRITE
#define TX_WRITE 1u
#endif
#ifndef TX_HALF
#define TX_HALF 0u
#endif
#define TX_FKHZ 440850u
#define TX_SETTLE_MS 80u        /* after 0x60 shows the analog TX: AT1846S TX + PA */
#define TX_IDENT_MS 12000u
#define TX_TAIL_MS 350u
#define TX_SLOT_MS 30u

#define TX_NOTES 7u
#define TX_NOTE 120u
typedef struct {
    char s[TX_NOTES][TX_NOTE];
    unsigned n, quiet;
} tx_notes;

static void note(const ortx_api_t *api, tx_notes *q, const char *s) {
    if (!q->quiet) {
        api->say(s);
        return;
    }
    if (q->n >= TX_NOTES)
        return;
    unsigned i = 0;
    for (; s[i] && i < TX_NOTE - 1u; i++)
        q->s[q->n][i] = s[i];
    q->s[q->n][i] = 0;
    q->n++;
}

/* after the key-down (0x60 bit 7 low for 300 ms, at most most_ms): the kept lines out */
static void flush_notes(const ortx_api_t *api, tx_notes *q, uint32_t most_ms) {
    if (q->quiet) {
        uint32_t t0 = api->ms(), low = t0;
        while (api->ms() - t0 < most_ms) {
            if (api->c6000_read(0x60) & 0x80u)
                low = api->ms();
            else if (api->ms() - low >= 300u)
                break;
            api->sleep_ms(10);
        }
    }
    q->quiet = 0;
    for (unsigned i = 0; i < q->n; i++)
        api->say(q->s[i]);
    q->n = 0;
}

static unsigned scale(unsigned g) {
    unsigned v = g * TX_DEV / 100u;
    return v < 0x08u ? 0x08u : v > 0xF8u ? 0xF8u : v;
}

/* both TX RAM halves */
static int put_slot(const ortx_api_t *api, const uint8_t *s) {
    return c6_write(api, C6_DATA, 0x00, s, CF_SLOT) | c6_write(api, C6_DATA, 0x24, s, CF_SLOT);
}

/* one half: 0 at 0x00, 1 at 0x24 */
static int put_half(const ortx_api_t *api, const uint8_t *s, unsigned half) {
    return c6_write(api, C6_DATA, half ? 0x24u : 0x00u, s, CF_SLOT);
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x01, 0x11, 0x20, 0x21, 0x45, 0x46, 0x60, 0x5F,
                                   0x81, 0x40, 0x41, 0};
    char line[180];
    c6_saved sv;
    c6s_state ss;
    cf_gen g;
    uint8_t slot[CF_SLOT], back[CF_SLOT];

    unsigned ig = 0, qg = 0;
    if (w25_dmr_pair(api, TX_FKHZ, &ig, &qg)) {
        api->say("err cal: DMR gains unreadable, nothing sent");
        return -1;
    }
    unsigned i45 = scale(ig), q46 = scale(qg);
    api->fmt(line, sizeof line, "cal I=%02x Q=%02x dev=%u%% -> 45=%02x 46=%02x (now %02x %02x) baud=%u pol=%u mode=%02x dry=%d",
             ig, qg, TX_DEV, i45, q46, api->c6000_read(0x45), api->c6000_read(0x46), TX_BAUD, TX_POL,
             TX_MODE, TX_DRY);
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
    tx_notes q;
    q.n = 0;
    q.quiet = 0;
    if (!TX_DRY) {
        if (api->level < 4u) {
            api->say("err image below level 4: no transmit guard, nothing sent");
            c6s_close(&ss);
            return -1;
        }
        if (api->tx_tone(TX_MS, 1000)) {
            api->say("err tx_tone refused (not on 440.850 MHz, or TX off)");
            c6s_close(&ss);
            return -1;
        }
        q.quiet = 1;                /* keyed within ms: no say until the key-down */
        note(api, &q, "tx_tone ok");
        for (unsigned k = 0; k < 3u; k++) {
            const char *what = k == 0u ? "ident keyed" : k == 1u ? "ident done" : "key ok";
            uint8_t want = k == 1u ? 0x00u : 0x80u;
            uint32_t most = k == 1u ? TX_IDENT_MS : 1000u, t0 = api->ms();
            while ((api->c6000_read(0x60) & 0x80u) != want) {
                if (api->ms() - t0 > most || api->stop()) {
                    api->fmt(line, sizeof line, "err waiting for %s: nothing sent", what);
                    note(api, &q, line);
                    c6s_close(&ss);
                    flush_notes(api, &q, TX_IDENT_MS + TX_MS + 1000u);
                    return -1;
                }
                api->sleep_ms(1);
            }
            if (k == 2u) {
                t_key = api->ms();
                api->sleep_ms(TX_SETTLE_MS);
            }
            api->fmt(line, sizeof line, "%s after %lu ms", what, (unsigned long)(api->ms() - t0));
            note(api, &q, line);
        }
    }
    uint32_t t_end = t_key + TX_MS - TX_TAIL_MS;

    /* the packets that fit: from now (the first slot edge within 30 ms) to two slots before t_end */
    uint32_t now0 = api->ms();
    unsigned room = t_end > now0 + 4u * TX_SLOT_MS ? t_end - now0 - 4u * TX_SLOT_MS : 0u;
    unsigned npk = cf_fit(room, TX_BAUD);
    cf_init(&g, TX_BAUD, TX_POL, npk, TX_SEQ);

    c6_save(api, &sv, regs);
    api->c6000_write(0x60, 0x00);           /* the FM chain off: the modem modulates */
    api->c6000_write(0x01, 0xF0);           /* two-point TX */
    api->c6000_write(0x45, (uint8_t)i45);
    api->c6000_write(0x46, (uint8_t)q46);
    api->c6000_write(0x10, (uint8_t)TX_MODE);   /* before 0xE3 (RadioFoundry 9.1: set later, it
                                                   moves the slot clock) */
    api->c6000_write(0x11, 0x80);
    api->c6000_write(0x20, 0x00);
    api->c6000_write(0x21, 0x02);
    api->c6000_write(0x5F, 0xF0);
    api->c6000_write(0x81, 0x1F);
    api->c6000_write(0x83, 0xFF);
    api->c6000_write(0x41, 0x00);
    cf_fill(slot, TX_BAUD, TX_POL);
    int spi_err = put_slot(api, slot);      /* what goes out before the first slot we set */
    c6s_poll(&ss);
    api->c6000_write(0x40, 0xE3);           /* TX and RX, active timing */
    api->fmt(line, sizeof line, "phy on mode=%02x npk=%u room=%u ms", TX_MODE, npk, room);
    note(api, &q, line);

    unsigned edges = 0, rftx = 0, slots = 0, late = 0, lost = 0, tail = 0, chk = 0;
    uint32_t t0 = api->ms(), first = 0, last_edge = 0, r42 = 0;
    while (!api->stop()) {
        uint32_t now = api->ms();
        unsigned e = c6s_poll(&ss);
        rftx += (e & C6S_RFTX) != 0;
        if (e & C6S_SLOT) {
            if (edges == 0)
                first = now - t0;
            else if (now - last_edge > TX_SLOT_MS + 3u) {
                late++;
                lost += (now - last_edge + TX_SLOT_MS / 2u) / TX_SLOT_MS - 1u;
            }
            edges++;
            last_edge = now;
            if (cf_done(&g) && ++tail > 2u)
                break;                      /* the last packet and two slots of fill are out */
            if (now + TX_SLOT_MS >= t_end)
                break;
            if (edges <= 8u)                /* the chip's slot flags (TransSlot 7, RecvSlot 6) */
                r42 |= (uint32_t)(api->c6000_read(0x42) >> 5) << (3u * (edges - 1u));
            cf_slot(&g, slot);
            if (TX_WRITE)
                spi_err |= put_half(api, slot, (edges - 1u + TX_HALF) & 1u);
            else
                spi_err |= put_slot(api, slot);
            api->c6000_write(0x41, 0x80);
            slots++;
            if (edges == 1u) {              /* the halves as the chip holds them (page 6); with
                                               TX_WRITE 1 only the half written matches */
                unsigned ok0 = 0, ok1 = 0;
                if (!c6_read(api, C6_DATA_R, 0x00, back, CF_SLOT))
                    for (ok0 = 0; ok0 < CF_SLOT && back[ok0] == slot[ok0]; ok0++) {
                    }
                if (!c6_read(api, C6_DATA_R, 0x24, back, CF_SLOT))
                    for (ok1 = 0; ok1 < CF_SLOT && back[ok1] == slot[ok1]; ok1++) {
                    }
                chk = ok0 << 8 | ok1;
            }
        }
        if (now - t0 > 400u && edges == 0) {
            note(api, &q, "err no slot edges on PC0: stopped");
            break;
        }
        if (now >= t_end)
            break;
        api->sleep_ms(1);
    }

    cf_fill(slot, TX_BAUD, TX_POL);
    put_slot(api, slot);
    api->c6000_write(0x41, 0x00);
    api->c6000_write(0x40, 0x43);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    c6s_close(&ss);
    api->fmt(line, sizeof line, "chk 00=%u/36 24=%u/36 spi=%d write=%u half=%u 42=%08lx", chk >> 8, chk & 0xFFu,
             spi_err, TX_WRITE, TX_HALF, (unsigned long)r42);
    note(api, &q, line);
    api->fmt(line, sizeof line, "tx slots=%u pk=%u/%u bytes=%lu edges=%u first=%lu ms rftx=%u late=%u lost=%u restored at %lu ms",
             slots, g.k < npk ? g.k : npk, npk, (unsigned long)g.i, edges, (unsigned long)first, rftx,
             late, lost, (unsigned long)(api->ms() - t_key));
    note(api, &q, line);
    if (!TX_DRY) {
        while (api->ms() - t_key < TX_MS + 200u && !api->stop())
            api->sleep_ms(10);
    }
    flush_notes(api, &q, 2000u);
    api->say("end");
    return (int)(g.k < npk ? g.k : npk);
}
