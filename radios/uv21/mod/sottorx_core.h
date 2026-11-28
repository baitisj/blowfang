/* sottorx_core.h: [S12] the UV-21 Pro's sotto packet receiver on the BK4829's FSK modem, shared by
   the receive check module (sottorx.c) and the port's built-in sotto 1200 receiver
   (radios/uv21/sotto/uv21_sotto.c), so the module tests the same code the firmware runs.

   The K6 voice pager's setup for its default PHY (radios/k6/fw/src/app/pager.rs configure and
   rearm_rx): F, FFSK 1200/1800 Hz tones at 1800 baud (REG_58 0x3F01, REG_72 0x4898), sync words
   0x076B 0x3E36, the chip's CRC off, REG_5D the packet length, FIFO almost-full at 3 words, DC
   cancellation after the demodulator off. Interrupt reads as the K6's (the REG_02 write latches
   the causes for the read), with its stall rule: with N or more words still waiting after a read
   the almost-full interrupt never comes again, so a word is taken by time after N + 1 word times
   without one. No writable statics (a RAM module is -fropi): the state is the caller's.
   SPDX-License-Identifier: MIT */
#ifndef SOTTORX_CORE_H
#define SOTTORX_CORE_H
#include "uv21_mod.h"

#ifndef SR_LEN
#define SR_LEN 128                       /* profile XL; 64 for L */
#endif
#ifndef SR_58
#define SR_58 0x3F01u                    /* F/1800 */
#endif
#ifndef SR_72
#define SR_72 0x4898u                    /* 1800 baud */
#endif
#ifndef SR_THR
#define SR_THR 3                         /* words an almost-full interrupt */
#endif
#ifndef SR_BAUD
#define SR_BAUD 1800u                    /* what SR_72 sets: the word timing (stall rule, stray RX-done) */
#endif
#ifndef SR_SWAP
/* the BK4829 hands each FIFO word over with the first byte on air in its low half (the RT-950's
   notes; on the UV-21, job s12rx3: 41 of 44 D/2400 packets checked only with the bytes swapped),
   the K6's FD6818 in its high half: 1 takes the low byte first, so the packet is in air order */
#define SR_SWAP 1
#endif
#ifndef SR_2B_OR
#define SR_2B_OR 0x0000u                 /* bits ORed into REG_2B: 0x0400 the RX 300 Hz HPF off (D/2400) */
#endif

#define SRX_REG_IRQ_FLAGS 0x02
#define SRX_REG_STATUS 0x0C
#define SRX_REG_AF_FILT 0x2B
#define SRX_REG_IRQ_EN 0x3F
#define SRX_REG_FSK_CFG 0x58
#define SRX_REG_FSK_CTRL 0x59
#define SRX_REG_SYNC01 0x5A
#define SRX_REG_SYNC23 0x5B
#define SRX_REG_FSK_5C 0x5C
#define SRX_REG_FSK_LEN 0x5D
#define SRX_REG_FIFO_THR 0x5E
#define SRX_REG_FIFO 0x5F
#define SRX_REG_BAUD 0x72

#define SRX_IRQ_RX_DONE (1u << 13)
#define SRX_IRQ_RX_FULL (1u << 12)
#define SRX_IRQ_RX_SYNC (1u << 1)
#define SRX_CTRL_BASE 0x00F8u            /* 16 preamble bytes, 4-byte sync, no scrambler */
#define SRX_CTRL_CLR_RX 0x4000u
#define SRX_CTRL_RX_EN 0x1000u
#define SRX_AF_DC_CANCEL 0x4000u
#define SRX_CRC_OFF 0x5625u
#define SRX_FIFO_WORDS 8
#define SRX_WORD_US (16u * 1000000u / SR_BAUD)
/* the packet's air time after its sync, ms (569 at 128 bytes, 1800 baud) */
#define SRX_PKT_MS (SR_LEN * 8u * 1000u / SR_BAUD)
/* an RX-done sooner than 60 % of that after the sync is the last packet's, late (the K6 pager's
   STRAY_DONE rule): taken as this one's end it would cut it short */
#define SRX_STRAY_MS (SRX_PKT_MS * 6u / 10u)

typedef struct {
  int in_pkt, got;                       /* a packet is coming in; its bytes so far */
  uint32_t t_read_ms;                    /* the last FIFO read (ms) */
  uint32_t t_sync_ms;                    /* the packet's sync interrupt (ms) */
  uint32_t syncs;                        /* sync interrupts seen */
  uint16_t r2b;                          /* REG_2B as set */
  uint8_t raw[SR_LEN];                   /* the packet as the FIFO gave it: first byte on air first */
} srx_t;

static inline void srx_wr(const um_api_t *api, uint8_t r, uint16_t v) { api->fd_write(r, v); }

static inline uint16_t srx_irq(const um_api_t *api) {
  if (!(api->fd_read(SRX_REG_STATUS) & 1u)) return 0;
  srx_wr(api, SRX_REG_IRQ_FLAGS, 0);
  return api->fd_read(SRX_REG_IRQ_FLAGS);
}

static inline void srx_rearm(const um_api_t *api, srx_t *s) {
  srx_wr(api, SRX_REG_FSK_CTRL, SRX_CTRL_BASE | SRX_CTRL_CLR_RX);
  srx_wr(api, SRX_REG_FSK_CTRL, SRX_CTRL_BASE | SRX_CTRL_RX_EN);
  srx_wr(api, SRX_REG_IRQ_FLAGS, 0);
  srx_wr(api, SRX_REG_IRQ_EN, SRX_IRQ_RX_DONE | SRX_IRQ_RX_FULL | SRX_IRQ_RX_SYNC);
  s->in_pkt = 0, s->got = 0;
}

/* tunes to hz and arms the receiver */
static inline void srx_arm(const um_api_t *api, srx_t *s, uint32_t hz) {
  api->tune(hz, 1);
  s->r2b = (uint16_t)((api->fd_read(SRX_REG_AF_FILT) & (uint16_t)~SRX_AF_DC_CANCEL) | SR_2B_OR);
  srx_wr(api, SRX_REG_AF_FILT, s->r2b);
  srx_wr(api, SRX_REG_IRQ_EN, 0);
  srx_wr(api, SRX_REG_FSK_CFG, SR_58);
  srx_wr(api, SRX_REG_BAUD, SR_72);
  srx_wr(api, SRX_REG_SYNC01, 0x076B);
  srx_wr(api, SRX_REG_SYNC23, 0x3E36);
  srx_wr(api, SRX_REG_FSK_5C, SRX_CRC_OFF);
  srx_wr(api, SRX_REG_FSK_LEN, (uint16_t)((SR_LEN - 1) << 8));
  srx_wr(api, SRX_REG_FIFO_THR, (uint16_t)(0x0200 | SR_THR));
  s->syncs = 0;
  srx_rearm(api, s);
}

/* the FSK modem off (as the K6 pager's disarm) */
static inline void srx_off(const um_api_t *api) {
  srx_wr(api, SRX_REG_IRQ_EN, 0);
  srx_wr(api, SRX_REG_FSK_CTRL, SRX_CTRL_BASE);
  srx_wr(api, SRX_REG_IRQ_FLAGS, 0);
  srx_wr(api, SRX_REG_FSK_CFG, 0);
}

static inline void srx_take(const um_api_t *api, srx_t *s, int n) {
  for (; n > 0 && s->got < SR_LEN; n--, s->got += 2) {
    uint16_t w = api->fd_read(SRX_REG_FIFO);
    if (SR_SWAP) w = (uint16_t)(w << 8 | w >> 8);
    s->raw[s->got] = (uint8_t)(w >> 8), s->raw[s->got + 1] = (uint8_t)w;
  }
}

/* one look at the chip (call every 0.5-5 ms): 1 when a packet has ended (s->raw, s->got bytes;
   got < SR_LEN: it came short), with the receiver armed again for the next */
static inline int srx_poll(const um_api_t *api, srx_t *s) {
  uint16_t f = srx_irq(api);
  uint32_t now = api->ms();
  if (f & SRX_IRQ_RX_SYNC) {
    s->syncs++;
    s->in_pkt = 1, s->got = 0, s->t_read_ms = now, s->t_sync_ms = now;
  }
  if (s->in_pkt && (f & SRX_IRQ_RX_DONE) && now - s->t_sync_ms < SRX_STRAY_MS)
    f &= (uint16_t)~SRX_IRQ_RX_DONE;
  if (s->in_pkt && (f & SRX_IRQ_RX_FULL)) {
    srx_take(api, s, SR_THR);
    s->t_read_ms = now;
  } else if (s->in_pkt && s->got < SR_LEN &&
             (now - s->t_read_ms) * 1000u > (SR_THR + 1) * SRX_WORD_US) {
    /* no almost-full for N + 1 word times: the edge was lost with N or more words waiting, and
       at least N + 1 came since the last read, so N are there to take */
    srx_take(api, s, SR_THR);
    s->t_read_ms = now;
  }
  if (s->in_pkt && s->got < SR_LEN && now - s->t_sync_ms > 2u * SRX_PKT_MS) {
    int got = s->got;                    /* a sync that never filled a packet: listen again */
    srx_rearm(api, s);
    s->got = got;
    return 1;
  }
  if (s->in_pkt && (f & SRX_IRQ_RX_DONE) && s->got < SR_LEN) srx_take(api, s, SRX_FIFO_WORDS);
  if (s->in_pkt && (s->got >= SR_LEN || (f & SRX_IRQ_RX_DONE))) {
    int got = s->got;
    srx_rearm(api, s);
    s->got = got;                        /* (what came; the next sync starts over) */
    return 1;
  }
  if (!s->in_pkt && (f & SRX_IRQ_RX_DONE)) srx_rearm(api, s);   /* an RX-done with no sync */
  return 0;
}

/* the low byte of the x^9 + x^5 + 1 register from 0x1FF, then 8 shifts (sotto_pager.c's pn9[]) */
static inline uint8_t srx_pn9(uint16_t *lfsr) {
  uint8_t out = (uint8_t)*lfsr;
  for (int k = 0; k < 8; k++) {
    uint16_t b = (*lfsr ^ (*lfsr >> 5)) & 1u;
    *lfsr = (uint16_t)((*lfsr >> 1) | (b << 8));
  }
  return out;
}

static inline unsigned srx_crc16(const uint8_t *p, int n) {
  unsigned c = 0xFFFF;
  while (n--) {
    c ^= (unsigned)*p++ << 8;
    for (int k = 0; k < 8; k++) c = c & 0x8000u ? ((c << 1) ^ 0x1021u) & 0xFFFFu : (c << 1) & 0xFFFFu;
  }
  return c;
}

/* de-whitens raw into out, each 16-bit word's bytes swapped first if swap; 1 if the CRC checks */
static inline int srx_check(const uint8_t *raw, uint8_t *out, int len, int swap) {
  uint16_t lfsr = 0x1FF;
  for (int i = 0; i < len; i++) out[i] = raw[swap ? i ^ 1 : i] ^ srx_pn9(&lfsr);
  return srx_crc16(out, len - 2) == ((unsigned)out[len - 2] << 8 | out[len - 1]);
}

#endif
