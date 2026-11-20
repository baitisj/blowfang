/* voxcap: does the K6's VOX pin carry the microphone's waveform? (2026-11-19, docs/k6-mic-probe.md)
   The mic goes to the FD6818's MICP and the MCU never sees its samples. The one analog line
   from the mic side is VOX_DET on PA0 (ADC channel 0), which Aura reads as a loudness for VOX.
   If the board's detector is fast, PA0 carries more than a loudness: the rectified waveform,
   which would hold the voice's pitch and some of its spectrum. This samples PA0 at 16 kHz while
   the K6's own speaker plays test tones, so nobody has to make a sound: PA11 (TIM1 channel 4,
   the voice prompts' pin) plays a square tone as a 48 kHz PWM between two duties, the speaker
   amplifier (PB2) on for the run.

   Captures, each dumped whole as 12-bit samples for tools/k6_micprobe.py:
     Q  1026 samples, speaker off, no tone: the pin's rest level and noise
     S  3078 samples, 1 kHz on for samples 512-1535 of them: how fast the detector follows
     a..d  1026 samples each inside a steady tone of 296, 500, 1000 and 2000 Hz
     e, f  1026 samples of 1 kHz at a quarter and at 1.5 times the level
   Lines (under 60 characters):
     voxcap start cr=00000001 smpr=7 ch=00000001 r64=000a
     Q 000 7ff8017fe...               tag, first sample's index (hex), 18 samples of 3 digits
     voxcap Q late=0000 conv=0120     samples the loop was late for; ADC conversion, 1/16 us (hex)
     voxcap done
   Writes: the MCU's TIM17 (the sample clock) and TIM1 (PWM on channel 4), PA11's mode, PB2
   (the amplifier) and the ADC's channel select, all put back; no FD6818 register. Nothing
   transmits. Returns the captures made, or -7 (TIM17 or TIM1 in use: something plays), -8
   (scratch under 6,160 bytes), -6 (a key stopped it), -5 (TIM17 doesn't count or the ADC
   doesn't convert). Level 4 (key_poll).
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 4
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"

#define RCC_APB2ENR 0x40021018u
#define TIM17EN (1u << 18)
#define TIM1EN (1u << 11)
#define GPIOA 0x48000000u
#define GPIOB 0x48000400u
#define G_MODER 0x00u
#define G_OSPEEDR 0x08u
#define G_ODR 0x14u
#define G_BSRR 0x18u
#define G_AFRH 0x24u
#define G_BRR 0x28u
#define ADC 0x40012400u
#define ADC_ISR (ADC + 0x00u)
#define ADC_CR (ADC + 0x08u)
#define ADC_SMPR (ADC + 0x14u)
#define ADC_CHSELR (ADC + 0x28u)
#define ADC_DR (ADC + 0x40u)
#define TIM17 0x40014800u
#define TIM1 0x40012C00u
#define T_CR1 0x00u
#define T_DIER 0x0Cu
#define T_SR 0x10u
#define T_EGR 0x14u
#define T_CCMR2 0x1Cu
#define T_CCER 0x20u
#define T_CNT 0x24u
#define T_PSC 0x28u
#define T_ARR 0x2Cu
#define T_RCR 0x30u
#define T_CCR4 0x40u
#define T_BDTR 0x44u
#define TOP 2000u    /* 48 kHz PWM, as the voice prompts */
#define MID 1000u
#define STEP 1000u   /* TIM17 at 16 MHz: counts a 16 kHz sample */
#define NMAX 3078u   /* 171 lines of 18 */
#define NONE 0xFFFFu

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

static void wait_us(uint32_t us) { /* on TIM17's 16 MHz count: at most 4 ms a lap */
  uint32_t t0 = rd(TIM17 + T_CNT) & 0xFFFFu, left = us * 16u, spin = 0;
  while (left && spin < 20000u) { /* (a clock that stands still ends the wait) */
    uint32_t t = rd(TIM17 + T_CNT) & 0xFFFFu, d = (t - t0) & 0xFFFFu;
    spin = d ? 0 : spin + 1;
    t0 = t;
    left = d < left ? left - d : 0;
  }
}

typedef struct {
  uint32_t late, conv;
} cap_t;

/* n samples of PA0 at 16 kHz. The tone is a square wave of half period k samples and PWM swing
   amp, on for samples [on, off); k 0: none. Returns 0, or -1 if the clock or the ADC never
   answers (the loop gives up after about a millisecond of waiting). */
static int capture(uint16_t *buf, uint32_t n, uint32_t k, uint32_t amp, uint32_t on, uint32_t off, cap_t *c) {
  uint32_t i, ph = 0, hi = 0, next = (rd(TIM17 + T_CNT) + STEP) & 0xFFFFu, spin;
  c->late = c->conv = 0;
  for (i = 0; i < n; i++) {
    uint32_t t0;
    for (spin = 0; (int16_t)(rd(TIM17 + T_CNT) - next) < 0; spin++)
      if (spin > 20000u) return -1;
    t0 = rd(TIM17 + T_CNT);
    next = (next + STEP) & 0xFFFFu;
    wr(ADC_CR, rd(ADC_CR) | (1u << 2)); /* ADSTART */
    if (k && i >= on && i < off) {
      if (ph == 0) {
        hi ^= 1u;
        wr(TIM1 + T_CCR4, hi ? MID + amp : MID - amp);
      }
      if (++ph >= k) ph = 0;
    } else if (i == off) {
      wr(TIM1 + T_CCR4, MID);
    }
    for (spin = 0; !(rd(ADC_ISR) & (1u << 2)); spin++)
      if (spin > 20000u) return -1;
    buf[i] = (uint16_t)rd(ADC_DR);
    if (i == 8) c->conv = (rd(TIM17 + T_CNT) - t0) & 0xFFFFu;
    if ((int16_t)(rd(TIM17 + T_CNT) - next) >= 0) c->late++;
  }
  return 0;
}

static void dump(const k6_api_t *api, char tag, const uint16_t *buf, uint32_t n, const cap_t *c) {
  char line[64], *p;
  uint32_t i, j;
  for (i = 0; i < n; i += 18) {
    line[0] = tag;
    line[1] = ' ';
    p = hexn(line + 2, i, 3);
    *p++ = ' ';
    for (j = i; j < i + 18 && j < n; j++) p = hexn(p, buf[j] & 0xFFFu, 3);
    *p = 0;
    api->say(line);
  }
  p = put(line, "voxcap ");
  *p++ = tag;
  p = hexn(put(p, " late="), c->late, 4);
  p = hexn(put(p, " conv="), c->conv, 4);
  *p = 0;
  api->say(line);
}

/* tag, samples, half period, swing, on, off, settle ms before it */
static const struct {
  char tag;
  uint16_t n, k, amp, on, off, settle;
} PLAN[] = {
    {'Q', 1026, 0, 0, 0, 0, 0},
    {'S', 3078, 8, 600, 512, 1536, 300},
    {'a', 1026, 27, 600, 0, NONE, 100},
    {'b', 1026, 16, 600, 0, NONE, 100},
    {'c', 1026, 8, 600, 0, NONE, 100},
    {'d', 1026, 4, 600, 0, NONE, 100},
    {'e', 1026, 8, 150, 0, NONE, 100},
    {'f', 1026, 8, 900, 0, NONE, 100},
};
#define NPLAN (sizeof PLAN / sizeof PLAN[0])

int k6_main(const k6_api_t *api) {
  uint32_t apb2 = rd(RCC_APB2ENR), moder = rd(GPIOA + G_MODER), afrh = rd(GPIOA + G_AFRH),
           ospeed = rd(GPIOA + G_OSPEEDR), chsel = rd(ADC_CHSELR), amp_on = rd(GPIOB + G_ODR) & 4u, room = 0, i;
  uint16_t *buf = (uint16_t *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  char line[64], *p;
  cap_t c;
  int ret = 0;
  if (room < 2 * NMAX + 4) return -8;
  wr(RCC_APB2ENR, apb2 | TIM1EN);
  if ((apb2 & TIM17EN) || (rd(TIM1 + T_CR1) & 1u)) {
    wr(RCC_APB2ENR, apb2);
    return -7;
  }
  p = hexn(put(line, "voxcap start cr="), rd(ADC_CR), 8);
  p = hexn(put(p, " smpr="), rd(ADC_SMPR), 1);
  p = hexn(put(p, " ch="), chsel, 8);
  p = hexn(put(p, " r64="), api->fd_read(0x64), 4);
  *p = 0;
  api->say(line);
  wr(RCC_APB2ENR, apb2 | TIM1EN | TIM17EN);
  wr(TIM17 + T_CR1, 0);
  wr(TIM17 + T_DIER, 0);
  wr(TIM17 + T_PSC, 5); /* 16 MHz */
  wr(TIM17 + T_ARR, 0xFFFF);
  wr(TIM17 + T_EGR, 1);
  wr(TIM17 + T_SR, 0);
  wr(TIM17 + T_CR1, 1);
  wr(ADC_CHSELR, 1u); /* channel 0 = PA0 */
  for (i = 0; i < NPLAN; i++) {
    if (api->key_poll()) {
      ret = -6;
      break;
    }
    if (i == 1) { /* the speaker path on: PWM at mid duty, PA11 to TIM1_CH4, the amplifier */
      wr(TIM1 + T_CR1, 0);
      wr(TIM1 + T_DIER, 0);
      wr(TIM1 + T_PSC, 0);
      wr(TIM1 + T_ARR, TOP);
      wr(TIM1 + T_RCR, 0);
      wr(TIM1 + T_CCR4, MID);
      wr(TIM1 + T_CCMR2, (rd(TIM1 + T_CCMR2) & ~0xFF00u) | (6u << 12) | (1u << 11)); /* PWM 1, preload */
      wr(TIM1 + T_CCER, 1u << 12);
      wr(TIM1 + T_EGR, 1);
      wr(GPIOA + G_AFRH, (afrh & ~(0xFu << 12)) | (2u << 12));
      wr(GPIOA + G_OSPEEDR, ospeed | (3u << 22));
      wr(GPIOA + G_MODER, (moder & ~(3u << 22)) | (2u << 22));
      wr(TIM1 + T_CR1, (1u << 7) | 1u);
      wr(TIM1 + T_BDTR, rd(TIM1 + T_BDTR) | (1u << 15)); /* MOE */
      wr(GPIOB + G_BSRR, 4u);
    }
    wait_us(1000u * PLAN[i].settle);
    if (PLAN[i].on == 0 && PLAN[i].k) /* a steady tone: let it run before the samples count */
      capture(buf, 1600, PLAN[i].k, PLAN[i].amp, 0, NONE, &c);
    if (capture(buf, PLAN[i].n, PLAN[i].k, PLAN[i].amp, PLAN[i].on, PLAN[i].off, &c)) {
      api->say("voxcap: no clock or no ADC answer");
      ret = -5;
      break;
    }
    wr(TIM1 + T_CCR4, MID);
    dump(api, PLAN[i].tag, buf, PLAN[i].n, &c);
    ret++;
  }
  /* everything back */
  if (!amp_on) wr(GPIOB + G_BRR, 4u);
  wr(TIM1 + T_BDTR, rd(TIM1 + T_BDTR) & ~(1u << 15));
  wr(TIM1 + T_CR1, 0);
  wr(TIM1 + T_CCER, 0);
  wr(GPIOA + G_MODER, moder);
  wr(GPIOA + G_OSPEEDR, ospeed);
  wr(GPIOA + G_AFRH, afrh);
  wr(ADC_CHSELR, chsel);
  wr(TIM17 + T_CR1, 0);
  wr(TIM17 + T_PSC, 0);
  wr(TIM17 + T_SR, 0);
  wr(RCC_APB2ENR, apb2);
  api->say("voxcap done");
  return ret;
}
