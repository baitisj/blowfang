/* Cortex-M4 start-up for the UV-380 (STM32F405): vector table, .data/.bss set-up, FPU on, main.
 * SPDX-License-Identifier: MIT */
#include <stdint.h>

#include "regs.h"

extern uint32_t _estack, _sidata, _sdata, _edata, _sbss, _ebss;
int main(void);
void reset_handler(void);

static void default_handler(void) {
    for (;;) {
    }
}

void nmi_handler(void) __attribute__((weak, alias("default_handler")));
void hardfault_handler(void) __attribute__((weak, alias("default_handler")));
void systick_handler(void) __attribute__((weak, alias("default_handler")));
void dma1_stream2_handler(void) __attribute__((weak, alias("default_handler")));

#define N_IRQ 82                        /* STM32F405 */
#define IRQ_DMA1_STREAM2 13

__attribute__((section(".vectors"), used)) static void (*const vectors[16 + N_IRQ])(void) = {
    [0] = (void (*)(void))(&_estack),
    [1] = reset_handler,
    [2] = nmi_handler,
    [3] = hardfault_handler,
    [4] = default_handler,              /* MemManage */
    [5] = default_handler,              /* BusFault */
    [6] = default_handler,              /* UsageFault */
    [11] = default_handler,             /* SVCall */
    [14] = default_handler,             /* PendSV */
    [15] = systick_handler,
    [16 + IRQ_DMA1_STREAM2] = dma1_stream2_handler,
};

void reset_handler(void) {
    uint32_t *s = &_sidata, *d = &_sdata;
    while (d < &_edata)
        *d++ = *s++;
    for (d = &_sbss; d < &_ebss;)
        *d++ = 0;
    SCB_VTOR = (uint32_t)vectors;       /* the TYT bootloader leaves VTOR at its own table */
    SCB_CPACR |= 0xFu << 20;            /* CP10, CP11: the FPU */
    __asm__ volatile("dsb\n isb");
    main();
    for (;;) {
    }
}
