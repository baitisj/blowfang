/* digi_tx: the RT-950 Pro's first transmit milestone, single-tone digital modes by PLL steps
   (docs/rt950-plan.md, "SSB transmit"; the numbers behind it in docs/rt950-polar-sim.md).

   In USB a single audio tone f is a carrier f above the dial, so FT8 and WSPR need no envelope
   and no audio path: only chip 0's frequency, moved N times a second. The BK4829's frequency
   register is 10 Hz a unit and FT8's tones are 6.25 Hz apart (WSPR's 1.46 Hz), so
   c/common/tone_fsk.c dithers each write between neighbouring register values by error
   feedback; the host simulation decodes the result with the K6's FT8 receiver and measures
   every symbol's mean frequency within 0.06 Hz at 1000 writes a second (plain rounding is
   4-5 Hz off and WSPR doesn't decode at all).

   The order of a transmission, as the two-chip rule asks: chip 1 off, chip 0 keyed on tone 0,
   the frame, AG7EW in CW at 20 wpm (the carrier keyed by power, on tone 0), keyed down. A key
   press anywhere keys down at once. Writes are paced from api->us, so a late write doesn't
   shift the rest of the frame; the frame's start (FT8's 15 s slots, WSPR's even minutes) is the
   caller's.

   What is not known until Andrew's radio (docs/rt950-polar-sim.md lists the measurements):
   whether a 10 Hz retune while keyed glitches or needs REG_30's relock, how long a write takes
   to settle, and how fast the bus can go (the base firmware's bit-banged bus takes about 1 ms
   for the REG_38 and REG_39 writes, which is the whole budget at 1000 writes a second, so a
   faster bus or a lower rate may be needed: FT8 holds 0.2 Hz down to about 300 writes a second,
   WSPR to about 100).
   SPDX-License-Identifier: GPL-3.0-or-later */
#include "digi_tx.h"

#define WPM 20
#define DIT_US (1200000u / WPM)

/* Morse for the ident's letters and digits, '.' and '-' */
static const char *morse(char ch) {
  static const char *const AZ[26] = {
      ".-",                     /* A */
      "-...",                   /* B */
      "-.-.",                   /* C */
      "-..",                    /* D */
      ".",                      /* E */
      "..-.",                   /* F */
      "--.",                    /* G */
      "....",                   /* H */
      "..",                     /* I */
      ".---",                   /* J */
      "-.-",                    /* K */
      ".-..",                   /* L */
      "--",                     /* M */
      "-.",                     /* N */
      "---",                    /* O */
      ".--.",                   /* P */
      "--.-",                   /* Q */
      ".-.",                    /* R */
      "...",                    /* S */
      "-",                      /* T */
      "..-",                    /* U */
      "...-",                   /* V */
      ".--",                    /* W */
      "-..-",                   /* X */
      "-.--",                   /* Y */
      "--.."                    /* Z */
  };
  static const char *const D09[10] = {
      "-----",                  /* 0 */
      ".----",                  /* 1 */
      "..---",                  /* 2 */
      "...--",                  /* 3 */
      "....-",                  /* 4 */
      ".....",                  /* 5 */
      "-....",                  /* 6 */
      "--...",                  /* 7 */
      "---..",                  /* 8 */
      "----."                   /* 9 */
  };
  if (ch >= 'A' && ch <= 'Z') return AZ[ch - 'A'];
  if (ch >= '0' && ch <= '9') return D09[ch - '0'];
  return "";
}

/* wait until t (us, api->us's clock); 1 if a key came meanwhile */
static int until(const rt_api_t *api, uint32_t t) {
  while ((int32_t)(t - api->us()) > 0)
    if (api->key()) return 1;
  return 0;
}

/* the ident in CW on hz (already keyed): the carrier off between elements; 1 if a key came */
static int ident(const rt_api_t *api, int power) {
  const char *p, *e;
  uint32_t t = api->us();
  api->tx_power(0);
  t += 3 * DIT_US;                      /* (a gap after the frame) */
  for (p = DIGI_ID; *p; p++) {
    for (e = morse(*p); *e; e++) {
      if (until(api, t)) return 1;
      api->tx_power(power);
      t += (*e == '-' ? 3u : 1u) * DIT_US;
      if (until(api, t)) return 1;
      api->tx_power(0);
      t += DIT_US;
    }
    t += 2 * DIT_US;                    /* (3 dits between letters) */
  }
  return until(api, t);
}

int digi_send(const rt_api_t *api, uint32_t dial_hz, const tf_cfg *c, const uint8_t *tones, unsigned nsym,
              int power, int outside_ok) {
  tf_state s;
  int32_t reg, trim;
  uint32_t t0, k = 0, tone0;
  if (!api->us || !api->tx_on || !api->tx_hz || !api->tx_power || !api->tx_off) return DIGI_NOTX;
  if (!outside_ok && (dial_hz < DIGI_LO || dial_hz + 3000u > DIGI_HI)) return DIGI_BAND;
  if (tf_init(&s, c, tones, nsym)) return DIGI_CFG;
  tone0 = dial_hz + (uint32_t)(c->base_hz + 0.5f);
  api->rf_off(RT_CHIP_SWEEP);           /* (the two-chip rule: chip 1 off before chip 0 keys up) */
  if (api->tx_on(tone0, power)) return DIGI_REFUSED;
  t0 = api->us();
  while (tf_next(&s, &reg, &trim, 0)) {
    /* write k holds from k / writes s after the start; the trim (no BK4829 trim is known) is
       left out: tone_fsk.c gives 0 without one */
    uint32_t at = t0 + (uint32_t)((double)k * 1e6 / (double)c->writes);
    if (until(api, at)) {
      api->tx_off();
      return DIGI_KEY;
    }
    api->tx_hz((uint32_t)((int32_t)dial_hz + reg * (int32_t)(c->step_hz + 0.5f)));
    k++;
  }
  if (until(api, t0 + (uint32_t)((double)nsym * (double)c->symbol_s * 1e6))) {
    api->tx_off();
    return DIGI_KEY;
  }
  api->tx_hz(tone0);
  if (ident(api, power)) {
    api->tx_off();
    return DIGI_KEY;
  }
  api->tx_off();
  return (int)k;
}
