/* fesw: the receive front end's band switch as an attenuator. Receive only.

   The K6 has two receive front ends, one for VHF and one for UHF, and the MCU picks one with
   two pins (Aura's board/rf.rs: PA13 high = the UHF path, PA14 high = the VHF path, exactly one
   of them high while receiving, both low for TX and for power-down). api->tune sets the one
   the frequency wants. With the other one picked, or neither, the signal reaches the chip
   through the wrong path's leakage: a loss ahead of the receiver, which is what a weak station
   is and what taking gain off inside the chip is not (docs/k6-dmr.md, run L's caution).

   How much loss, this probe measures, and it asks the 2 m question on the way
   (docs/k6-aprs-clock.md: 144.390 MHz at hamlet has a floor of -102 dBm against -117 to -123
   elsewhere). If that floor stays with the front end off, the noise is made behind the
   switch; if it falls, it comes from the VHF path (the antenna, the cable, or the VHF
   amplifier itself, whose noise goes with the path too).
   Run 1 (2026-11-11, docs/k6-fesw.md): about 50 dB of loss on a strong 70 cm signal (48 the
   other path, 52 neither); the chip's own floor reads -151 to -154 dBm, the UHF path's -132,
   the VHF path's -111 on 144.390, and that noise is gone with the path off.

   FE_ROUNDS times round the table of frequencies (FE_KHZ), and on each the three states:
     s=0  the path api->tune picked
     s=1  the other path
     s=2  neither
   A state: the pins, FE_SETTLE_MS, then FE_N readings FE_STEP_US apart of RSSI (REG_67), the
   noise meter (REG_65 bits 6:0) and the glitch meter (REG_63 bits 7:0). Its line:
     fe 3 f=440925 s=1 t=41234 r=-104 -101 -99 n=41 g=18
   round, kHz, state, ms at its end, RSSI lowest / mean / highest, mean noise, mean glitch; an
   x at the end if the pins were not as set when the readings ended (the firmware moved them).
   The chip's AGC is left as the radio has it, as it was for every floor quoted so far.

   The pins: the state as found after each api->tune must be exactly one of the two high, or
   the module says so and returns -5 with nothing written. A change clears before it sets, so
   the two are never high together, and the path api->tune picked is put back after each
   frequency and before the return. Writes GPIOA BSRR / BRR bits 13 and 14 only; no register
   of the chip.

   Returns the lines said, -2 (a key), -3 (old image), -5 (the pins not as expected).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#include "../k6_mod.h"

#ifndef FE_KHZ
/* the 2 m APRS channel, NOAA weather radio (a steady carrier on the VHF path), the DMR
   repeater's output, the bench's own channel (the IC-7100's test carrier, on the UHF path) */
#define FE_KHZ 144390u, 162550u, 440925u, 440850u
#endif
#ifndef FE_ROUNDS
#define FE_ROUNDS 20u
#endif
#ifndef FE_N_LOG2
#define FE_N_LOG2 6           /* 64 readings a state */
#endif
#ifndef FE_STEP_US
#define FE_STEP_US 10000u
#endif
#ifndef FE_SETTLE_MS
#define FE_SETTLE_MS 100u
#endif
#define FE_N (1u << FE_N_LOG2)

#define GPIOA_ODR 0x48000014u
#define GPIOA_BSRR 0x48000018u
#define GPIOA_BRR 0x48000028u
#define PIN_UHF (1u << 13)
#define PIN_VHF (1u << 14)
#define PINS (PIN_UHF | PIN_VHF)

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {
  static const uint32_t pw[] = {1000000000, 100000000, 10000000, 1000000, 100000,
                                10000,      1000,      100,      10,      1};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    int d = 0;
    while (v >= pw[i]) v -= pw[i], d++;
    if (d || started || i == 9) *p++ = (char)('0' + d), started = 1;
  }
  return p;
}

static char *sdec(char *p, int v) {
  if (v < 0) *p++ = '-', v = -v;
  return dec(p, (uint32_t)v);
}

/* the pins to `want` (one of them, or none): clear first, so never both */
static void pins(uint32_t want) {
  wr(GPIOA_BRR, PINS & ~want);
  if (want) wr(GPIOA_BSRR, want);
}

int k6_main(const k6_api_t *api) {
  static const uint32_t khz[] = {FE_KHZ};
  enum { NF = sizeof khz / sizeof khz[0] };
  uint32_t round, own = 0;
  int said = 0, ret = 0, f, s;
  char line[64], *p;
  if (api->abi != 1 || api->level < 2) return -3;
  p = put(line, "fesw start n=");
  p = dec(p, NF);
  p = put(p, " rounds=");
  p = dec(p, FE_ROUNDS);
  p = put(p, " odr=");
  p = dec(p, rd(GPIOA_ODR) & PINS);
  *p = 0;
  api->say(line);
  for (round = 0; round < FE_ROUNDS && !ret; round++) {
    for (f = 0; f < NF && !ret; f++) {
      api->tune(khz[f] * 1000u, 0);
      own = rd(GPIOA_ODR) & PINS;
      if (own != PIN_UHF && own != PIN_VHF) {
        p = put(line, "fesw pins=");
        p = dec(p, own);
        p = put(p, " f=");
        p = dec(p, khz[f]);
        *p = 0;
        api->say(line);
        return -5;                      /* nothing written: not the board this was made for */
      }
      for (s = 0; s < 3; s++) {
        uint32_t i, sr = 0, sn = 0, sg = 0;
        int lo = 0x7FFF, hi = -0x7FFF;
        uint32_t want = s == 0 ? own : s == 1 ? own ^ PINS : 0;
        pins(want);
        api->delay_us(FE_SETTLE_MS * 1000u);
        for (i = 0; i < FE_N; i++) {
          int r = (int)((api->fd_read(0x67) & 0x1FF) >> 1);
          sr += (uint32_t)r;
          sn += api->fd_read(0x65) & 0x7Fu;
          sg += api->fd_read(0x63) & 0xFFu;
          if (r < lo) lo = r;
          if (r > hi) hi = r;
          api->delay_us(FE_STEP_US);
        }
        p = put(line, "fe ");
        p = dec(p, round);
        p = put(p, " f=");
        p = dec(p, khz[f]);
        p = put(p, " s=");
        p = dec(p, (uint32_t)s);
        p = put(p, " t=");
        p = dec(p, api->ms());
        p = put(p, " r=");
        p = sdec(p, lo - 174);
        *p++ = ' ';
        p = sdec(p, (int)((sr + FE_N / 2) >> FE_N_LOG2) - 174);
        *p++ = ' ';
        p = sdec(p, hi - 174);
        p = put(p, " n=");
        p = dec(p, (sn + FE_N / 2) >> FE_N_LOG2);
        p = put(p, " g=");
        p = dec(p, (sg + FE_N / 2) >> FE_N_LOG2);
        if ((rd(GPIOA_ODR) & PINS) != want) p = put(p, " x"); /* something else moved the pins */
        *p = 0;
        api->say(line);
        said++;
        if (api->key_down()) {
          ret = -2;
          break;
        }
      }
      pins(own);                        /* the path api->tune picked, before the next tune */
    }
  }
  p = put(line, "fesw end n=");
  p = dec(p, (uint32_t)said);
  *p = 0;
  api->say(line);
  return ret ? ret : said;
}
