/* c6m17tx: standard M17 (m17project.org) straight out of the HR_C6000's physical layer mode, no
   hardware mod and no audio path: milestone 1 of docs/uv380-m17-scope.md.

   The chip sends TX RAM as 4FSK at 4800 symbols a second, a dibit a symbol (01 +3, 00 +1, 10 -1,
   11 -3), which is M17's own map, and +3 goes above the carrier as M17 wants (docs/uv380-c6phy.md,
   run 3). So an M17 transmission's bytes (preamble, LSF, frames, EOT, built on the host by
   modem/m17.py into m17tx_PLAN.h) go to the chip unchanged, 36 bytes a 30 ms slot. M17 wants the
   outer symbols at +-2.4 kHz; the chip's DMR deviation is +-1944 Hz, so TX_DEV is 123 percent of
   the radio's DMR calibration pair (0x45/0x46). The chip's shaping is DMR's RRC alpha 0.2, not
   M17's 0.5: a standard receiver loses next to nothing (docs/uv380-m17-scope.md).

   Everything else is c6phytx.c's, which works (11-16 run 3: 0 bits wrong): the keying borrowed
   from api->tx_tone (the Morse ident AG7EW first, the image's transmit guard, 441.850 or 440.850
   MHz only, the channel's power: the bench runs at power 0), registers saved and put back
   TX_TAIL_MS before the tone ends, nothing said over USB while keyed (the lines are kept and said
   after the key-down), and each slot edge writing the half the chip isn't sending (TX_WRITE 1,
   TX_HALF 0). The raw mode sits about 6.7 kHz above the FM carrier: the bench sets the radio's
   transmit trim first (uv380.cmd "trim", 5745 - 6680) and back after.

   Before the first slot both halves hold the transmission's first byte (its preamble, 0x77 or
   0xDD), so the preamble is longer than 40 ms, never shorter; after the last byte the slots carry
   EOT (0x55 0x5D) until the key-down.

   M17_PLAN picks the data: bert (the default; PRBS9 frames), sms (20 text packets), voice (Codec2
   3200, "A G 7 E W. M 17 test."). TX_DRY 1 (the default) clocks the slots with no keying; the
   wrappers c6m17tx_bert.c, _sms.c, _voice.c transmit.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "w25sec.h"

#ifndef TX_DRY
#define TX_DRY 1
#endif
#ifndef TX_DEV
#define TX_DEV 123u             /* percent of the DMR pair: 1944 Hz -> 2400 Hz outer */
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

#define M17_SLOT 36u
#if defined(M17_PLAN_SMS)
#include "m17tx_sms.h"
#define M17_NAME "sms"
#elif defined(M17_PLAN_VOICE)
#include "m17tx_voice.h"
#define M17_NAME "voice"
#else
#include "m17tx_bert.h"
#define M17_NAME "bert"
#endif

#define TX_SETTLE_MS 80u
#define TX_IDENT_MS 12000u
#define TX_TAIL_MS 350u
#define TX_SLOT_MS 30u
/* the key-up: settle, a slot to the first edge, the data, three slots of EOT, the tail */
#define TX_DATA_MS ((M17_TX_N * 1000u + 1199u) / 1200u)
#define TX_MS (TX_SETTLE_MS + TX_SLOT_MS + TX_DATA_MS + 3u * TX_SLOT_MS + TX_TAIL_MS + 100u)
#if TX_MS > 5000u
#error "the M17 data doesn't fit a 5 s key-up (tx_tone's limit)"
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

/* the next slot: 36 bytes from pos, EOT (0x55 0x5D, paired by stream position) past the end */
static void m17_slot(uint8_t *s, uint32_t pos) {
    for (unsigned i = 0; i < M17_SLOT; i++) {
        uint32_t j = pos + i;
        s[i] = j < M17_TX_N ? M17_TX[j] : (j & 1u) ? 0x5Du : 0x55u;
    }
}

static void fill(uint8_t *s, uint8_t b) {
    for (unsigned i = 0; i < M17_SLOT; i++)
        s[i] = b;
}

static int put_half(const ortx_api_t *api, const uint8_t *s, unsigned half) {
    return c6_write(api, C6_DATA, half ? 0x24u : 0x00u, s, M17_SLOT);
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
    uint8_t slot[M17_SLOT], back[M17_SLOT];

    unsigned ig = 0, qg = 0;
    if (w25_dmr_pair(api, TX_FKHZ, &ig, &qg)) {
        api->say("err cal: DMR gains unreadable, nothing sent");
        return -1;
    }
    unsigned i45 = scale(ig), q46 = scale(qg);
    api->fmt(line, sizeof line, "m17 %s bytes=%u frames=%u tx_ms=%u cal I=%02x Q=%02x dev=%u%% -> 45=%02x 46=%02x mode=%02x dry=%d",
             M17_NAME, M17_TX_N, M17_TX_N / 48u, TX_MS, ig, qg, TX_DEV, i45, q46, TX_MODE, TX_DRY);
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
    fill(slot, (uint8_t)M17_TX_FILL);
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
            if (pos >= M17_TX_N && ++tail > 2u)
                break;                      /* the data and two slots of EOT are out */
            if (now + TX_SLOT_MS >= t_end)
                break;
            m17_slot(slot, pos);
            pos += M17_SLOT;
            if (TX_WRITE)
                spi_err |= put_half(api, slot, (edges - 1u + TX_HALF) & 1u);
            else
                spi_err |= put_both(api, slot);
            api->c6000_write(0x41, 0x80);
            slots++;
            if (edges == 1u) {
                unsigned ok0 = 0, ok1 = 0;
                if (!c6_read(api, C6_DATA_R, 0x00, back, M17_SLOT))
                    for (ok0 = 0; ok0 < M17_SLOT && back[ok0] == slot[ok0]; ok0++) {
                    }
                if (!c6_read(api, C6_DATA_R, 0x24, back, M17_SLOT))
                    for (ok1 = 0; ok1 < M17_SLOT && back[ok1] == slot[ok1]; ok1++) {
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

    m17_slot(slot, M17_TX_N + (M17_TX_N & 1u));     /* EOT in both halves for the last moments */
    put_both(api, slot);
    api->c6000_write(0x41, 0x00);
    api->c6000_write(0x40, 0x43);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    c6s_close(&ss);
    uint32_t sent = pos < M17_TX_N ? pos : M17_TX_N;
    api->fmt(line, sizeof line, "chk 00=%u/36 24=%u/36 spi=%d write=%u half=%u", chk >> 8, chk & 0xFFu, spi_err,
             TX_WRITE, TX_HALF);
    note(api, &q, line);
    api->fmt(line, sizeof line, "tx slots=%u bytes=%lu/%u edges=%u first=%lu ms rftx=%u late=%u lost=%u restored at %lu ms",
             slots, (unsigned long)sent, M17_TX_N, edges, (unsigned long)first, rftx, late, lost,
             (unsigned long)(api->ms() - t_key));
    note(api, &q, line);
    if (!TX_DRY) {
        while (api->ms() - t_key < TX_MS + 200u && !api->stop())
            api->sleep_ms(10);
    }
    flush_notes(api, &q, 2000u);
    api->say(sent == M17_TX_N && !late ? "m17 done" : "m17 short");
    api->say("end");
    return sent == M17_TX_N && !late ? 0x17 : -2;
}
