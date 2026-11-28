/* uv21_sotto: [S12] the UV-21 Pro port's sotto 1200 receiver and player, built into the image
   (radios/uv21/fw/build.rs links it with libsotto_uv21.a). It runs like a RAM module, with the
   whole 12 KB arena while SURF is paused, through the same um_api_t, and returns to SURF.

     receive  the K6 voice pager's packets (radios/uv21/mod/sottorx_core.h, which the sottorx
              module checks on the radio): F/1800, profile XL, 0xA9 sotto 1200 voice packets
     store    c/k6/sotto_pager.c (-DSP_LR, stream mode): each 6-byte slot in its place, lost
              ones concealed; the decoder's pool in the arena (SP_DEC_POOL)
     decode   the fixed-point sotto 1200 decoder, the codebook read from SPI flash at 0x1F0000
              (sp_flash_read in surf.rs maps the K6's 0x100000 there; radios/uv21/mod/cbspi.c
              wrote it once); bit for bit the K6's (tests/test_uv21_sotto.py)
     play     TMR1 CH4 PWM on PA11 (AF2) at 48 kHz, repetition 6, so its overflow DMA request
              (DMA1 channel 5, fixed) writes the next compare value at 8 kHz from a two-half ring;
              the stock firmware's voice-prompt path (TMR1, repetition 5, channel 5 to C4DT).
              The RF chip's audio is muted meanwhile (REG_47 base, the K6's AfOutState::Mute)
              and put back after; the speaker amplifier (PB2) stays as the port holds it, on

   It listens up to listen_s seconds for a message, then plays it as it streams in, and returns
   listen_s seconds after the last packet once playback is over. Lines (the serial console):
     so start hz=.. cb=r store=frames pool=bytes
     so pk n=N r=R seq=SSSS                    each packet, R sp_packet's result (-1 bad CRC ...)
     so play gain=G                            playback starts
     so end pk=N good=N frames=got/total played=N late=N pcm=HHHHHHHH n=samples
   pcm= is the FNV-1a hash of the samples played (sp_pcm_hash): c/k6/pager_host_lr prints the
   same hash for the same packets, so a bench run checks playback without anyone listening.
   Returns the frames played, or a negative number (-1: no codebook in SPI flash, -2: no room).
   SPDX-License-Identifier: MIT */
#include "../mod/sottorx_core.h"
#include "../../../c/k6/sotto_pager.h"
#include "../../../c/common/sp_cb.h"

#define REG(a) (*(volatile uint32_t *)(a))
#define CRM_AHBEN REG(0x40021014u)
#define CRM_APB2EN REG(0x40021018u)
#define GPIOA_CFGR REG(0x48000000u)
#define GPIOA_MUXH REG(0x48000024u)
#define TMR1 0x40012C00u
#define T_CTRL1 REG(TMR1 + 0x00)
#define T_IDEN REG(TMR1 + 0x0C)
#define T_SWEVT REG(TMR1 + 0x14)
#define T_CM2 REG(TMR1 + 0x1C)
#define T_CCTRL REG(TMR1 + 0x20)
#define T_DIV REG(TMR1 + 0x28)
#define T_PR REG(TMR1 + 0x2C)
#define T_RPR REG(TMR1 + 0x30)
#define T_C4DT REG(TMR1 + 0x40)
#define T_BRK REG(TMR1 + 0x44)
#define DMA1 0x40020000u
#define D_CLR REG(DMA1 + 0x04)
#define D5_CTRL REG(DMA1 + 0x58)
#define D5_DTCNT REG(DMA1 + 0x5C)
#define D5_PADDR REG(DMA1 + 0x60)
#define D5_MADDR REG(DMA1 + 0x64)

#define SCLK 120000000u
#define TOP (SCLK / 48000u)              /* 2500: 48 kHz carrier, the K6's */
#define HALF SP_FRAME_SAMPLES            /* 160 samples, 20 ms */
#define STORE 6144u                      /* 992 slots, 39.7 s of sotto 1200 (stream mode rings it) */
#define REG_AF_OUT 0x47
#define AF_MUTE 0x6040u

#ifndef SO_EMPHASIS
#define SO_EMPHASIS 0                    /* the K6's RC correction; the UV-21's filter isn't measured */
#endif

static void say2(const um_api_t *api, const char *a, int32_t x, const char *b, int32_t y) {
  char t[64];
  char *p = um_put(t, a);
  p = um_int(p, x);
  p = um_put(p, b);
  um_int(p, y);
  api->say(t);
}

static void pwm_start(uint16_t *ring) {
  CRM_AHBEN |= 1u << 0;                  /* DMA1 */
  CRM_APB2EN |= 1u << 11;                /* TMR1 */
  T_CTRL1 = 0;
  D5_CTRL = 0;
  D_CLR = 0xFu << 16;                    /* channel 5's flags */
  D5_PADDR = TMR1 + 0x40;                /* C4DT */
  D5_MADDR = (uint32_t)ring;
  D5_DTCNT = 2 * HALF;
  /* priority high, 16-bit both sides, memory increments, circular, memory to peripheral */
  D5_CTRL = (2u << 12) | (1u << 10) | (1u << 8) | (1u << 7) | (1u << 5) | (1u << 4);
  T_DIV = 0;
  T_PR = TOP - 1;
  T_RPR = 5;                             /* an overflow event every 6 periods: 8 kHz */
  T_C4DT = TOP / 2;
  T_CM2 = (T_CM2 & ~0xFF00u) | (6u << 12) | (1u << 11);   /* CH4: PWM mode A, preload */
  T_CCTRL |= 1u << 12;                   /* C4EN */
  T_BRK |= 1u << 15;                     /* OEN */
  T_SWEVT = 1;
  T_IDEN |= 1u << 8;                     /* OVFDEN: the overflow's DMA request */
  D5_CTRL |= 1u;                         /* CHEN */
  GPIOA_MUXH = (GPIOA_MUXH & ~(0xFu << 12)) | (2u << 12); /* PA11 AF2: TMR1_CH4 */
  GPIOA_CFGR = (GPIOA_CFGR & ~(3u << 22)) | (2u << 22);
  T_CTRL1 = (1u << 7) | 1u;              /* PRBEN, TMREN */
}

static void pwm_stop(uint32_t cfgr0, uint32_t muxh0) {
  T_CTRL1 = 0;
  T_IDEN &= ~(1u << 8);
  D5_CTRL = 0;
  T_CCTRL &= ~(1u << 12);
  GPIOA_CFGR = (GPIOA_CFGR & ~(3u << 22)) | (cfgr0 & (3u << 22));
  GPIOA_MUXH = (GPIOA_MUXH & ~(0xFu << 12)) | (muxh0 & (0xFu << 12));
}

/* the half the DMA is in now (0 or 1) */
static int pwm_half(void) { return (2 * HALF - D5_DTCNT) >= HALF; }

int uv21_sotto(const um_api_t *api, uint8_t *ram, uint32_t ram_len, uint32_t hz, uint32_t listen_s) {
  srx_t *rx = (srx_t *)ram;
  uint32_t at = (sizeof(srx_t) + 7u) & ~7u;
  uint8_t *store = ram + at;
  uint8_t *pool = store + STORE;
  uint32_t need = sp_dec_need();
  uint16_t *ring = (uint16_t *)(pool + ((need + 7u) & ~7u));
  if ((uint8_t *)(ring + 2 * HALF) > ram + ram_len) return -2;

  int cb = sp_cb_setup();
  sp_dec_pool(pool, need);
  int nmax = sp_init(store, STORE);
  char t[96];
  char *p = um_put(t, "so start hz=");
  p = um_int(p, (int32_t)hz);
  p = um_int(um_put(p, " cb="), cb);
  p = um_int(um_put(p, " store="), nmax);
  um_int(um_put(p, " pool="), (int32_t)need);
  api->say(t);
  if (cb <= 0 || !sp_cb_ready()) return -1;

  uint16_t af0 = api->fd_read(REG_AF_OUT);
  api->fd_write(REG_AF_OUT, AF_MUTE);
  uint32_t cfgr0 = GPIOA_CFGR, muxh0 = GPIOA_MUXH;
  srx_arm(api, rx, hz);

  uint32_t pk = 0, good = 0, played = 0, late = 0, now = api->ms();
  uint32_t t_last = now, t_quiet_end = now + listen_s * 1000u;
  int playing = 0, ended = 0, fill = 0, done = 0, gain = 256, played_msg = 0;
  for (;;) {
    now = api->ms();
    if (srx_poll(api, rx)) {
      int r = rx->got >= SR_LEN ? sp_packet(rx->raw, SR_LEN) : SP_BAD_LENGTH;
      pk++;
      good += r >= 0;
      const uint8_t *d = sp_last_packet();
      char *q = um_put(t, "so pk n=");
      q = um_int(q, (int32_t)pk);
      q = um_int(um_put(q, " r="), r);
      um_hex(um_put(q, " seq="), (uint32_t)d[2] << 8 | d[3], 4);
      api->say(t);
      if (r >= 0) t_last = now, ended = 0, t_quiet_end = now + listen_s * 1000u;
      if (r == SP_NEW) {                 /* a new message: the store dropped the old one */
        if (playing) pwm_stop(cfgr0, muxh0);
        playing = 0, played_msg = 0;
      }
    }
    if (!ended && good && now - t_last > SP_GAP_MS) {
      sp_stream_end();                   /* the sender stopped: play what came */
      ended = 1;
    }
    if (!playing && !played_msg && good && (sp_stream_ready() || ended)) {
      for (int i = 0; i < 2 * HALF; i++) ring[i] = TOP / 2;   /* a half no frame fills is silence */
      sp_play_start();
      gain = sp_auto_gain_q8();
      int a = sp_play_pwm(ring, TOP, gain, SO_EMPHASIS);
      int b = a ? sp_play_pwm(ring + HALF, TOP, gain, SO_EMPHASIS) : 0;
      played += (a == 1) + (b == 1);
      pwm_start(ring);
      playing = 1, played_msg = 1, fill = 0, done = !b;
      say2(api, "so play gain=", gain, " frames=", (int32_t)played);
    } else if (playing && pwm_half() != fill) {
      /* the DMA has moved into the other half: the one it left is free */
      {
        if (done) {
          for (int i = 0; i < HALF; i++) ring[fill * HALF + i] = TOP / 2;
          if (++done > 2) {              /* both halves silent: the message is over */
            pwm_stop(cfgr0, muxh0);
            playing = 0;
            t_quiet_end = now + listen_s * 1000u;
          }
        } else {
          int r = sp_play_pwm(ring + fill * HALF, TOP, gain, SO_EMPHASIS);
          if (pwm_half() == fill) late++;  /* the DMA came back before the decode ended */
          if (r == 0) {                  /* the message is over: this half silent too */
            for (int i = 0; i < HALF; i++) ring[fill * HALF + i] = TOP / 2;
            done = 1;
          } else {
            played += r == 1;
          }
        }
        fill ^= 1;
      }
    }
    /* nothing came, or the message has played and nothing more came for listen_s */
    if (!playing && (int32_t)(now - t_quiet_end) > 0 && (!good || played_msg)) break;
    api->delay_us(300);
  }
  if (playing) pwm_stop(cfgr0, muxh0);
  srx_off(api);
  api->fd_write(REG_AF_OUT, af0);

  sp_status_t st;
  sp_status(&st);
  unsigned n = 0;
  uint32_t h = sp_pcm_hash(&n);
  p = um_put(t, "so end pk=");
  p = um_int(p, (int32_t)pk);
  p = um_int(um_put(p, " good="), (int32_t)good);
  p = um_int(um_put(p, " frames="), st.frames_got);
  p = um_int(um_put(p, "/"), st.total);
  p = um_int(um_put(p, " played="), (int32_t)played);
  p = um_int(um_put(p, " late="), (int32_t)late);
  p = um_hex(um_put(p, " pcm="), h, 8);
  um_int(um_put(p, " n="), (int32_t)n);
  api->say(t);
  return (int)played;
}
