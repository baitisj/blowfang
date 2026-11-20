/* micadc.h: a module switches the FD6818's mic ADC (and TX DSP) on while the K6 receives
   (2026-11-19, docs/k6-mic-probe.md; Jeff: "No flash: module writes the chip bus").
   REG_30 holds the RX/TX state, and fd_write refuses it below API level 14. Where it does,
   mic_state() writes the register over the chip's bus pins itself (PB3 SCN, PB5 SCK, PB6 SDA,
   as Aura's driver does), under level 14's own rule:
     only bits 2 (the mic ADC) and 1 (the TX DSP) change;
     never unless the chip is in receive with the PA off (REG_30 bit 3 and REG_36 bit 7 clear,
     REG_30 bit 0 set);
     the register is read back after.
   A retune (api->tune) writes REG_30 whole, so the bits are gone after one, and the core's
   return to receive after the module does the same. A module that sets them clears them before
   it returns all the same.
   SPDX-License-Identifier: MIT */
#ifndef K6_MICADC_H
#define K6_MICADC_H

#define MIC_GPIOB 0x48000400u
#define MIC_BSRR 0x18u
#define MIC_BRR 0x28u
#define MIC_SCN (1u << 3)
#define MIC_SCK (1u << 5)
#define MIC_SDA (1u << 6)
#define MIC_ADC 0x0004u /* REG_30 bit 2 */
#define MIC_DSP 0x0002u /* REG_30 bit 1: the TX DSP */
#define MIC_BITS (MIC_ADC | MIC_DSP)

/* one register over the chip's bus pins, as Aura's write_reg: SCN low, 8 address bits and 16
   data bits MSB first, each taken on SCK's rising edge, SCN high */
static void mic_bus_write(const k6_api_t *api, uint32_t reg, uint32_t v) {
  volatile uint32_t *set = (volatile uint32_t *)(MIC_GPIOB + MIC_BSRR), *clr = (volatile uint32_t *)(MIC_GPIOB + MIC_BRR);
  uint32_t word = ((reg & 0x7Fu) << 16) | (v & 0xFFFFu);
  int i;
  *clr = MIC_SCN;
  api->delay_us(1);
  for (i = 23; i >= 0; i--) {
    *clr = MIC_SCK;
    *((word >> i) & 1u ? set : clr) = MIC_SDA;
    api->delay_us(1);
    *set = MIC_SCK;
    api->delay_us(1);
  }
  *set = MIC_SCN;
  api->delay_us(1);
  *clr = MIC_SCK;
  *clr = MIC_SDA;
}

/* REG_30's mic ADC and TX DSP bits = on. Returns 0 (taken by fd_write), 1 (by the bus pins),
   or -1: not in receive with the PA off, or the register did not read back. */
static int __attribute__((unused)) mic_state(const k6_api_t *api, uint32_t on) {
  uint32_t now = api->fd_read(0x30), want = (now & ~MIC_BITS) | (on & MIC_BITS);
  int via = 0;
  if ((now & 0x0008u) || !(now & 0x0001u) || (api->fd_read(0x36) & 0x0080u)) return -1;
  if (want != now && api->fd_write(0x30, (uint16_t)want) != 0) {
    mic_bus_write(api, 0x30, want);
    via = 1;
  }
  return api->fd_read(0x30) == want ? via : -1;
}

#endif
