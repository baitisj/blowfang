/* sottorx: [S12] does the UV-21 Pro's BK4829 receive the K6 voice pager's sotto packets? A
   receive-only RAM module that arms the chip's FSK modem as the K6 pager does for its default PHY
   (F: FFSK 1200/1800 Hz tones at 1800 baud, profile XL 128-byte packets; radios/k6/fw/src/app/pager.rs
   configure / rearm_rx, interrupt reads at 3 words), listens on SR_HZ for SR_S seconds, and checks
   each packet the way c/k6/sotto_pager.c does: PN9 de-whitening, the magic byte, CRC-16/CCITT-FALSE
   over all but the last two bytes. The port's firmware receiver (stage B, docs/uv21-sotto1200.md)
   will be this loop, so this is the framing check before any flash. MIT

     sr arm hz=HHHHHHHHH 2b=XXXX                     the setup
     sr pk N ok|crc|short magic=MM seq=SSSS tot=TTTT rssi=-NN.N swap=0|1 w=WW
                                                     each packet (seq and total from its header)
     sr end syncs=N pk=N ok=N a9=N bad=N short=N    the totals; returns ok (good packets)

   Settings: SR_HZ (440850000: the K6 pager's channel), SR_S (60), SR_LEN (128; 64 for profile L),
   SR_58 (0x3F01, F/1800), SR_72 (0x4898, 1800 baud), SR_THR (3 words an interrupt).
   Writes only the RF chip's receive registers (each through the API's receive-only filter); it
   leaves the FSK modem off at the end, and SURF retunes when the module returns. */
#include "uv21_mod.h"

#ifndef SR_HZ
#define SR_HZ 440850000u
#endif
#ifndef SR_S
#define SR_S 60
#endif
#ifndef SR_LEN
#define SR_LEN 128
#endif
#ifndef SR_58
#define SR_58 0x3F01u
#endif
#ifndef SR_72
#define SR_72 0x4898u
#endif
#ifndef SR_THR
#define SR_THR 3
#endif
#define SR_BAUD 1800u

/* FD6818 / BK4829 registers (the K6 pager's names) */
#define REG_IRQ_FLAGS 0x02
#define REG_STATUS 0x0C
#define REG_AF_FILT 0x2B
#define REG_IRQ_EN 0x3F
#define REG_FSK_CFG 0x58
#define REG_FSK_CTRL 0x59
#define REG_SYNC01 0x5A
#define REG_SYNC23 0x5B
#define REG_FSK_5C 0x5C
#define REG_FSK_LEN 0x5D
#define REG_FIFO_THR 0x5E
#define REG_FIFO 0x5F
#define REG_BAUD 0x72

#define IRQ_RX_DONE (1u << 13)
#define IRQ_RX_FULL (1u << 12)
#define IRQ_RX_SYNC (1u << 1)
#define CTRL_BASE 0x00F8u                /* 16 preamble bytes, 4-byte sync, no scrambler */
#define CTRL_CLR_RX 0x4000u
#define CTRL_RX_EN 0x1000u
#define AF_DC_CANCEL 0x4000u
#define CRC_OFF 0x5625u
#define FIFO_WORDS 8

typedef struct {
  uint32_t syncs, pk, ok, a9, bad, shorts;
} counts_t;

static void wr(const um_api_t *api, uint8_t r, uint16_t v) { api->fd_write(r, v); }

static uint16_t take_irq(const um_api_t *api) {
  if (!(api->fd_read(REG_STATUS) & 1u)) return 0;
  wr(api, REG_IRQ_FLAGS, 0);             /* the write latches the causes for the read */
  return api->fd_read(REG_IRQ_FLAGS);
}

static void rearm(const um_api_t *api) {
  wr(api, REG_FSK_CTRL, CTRL_BASE | CTRL_CLR_RX);
  wr(api, REG_FSK_CTRL, CTRL_BASE | CTRL_RX_EN);
  wr(api, REG_IRQ_FLAGS, 0);
  wr(api, REG_IRQ_EN, IRQ_RX_DONE | IRQ_RX_FULL | IRQ_RX_SYNC);
}

static uint8_t pn9_byte(uint16_t *lfsr) {
  /* the low byte of the x^9 + x^5 + 1 register from 0x1FF, then 8 shifts (sotto_pager.c's pn9[]) */
  uint8_t out = (uint8_t)*lfsr;
  for (int k = 0; k < 8; k++) {
    uint16_t b = ((*lfsr >> 0) ^ (*lfsr >> 5)) & 1u;
    *lfsr = (uint16_t)((*lfsr >> 1) | (b << 8));
  }
  return out;
}

static unsigned crc16(const uint8_t *p, int n) {
  unsigned c = 0xFFFF;
  while (n--) {
    c ^= (unsigned)*p++ << 8;
    for (int k = 0; k < 8; k++) c = c & 0x8000u ? ((c << 1) ^ 0x1021u) & 0xFFFFu : (c << 1) & 0xFFFFu;
  }
  return c;
}

/* de-whitens raw (as read) into out, with each word's bytes swapped first if swap; 1 if the CRC
   checks */
static int check(const uint8_t *raw, uint8_t *out, int len, int swap) {
  uint16_t lfsr = 0x1FF;
  for (int i = 0; i < len; i++) out[i] = raw[swap ? i ^ 1 : i] ^ pn9_byte(&lfsr);
  return crc16(out, len - 2) == ((unsigned)out[len - 2] << 8 | out[len - 1]);
}

static int magic_ok(uint8_t m) {
  m &= (uint8_t)~0x10u;                  /* parity packets: magic | 0x10 */
  return m == 0xA7 || m == 0xA9 || m == 0xAC || m == 0xAD;
}

static void report(const um_api_t *api, counts_t *c, const uint8_t *raw, int got, uint8_t *tmp) {
  char t[112];
  int good = 0, swap = 0;
  c->pk++;
  if (got < SR_LEN) {
    c->shorts++;
  } else if (check(raw, tmp, SR_LEN, 0)) {
    good = 1;
  } else if (check(raw, tmp, SR_LEN, 1)) {
    good = 1, swap = 1;
  } else {
    check(raw, tmp, SR_LEN, 0);
    c->bad++;
  }
  if (good) {
    c->ok++;
    if ((tmp[0] & (uint8_t)~0x10u) == 0xA9) c->a9++;
  }
  char *p = um_put(t, "sr pk ");
  p = um_int(p, (int32_t)c->pk);
  p = um_put(p, got < SR_LEN ? " short" : good ? (magic_ok(tmp[0]) ? " ok" : " ok?") : " crc");
  p = um_hex(um_put(p, " magic="), tmp[0], 2);
  p = um_hex(um_put(p, " seq="), (uint32_t)tmp[2] << 8 | tmp[3], 4);
  p = um_hex(um_put(p, " tot="), (uint32_t)tmp[4] << 8 | tmp[5], 4);
  p = um_half_db(um_put(p, " rssi="), um_rssi2(api));
  p = um_int(um_put(p, " swap="), swap);
  um_int(um_put(p, " w="), got / 2);
  api->say(t);
}

static void say_counts(const um_api_t *api, const counts_t *c) {
  char t[96];
  char *p = um_put(t, "sr end syncs=");
  p = um_int(p, (int32_t)c->syncs);
  p = um_int(um_put(p, " pk="), (int32_t)c->pk);
  p = um_int(um_put(p, " ok="), (int32_t)c->ok);
  p = um_int(um_put(p, " a9="), (int32_t)c->a9);
  p = um_int(um_put(p, " bad="), (int32_t)c->bad);
  um_int(um_put(p, " short="), (int32_t)c->shorts);
  api->say(t);
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  char t[64];
  counts_t c = {0, 0, 0, 0, 0, 0};
  uint8_t *raw = ram, *tmp = ram + 256;
  if (ram_len < 512) return -1;

  api->tune(SR_HZ, 1);
  uint16_t r2b = api->fd_read(REG_AF_FILT) & (uint16_t)~AF_DC_CANCEL;
  wr(api, REG_AF_FILT, r2b);
  wr(api, REG_IRQ_EN, 0);
  wr(api, REG_FSK_CFG, SR_58);
  wr(api, REG_BAUD, SR_72);
  wr(api, REG_SYNC01, 0x076B);
  wr(api, REG_SYNC23, 0x3E36);
  wr(api, REG_FSK_5C, CRC_OFF);
  wr(api, REG_FSK_LEN, (uint16_t)((SR_LEN - 1) << 8));
  wr(api, REG_FIFO_THR, (uint16_t)(0x0200 | SR_THR));
  rearm(api);
  char *p = um_put(t, "sr arm hz=");
  p = um_int(p, (int32_t)SR_HZ);
  um_hex(um_put(p, " 2b="), r2b, 4);
  api->say(t);

  /* a word is 16 bits: 8.9 ms at 1800 baud. With N or more words waiting after a read the
     almost-full interrupt never comes again, so a word is taken by time after N + 1 word times
     without one (the K6's stall rule) */
  const uint32_t word_us = 16u * 1000000u / SR_BAUD;
  int in_pkt = 0, got = 0;
  uint32_t t_end = api->ms() + (uint32_t)SR_S * 1000u, t_read = 0;
  while ((int32_t)(api->ms() - t_end) < 0) {
    uint16_t f = take_irq(api);
    uint32_t now_us = api->ms() * 1000u;
    if (f & IRQ_RX_SYNC) {
      c.syncs++;
      in_pkt = 1, got = 0, t_read = now_us;
    }
    if (in_pkt && (f & IRQ_RX_FULL)) {
      for (int k = 0; k < SR_THR && got < SR_LEN; k++, got += 2) {
        uint16_t w = api->fd_read(REG_FIFO);
        raw[got] = (uint8_t)(w >> 8), raw[got + 1] = (uint8_t)w;   /* the first byte on air */
      }
      t_read = now_us;
    } else if (in_pkt && got < SR_LEN && now_us - t_read > (SR_THR + 1) * word_us) {
      uint16_t w = api->fd_read(REG_FIFO);
      raw[got] = (uint8_t)(w >> 8), raw[got + 1] = (uint8_t)w;
      got += 2;
      t_read = now_us;
    }
    if (in_pkt && (f & IRQ_RX_DONE) && got < SR_LEN) {
      for (int k = 0; k < FIFO_WORDS && got < SR_LEN; k++, got += 2) {
        uint16_t w = api->fd_read(REG_FIFO);
        raw[got] = (uint8_t)(w >> 8), raw[got + 1] = (uint8_t)w;
      }
    }
    if (in_pkt && (got >= SR_LEN || (f & IRQ_RX_DONE))) {
      report(api, &c, raw, got, tmp);
      in_pkt = 0;
      rearm(api);
    } else if (!in_pkt && (f & IRQ_RX_DONE)) {
      rearm(api);                        /* an RX-done with no sync: listen again */
    }
    api->delay_us(500);
  }
  wr(api, REG_IRQ_EN, 0);
  wr(api, REG_FSK_CTRL, CTRL_BASE);
  wr(api, REG_IRQ_FLAGS, 0);
  wr(api, REG_FSK_CFG, 0);
  say_counts(api, &c);
  return (int)c.ok;
}
