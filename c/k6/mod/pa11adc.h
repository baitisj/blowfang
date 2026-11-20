/* pa11adc.h: the beep pin as an audio input (2026-11-19, docs/k6-mic-probe.md).
   The FD6818's AF output (pin 9) reaches no ADC pin. But the voice prompts' PWM pin, PA11, joins
   the same node: PA11 - 4.7k - (3.9 nF) - 4.7k - (22 nF) - 22k - 1 uF - VOICE_IN, and the AF
   output feeds VOICE_IN through 2.2k. So audio on the AF pin leaks back to the 3.9 nF capacitor
   at PA11, far too small to cross a logic threshold by itself. This runs PA11 as a one-pin
   delta-sigma converter with that capacitor as its integrator, a million and a half times a
   second:
     push the capacitor up a fixed step (the pin an output, high, for 6 CPU cycles), let go,
     read the pin as an input (is the capacitor above the input's threshold?), and if it is,
     pull it down (an output, low, for 6 + 5 * hold cycles).
   The share of "above" answers in each hundred or so turns then follows the current the audio
   pushes into the capacitor. The fixed push also leaves the pin's Schmitt input high, so every
   read is against its lower threshold: a comparator with no memory. (Without the push, reading
   and then driving the opposite way, the pin only toggles: the capacitor never leaves the
   Schmitt input's dead band. Measured on the radio 2026-11-20: exactly half, no noise at all.)

   The answers are summed twice (i1, then i2) and a sample is the second difference of i2 every
   w turns: a triangle-weighted count (a second-order CIC decimator), 0 .. w * w, which keeps the
   converter's fine-grained noise from folding into the audio band as a plain count would.

   On the radio (2026-11-20, hold 3, w 90: 17.8 kHz): the muted output's noise in 300-3000 Hz is
   0.00017 of full scale, the receiver's hiss 0.051. The rest level is 0.87, 0.68 and 0.48 of
   full scale at hold 2, 3 and 5.

   The caller: PA11's speed bits (OSPEEDR) at 3 for the run (the drive times are tens of ns),
   m_in = GPIOA's MODER with PA11's two bits cleared, and MODER, OSPEEDR and the output bit put
   back after. The speaker amplifier (PB2) should be off: the pin's pulses reach it too.
   SPDX-License-Identifier: MIT */
#ifndef K6_PA11ADC_H
#define K6_PA11ADC_H

#define PA11_GPIOA 0x48000000u
#define PA11_MODER 0x00u
#define PA11_OSPEEDR 0x08u
#define PA11_PUPDR 0x0Cu
#define PA11_IDR 0x10u
#define PA11_ODR 0x14u
#define PA11_BSRR 0x18u
#define PA11_PIN 11u

typedef struct {
  uint32_t i1, i2, z1, z2;
} pa11_t;

/* w turns of the converter. Every turn takes the same time whatever it reads (no branch on the
   pin): about 44 + 5 * hold CPU cycles. hold goes through a volatile so that a caller's constant
   cannot be folded into the pull-down's loop: micprobe's first build (hold a #define) came out
   with a shorter pull-down, resting at 0.82 instead of 0.68. */
static void __attribute__((noinline)) pa11_turns(uint32_t m_in, uint32_t hold, uint32_t w, pa11_t *c) {
  volatile uint32_t *g = (volatile uint32_t *)PA11_GPIOA;
  volatile uint32_t hv = hold;
  uint32_t i1 = c->i1, i2 = c->i2, h, m_out = m_in | (1u << (2 * PA11_PIN));
  hold = hv;
  do {
    uint32_t b;
    g[PA11_BSRR / 4] = 1u << PA11_PIN;
    g[PA11_MODER / 4] = m_out;
    __asm volatile("nop\nnop\nnop\nnop");
    g[PA11_MODER / 4] = m_in;
    __asm volatile("nop\nnop\nnop\nnop\nnop\nnop\nnop\nnop"); /* the pin settles to the capacitor */
    b = (g[PA11_IDR / 4] >> PA11_PIN) & 1u;
    g[PA11_BSRR / 4] = 1u << (PA11_PIN + 16);
    g[PA11_MODER / 4] = m_in | (b << (2 * PA11_PIN));
    for (h = hold; h; h--) __asm volatile("nop");
    g[PA11_MODER / 4] = m_in;
    i1 += b;
    i2 += i1;
  } while (--w);
  c->i1 = i1;
  c->i2 = i2;
}

/* one sample: w turns, then the second difference. The first two after a zeroed pa11_t are
   not yet differences: drop them. */
static uint32_t pa11_sample(uint32_t m_in, uint32_t hold, uint32_t w, pa11_t *c) {
  uint32_t d, y;
  pa11_turns(m_in, hold, w, c);
  d = c->i2 - c->z1;
  c->z1 = c->i2;
  y = d - c->z2;
  c->z2 = d;
  return y;
}

#endif
