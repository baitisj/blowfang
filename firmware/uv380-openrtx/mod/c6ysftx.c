/* c6ysftx: Yaesu System Fusion (YSF) frames, C4FM 4FSK with Yaesu framing, straight out of the
   HR_C6000's physical layer mode, no hardware mod and no audio path (docs/uv380-ysf.md).

   The chip sends TX RAM as 4FSK at 4800 symbols a second, a dibit a symbol (01 +3, 00 +1, 10 -1,
   11 -3), which is YSF's map too, with +3 above the carrier (docs/uv380-c6phy.md, run 3; M17 on
   air 11-17). So a YSF transmission's bytes (a 0x77 preamble, a header frame, communication
   frames, a terminator, built on the host by modem/ysf.py into ysftx_PLAN.h) go to the chip
   unchanged, 36 bytes a 30 ms slot (a 120 byte YSF frame spans 3 1/3 slots: the stream is
   continuous, so frames need not line up with slots). YSF wants the outer symbols at +-2.7 kHz
   (inner +-0.9 kHz); the chip's DMR deviation is about +-1870 Hz at 100 percent (M17: 2300 Hz
   measured at 123), so TX_DEV is 144 percent of the radio's DMR calibration pair (0x45/0x46),
   close to RadioFoundry's C4FM factor 1.39. The chip's shaping is DMR's RRC alpha 0.2, YSF's own.

   Everything else is c6m17tx.c's, which works on air (11-17: BERT 0 bits wrong): the keying
   borrowed from api->tx_tone (the Morse ident AG7EW first, the image's transmit guard, 441.850 or
   440.850 MHz only, the channel's power: the bench runs at power 0), registers saved and put back
   TX_TAIL_MS before the tone ends, nothing said over USB while keyed (the lines are kept and said
   after the key-down), and each slot edge writing the half the chip isn't sending (TX_WRITE 1,
   TX_HALF 0). The raw mode sits about 6.7 kHz above the FM carrier: the bench sets the radio's
   transmit trim first (uv380.cmd "trim", FM trim - 6680) and back after.

   Before the first slot both halves hold the preamble byte (0x77), so the preamble is longer than
   its 40 ms, never shorter; after the terminator the slots carry 0x77 until the key-down.

   YSF_PLAN picks the data: dn (the default; V/D mode 2 "DN": header, 37 frames of AMBE+2 silence
   with the callsigns in the data channel, terminator), data (data full rate: 59 numbered text
   blocks). TX_DRY 1 (the default) clocks the slots with no keying; the wrappers c6ysftx_dn.c and
   c6ysftx_data.c transmit.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "w25sec.h"

#ifndef TX_DRY
#define TX_DRY 1
#endif
#ifndef TX_DEV
#define TX_DEV 144u             /* percent of the DMR pair: -> 2700 Hz outer */
#endif
#ifndef TX_MODE
#define TX_MODE 0x66u
#endif
#ifndef TX_WRITE
#define TX_WRITE 1u
#endif
#ifndef TX_HALF
#define TX_HALF 0u
#endif
#ifndef TX_FKHZ
#define TX_FKHZ 441850u         /* the calibration pair's frequency: the UV-380's bench channel */
#endif

#define YSF_SLOT 36u
#if defined(YSF_PLAN_DATA)
#include "ysftx_data.h"
#define YSF_NAME "data"
#else
#include "ysftx_dn.h"
#define YSF_NAME "dn"
#endif
#define YSF_IDLE 0x77u

#define TX_SETTLE_MS 80u
#define TX_IDENT_MS 12000u
#define TX_TAIL_MS 350u
#define TX_SLOT_MS 30u
/* the key-up: settle, a slot to the first edge, the data, three slots of idle, the tail */
#define TX_DATA_MS ((YSF_TX_N * 1000u + 1199u) / 1200u)
#define TX_MS (TX_SETTLE_MS + TX_SLOT_MS + TX_DATA_MS + 3u * TX_SLOT_MS + TX_TAIL_MS + 100u)
#if TX_MS > 5000u
#error "the YSF data doesn't fit a 5 s key-up (tx_tone's limit)"
#endif

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

/* the next slot: 36 bytes from pos, idle (0x77) past the end */
static void ysf_slot(uint8_t *s, uint32_t pos) {
    for (unsigned i = 0; i < YSF_SLOT; i++) {
        uint32_t j = pos + i;
        s[i] = j < YSF_TX_N ? YSF_TX[j] : (uint8_t)YSF_IDLE;
    }
}

static void fill(uint8_t *s, uint8_t b) {
    for (unsigned i = 0; i < YSF_SLOT; i++)
        s[i] = b;
}

static int put_half(const ortx_api_t *api, const uint8_t *s, unsigned half) {
    return c6_write(api, C6_DATA, half ? 0x24u : 0x00u, s, YSF_SLOT);
}

static int put_both(const ortx_api_t *api, const uint8_t *s) {
    return put_half(api, s, 0) | put_half(api, s, 1);
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x01, 0x11, 0x20, 0x21, 0x45, 0x46, 0x60, 0x5F,
                                   0x81, 0x40, 0x41, 0};
    char line[180];
    c6_saved sv;
    c6s_state ss;
    uint8_t slot[YSF_SLOT], back[YSF_SLOT];

    unsigned ig = 0, qg = 0;
    if (w25_dmr_pair(api, TX_FKHZ, &ig, &qg)) {
        api->say("err cal: DMR gains unreadable, nothing sent");
        return -1;
    }
    unsigned i45 = scale(ig), q46 = scale(qg);
    api->fmt(line, sizeof line, "ysf %s bytes=%u frames=%u tx_ms=%u cal I=%02x Q=%02x dev=%u%% -> 45=%02x 46=%02x mode=%02x dry=%d",
             YSF_NAME, YSF_TX_N, YSF_TX_N / 120u, TX_MS, ig, qg, TX_DEV, i45, q46, TX_MODE, TX_DRY);
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
            api->say("err tx_tone refused (not on 441.850 or 440.850 MHz, or TX off)");
            c6s_close(&ss);
            return -1;
        }
        q.quiet = 1;
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

    c6_save(api, &sv, regs);
    api->c6000_write(0x60, 0x00);           /* the FM chain off: the modem modulates */
    api->c6000_write(0x01, 0xF0);           /* two-point TX */
    api->c6000_write(0x45, (uint8_t)i45);
    api->c6000_write(0x46, (uint8_t)q46);
    api->c6000_write(0x10, (uint8_t)TX_MODE);   /* before 0xE3 */
    api->c6000_write(0x11, 0x80);
    api->c6000_write(0x20, 0x00);
    api->c6000_write(0x21, 0x02);
    api->c6000_write(0x5F, 0xF0);
    api->c6000_write(0x81, 0x1F);
    api->c6000_write(0x83, 0xFF);
    api->c6000_write(0x41, 0x00);
    fill(slot, (uint8_t)YSF_TX_FILL);
    int spi_err = put_both(api, slot);      /* preamble until the first slot we set */
    c6s_poll(&ss);
    api->c6000_write(0x40, 0xE3);           /* TX and RX, active timing */

    unsigned edges = 0, rftx = 0, slots = 0, late = 0, lost = 0, tail = 0, chk = 0;
    uint32_t pos = 0, t0 = api->ms(), first = 0, last_edge = 0;
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
            if (pos >= YSF_TX_N && ++tail > 2u)
                break;                      /* the data and two slots of idle are out */
            if (now + TX_SLOT_MS >= t_end)
                break;
            ysf_slot(slot, pos);
            pos += YSF_SLOT;
            if (TX_WRITE)
                spi_err |= put_half(api, slot, (edges - 1u + TX_HALF) & 1u);
            else
                spi_err |= put_both(api, slot);
            api->c6000_write(0x41, 0x80);
            slots++;
            if (edges == 1u) {
                unsigned ok0 = 0, ok1 = 0;
                if (!c6_read(api, C6_DATA_R, 0x00, back, YSF_SLOT))
                    for (ok0 = 0; ok0 < YSF_SLOT && back[ok0] == slot[ok0]; ok0++) {
                    }
                if (!c6_read(api, C6_DATA_R, 0x24, back, YSF_SLOT))
                    for (ok1 = 0; ok1 < YSF_SLOT && back[ok1] == slot[ok1]; ok1++) {
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

    ysf_slot(slot, YSF_TX_N);              /* idle in both halves for the last moments */
    put_both(api, slot);
    api->c6000_write(0x41, 0x00);
    api->c6000_write(0x40, 0x43);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    c6s_close(&ss);
    uint32_t sent = pos < YSF_TX_N ? pos : YSF_TX_N;
    api->fmt(line, sizeof line, "chk 00=%u/36 24=%u/36 spi=%d write=%u half=%u", chk >> 8, chk & 0xFFu, spi_err,
             TX_WRITE, TX_HALF);
    note(api, &q, line);
    api->fmt(line, sizeof line, "tx slots=%u bytes=%lu/%u edges=%u first=%lu ms rftx=%u late=%u lost=%u restored at %lu ms",
             slots, (unsigned long)sent, YSF_TX_N, edges, (unsigned long)first, rftx, late, lost,
             (unsigned long)(api->ms() - t_key));
    note(api, &q, line);
    if (!TX_DRY) {
        while (api->ms() - t_key < TX_MS + 200u && !api->stop())
            api->sleep_ms(10);
    }
    flush_notes(api, &q, 2000u);
    api->say(sent == YSF_TX_N && !late ? "ysf done" : "ysf short");
    api->say("end");
    return sent == YSF_TX_N && !late ? 0x5F : -2;
}
