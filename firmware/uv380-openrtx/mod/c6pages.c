/* c6pages: a read-only picture of the HR_C6000 modem and the radio's DMR calibration, the first
   step of using the C6000 as a modem from RAM modules (docs/uv380-c6000-modem.md). Writes nothing
   to the C6000, the AT1846S or the flash; no RF.

   1. c6spi.h's own port against the image's: page 4 read both ways, register by register (status
      registers that move by themselves left out): "chk same=N diff=M" and the differing ones.
   2. Every space c6spi.h reaches: page 4 00-FF, the auxiliary table 00-7F and 100-12F (sync
      pattern, CRC masks, tone table), the RX RAM 00-8F (last burst, EMB, short LC, slot 0's
      264 decoded bits), the TX RAM 00-4F (read back through space 6), slot 0's first 64 soft
      values (A8-E7).
   3. The calibration rows a DMR transmitter needs (w25sec.h, read only): the UHF DMR and FM
      gain pairs and the pair at 440.850 MHz, against what 0x45 / 0x46 hold now.
   4. The C6000's interrupt lines (c6slot.h): edges counted over 600 ms as the image has the chip
      set up (FM: no slot timing expected, so slot=0 is right here).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "w25sec.h"

/* page 4 registers that change by themselves (status, RSSI, interrupt flags, codec status) */
static int volatile_reg(unsigned r) {
    return r == 0x0F || (r >= 0x42 && r <= 0x44) || r == 0x4E || r == 0x4F ||
           (r >= 0x51 && r <= 0x55) || r == 0x5F || r == 0x82 || r == 0x84 || r == 0x86 ||
           r == 0x88 || r == 0x89 || r == 0x90 || (r >= 0x93 && r <= 0x98) || r == 0xE3;
}

static void dump(const ortx_api_t *api, const char *tag, unsigned sp, unsigned a, unsigned n) {
    uint8_t b[32];
    char hex[65], line[100];
    for (unsigned o = 0; o < n; o += 32) {
        unsigned k = n - o < 32 ? n - o : 32;
        if (c6_read(api, sp, a + o, b, k)) {
            api->fmt(line, sizeof line, "%s %03x: busy", tag, a + o);
            api->say(line);
            return;
        }
        c6_hex(hex, b, k);
        api->fmt(line, sizeof line, "%s %03x: %s", tag, a + o, hex);
        api->say(line);
    }
}

static void cal(const ortx_api_t *api) {
    uint8_t r1[16], ir[9], qr[9], fi[9], fq[9];
    char hex[40], line[160];
    if (w25_sec_read(api, 0x1000u, r1, 16) || w25_sec_read(api, 0x2030u, ir, 9) ||
        w25_sec_read(api, 0x2040u, qr, 9) || w25_sec_read(api, 0x2070u, fi, 9) ||
        w25_sec_read(api, 0x2080u, fq, 9)) {
        api->say("cal err: SPI1 busy or off");
        return;
    }
    c6_hex(hex, r1, 16);
    api->fmt(line, sizeof line, "cal r1 000: %s (09 = UHF ref tune)", hex);
    api->say(line);
    c6_hex(hex, ir, 9);
    api->fmt(line, sizeof line, "cal dmr_i 030: %s", hex);
    api->say(line);
    c6_hex(hex, qr, 9);
    api->fmt(line, sizeof line, "cal dmr_q 040: %s", hex);
    api->say(line);
    c6_hex(hex, fi, 9);
    api->fmt(line, sizeof line, "cal fm_i 070: %s", hex);
    api->say(line);
    c6_hex(hex, fq, 9);
    api->fmt(line, sizeof line, "cal fm_q 080: %s", hex);
    api->say(line);
    unsigned di, dq;
    int ok = w25_dmr_pair(api, 440850u, &di, &dq);
    api->fmt(line, sizeof line, "cal 440.850 dmr I=%02x Q=%02x (%s) fm I=%02x Q=%02x; now 45=%02x 46=%02x "
             "47=%02x 48=%02x 04=%02x", ok ? 0 : di, ok ? 0 : dq, ok ? "bad" : "ok",
             w25_uhf_interp(fi, 440850u), w25_uhf_interp(fq, 440850u), api->c6000_read(0x45),
             api->c6000_read(0x46), api->c6000_read(0x47), api->c6000_read(0x48),
             api->c6000_read(0x04));
    api->say(line);
}

int uv_main(const ortx_api_t *api) {
    char line[160];
    unsigned same = 0, diff = 0, busy = 0;
    for (unsigned r = 0; r < 256; r++) {
        if (volatile_reg(r))
            continue;
        int a = api->c6000_read((uint8_t)r), b = c6_r(api, r);
        if (b > 0xFF) {
            busy++;
        } else if (a == b) {
            same++;
        } else {
            diff++;
            if (diff <= 8) {
                api->fmt(line, sizeof line, "chk %02x image=%02x c6spi=%02x", r, a, b);
                api->say(line);
            }
        }
    }
    api->fmt(line, sizeof line, "chk same=%u diff=%u busy=%u", same, diff, busy);
    api->say(line);

    dump(api, "p4", C6_CFG, 0x000, 256);
    dump(api, "p1", C6_AUX, 0x000, 128);
    dump(api, "p1", C6_AUX, 0x100, 48);
    dump(api, "rx", C6_DATA, 0x000, 144);
    dump(api, "tx", C6_DATA_R, 0x000, 80);
    dump(api, "sf", C6_DATA, 0x0A8, 64);
    cal(api);

    c6s_state s;
    if (c6s_open(&s)) {
        api->say("irq err: EXTI0-2 in use");
        return 0;
    }
    unsigned lv0 = c6s_levels(), n[3] = {0, 0, 0};
    c6s_poll(&s);
    for (uint32_t t0 = api->ms(); api->ms() - t0 < 600u && !api->stop();) {
        unsigned p = c6s_poll(&s);
        for (unsigned k = 0; k < 3; k++)
            n[k] += (p >> k) & 1u;
        api->sleep_ms(1);
    }
    api->fmt(line, sizeof line, "irq 600ms slot=%u sys=%u rftx=%u levels=%x->%x 82=%02x 81=%02x", n[0],
             n[1], n[2], lv0, c6s_levels(), api->c6000_read(0x82), api->c6000_read(0x81));
    api->say(line);
    c6s_close(&s);
    return 0;
}
