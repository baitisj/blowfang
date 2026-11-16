/* c6dmrrx: the HR_C6000 as a DMR receiver, from a RAM module, receive only (no RF out). Step 2 of
   docs/uv380-c6000-modem.md: before the UV-380 sends anything through the chip's 4FSK modem, its
   receiver has to lock on DMR bursts (from the IC-7100, tools/uv380_dmrburst.py) and hand over
   their payloads, soft values and error counts.

   What it does, all of it put back at the end (or at PTT, mode change, Ctrl-C):
   - AT1846S into its DMR receive set (at1846.h: filters off, LF passed, AF gain; RX_AT 0 skips,
     2 writes the full RadioFoundry set), RX_AUDIO_MUX (PD9) high so its audio reaches the C6000;
   - C6000 into DMR layer 2 receive (RadioFoundry modem-hrc6000.md 3.1 and 5.1): 0x10 = 0x6E,
     0x01 = 0xF0, colour code RX_CC, every sync family, the receiver restart, any colour code
     deframed (0x41 bit 3) unless RX_CCANY 0; RX_RAW 1 instead runs the physical layer test mode
     (0x10 = 0x02, 0x40 = 0x40, 0x41 = 0x41: each demodulated 288-bit burst at RX RAM 0x30);
   - RX_SLOTCHECK: first 300 ms of active timing (0x40 = 0x63, receive only) to prove the slot
     interrupt reaches PC0 ("slotcheck n=10" is right: one per 30 ms);
   - then RX_MS ms of listening: at every slot edge 0x41 = receive, every ms 0x82 polled. Lines:
       b T sc=SYNC dt=TYPE crc=E cc=CC sy=FAM se=SYNCERR fe=FSKERR new=N d=24 RX RAM bytes (hex)
       r T se= fe= d=36 bytes                     (RX_RAW: the raw burst)
       sf T BASE OFF hex                          soft values of the first RX_SOFT synced bursts
       st T slots= bursts= nosync= ok= rearm= raw= 42= rssi= 43=  once a second
       end slots= bursts= nosync= ok= rearm= raw=
   T is ms since the start. sc 2 = data sync; crc 0 = the chip's checks passed. new=1: d's
   payload (bytes 0-11) differs from the last b line's.

   11-16 bench (IC-7100 sending): with no sync the chip still raises 0x82 bit 3 every slot (sc=0,
   stale RX RAM): those only count as nosync. Once locked it free-runs on its own slot clock and
   misses the next transmission, so after RX_REARM slots with no synced burst it restarts the
   receiver (0x41 = 20 00 20 rx). Soft values: one byte per bit, two per symbol, 7-bit signed:
   the symbol's level L, then 16 - |L| (16 = the inner / outer threshold).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "at1846.h"

#ifndef RX_MS
#define RX_MS 30000u
#endif
#ifndef RX_AT
#define RX_AT 1
#endif
#ifndef RX_CC
#define RX_CC 1u
#endif
#ifndef RX_CCANY
#define RX_CCANY 1
#endif
#ifndef RX_SOFT
#define RX_SOFT 32u
#endif
#ifndef RX_REARM
#define RX_REARM 13u
#endif
#ifndef RX_RAW
#define RX_RAW 0
#endif
#ifndef RX_SLOTCHECK
#define RX_SLOTCHECK 1
#endif

#define GPIOD 0x40020C00u
#define MUX 9u

/* one slot's 264 soft values, unless the first 8 are what the last dump of that base had */
static void soft(const ortx_api_t *api, uint32_t t, unsigned base, uint8_t *last8) {
    uint8_t b[66];
    char hex[133], line[180];
    for (unsigned o = 0; o < 264; o += 66) {
        if (c6_read(api, C6_DATA, base + o, b, 66))
            return;
        if (o == 0) {
            unsigned same = 1;
            for (unsigned i = 0; i < 8; i++) {
                same &= b[i] == last8[i];
                last8[i] = b[i];
            }
            if (same)
                return;
        }
        c6_hex(hex, b, 66);
        api->fmt(line, sizeof line, "sf %lu %03x %03x %s", (unsigned long)t, base, o, hex);
        api->say(line);
    }
}

static void restart(const ortx_api_t *api, uint8_t rx41) {
    api->c6000_write(0x41, 0x20);
    api->c6000_write(0x41, 0x00);
    api->c6000_write(0x41, 0x20);
    api->c6000_write(0x41, rx41);
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x01, 0x11, 0x1F, 0x5F, 0x81, 0x26, 0x36, 0x40, 0x41, 0};
    char line[180], hex[80];
    c6_saved sv;
    at_saved as;
    c6s_state ss;
    as.ok = 0;

    if (c6s_open(&ss))
        api->say("irq err: EXTI0-2 in use, no slot clock (0x41 rewritten every 10 ms)");
    c6_save(api, &sv, regs);
    uint32_t mux = (C6_REG(GPIOD + 0x14u) >> MUX) & 1u;
    C6_REG(GPIOD + 0x18u) = 1u << MUX;
    if (RX_AT) {
        int r = at_dmr_rx(api, &as, RX_AT > 1);
        api->fmt(line, sizeof line, "at1846 dmr rx %s (%u regs)", r ? "err" : "ok", as.n);
        api->say(line);
    }

    const uint8_t rx41 = (uint8_t)(RX_RAW ? 0x41u : (0x50u | (RX_CCANY ? 0x08u : 0u)));
    api->c6000_write(0x10, RX_RAW ? 0x02 : 0x6E);
    api->c6000_write(0x01, 0xF0);
    api->c6000_write(0x11, 0x80);
    api->c6000_write(0x1F, (uint8_t)(RX_CC << 4));
    api->c6000_write(0x5F, 0xF0);
    api->c6000_write(0x81, 0x1F);
    api->c6000_write(0x26, 0xFD);
    api->c6000_write(0x83, 0xFF);

    unsigned slots = 0, bursts = 0, nosync = 0, ok = 0, raw = 0, softs = 0, rearm = 0, quiet = 0;
    uint8_t last[12] = {0}, s0[8] = {0}, s1[8] = {0};
    if (RX_SLOTCHECK && !RX_RAW) {
        api->c6000_write(0x40, 0x63);           /* RX on, active timing: slots with no signal */
        c6s_poll(&ss);
        unsigned n = 0;
        for (uint32_t t0 = api->ms(); api->ms() - t0 < 300u && !api->stop();) {
            n += c6s_poll(&ss) & C6S_SLOT;
            api->sleep_ms(1);
        }
        api->fmt(line, sizeof line, "slotcheck n=%u in 300 ms (10 = PC0 carries TIME_SLOT_INTER) "
                 "levels=%x", n, c6s_levels());
        api->say(line);
    }
    api->c6000_write(0x40, RX_RAW ? 0x40 : 0x43);
    restart(api, rx41);
    api->say(RX_RAW ? "listening raw" : "listening");

    uint32_t t0 = api->ms(), last_st = 0, last_41 = 0;
    while (api->ms() - t0 < RX_MS && !api->stop()) {
        uint32_t t = api->ms() - t0;
        unsigned e = c6s_poll(&ss);
        if (e & C6S_SLOT) {
            slots++;
            last_41 = t;
            if (!RX_RAW && ++quiet >= RX_REARM) {
                restart(api, rx41);
                rearm++;
                quiet = 0;
            } else {
                api->c6000_write(0x41, rx41);
            }
        } else if (!ss.open && t - last_41 >= 10u) {
            api->c6000_write(0x41, rx41);
            last_41 = t;
        }
        uint8_t f = api->c6000_read(0x82);
        if (f) {
            uint8_t s51 = api->c6000_read(0x51), s52 = api->c6000_read(0x52),
                    s5f = api->c6000_read(0x5F), s89 = api->c6000_read(0x89),
                    s0f = api->c6000_read(0x0F);
            if ((f & 0x08u) && (s51 & 3u) == 0) {
                nosync++;
            } else if (f & 0x08u) {
                uint8_t d[24];
                bursts++;
                quiet = 0;
                ok += (s51 & 0x04u) == 0;
                if (c6_read(api, C6_DATA, 0x00, d, 24) == 0) {
                    unsigned fresh = 0;
                    for (unsigned i = 0; i < 12; i++) {
                        fresh |= d[i] != last[i];
                        last[i] = d[i];
                    }
                    c6_hex(hex, d, 24);
                    api->fmt(line, sizeof line, "b %lu sc=%u dt=%x crc=%u cc=%u sy=%u se=%u fe=%u new=%u d=%s",
                             (unsigned long)t, s51 & 3u, s51 >> 4, (s51 >> 2) & 1u, s52 >> 4,
                             s5f & 3u, s89 & 0x3Fu, s0f, fresh, hex);
                    api->say(line);
                }
                if (softs < RX_SOFT) {
                    softs++;
                    soft(api, t, 0x0A8, s0);
                    soft(api, t, 0x1C8, s1);
                }
            }
            if (f & 0x01u) {
                uint8_t d[36];
                raw++;
                if (c6_read(api, C6_DATA, 0x30, d, 36) == 0) {
                    c6_hex(hex, d, 36);
                    api->fmt(line, sizeof line, "r %lu se=%u fe=%u d=%s", (unsigned long)t,
                             s89 & 0x3Fu, s0f, hex);
                    api->say(line);
                }
            }
            if (f & ~0x09u) {
                api->fmt(line, sizeof line, "f %lu 82=%02x 84=%02x 86=%02x 90=%02x 98=%02x", (unsigned long)t,
                         f, api->c6000_read(0x84), api->c6000_read(0x86), api->c6000_read(0x90),
                         api->c6000_read(0x98));
                api->say(line);
            }
            api->c6000_write(0x83, f);
        }
        if (t - last_st >= 1000u) {
            last_st = t;
            api->fmt(line, sizeof line, "st %lu slots=%u bursts=%u nosync=%u ok=%u rearm=%u raw=%u 42=%02x "
                     "rssi=%ld 43=%02x%02x", (unsigned long)t, slots, bursts, nosync, ok, rearm, raw,
                     api->c6000_read(0x42),
                     (long)api->rssi(), api->c6000_read(0x43), api->c6000_read(0x44));
            api->say(line);
        }
        api->sleep_ms(1);
    }

    api->c6000_write(0x41, 0x20);
    api->c6000_write(0x40, 0x03);
    api->c6000_write(0x41, 0x00);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    at_restore(api, &as);
    C6_REG(GPIOD + 0x18u) = mux ? (1u << MUX) : (1u << (MUX + 16u));
    c6s_close(&ss);
    api->fmt(line, sizeof line, "end slots=%u bursts=%u nosync=%u ok=%u rearm=%u raw=%u", slots, bursts,
             nosync, ok, rearm, raw);
    api->say(line);
    return 0;
}
