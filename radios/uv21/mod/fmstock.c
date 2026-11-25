/* fmstock: talk to the UV-21 Pro's FM chip exactly the way its stock firmware does (hamlet's
   static read of the stock image, 2026-11-25; docs/k6-fm-chip.md), to see whether the way the
   bus is driven is why every read so far came back 0xFF. Receive only, audio muted.

   The stock bus: SCL PA4 push-pull, never released; SDA PB6 push-pull while the MCU sends, and
   an input with its pull-up for the acknowledge bit and for reads; 10 us a phase; a read waits
   about 70 us after SDA turns input before its first clock and samples at the end of SCL high.
   Address bytes 0x60 / 0x61. Between transactions both lines rest low (SDA is shared with the
   BK4829, whose SCN on PB3 stays high and is never touched here).

     s 0a=ffff 0b=ffff                     the stock status read before the setup (register 0AH, stop,
                                           0x61 and 4 bytes, one more byte with no acknowledge)
     r00 ffff ffff ...                     REG 00H-1FH one at a time, 8 a line, before (r) and after (R)
     S 0a=4000 0b=1800                     the status read after the stock setup (muted)
     T 100.100 0a=.... 0b=....             after a tune to FT_KHZ (default 100.1 MHz), FT_WAIT_MS later
   Settings: FT_INIT 0 skips the setup; FT_KHZ, FT_WAIT_MS (300); FT_PA3 1 first drives PA3 high
   the way the stock firmware holds it (purpose unknown: the port leaves it alone; perhaps the
   chip's supply or clock), says "p cfgr=.. idt=..", waits FT_PA3_MS (100), and puts it back at
   the end (FT_PA3_KEEP 1 leaves it high, the stock state, for the next run). FT_PLAY_S n: after
   the tune, unmute at volume FT_VOL (3 of 15) with the amplifier (PB2) on for n seconds, so a
   person at the radio can hear whether the chip receives at all even if reads fail; "play on" /
   "play off" mark it, and PB2 goes back to what it was. FT_KEY_S n: before playing, turn the
   backlight on (PF7) and wait up to n seconds for the top side key (it grounds row PC14),
   saying "key wait" and then "key" or "key timeout" (no play); the backlight goes back after. Returns 0x0B after the tune.
   At the end the chip is powered down (02H = 0x8060, the stock power-down) and both pins put
   back as they were. SPDX-License-Identifier: MIT */
#include "uv21_mod.h"

#ifndef FT_INIT
#define FT_INIT 1
#endif
#ifndef FT_KHZ
#define FT_KHZ 100100
#endif
#ifndef FT_PA3
#define FT_PA3 0
#endif
#ifndef FT_PLAY_S
#define FT_PLAY_S 0
#endif
#ifndef FT_KEY_S
#define FT_KEY_S 0
#endif
#ifndef FT_VOL
#define FT_VOL 3
#endif
#if FT_VOL > 8
#error "FT_VOL: 0-8 (of 15)"
#endif
#ifndef FT_PA3_KEEP
#define FT_PA3_KEEP 0
#endif
#ifndef FT_PA3_MS
#define FT_PA3_MS 100
#endif
#ifndef FT_WAIT_MS
#define FT_WAIT_MS 300
#endif
#if FT_KHZ < 76000 || FT_KHZ > 108000
#error "FT_KHZ: 76000-108000"
#endif

#define GPIOA 0x48000000u
#define GPIOB 0x48000400u
#define G_CFGR 0x00u
#define G_OMODE 0x04u
#define G_PULL 0x0Cu
#define G_IDT 0x10u
#define G_ODT 0x14u
#define G_SCR 0x18u
#define G_CLR 0x28u
#define SCL (1u << 4)
#define SDA (1u << 6)
#define MSCL (3u << 8)
#define MSDA (3u << 12)

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static void us(const um_api_t *a, uint32_t n) { a->delay_us(n); }
static void scl(int h) { wr(GPIOA + (h ? G_SCR : G_CLR), SCL); }
static void sda(int h) { wr(GPIOB + (h ? G_SCR : G_CLR), SDA); }
static void sda_out(void) { wr(GPIOB + G_CFGR, (rd(GPIOB + G_CFGR) & ~MSDA) | (1u << 12)); }
static void sda_inp(void) { wr(GPIOB + G_CFGR, rd(GPIOB + G_CFGR) & ~MSDA); }
static int sda_in(void) { return (rd(GPIOB + G_IDT) & SDA) != 0; }

static void start(const um_api_t *a) {
  sda_out();
  sda(1);
  us(a, 10);
  scl(1);
  us(a, 10);
  sda(0);
  us(a, 10);
  scl(0);
  us(a, 10);
}
static void stop(const um_api_t *a) {
  sda_out();
  sda(0);
  us(a, 10);
  scl(1);
  us(a, 10);
  sda(1);
  us(a, 10);
  scl(0); /* the stock bus rests low */
  sda(0);
  us(a, 10);
}
static int wb(const um_api_t *a, uint32_t b) {
  int i, ack = 0;
  sda_out();
  for (i = 7; i >= 0; i--) {
    sda((b >> i) & 1);
    us(a, 10);
    scl(1);
    us(a, 10);
    scl(0);
    us(a, 10);
  }
  sda_inp();
  us(a, 30);
  scl(1);
  us(a, 10);
  for (i = 0; i < 40; i++) ack |= sda_in();
  scl(0);
  us(a, 10);
  return !ack;
}
static uint32_t rb(const um_api_t *a, int ack) {
  uint32_t v = 0;
  int i;
  sda_inp();
  us(a, 70);
  for (i = 0; i < 8; i++) {
    scl(1);
    us(a, 10);
    v = v << 1 | (uint32_t)sda_in();
    scl(0);
    us(a, 10);
  }
  sda_out();
  sda(!ack);
  us(a, 10);
  scl(1);
  us(a, 10);
  scl(0);
  us(a, 10);
  return v;
}
static void w16(const um_api_t *a, uint32_t r, uint32_t v) {
  start(a);
  wb(a, 0x60);
  wb(a, r);
  wb(a, v >> 8);
  wb(a, v & 0xFF);
  stop(a);
}
/* register r and the n - 1 after it, the stock way: number, stop, 0x61, run-on read, one more */
static int rn(const um_api_t *a, uint32_t r, uint16_t *w, int n) {
  int i, ok;
  start(a);
  ok = wb(a, 0x60) && wb(a, r);
  stop(a);
  start(a);
  ok = wb(a, 0x61) && ok;
  for (i = 0; i < n; i++) {
    uint32_t hi = rb(a, 1);
    w[i] = (uint16_t)(hi << 8 | rb(a, 1));
  }
  rb(a, 0);
  stop(a);
  return ok;
}

static void status(const um_api_t *a, const char *tag) {
  uint16_t w[2];
  char line[48], *p;
  int ok = rn(a, 0x0A, w, 2);
  p = um_put(line, tag);
  p = um_hex(um_put(p, " 0a="), w[0], 4);
  p = um_hex(um_put(p, " 0b="), w[1], 4);
  if (!ok) um_put(p, " nack");
  a->say(line);
}
static void regs(const um_api_t *a, char t) {
  char line[64], *p;
  uint32_t r, k;
  for (r = 0; r < 0x20; r += 8) {
    p = line;
    *p++ = t;
    p = um_hex(p, r, 2);
    for (k = r; k < r + 8; k++) {
      uint16_t w;
      rn(a, k, &w, 1);
      p = um_hex(um_put(p, " "), w, 4);
    }
    a->say(line);
  }
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  uint32_t cfa = rd(GPIOA + G_CFGR), oma = rd(GPIOA + G_OMODE), pua = rd(GPIOA + G_PULL);
  uint32_t cfb = rd(GPIOB + G_CFGR), omb = rd(GPIOB + G_OMODE), pub = rd(GPIOB + G_PULL);
  uint32_t oda = rd(GPIOA + G_ODT), odb = rd(GPIOB + G_ODT), i;
  uint16_t w[2];
  char line[48], *p;
  (void)ram;
  (void)ram_len;
#if FT_PA3
  {
    uint32_t c3 = (cfa >> 6) & 3u, o3 = (oma >> 3) & 1u, p3 = (pua >> 6) & 3u, d3 = (oda >> 3) & 1u;
    p = um_put(line, "p mode=");
    *p++ = (char)('0' + c3);
    p = um_put(p, " od=");
    *p++ = (char)('0' + o3);
    p = um_put(p, " pull=");
    *p++ = (char)('0' + p3);
    p = um_put(p, " out=");
    *p++ = (char)('0' + d3);
    p = um_put(p, " in=");
    *p++ = (char)('0' + ((rd(GPIOA + G_IDT) >> 3) & 1u));
    *p = 0;
    api->say(line);
    wr(GPIOA + G_SCR, 1u << 3);
    wr(GPIOA + G_OMODE, (oma & ~(1u << 3)));
    wr(GPIOA + G_CFGR, (cfa & ~(3u << 6)) | (1u << 6));
    us(api, FT_PA3_MS * 1000u);
  }
#endif
  /* the stock pin setup: both push-pull outputs, low; SDA's pull-up for its input phases */
  scl(0);
  sda(0);
  wr(GPIOA + G_OMODE, oma & ~SCL);
  wr(GPIOB + G_OMODE, omb & ~SDA);
  wr(GPIOB + G_PULL, (pub & ~MSDA) | (1u << 12));
  wr(GPIOA + G_CFGR, (cfa & ~MSCL) | (1u << 8));
  sda_out();
  us(api, 100);

  status(api, "s");
  regs(api, 'r');
#if FT_INIT
  {
    static const uint8_t late[] = {0x05, 0x00, 0xE0, 0x07, 0x02, 0x02, 0x12, 0x86, 0x60, 0x16, 0x44, 0x00,
                                   0x1B, 0x8A, 0xDE, 0xFF, 0, 0, /* (the 200 ms wait) */
                                   0x36, 0x71, 0x14, 0x39, 0x1A, 0x16, 0x3B, 0x27, 0x3C, 0x3C, 0x3C, 0x41,
                                   0x3D, 0x04, 0x46, 0x3E, 0x1E, 0x12, 0x3F, 0x14, 0x28, 0x43, 0x02, 0x02};
    w16(api, 0x02, 0x0002);
    us(api, 10000);
    w16(api, 0x02, 0x8061); /* stock 0xC061 with DMUTE 0: muted */
    us(api, 10000);
    for (i = 0; i < sizeof late; i += 3) {
      if (late[i] == 0xFF) {
        us(api, 200000);
        continue;
      }
      w16(api, late[i], (uint32_t)late[i + 1] << 8 | late[i + 2]); /* 05H: volume 0 (stock 15) */
    }
  }
#endif
  status(api, "S");
  regs(api, 'R');
  /* the stock tune: 76-108 band, channel above 76 MHz, TUNE */
  w16(api, 0x02, 0x8061);
  w16(api, 0x03, (uint32_t)((FT_KHZ - 76000) / 100) << 6 | 0x18u);
  w16(api, 0x05, 0x00E0);
  us(api, FT_WAIT_MS * 1000u);
  rn(api, 0x0A, w, 2);
  p = um_put(line, "T ");
  p = um_int(p, FT_KHZ);
  p = um_hex(um_put(p, " 0a="), w[0], 4);
  um_hex(um_put(p, " 0b="), w[1], 4);
  api->say(line);
#if FT_PLAY_S
  {
    uint32_t pb2 = rd(GPIOB + G_ODT) & 4u, cf2 = rd(GPIOB + G_CFGR) & (3u << 4);
#if FT_KEY_S
    uint32_t bl = rd(0x48001400u + G_ODT) & 0x80u, t0 = api->ms(), held = 0, go = 0;
    wr(0x48001400u + G_SCR, 0x80u); /* backlight on */
    api->say("key wait");
    while (!go && api->ms() - t0 < FT_KEY_S * 1000u) {
      us(api, 10000);
      held = rd(0x48000800u + G_IDT) & (1u << 14) ? 0 : held + 1;
      go = held >= 5;
    }
    api->say(go ? "key" : "key timeout");
    if (!go) {
      wr(0x48001400u + (bl ? G_SCR : G_CLR), 0x80u);
      goto done;
    }
#endif
    w16(api, 0x05, 0x00E0 | FT_VOL);
    w16(api, 0x02, 0xC061); /* stock: unmuted */
    wr(GPIOB + G_SCR, 4u);
    wr(GPIOB + G_CFGR, (rd(GPIOB + G_CFGR) & ~(3u << 4)) | (1u << 4));
    api->say("play on");
    for (i = 0; i < FT_PLAY_S; i++) us(api, 1000000);
    w16(api, 0x05, 0x00E0);
    w16(api, 0x02, 0x8061);
    wr(GPIOB + (pb2 ? G_SCR : G_CLR), 4u);
    wr(GPIOB + G_CFGR, (rd(GPIOB + G_CFGR) & ~(3u << 4)) | cf2);
    api->say("play off");
#if FT_KEY_S
    wr(0x48001400u + (bl ? G_SCR : G_CLR), 0x80u);
#endif
  }
#endif
#if FT_PLAY_S && FT_KEY_S
done:
#endif
  w16(api, 0x02, 0x8060); /* the stock power-down */

  /* the pins back as they were */
#if FT_PA3 && !FT_PA3_KEEP
  wr(GPIOA + G_CFGR, (rd(GPIOA + G_CFGR) & ~(MSCL | (3u << 6))) | (cfa & (MSCL | (3u << 6))));
  wr(GPIOA + G_OMODE, (rd(GPIOA + G_OMODE) & ~(1u << 3)) | (oma & (1u << 3)));
  wr(GPIOA + ((oda >> 3) & 1u ? G_SCR : G_CLR), 1u << 3);
#else
  wr(GPIOA + G_CFGR, (rd(GPIOA + G_CFGR) & ~MSCL) | (cfa & MSCL));
#endif
  wr(GPIOB + G_CFGR, (rd(GPIOB + G_CFGR) & ~MSDA) | (cfb & MSDA));
  wr(GPIOA + G_OMODE, (rd(GPIOA + G_OMODE) & ~SCL) | (oma & SCL));
  wr(GPIOB + G_OMODE, (rd(GPIOB + G_OMODE) & ~SDA) | (omb & SDA));
  wr(GPIOA + G_PULL, (rd(GPIOA + G_PULL) & ~MSCL) | (pua & MSCL));
  wr(GPIOB + G_PULL, (rd(GPIOB + G_PULL) & ~MSDA) | (pub & MSDA));
  scl(oda & SCL);
  sda(odb & SDA);
  return (int)w[1];
}
