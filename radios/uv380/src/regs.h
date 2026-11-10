/* The few STM32F405 registers the UV-380 board layer touches, from RM0090 (reference manual,
 * rev 19). Plain addresses instead of ST's CMSIS headers, so the build needs only a compiler.
 * SPDX-License-Identifier: MIT */
#ifndef UV380_REGS_H
#define UV380_REGS_H

#include <stdint.h>

#define REG32(a) (*(volatile uint32_t *)(a))

/* RCC */
#define RCC_BASE 0x40023800u
#define RCC_CR REG32(RCC_BASE + 0x00)
#define RCC_PLLCFGR REG32(RCC_BASE + 0x04)
#define RCC_CFGR REG32(RCC_BASE + 0x08)
#define RCC_AHB1ENR REG32(RCC_BASE + 0x30)
#define RCC_APB1ENR REG32(RCC_BASE + 0x40)
#define RCC_APB2ENR REG32(RCC_BASE + 0x44)

/* flash interface */
#define FLASH_ACR REG32(0x40023C00u)

/* GPIO ports A..E, 0x400 apart */
#define GPIO_BASE(port) (0x40020000u + 0x400u * (uint32_t)(port))
#define GPIO_MODER(p) REG32(GPIO_BASE(p) + 0x00)
#define GPIO_OTYPER(p) REG32(GPIO_BASE(p) + 0x04)
#define GPIO_OSPEEDR(p) REG32(GPIO_BASE(p) + 0x08)
#define GPIO_PUPDR(p) REG32(GPIO_BASE(p) + 0x0C)
#define GPIO_IDR(p) REG32(GPIO_BASE(p) + 0x10)
#define GPIO_BSRR(p) REG32(GPIO_BASE(p) + 0x18)
#define GPIO_AFRL(p) REG32(GPIO_BASE(p) + 0x20)
#define GPIO_AFRH(p) REG32(GPIO_BASE(p) + 0x24)

/* general-purpose timers (TIM3 0x40000400, TIM7 0x40001400) */
#define TIM3_BASE 0x40000400u
#define TIM7_BASE 0x40001400u
#define TIM_CR1(t) REG32((t) + 0x00)
#define TIM_DIER(t) REG32((t) + 0x0C)
#define TIM_SR(t) REG32((t) + 0x10)
#define TIM_EGR(t) REG32((t) + 0x14)
#define TIM_CCMR2(t) REG32((t) + 0x1C)
#define TIM_CCER(t) REG32((t) + 0x20)
#define TIM_PSC(t) REG32((t) + 0x28)
#define TIM_ARR(t) REG32((t) + 0x2C)
#define TIM_CCR3(t) REG32((t) + 0x3C)

/* DMA1 */
#define DMA1_BASE 0x40026000u
#define DMA_LISR REG32(DMA1_BASE + 0x00)
#define DMA_LIFCR REG32(DMA1_BASE + 0x08)
#define DMA_SCR(s) REG32(DMA1_BASE + 0x10 + 0x18u * (s))
#define DMA_SNDTR(s) REG32(DMA1_BASE + 0x14 + 0x18u * (s))
#define DMA_SPAR(s) REG32(DMA1_BASE + 0x18 + 0x18u * (s))
#define DMA_SM0AR(s) REG32(DMA1_BASE + 0x1C + 0x18u * (s))

/* Cortex-M4 system */
#define SCB_CPACR REG32(0xE000ED88u)
#define SCB_VTOR REG32(0xE000ED08u)
#define NVIC_ISER(n) REG32(0xE000E100u + 4u * (n))

#endif
