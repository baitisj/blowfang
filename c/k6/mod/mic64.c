/* mic64: does REG_64 follow a sound at the K6's mic? (2026-11-20, Jeff: "Prove to yourself
   that the IC-7100 is actually producing tone by measuring a change in REG_64. Try modulating AF
   from 0 to 0.3.") mictx (mtx1a) found REG_64, the voice amplitude that VOX and other firmwares'
   mic bar read, holding each value about 20 ms and sitting at 14-18 with the IC-7100's tone or
   without, in captures only 0.55 s long. This one logs it for 105 s: after the upload it waits for
   a carrier on SOUND_HZ (the job's first tone, which the IC-7100 tuned there plays from its
   speaker beside the K6), keys up on the bench frequency (tx_on: the core's guards, low power),
   sends AG7EW in CW with the PA, then with the PA OFF switches the mic ADC on (REG_30 bit 2, and
   the AF DAC) and reads REG_64 every STEP_MS, N_MAX times, while the job steps the IC-7100's AF
   level (0 to 0.3) through its tones. The log's time stamps line the samples up with the steps:
   sample 0 comes SETTLE_MS after the "mic64 go" line.

   The mic is never on the air: its bit is set only while REG_36 says the PA is off (checked again
   every 32 samples), and tx_pa(1) is not called after. REG_30 goes over the chip's bus pins
   (micadc.h) where fd_write refuses it, and is put back before key-down.

   Lines (under 60 characters):
     mic64 start 30=bff1 47=6040 rx=1a46e150
     mic64 sound rssi=-4a              (or: mic64 no sound)
     mic64 go
     mic64 keyed 30=c1fa 36=007f now=c3fe late=0000   late: samples read over half a step late
     A 000 00100011000f...                            REG_64, four hex digits a sample, 13 a line
     mic64 A ticks=a1b2 step=14 n=1484
     mic64 done
   MIC64_OUT (micout.c, 2026-11-20 Jeff: "WE DID EVERY TEST WITH THE SERIAL JACK IN THE K6"): the
   programming cable in the jack takes the mic out, so this build runs with it pulled. The screen
   says PULL CABLE / THEN ANY KEY; after that key it waits for the sound as above. TALK_N samples
   before the end the screen says TALK NOW (the line "mic64 out talk=" gives the sample). After
   key-down it says PLUG CABLE IN / THEN ANY KEY and only then sends its lines, all of them
   (nothing said while the cable was out reaches the logger). -7: no key within KEY_WAIT_MS.
   Returns the samples, or -8 (scratch under 3 KB), -6 (a key), -5 (the PA would not go off, or
   REG_30 did not take the bit), -4 (the bench frequency is busy), -3 (tx_on refused), -2 (not on
   the bench frequency), -1 (no transmitter). Level 13.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 13
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "micadc.h"

#define AMP (1u << 2)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef N_MAX
#define N_MAX 5252u /* 105 s at STEP_MS (a multiple of 13) */
#endif
#ifndef STEP_MS
#define STEP_MS 20u /* REG_64 holds a value about 20 ms (mictx, mtx1a) */
#endif
#ifndef SETTLE_MS
#define SETTLE_MS 500u /* REG_64 reads 0x7Cxx for ~20 ms after the mic ADC comes on */
#endif
#ifndef QUIET_WAIT_MS
#define QUIET_WAIT_MS 30000u
#endif
#ifndef HOLD_MS
#define HOLD_MS 2000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 120000u
#endif
#ifndef BENCH_HZ
#define BENCH_HZ 440850000u
#endif
#define RSSI_ON -90 /* dBm: the sound's carrier is sent weak */
#define AF_DAC 0x0200u
#define CW_TEXT "AG7EW"
#ifdef MIC64_OUT
#ifndef TALK_N
#define TALK_N 1000u /* the last 20 s: Jeff talks into the mic */
#endif
#ifndef KEY_WAIT_MS
#define KEY_WAIT_MS 1200000u /* 20 minutes for each of the two keys */
#endif
#endif
#define UNIT_US 60000u /* 20 wpm */
#ifndef TX_MS
#define TX_MS 118000u /* the ID, SETTLE_MS and N_MAX x STEP_MS (the core's cap is 120 s) */
#endif

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hexn(char *p, uint32_t v, int n) {
  const char *x = "0123456789abcdef";
  int i;
  for (i = 4 * (n - 1); i >= 0; i -= 4) *p++ = x[(v >> i) & 15];
  return p;
}

static char *dbm(char *p, int v) {
  if (v < 0) {
    *p++ = '-';
    v = -v;
  }
  return hexn(p, (uint32_t)v, 2);
}

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

#ifdef MIC64_OUT
static void show(const k6_api_t *api, const char *l1, const char *l2) {
  if (api->level < 6) return;
  api->lcd_text(0, "MIC64 CABLE OUT");
  api->lcd_text(2, l1);
  api->lcd_text(3, l2);
  api->lcd_flush();
}

/* a whole key press (any key held now must be let go first): 0, or -7 after ms */
static int key_go(const k6_api_t *api, uint32_t ms) {
  uint32_t t0 = api->ms();
  int phase;
  for (phase = 0; phase < 3; phase++) /* let go, press, let go */
    while (api->key_poll() == !(phase & 1)) {
      if (api->ms() - t0 >= ms) return -7;
      api->delay_us(20000);
    }
  return 0;
}
#endif

typedef struct {
  const k6_api_t *api;
  char *line; /* 64 bytes, in the scratch like buf */
  uint16_t *buf;
  uint32_t n;
} run_t;

/* Morse by code, as cwid.c: start at 1, then 0 for a dot and 1 for a dash (E = 2, A = 5) */
static int code_of(char c) {
  static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c) return i;
  return 0;
}

/* the PA on or off for n units; 0, -5 (the core keyed down) or -6 (a key) */
static int hold(const k6_api_t *api, int on, int n) {
  if (api->tx_pa(on) < 0) return -5;
  while (n--) {
    if (api->key_poll()) return -6;
    api->delay_us(UNIT_US);
  }
  return 0;
}

/* the keyed run: the ID, then with the PA off and the mic ADC on REG_64 every STEP_MS for n
   samples, key-down, the dump. 0, or a negative error (keyed down either way) */
static int pass(const run_t *r, uint32_t r47, int *made) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p;
  uint32_t i, j, s30 = 0, now = 0, want, t0 = 0, tk0 = 0, tk = 0, late = 0;
  const char *s;
  int e;
  if (rssi(api) >= RSSI_ON) return -4; /* never onto a busy channel */
  e = api->tx_on(TX_MS);
  if (e != 0) return -3;
  e = hold(api, 0, 5);
  for (s = CW_TEXT; *s && !e; s++) {
    int c = code_of(*s), b;
    for (b = 31; !(c >> b); b--) {}
    while (b-- && !e) {
      e = hold(api, 1, (c >> b) & 1 ? 3 : 1);
      if (!e) e = hold(api, 0, b ? 1 : 3);
    }
  }
  if (!e && api->tx_pa(0) < 0) e = -5;
  if (!e) {
    s30 = api->fd_read(0x30);
    if (api->fd_read(0x36) & 0x0080u) e = -5; /* the PA is still on: no mic bit */
  }
  want = (s30 | AF_DAC | MIC_ADC) & 0xFFFFu;
  if (!e && api->fd_read(0x30) != want && api->fd_write(0x30, (uint16_t)want) != 0) mic_bus_write(api, 0x30, want);
  if (!e && (now = api->fd_read(0x30)) != want) e = -5;
  if (!e) {
    api->say("mic64 go");   /* (the logger stamps it: sample 0 is SETTLE_MS after) */
    api->delay_us(SETTLE_MS * 1000u);
    t0 = api->ms();
    tk0 = api->ticks();
  }
  for (i = 0; i < r->n && !e; i++) {
    while (api->ms() - t0 < i * STEP_MS) {
      if (api->key_poll()) e = -6;
      if (e) break;
      api->delay_us(1000);
    }
    if (e) break;
    if (api->ms() - t0 > i * STEP_MS + STEP_MS / 2u) late++;
#ifdef MIC64_OUT
    if (i + TALK_N == r->n) show(api, "TALK NOW", "INTO THE MIC");
#endif
    r->buf[i] = (uint16_t)api->fd_read(0x64);
    if (!(i & 31u) && (api->fd_read(0x36) & 0x0080u)) e = -5;
  }
  tk = api->ticks() - tk0;
  if (s30 && api->fd_read(0x30) != s30 && api->fd_write(0x30, (uint16_t)s30) != 0) mic_bus_write(api, 0x30, s30);
  api->fd_write(0x47, (uint16_t)r47);
  api->tx_off();
#ifdef MIC64_OUT
  show(api, "PLUG CABLE IN", "THEN ANY KEY");
  if (key_go(api, KEY_WAIT_MS) < 0 && !e) e = -7;
  show(api, "SENDING", "");
  api->delay_us(1000000);
  p = hexn(put(line, "mic64 out talk="), r->n > TALK_N ? r->n - TALK_N : 0u, 4);
  *p = 0;
  api->say(line);
#endif
  p = hexn(put(line, "mic64 keyed 30="), s30, 4);
  p = hexn(put(p, " 36="), api->fd_read(0x36), 4);
  p = hexn(put(p, " now="), now, 4);
  p = hexn(put(p, " late="), late, 4);
  *p = 0;
  api->say(line);
  if (e) return e;
  for (i = 0; i < r->n; i += 13) {
    line[0] = 'A';
    line[1] = ' ';
    p = hexn(line + 2, i, 3);
    *p++ = ' ';
    for (j = i; j < i + 13 && j < r->n; j++) p = hexn(p, r->buf[j], 4);
    *p = 0;
    api->say(line);
    api->delay_us(15000); /* the serial port drains */
  }
  p = hexn(put(line, "mic64 A ticks="), tk & 0xFFFFu, 4);
  p = hexn(put(p, " step="), STEP_MS, 2);
  p = hexn(put(p, " n="), r->n, 4);
  *p = 0;
  api->say(line);
  *made = (int)r->n;
  return 0;
}

int k6_main(const k6_api_t *api) {
  uint32_t amp = rd(MIC_GPIOB + 0x14u) & AMP, home = api->rx_hz(), /* (GPIOB ODR: the speaker amp) */
           room = 0, t0, t1, up = 0;
  uint16_t r47 = api->fd_read(0x47);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  run_t r;
  char *line, *p;
  int ret = 0, e;
  if (api->level < 3) return -1;
  r.api = api;
  r.line = line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  r.buf = (uint16_t *)(line + 64);
  if (room < 3072) return -8;
  for (r.n = N_MAX; 2 * r.n + 72 > room; r.n -= 13) {} /* (N_MAX: a multiple of 13) */
  p = hexn(put(line, "mic64 start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " rx="), home, 8);
  *p = 0;
  api->say(line);
  if (home != BENCH_HZ) return -2;
  wr(MIC_GPIOB + MIC_BRR, AMP);
#ifdef MIC64_OUT
  show(api, "PULL CABLE", "THEN ANY KEY");
  if (key_go(api, KEY_WAIT_MS) < 0) {
    ret = -7;
    goto out;
  }
  show(api, "QUIET PLEASE", "RECORDING");
#endif
  /* the upload's tail: wait until the channel has been quiet for a second */
  t0 = api->ms();
  t1 = t0;
  while (api->ms() - t0 < QUIET_WAIT_MS && api->ms() - t1 < 1000u) {
    if (rssi(api) >= RSSI_ON) t1 = api->ms();
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    api->delay_us(20000);
  }
  /* the go: a carrier on SOUND_HZ that stays HOLD_MS (the job's first tone) */
  api->tune(SOUND_HZ, wide);
  t0 = api->ms();
  for (;;) {
    if (rssi(api) < RSSI_ON) {
      up = 0;
    } else if (!up) {
      up = 1;
      t1 = api->ms();
    } else if (api->ms() - t1 >= HOLD_MS) {
      break;
    }
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    if (api->ms() - t0 >= WAIT_MS) {
      api->say("mic64 no sound");
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "mic64 sound rssi="), rssi(api)) = 0;
  api->say(line);
  api->tune(home, wide);
  api->delay_us(300000);
  if ((e = pass(&r, r47, &ret)) < 0) ret = e;
out:
  api->tx_off();
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  api->say("mic64 done");
  return ret;
}
