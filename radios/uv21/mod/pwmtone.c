/* pwmtone: [S12] the UV-21 Pro's MCU audio path, before the port plays sotto through it: TMR1 CH4
   PWM on PA11 (the stock firmware's voice-prompt path, docs/uv21-characteristics.md), the K6's
   48 kHz carrier, then RC filtering on the board to the speaker amplifier (enable PB2, which the
   port already holds on). The samples come at 8 kHz from a polled DWT cycle count, as sp_pcm_to_pwm's
   compare values would (top / 2 for silence), so a listener hears what the decoder's output will
   sound like through this path. No DMA yet: this only proves the pin, the timer and the
   amplifier. MIT

   It plays PT_N tones of PT_MS ms each with PT_GAP_MS of silence between them: 500, 1000, 2000 Hz
   (the defaults), at about PT_LEVEL % of full scale, then puts PA11 back as it found it and stops
   TMR1. It drives no RF register.

     pt arm psr=.. arr=AAAA moden=PA11 mode before
     pt tone hz=HHHH ms=MMMM late=N               each tone (late: samples written after their time)
     pt done
   Returns the late samples (0: the 8 kHz pace held). */
#include "uv21_mod.h"

#ifndef PT_MS
#define PT_MS 1500
#endif
#ifndef PT_GAP_MS
#define PT_GAP_MS 500
#endif
#ifndef PT_LEVEL
#define PT_LEVEL 25                      /* % of full scale */
#endif
#ifndef PT_HZ
#define PT_HZ 500, 1000, 2000
#endif

#define REG(a) (*(volatile uint32_t *)(a))
#define CRM_APB2EN REG(0x40021018u)
#define GPIOA_CFGR REG(0x48000000u)
#define GPIOA_MUXH REG(0x48000024u)
#define TMR1 0x40012C00u
#define T_CTRL1 REG(TMR1 + 0x00)
#define T_SWEVT REG(TMR1 + 0x14)
#define T_CM2 REG(TMR1 + 0x1C)
#define T_CCTRL REG(TMR1 + 0x20)
#define T_CVAL REG(TMR1 + 0x24)
#define T_DIV REG(TMR1 + 0x28)
#define T_PR REG(TMR1 + 0x2C)
#define T_C4DT REG(TMR1 + 0x40)
#define T_BRK REG(TMR1 + 0x44)
#define DEMCR REG(0xE000EDFCu)
#define DWT_CTRL REG(0xE0001000u)
#define DWT_CYCCNT REG(0xE0001004u)

#define SCLK 120000000u
#define CARRIER 48000u
#define RATE 8000u
#define TOP (SCLK / CARRIER)             /* 2500 counts */

/* a quarter of a sine in Q15, 64 steps (the rest by symmetry) */
static const int16_t qsin[65] = {
  0, 804, 1608, 2410, 3212, 4011, 4808, 5602, 6393, 7179, 7962, 8739, 9512, 10278, 11039, 11793,
  12539, 13279, 14010, 14732, 15446, 16151, 16846, 17530, 18204, 18868, 19519, 20159, 20787, 21403,
  22005, 22594, 23170, 23731, 24279, 24811, 25329, 25832, 26319, 26790, 27245, 27683, 28105, 28510,
  28898, 29268, 29621, 29956, 30273, 30571, 30852, 31113, 31356, 31580, 31785, 31971, 32137, 32285,
  32412, 32521, 32609, 32678, 32728, 32757, 32767};

static int32_t sine_q15(uint32_t ph) {   /* ph: 0..255 a cycle */
  uint32_t q = (ph >> 6) & 3, i = ph & 63;
  int32_t v = q & 1 ? qsin[64 - i] : qsin[i];
  return q & 2 ? -v : v;
}

static void line(const um_api_t *api, const char *a, uint32_t hz, uint32_t ms, uint32_t late) {
  char t[64];
  char *p = um_put(t, a);
  p = um_int(um_put(p, " hz="), (int32_t)hz);
  p = um_int(um_put(p, " ms="), (int32_t)ms);
  um_int(um_put(p, " late="), (int32_t)late);
  api->say(t);
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  static const uint32_t tones[] = {PT_HZ};
  char t[64];
  (void)ram, (void)ram_len;
  DEMCR |= 1u << 24;
  DWT_CTRL |= 1u;

  uint32_t cfgr0 = GPIOA_CFGR, muxh0 = GPIOA_MUXH;
  CRM_APB2EN |= 1u << 11;                /* TMR1 */
  T_CTRL1 = 0;
  T_DIV = 0;
  T_PR = TOP - 1;
  T_C4DT = TOP / 2;
  T_CM2 = (T_CM2 & ~0xFF00u) | (6u << 12) | (1u << 11);   /* CH4: PWM mode A, preload */
  T_CCTRL |= 1u << 12;                   /* C4EN */
  T_BRK |= 1u << 15;                     /* OEN: the advanced timer's outputs on */
  T_SWEVT = 1;                           /* load PR and C4DT */
  T_CTRL1 = (1u << 7) | 1u;              /* PRBEN, TMREN */
  GPIOA_MUXH = (muxh0 & ~(0xFu << 12)) | (2u << 12);      /* PA11 AF2: TMR1_CH4 */
  GPIOA_CFGR = (cfgr0 & ~(3u << 22)) | (2u << 22);        /* PA11 alternate */

  char *p = um_put(t, "pt arm arr=");
  p = um_int(p, (int32_t)TOP);
  p = um_hex(um_put(p, " pa11mode="), (cfgr0 >> 22) & 3u, 1);
  um_hex(um_put(p, " af="), (muxh0 >> 12) & 0xFu, 1);
  api->say(t);

  uint32_t late_all = 0;
  const int32_t amp = (int32_t)(TOP / 2) * PT_LEVEL / 100;
  for (unsigned k = 0; k < sizeof tones / sizeof tones[0]; k++) {
    uint32_t hz = tones[k], late = 0, ph = 0;
    uint32_t step = hz * 2097u + hz * 152u / 1000u;          /* 2^24 / 8000 = 2097.152: 1/2^24 cycles */
    uint32_t n = (uint32_t)PT_MS * RATE / 1000u;
    uint32_t due = DWT_CYCCNT;
    for (uint32_t i = 0; i < n; i++) {
      due += SCLK / RATE;
      while ((int32_t)(DWT_CYCCNT - due) < 0) {}
      if ((int32_t)(DWT_CYCCNT - due) > (int32_t)(SCLK / RATE / 2)) late++;
      T_C4DT = (uint32_t)((int32_t)(TOP / 2) + (int32_t)((sine_q15(ph >> 16) * amp) >> 15));
      ph += step;
    }
    T_C4DT = TOP / 2;
    line(api, "pt tone", hz, PT_MS, late);
    late_all += late;
    api->delay_us((uint32_t)PT_GAP_MS * 1000u);
  }
  GPIOA_CFGR = cfgr0;
  GPIOA_MUXH = muxh0;
  T_CTRL1 = 0;
  T_CCTRL &= ~(1u << 12);
  api->say("pt done");
  return (int)late_all;
}
