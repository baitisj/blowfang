/* rt950_sim: the RT-950 Pro's two BK4829s modelled on a PC, running blowfang's RT-950 apps
   (radios/rt950/src) against them, for tests/test_rt950_surf.py.

   The model:
   - Each chip has its 128 registers. A tune is what the firmware writes (jaymart1983's
     bk4829_set_frequency, verified on the radio): REG_38 and REG_39 in 10 Hz units, REG_30
     0x0200, then REG_30 0xBFF1, which locks the chip there. REG_30 0 turns it off.
   - REG_67 (RSSI) on a locked chip restarts at -173 dBm at each tune and rises towards the
     level there as the K6's FD6818 did (59 % of the way in dB after 3 ms, Phase 0); the level is
     the noise floor (FLOOR_DBM plus the chip's offset) power-summed with every signal inside the
     wide filter (+-12.5 kHz), the ones outside down 60 dB. Encoded as the glue decodes it:
     (dBm + 160) x 2.
   - A signal can pulse (on ON ms of every PERIOD ms), as the 464.495 pulser that taught the K6's
     SURF to confirm in the sweep (PR #83).
   - Desense: while chip 1 is on and tuned within LEAK_HZ of chip 0, chip 0 hears LEAK_DBM more
     (its LO in the listener's passband). The real figure is one of the measurements for
     Andrew's radio; the test shows the guard keeps it out.
   - Every register access costs SPI_US of bit-banged bus time (24 bits at ~10 us, the OEM's
     timing that the firmware copies), and delay_us moves the clock: time is simulated, so runs
     are exact and fast.

   Checks it keeps while the app runs, printed at the end:
     tunes0 / tunes1    tunes per chip
     guard_min          the closest chip 1 came to chip 0 while both were on (Hz)
     ptt_traffic        chip 1 bus accesses after it went off with PTT down (must be 0)
     c1_audio           times chip 1's REG_47 was set to an audio path (must be 0)
     c1_demod           writes to chip 1's REG_3D or REG_73 (the listener's demodulator only)
     c0hz c1hz c0r47 c0r3d c0r4f spk   how the app left the chips and the speaker
   and as it goes:
     audio              each change of the speaker: "audio t=MS on|off f=HZ"
     ptt                at PTT release: "ptt t=MS c1hz=HZ spk=0|1"

   Usage: rt950_sim [--start HZ] [--end MS] [--sig HZ:DBM[:ON:PERIOD]]... [--key MS:K]...
                    [--leak DBM] [--leak-hz HZ] [--offset1 DB] [--dump MS]...
   or:    rt950_sim --digi ft8|wspr --tones DIGITS [--dial HZ] [--writes N] [--outside] [--key MS:K]
   which sends one frame with src/digi_tx.c and logs the transmitter: "txon US HZ P", "txw US HZ"
   (each PLL write), "txp US P" (the CW ident's keying), "txoff US", then "digi RET".
   Keys are the app's characters ('A' side key, 'U' up, 'X' exit, 'P' / 'p' PTT ...). At --end the
   sim sends X. --dump prints the next frame after MS ("view ..." then a line a bin).
   SPDX-License-Identifier: GPL-3.0-or-later */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/rt_api.h"
#include "../src/surf2.h"
#include "../src/digi_tx.h"

#define FLOOR_DBM (-127.0)
#define SPI_US 250
#define NSIG 32
#define NKEY 256
#define NDUMP 16

typedef struct {
  uint32_t hz;
  double dbm;
  uint32_t on, period;          /* ms; period 0: always on */
} sig_t;

typedef struct {
  uint16_t r[128];
  uint32_t hz;                  /* locked here (0: off or not locked) */
  uint64_t t_tune;              /* us */
  int pend;                     /* REG_30 0x0200 seen */
  double ofs;                   /* this chip's RSSI offset, dB */
} chip_t;

static chip_t ch[2];
static sig_t sig[NSIG];
static int nsig;
static struct {
  uint32_t t;
  int k;
} keys[NKEY];
static int nkey, ki;
static uint32_t dumps[NDUMP];
static int ndump, di;
static uint64_t now_us;
static uint32_t end_ms = 30000, start_hz = 440850000u;
static double leak_dbm = -90.0;
static uint32_t leak_hz = 15000;
static int spk, ptt_down, ended;
static long tunes[2], ptt_traffic, c1_audio, c1_demod, tx_traffic1, tx_c1_on;
static int keyed;
static uint32_t guard_min = 0xFFFFFFFFu;

static void spi(int c) {
  now_us += SPI_US;
  if (c == 1 && ptt_down && !ch[1].hz && !ch[1].pend) ptt_traffic++;   /* (after it went off) */
  if (c == 1 && keyed) tx_traffic1++;
}

static double level(int c) {
  chip_t *k = &ch[c];
  double p = pow(10.0, (FLOOR_DBM + k->ofs) / 10.0);
  uint32_t ms = (uint32_t)(now_us / 1000);
  int i;
  for (i = 0; i < nsig; i++) {
    double d = fabs((double)sig[i].hz - (double)k->hz), l = sig[i].dbm;
    if (sig[i].period && ms % sig[i].period >= sig[i].on) continue;
    if (d > 12500.0) l -= 60.0;
    p += pow(10.0, l / 10.0);
  }
  if (c == 0 && ch[1].hz) {
    double d = fabs((double)ch[1].hz - (double)k->hz);
    if (d < leak_hz) p += pow(10.0, leak_dbm / 10.0);
  }
  return 10.0 * log10(p);
}

static uint16_t rf_read(int c, int reg) {
  spi(c);
  if (reg == 0x67) {
    chip_t *k = &ch[c];
    double t, l, v;
    int code;
    if (!k->hz) return 0;
    t = (double)(now_us - k->t_tune) / 1000.0;
    l = level(c);
    v = -173.0 + (l + 173.0) * (1.0 - exp(-t / 3.36));
    code = (int)lround((v + 160.0) * 2.0);
    if (code < 0) code = 0;
    if (code > 511) code = 511;
    return (uint16_t)code;
  }
  return ch[c].r[reg & 0x7F];
}

static void rf_write(int c, int reg, uint16_t v) {
  chip_t *k = &ch[c];
  spi(c);
  k->r[reg & 0x7F] = v;
  if (reg == 0x30) {
    if (v == 0x0200) {
      k->pend = 1;
      k->hz = 0;
    } else if (v == 0xBFF1 && k->pend) {
      k->pend = 0;
      k->hz = ((uint32_t)k->r[0x39] << 16 | k->r[0x38]) * 10u;
      k->t_tune = now_us;
      tunes[c]++;
    } else if (v == 0) {
      k->hz = 0;
      k->pend = 0;
    }
  }
  if (c == 1 && reg == 0x47 && v != 0x6040u && v != 0) c1_audio++;
  if (c == 1 && (reg == 0x3D || reg == 0x73)) c1_demod++;
  if (ch[0].hz && ch[1].hz) {
    uint32_t d = ch[0].hz > ch[1].hz ? ch[0].hz - ch[1].hz : ch[1].hz - ch[0].hz;
    if (d < guard_min) guard_min = d;
  }
}

static void rf_tune(int c, uint32_t hz) {
  uint32_t f10 = hz / 10u;
  rf_write(c, 0x38, (uint16_t)(f10 & 0xFFFF));
  rf_write(c, 0x39, (uint16_t)(f10 >> 16));
  rf_write(c, 0x30, 0x0200);
  now_us += 52;
  rf_write(c, 0x30, 0xBFF1);
}

static int rf_dbm(int c) { return (int)((rf_read(c, 0x67) & 0x1FF) >> 1) - 160; }

static void rf_off(int c) { rf_write(c, 0x30, 0); }

static uint32_t ms(void) { return (uint32_t)(now_us / 1000); }

static void delay_us(uint32_t us) { now_us += us; }

static void say(const char *line) { printf("t=%u say %s\n", ms(), line); }

static void speaker(int on) {
  if (on == spk) return;
  spk = on;
  printf("audio t=%u %s f=%u\n", ms(), on ? "on" : "off", ch[0].hz);
}

static int key(void) {
  uint32_t t = ms();
  if (!ended && t >= end_ms) {
    ended = 1;
    return 'X';
  }
  if (ki < nkey && t >= keys[ki].t) {
    int k = keys[ki++].k;
    if (k == 'P') ptt_down = 1;
    if (k == 'p') {
      ptt_down = 0;
      printf("ptt t=%u c1hz=%u spk=%d\n", t, ch[1].hz, spk);
    }
    printf("key t=%u %c\n", t, k);
    return k;
  }
  return 0;
}

static void show(const rt_view_t *v) {
  int i;
  if (di >= ndump || ms() < dumps[di]) return;
  di++;
  printf("view t=%u cen=%u span=%u band=%s mode=%s dbm=%d floor=%d audio=%d mon=%d hold=%d ptt=%d "
         "sweeps=%u fq=%u msg=%s\n",
         ms(), v->cen, v->span, v->band, v->mode, v->dbm, v->floor_dbm, v->audio, v->mon, v->hold, v->ptt,
         v->sweeps, v->floor_q, v->msg ? v->msg : "-");
  for (i = 0; i < SURF2_NV; i++)
    printf("bin %d %u %u %u %u\n", i, v->hz[i], v->lvl[i], v->caret[i], v->flags[i]);
}

/* the transmitter (level 2): every call logged with the time in us; a frequency write costs
   the two register writes (REG_38, REG_39) of bus time */
static uint32_t us(void) {
  now_us += 10;                         /* (a poll of the clock costs a little) */
  return (uint32_t)now_us;
}

static int tx_on(uint32_t hz, int power) {
  spi(0);
  if (ch[1].hz) tx_c1_on++;
  keyed = 1;
  printf("txon %llu %u %d\n", (unsigned long long)now_us, hz, power);
  return 0;
}

static void tx_hz(uint32_t hz) {
  spi(0);
  spi(0);
  printf("txw %llu %u\n", (unsigned long long)now_us, hz);
}

static void tx_power(int power) {
  spi(0);
  printf("txp %llu %d\n", (unsigned long long)now_us, power);
}

static void tx_off(void) {
  spi(0);
  keyed = 0;
  printf("txoff %llu\n", (unsigned long long)now_us);
}

static const rt_api_t API = {
    RT_API_LEVEL,
    say,
    ms,
    delay_us,
    rf_read,
    rf_write,
    rf_tune,
    rf_dbm,
    rf_off,
    speaker,
    key,
    show,
    us,
    tx_on,
    tx_hz,
    tx_power,
    tx_off,
};

int main(int argc, char **argv) {
  static uint8_t ram[16384];
  static uint8_t tones[256];
  const char *digi = 0;
  uint32_t dial = 0, writes = 0;
  unsigned ntone = 0;
  int i, r, outside = 0;
  for (i = 1; i < argc; i++) {
    const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : "";
    if (!strcmp(a, "--start")) start_hz = (uint32_t)strtoul(v, 0, 10), i++;
    else if (!strcmp(a, "--end")) end_ms = (uint32_t)strtoul(v, 0, 10), i++;
    else if (!strcmp(a, "--leak")) leak_dbm = atof(v), i++;
    else if (!strcmp(a, "--leak-hz")) leak_hz = (uint32_t)strtoul(v, 0, 10), i++;
    else if (!strcmp(a, "--offset1")) ch[1].ofs = atof(v), i++;
    else if (!strcmp(a, "--digi")) digi = v, i++;
    else if (!strcmp(a, "--dial")) dial = (uint32_t)strtoul(v, 0, 10), i++;
    else if (!strcmp(a, "--writes")) writes = (uint32_t)strtoul(v, 0, 10), i++;
    else if (!strcmp(a, "--outside")) outside = 1;
    else if (!strcmp(a, "--tones")) {
      for (ntone = 0; v[ntone] && ntone < sizeof tones; ntone++) tones[ntone] = (uint8_t)(v[ntone] - '0');
      i++;
    } else if (!strcmp(a, "--dump") && ndump < NDUMP) dumps[ndump++] = (uint32_t)strtoul(v, 0, 10), i++;
    else if (!strcmp(a, "--sig") && nsig < NSIG) {
      sig_t *s = &sig[nsig++];
      char *e;
      s->hz = (uint32_t)strtoul(v, &e, 10);
      s->dbm = strtod(e + 1, &e);
      s->on = *e == ':' ? (uint32_t)strtoul(e + 1, &e, 10) : 0;
      s->period = *e == ':' ? (uint32_t)strtoul(e + 1, &e, 10) : 0;
      i++;
    } else if (!strcmp(a, "--key") && nkey < NKEY) {
      char *e;
      keys[nkey].t = (uint32_t)strtoul(v, &e, 10);
      keys[nkey++].k = e[1];
      i++;
    } else {
      fprintf(stderr, "rt950_sim: what is %s?\n", a);
      return 2;
    }
  }
  /* the firmware's receive set-up as jaymart1983's bk4829_init leaves it (REG_13 0x03DF, REG_7E
     0x303E, REG_47 0x6142 on chip 0; chip 1 off the audio bus); chip 0 on the VFO */
  for (i = 0; i < 2; i++) {
    ch[i].r[0x13] = 0x03DF;
    ch[i].r[0x7E] = 0x303E;
    ch[i].r[0x4D] = 0xA004;
    ch[i].r[0x4E] = 0x3815;
    ch[i].r[0x4F] = 0x3F3B;
  }
  ch[0].r[0x47] = 0x6142;
  rf_tune(0, start_hz);
  if (digi) {
    /* --digi ft8|wspr: one frame by digi_send instead of SURF, with chip 1 on (as SURF leaves
       it) so the test sees it go off first */
    tf_cfg c;
    if (!strcmp(digi, "wspr")) tf_wspr_defaults(&c);
    else tf_ft8_defaults(&c);
    if (writes) c.writes = (float)writes;
    rf_tune(1, start_hz + 100000u);
    r = digi_send(&API, dial ? dial : start_hz, &c, tones, ntone, 1, outside);
    printf("digi %d\ntx_traffic1 %ld\ntx_c1_on %ld\nc1hz %u\n", r, tx_traffic1, tx_c1_on, ch[1].hz);
    return 0;
  }
  r = surf2_run(&API, ram, sizeof ram, start_hz);
  printf("ret %d\n", r);
  printf("tunes0 %ld\ntunes1 %ld\nguard_min %u\nptt_traffic %ld\nc1_audio %ld\nc1_demod %ld\nram %u\n", tunes[0],
         tunes[1], guard_min, ptt_traffic, c1_audio, c1_demod, surf2_ram());
  printf("c0hz %u\nc1hz %u\nc0r47 %04x\nc0r3d %04x\nc0r4f %04x\nspk %d\n", ch[0].hz, ch[1].hz, ch[0].r[0x47], ch[0].r[0x3D],
         ch[0].r[0x4F], spk);
  return 0;
}
