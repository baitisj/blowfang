/* c6slot.h: the HR_C6000's interrupt lines seen from a RAM module. The chip pulses (active low,
   3 Sys_clk = 0.3 us) TIME_SLOT_INTER at every 30 ms slot boundary once it has slot timing,
   SYS_INTER for events (0x82) and RF_TX_INTER before a transmitted burst. On the MD-UV380 they
   are PC0, PC1 and PC2 (RadioFoundry docs/spec/hardware-dm1701.md 4.1, the same on the UV380
   family: hardware-mduv380.md 1). OpenRTX has no DMR, so it never uses them.

   A pulse that short can't be polled, so EXTI lines 0-2 latch the falling edges in their pending
   bits, which the module reads and clears. The pending bit needs the line's interrupt mask set,
   which also pends the NVIC interrupt; its handler never runs because the NVIC interrupt stays
   disabled (c6s_open refuses if anything enabled EXTI0-2, and c6s_close clears the NVIC pending
   bits and puts every register back).
   SPDX-License-Identifier: MIT */
#ifndef C6SLOT_H
#define C6SLOT_H

#include <stdint.h>

#define C6S_FN static __attribute__((unused))

#define C6S_REG(a) (*(volatile uint32_t *)(a))
#define C6S_RCC_APB2ENR 0x40023844u
#define C6S_SYSCFG_EXTICR1 0x40013808u
#define C6S_EXTI 0x40013C00u          /* IMR, EMR, RTSR, FTSR, SWIER, PR */
#define C6S_GPIOC 0x40020800u
#define C6S_NVIC_ISER0 0xE000E100u
#define C6S_NVIC_ICPR0 0xE000E280u
#define C6S_NVIC_BITS (7u << 6)       /* EXTI0, EXTI1, EXTI2 = IRQ 6, 7, 8 */
#define C6S_LINES 7u                  /* EXTI lines 0-2 */
#define C6S_SLOT 1u                   /* PC0 TIME_SLOT_INTER */
#define C6S_SYS 2u                    /* PC1 SYS_INTER */
#define C6S_RFTX 4u                   /* PC2 RF_TX_INTER */

typedef struct {
    uint32_t exticr, imr, rtsr, ftsr, moder, pupdr;   /* our bits only */
    int open;
} c6s_state;

C6S_FN inline uint32_t c6s_irq_off(void) {
    uint32_t m;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(m)::"memory");
    return m;
}

C6S_FN inline void c6s_irq_on(uint32_t m) {
    __asm volatile("msr primask, %0" ::"r"(m) : "memory");
}

/* reg = (reg & ~mask) | val, with interrupts off; the old masked bits back */
C6S_FN uint32_t c6s_rmw(uint32_t a, uint32_t mask, uint32_t val) {
    uint32_t m = c6s_irq_off(), old = C6S_REG(a);
    C6S_REG(a) = (old & ~mask) | (val & mask);
    c6s_irq_on(m);
    return old & mask;
}

/* 0, or -1: an NVIC interrupt for EXTI0-2 is enabled (someone else's: hands off). Every write
   is a masked read-modify-write with interrupts off and touches only lines 0-2 and PC0-2: GPIOC
   also holds the AT1846S's SDA (PC9), whose mode OpenRTX flips at every I2C transfer. */
C6S_FN int c6s_open(c6s_state *s) {
    s->open = 0;
    if (C6S_REG(C6S_NVIC_ISER0) & C6S_NVIC_BITS)
        return -1;
    c6s_rmw(C6S_RCC_APB2ENR, 1u << 14, 1u << 14);              /* SYSCFG clock (left on) */
    s->moder = c6s_rmw(C6S_GPIOC + 0x00u, 0x3Fu, 0x00u);       /* PC0-2 inputs */
    s->pupdr = c6s_rmw(C6S_GPIOC + 0x0Cu, 0x3Fu, 0x15u);       /* pulled up (lines idle high) */
    s->exticr = c6s_rmw(C6S_SYSCFG_EXTICR1, 0xFFFu, 0x222u);   /* lines 0-2 from port C */
    s->rtsr = c6s_rmw(C6S_EXTI + 0x08u, C6S_LINES, 0);
    s->ftsr = c6s_rmw(C6S_EXTI + 0x0Cu, C6S_LINES, C6S_LINES);
    C6S_REG(C6S_EXTI + 0x14u) = C6S_LINES;                     /* old edges out */
    s->imr = c6s_rmw(C6S_EXTI + 0x00u, C6S_LINES, C6S_LINES);
    s->open = 1;
    return 0;
}

/* the edges since the last call: C6S_SLOT, C6S_SYS, C6S_RFTX bits */
C6S_FN unsigned c6s_poll(c6s_state *s) {
    if (!s->open)
        return 0;
    unsigned p = C6S_REG(C6S_EXTI + 0x14u) & C6S_LINES;
    if (p)
        C6S_REG(C6S_EXTI + 0x14u) = p;
    return p;
}

/* the line levels now (bit 0 PC0 ...) */
C6S_FN unsigned c6s_levels(void) {
    return C6S_REG(C6S_GPIOC + 0x10u) & C6S_LINES;
}

C6S_FN void c6s_close(c6s_state *s) {
    if (!s->open)
        return;
    c6s_rmw(C6S_EXTI + 0x00u, C6S_LINES, s->imr);
    c6s_rmw(C6S_EXTI + 0x0Cu, C6S_LINES, s->ftsr);
    c6s_rmw(C6S_EXTI + 0x08u, C6S_LINES, s->rtsr);
    C6S_REG(C6S_EXTI + 0x14u) = C6S_LINES;
    c6s_rmw(C6S_SYSCFG_EXTICR1, 0xFFFu, s->exticr);
    c6s_rmw(C6S_GPIOC + 0x0Cu, 0x3Fu, s->pupdr);
    c6s_rmw(C6S_GPIOC + 0x00u, 0x3Fu, s->moder);
    C6S_REG(C6S_NVIC_ICPR0) = C6S_NVIC_BITS;
    s->open = 0;
}

#endif
