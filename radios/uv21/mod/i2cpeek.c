/* i2cpeek: read the UV-21 Pro's I2C device (SCL PA4, SDA PB6 shared with the BK4829) two ways,
   to find what it is (docs/k6-fm-chip.md). Reads only: the only bytes sent after an address are
   register numbers.

   fmprobe's scan (2026-11-25, run ufmp1) found 7-bit 0x10, 0x11, 0x30 and 0x40 acknowledging, but
   every plain read gave 0xFF, as a Beken BK1080 / BK1088 would: its "2-wire" bus takes the
   device byte 0x80 and puts the direction in the next byte, (reg << 1) | 1, before it sends data.
     A 10 11 30 40                          the addresses that acknowledge (0x08-0x77)
     k80 00 0006 1080 ....                  Beken reads: device byte, first register, 8 words
     q22 00 5804 ....                       I2C reads with a repeated start: device byte (write
                                            form), first register, 8 words
   The I2C way goes first at every address; the Beken way follows only at 0x80 and only if every
   I2C read there began 0xFF (what a Beken chip answers to one), since to a plain I2C chip the clocks after (reg << 1) | 1 would
   write 0xFF bytes. "----" is a read nobody acknowledged. Settings (-D): IP_REGS registers per device (32),
   IP_HALF_US the half bit (10). Returns the number of addresses that answered.
   SPDX-License-Identifier: MIT */
#include "uv21_mod.h"

#ifndef IP_REGS
#define IP_REGS 32
#endif
#ifndef IP_HALF_US
#define IP_HALF_US 10
#endif
#if IP_REGS > 128 || IP_REGS % 8
#error "IP_REGS: a multiple of 8, up to 128"
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

static void hb(const um_api_t *a) { a->delay_us(IP_HALF_US); }
static void scl(const um_api_t *a, int h) {
  wr(GPIOA + (h ? G_SCR : G_CLR), SCL);
  hb(a);
}
static void sda(int h) { wr(GPIOB + (h ? G_SCR : G_CLR), SDA); }
static int sda_in(void) { return (rd(GPIOB + G_IDT) & SDA) != 0; }
static void start(const um_api_t *a) {
  sda(1);
  scl(a, 1);
  sda(0);
  hb(a);
  scl(a, 0);
}
static void stop(const um_api_t *a) {
  int i;
  sda(1);
  for (i = 0; i < 9 && !sda_in(); i++) { /* a device still sending: clock it out first */
    scl(a, 1);
    scl(a, 0);
  }
  sda(0);
  hb(a);
  scl(a, 1);
  sda(1);
  hb(a);
}
static int wb(const um_api_t *a, uint32_t b) {
  int i, ack;
  for (i = 7; i >= 0; i--) {
    sda((b >> i) & 1);
    hb(a);
    scl(a, 1);
    scl(a, 0);
  }
  sda(1);
  hb(a);
  scl(a, 1);
  ack = !sda_in();
  scl(a, 0);
  return ack;
}
static uint32_t rb(const um_api_t *a, int ack) {
  uint32_t v = 0;
  int i;
  sda(1);
  for (i = 0; i < 8; i++) {
    hb(a);
    scl(a, 1);
    v = v << 1 | (uint32_t)sda_in();
    scl(a, 0);
  }
  sda(!ack);
  hb(a);
  scl(a, 1);
  scl(a, 0);
  sda(1);
  return v;
}

/* one 16-bit register: Beken (dev, reg << 1 | 1, data) or I2C (dev, reg, Sr, dev | 1, data) */
static int32_t reg(const um_api_t *a, uint32_t dev, uint32_t r, int beken) {
  int32_t v = -1;
  start(a);
  if (wb(a, dev) && wb(a, beken ? (r << 1 | 1u) : r)) {
    if (beken || (start(a), wb(a, dev | 1u))) {
      uint32_t hi = rb(a, 1);
      v = (int32_t)(hi << 8 | rb(a, 0));
    }
  }
  stop(a);
  return v;
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  uint32_t i, n = 0, cfa, cfb, oma, omb, pua, pub, oda, odb;
  uint8_t acks[8];
  char line[64], *p;
  (void)ram;
  (void)ram_len;
  cfa = rd(GPIOA + G_CFGR), oma = rd(GPIOA + G_OMODE), pua = rd(GPIOA + G_PULL), oda = rd(GPIOA + G_ODT);
  cfb = rd(GPIOB + G_CFGR), omb = rd(GPIOB + G_OMODE), pub = rd(GPIOB + G_PULL), odb = rd(GPIOB + G_ODT);
  wr(GPIOA + G_SCR, SCL);
  wr(GPIOB + G_SCR, SDA);
  wr(GPIOA + G_OMODE, oma | SCL);
  wr(GPIOB + G_OMODE, omb | SDA);
  wr(GPIOA + G_PULL, (pua & ~MSCL) | (1u << 8));
  wr(GPIOB + G_PULL, (pub & ~MSDA) | (1u << 12));
  wr(GPIOA + G_CFGR, (cfa & ~MSCL) | (1u << 8));
  wr(GPIOB + G_CFGR, (cfb & ~MSDA) | (1u << 12));
  hb(api);
  stop(api);

  p = um_put(line, "A");
  for (i = 8; i < 0x78; i++) {
    int ack;
    start(api);
    ack = wb(api, i << 1);
    stop(api);
    if (!ack) continue;
    if (n < 8) acks[n++] = (uint8_t)i;
    if (p < line + 56) p = um_hex(um_put(p, " "), i, 2);
  }
  api->say(line);

  for (i = 0; i < n; i++) {
    int beken, any = 0;
    for (beken = 0; beken < 2; beken++) {
      uint32_t r, k;
      /* the Beken way only at its own device byte 0x80, and only when every I2C read there began
         0xFF (a Beken chip takes the released first byte as "read REG 7FH" and sends its high
         byte second): to a plain I2C chip the clocks after (reg << 1) | 1 would write 0xFF */
      if (beken && (any || acks[i] != 0x40)) break;
      for (r = 0; r < IP_REGS; r += 8) {
        p = line;
        *p++ = beken ? 'k' : 'q';
        p = um_hex(p, (uint32_t)acks[i] << 1, 2);
        p = um_hex(um_put(p, " "), r, 2);
        for (k = r; k < r + 8; k++) {
          int32_t v = reg(api, (uint32_t)acks[i] << 1, k, beken);
          if (v >= 0 && (v >> 8) != 0xFF) any = 1;
          p = um_put(p, " ");
          p = v < 0 ? um_put(p, "----") : um_hex(p, (uint32_t)v, 4);
        }
        api->say(line);
      }
    }
  }

  stop(api);
  wr(GPIOA + G_CFGR, (rd(GPIOA + G_CFGR) & ~MSCL) | (cfa & MSCL));
  wr(GPIOB + G_CFGR, (rd(GPIOB + G_CFGR) & ~MSDA) | (cfb & MSDA));
  wr(GPIOA + G_OMODE, (rd(GPIOA + G_OMODE) & ~SCL) | (oma & SCL));
  wr(GPIOB + G_OMODE, (rd(GPIOB + G_OMODE) & ~SDA) | (omb & SDA));
  wr(GPIOA + G_PULL, (rd(GPIOA + G_PULL) & ~MSCL) | (pua & MSCL));
  wr(GPIOB + G_PULL, (rd(GPIOB + G_PULL) & ~MSDA) | (pub & MSDA));
  wr(GPIOA + (oda & SCL ? G_SCR : G_CLR), SCL);
  wr(GPIOB + (odb & SDA ? G_SCR : G_CLR), SDA);
  return (int)n;
}
