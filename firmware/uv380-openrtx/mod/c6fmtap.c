/* c6fmtap: the HR_C6000's 8 kHz FM receive tap (RX RAM 0x030-0x22F and 0x230-0x42F) to the USB
   serial, receive only (no RF out). Milestone 2 of docs/uv380-m17-scope.md (R1): is the tap DC
   coupled and flat enough to demodulate M17's 4FSK in software, with no hardware mod?

   RadioFoundry modem-hrc6000.md 8.1 and hrc6000-registers.md 7.3 (MIT, measured on an AT1846S
   board): in FM receive the chip writes 8 kHz big endian 16-bit samples, ungated, after page 4
   0x34's de-emphasis and filters, into two 512-byte halves, one per 32 ms, filled progressively;
   0x82 bit 1 comes once per half when 0x81 unmasks it. They also saw "a fixed sub-audio high-pass,
   about 20 dB down below 300 Hz": that is what R1 measures on our radio.

   What it does, all of it put back at the end (or at PTT, mode change, Ctrl-C):
   - AT1846S into its DMR receive set (at1846.h: audio filters off, low frequencies passed),
     RX_AUDIO_MUX (PD9) high, as c6dmrrx;
   - C6000 in FM receive (0x10 = 0x80, 0x60 = 0x00, 0x26 = 0xFD), 0x81 |= 0x02 (unmask bit 1);
   - 0x34 stepped through TAP_V34 every TAP_STEP_MS (bit 7 band-pass, 6 expander, 5 de-emphasis,
     2 RX bandwidth 25 kHz), so one Pluto send measures every setting;
   - which half to read: every ms it reads sample 0 and sample 255 of both halves. When a half's
     sample 0 changes the chip has started writing it, so the other half is complete (a
     progressive fill); if its sample 255 changed at the same moment the half was written as a
     block and it is the complete one. Each complete half goes out as four rxrec "a" lines.
   Lines (tools/uv380_i2sprobe.py audio LOG OUT.raw reads the "a" lines; a lost half is a gap of
   four sequence numbers, filled with zeros):
     tap start v34=1c,18,3c,9c step=5000 s=120 at=ok
     a SEQ hhhh x64              64 samples, signed 16-bit as hex, SEQ counts lines from 0
     v T idx=I 34=XX seq=SEQ     a new 0x34 setting from line SEQ on
     d T half=H mode=prog|block  the first TAP_DIAG decisions (which half, how it was found)
     st T halves= gaps= flags= prog= block= rssi= 43= 4e= 4f=   once a second
     end halves= gaps= flags= prog= block=
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "at1846.h"

#ifndef TAP_S
#define TAP_S 120u
#endif
#ifndef TAP_STEP_MS
#define TAP_STEP_MS 5000u
#endif
#ifndef TAP_V34
#define TAP_V34 0x1C, 0x18, 0x3C, 0x9C
#endif
#ifndef TAP_AT
#define TAP_AT 1
#endif
#ifndef TAP_DIAG
#define TAP_DIAG 12u
#endif

#define GPIOD 0x40020C00u
#define MUX 9u
#define HALF0 0x030u
#define HALF1 0x230u
#define BLK 64u

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

static unsigned rd16(const ortx_api_t *api, unsigned a) {
    uint8_t b[2];
    if (c6_read(api, C6_DATA, a, b, 2))
        return 0x10000u;                 /* port busy: never equals a sample */
    return ((unsigned)b[0] << 8) | b[1];
}

/* one complete half as four "a" lines */
static void dump(const ortx_api_t *api, unsigned base, uint32_t *seq) {
    uint8_t b[2 * BLK];
    char line[24 + BLK * 5u];
    for (unsigned k = 0; k < 4u; k++) {
        if (c6_read(api, C6_DATA, base + k * 2u * BLK, b, sizeof b)) {
            (*seq)++;                    /* a gap the host sees */
            continue;
        }
        unsigned n = (unsigned)api->fmt(line, sizeof line, "a %lu", (unsigned long)(*seq)++);
        for (unsigned i = 0; i < BLK; i++) {
            unsigned v = ((unsigned)b[2 * i] << 8) | b[2 * i + 1];
            line[n++] = ' ';
            for (int s = 12; s >= 0; s -= 4)
                line[n++] = hexd((v >> s) & 15u);
        }
        line[n++] = '\r';
        line[n++] = '\n';
        api->usb_write(line, n);
    }
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x60, 0x26, 0x81, 0x34, 0x36, 0xE0, 0};
    static const uint8_t v34[] = {TAP_V34};
    const unsigned nv = sizeof v34;
    char line[200];
    c6_saved sv;
    at_saved as;
    as.ok = 0;

    c6_save(api, &sv, regs);
    uint32_t mux = (C6_REG(GPIOD + 0x14u) >> MUX) & 1u;
    C6_REG(GPIOD + 0x18u) = 1u << MUX;
    int atr = 0;
    if (TAP_AT)
        atr = at_dmr_rx(api, &as, TAP_AT > 1);
    uint8_t m81 = api->c6000_read(0x81);
    api->c6000_write(0x10, 0x80);
    api->c6000_write(0x60, 0x00);
    api->c6000_write(0x26, 0xFD);
    api->c6000_write(0x81, (uint8_t)(m81 | 0x02u));
    api->c6000_write(0x34, v34[0]);
    api->c6000_write(0x83, 0xFF);

    unsigned n = (unsigned)api->fmt(line, sizeof line, "tap start v34=");
    for (unsigned i = 0; i < nv; i++)
        n += (unsigned)api->fmt(line + n, sizeof line - n, i ? ",%02x" : "%02x", v34[i]);
    api->fmt(line + n, sizeof line - n, " step=%u s=%u at=%s 81=%02x 34was=%02x 10was=%02x",
             TAP_STEP_MS, TAP_S, TAP_AT ? (atr ? "err" : "ok") : "off", m81, sv.val[4], sv.val[0]);
    api->say(line);

    uint32_t seq = 0, t0 = api->ms(), last_st = 0, t_v = 0;
    unsigned vi = 0, halves = 0, gaps = 0, flags = 0, prog = 0, block = 0, diag = 0;
    unsigned p0[2], pe[2], last = 2;
    p0[0] = rd16(api, HALF0), p0[1] = rd16(api, HALF1);
    pe[0] = rd16(api, HALF0 + 510u), pe[1] = rd16(api, HALF1 + 510u);
    api->fmt(line, sizeof line, "v 0 idx=0 34=%02x seq=0", v34[0]);
    api->say(line);

    while (api->ms() - t0 < TAP_S * 1000u && !api->stop()) {
        uint32_t t = api->ms() - t0;
        if (t - t_v >= TAP_STEP_MS) {
            t_v = t;
            vi = (vi + 1u) % nv;
            api->c6000_write(0x34, v34[vi]);
            api->fmt(line, sizeof line, "v %lu idx=%u 34=%02x seq=%lu", (unsigned long)t, vi, v34[vi],
                     (unsigned long)seq);
            api->say(line);
        }
        uint8_t f = api->c6000_read(0x82);
        if (f) {
            flags += (f & 0x02u) != 0;
            api->c6000_write(0x83, f);
        }
        unsigned s0[2], se[2];
        s0[0] = rd16(api, HALF0), s0[1] = rd16(api, HALF1);
        se[0] = rd16(api, HALF0 + 510u), se[1] = rd16(api, HALF1 + 510u);
        int done = -1, isblock = 0;
        for (unsigned h = 0; h < 2u && done < 0; h++) {
            if (s0[h] != p0[h] && s0[h] <= 0xFFFFu && p0[h] <= 0xFFFFu) {
                isblock = se[h] != pe[h];
                done = isblock ? (int)h : (int)(1u - h);
            }
        }
        p0[0] = s0[0], p0[1] = s0[1], pe[0] = se[0], pe[1] = se[1];
        if (done >= 0) {
            if ((unsigned)done == last) {
                gaps++;
                seq += 4u;              /* the half between was missed */
            }
            last = (unsigned)done;
            prog += !isblock;
            block += isblock;
            if (diag < TAP_DIAG) {
                diag++;
                api->fmt(line, sizeof line, "d %lu half=%d mode=%s f=%02x", (unsigned long)t, done,
                         isblock ? "block" : "prog", f);
                api->say(line);
            }
            dump(api, done ? HALF1 : HALF0, &seq);
            halves++;
        }
        if (t - last_st >= 1000u) {
            last_st = t;
            api->fmt(line, sizeof line, "st %lu halves=%u gaps=%u flags=%u prog=%u block=%u rssi=%ld "
                     "43=%02x%02x 4e=%02x 4f=%02x", (unsigned long)t, halves, gaps, flags, prog, block,
                     (long)api->rssi(), api->c6000_read(0x43), api->c6000_read(0x44),
                     api->c6000_read(0x4E), api->c6000_read(0x4F));
            api->say(line);
        }
        api->sleep_ms(1);
    }

    api->c6000_write(0x81, m81);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    at_restore(api, &as);
    C6_REG(GPIOD + 0x18u) = mux ? (1u << MUX) : (1u << (MUX + 16u));
    api->fmt(line, sizeof line, "end halves=%u gaps=%u flags=%u prog=%u block=%u lines=%lu", halves, gaps,
             flags, prog, block, (unsigned long)seq);
    api->say(line);
    return 0;
}
