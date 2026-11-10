/* UV-380 board layer, real radio (board.h). Clocks, GPIO, SysTick and the MCU audio output.
 *
 * Written from RM0090 and the OpenRTX pin notes; NOT yet run on a radio (2026-11-10: Jeff's bench
 * belongs to the K6). Nothing here keys the transmitter: rf.h has no driver yet.
 *
 * SPDX-License-Identifier: MIT */
#include "board.h"

#include "regs.h"

#define IRQ_DMA1_STREAM2 13
#define AUDIO_DMA 2                     /* DMA1 stream 2, channel 1 = TIM7_UP */
#define HALF 256                        /* samples per half buffer: 5.3 ms at 48 kHz */

static volatile uint32_t ms;
static uint16_t dma_buf[2 * HALF];      /* PWM duty values, 0..255 */
static volatile uint8_t free_half = 0xFF;   /* the half the DMA just finished, 0xFF: none yet */
static unsigned fill_pos;
static uint8_t fill_half;

static void pin_mode(uint8_t pin, uint32_t mode, uint32_t af) {
    unsigned port = pin >> 4, n = pin & 15;
    GPIO_MODER(port) = (GPIO_MODER(port) & ~(3u << 2 * n)) | mode << 2 * n;
    if (mode == 2) {
        volatile uint32_t *afr = n < 8 ? &GPIO_AFRL(port) : &GPIO_AFRH(port);
        unsigned sh = 4 * (n & 7);
        *afr = (*afr & ~(15u << sh)) | af << sh;
        GPIO_OSPEEDR(port) |= 2u << 2 * n;
    }
}

static void pin_set(uint8_t pin, int on) {
    GPIO_BSRR(pin >> 4) = 1u << ((pin & 15) + (on ? 0 : 16));
}

static int pin_get(uint8_t pin) {
    return (int)((GPIO_IDR(pin >> 4) >> (pin & 15)) & 1);
}

/* 8 MHz HSE -> PLL: /8 *336 /2 = 168 MHz SYSCLK, /7 = 48 MHz USB; AHB 168, APB1 42 (timers 84),
 * APB2 84 (timers 168); flash 5 wait states with prefetch and caches. */
static void clocks(void) {
    RCC_CR |= 1u << 16;                                 /* HSEON */
    while (!(RCC_CR & (1u << 17))) {
    }
    FLASH_ACR = 5u | 1u << 8 | 1u << 9 | 1u << 10;      /* LATENCY 5, PRFTEN, ICEN, DCEN */
    RCC_CFGR = (RCC_CFGR & ~0xFCF0u) | 5u << 10 | 4u << 13;   /* PPRE1 /4, PPRE2 /2 */
    RCC_PLLCFGR = 8u | 336u << 6 | 0u << 16 | 1u << 22 | 7u << 24;   /* M, N, P=/2, HSE, Q */
    RCC_CR |= 1u << 24;                                 /* PLLON */
    while (!(RCC_CR & (1u << 25))) {
    }
    RCC_CFGR = (RCC_CFGR & ~3u) | 2u;                   /* SW = PLL */
    while ((RCC_CFGR & 0xCu) != 0x8u) {
    }
}

void systick_handler(void) {
    ms++;
}

void board_init(void) {
    clocks();
    RCC_AHB1ENR |= 0x1Fu | 1u << 21;                    /* GPIOA..E, DMA1 */
    RCC_APB1ENR |= 1u << 1 | 1u << 5;                   /* TIM3, TIM7 */
    pin_mode(PIN_PWR_HOLD, 1, 0);
    pin_set(PIN_PWR_HOLD, 1);
    pin_mode(PIN_LED_GREEN, 1, 0);
    pin_mode(PIN_LED_RED, 1, 0);
    pin_mode(PIN_SPK_MUTE, 1, 0);
    pin_set(PIN_SPK_MUTE, 1);
    pin_mode(PIN_AUDIO_AMP, 1, 0);
    pin_set(PIN_AUDIO_AMP, 0);
    /* RF outputs held off explicitly */
    pin_mode(PIN_TX_PA_EN, 1, 0);
    pin_set(PIN_TX_PA_EN, 0);
    pin_mode(PIN_RF_APC_SW, 1, 0);
    pin_set(PIN_RF_APC_SW, 0);
    pin_mode(PIN_PTT, 0, 0);
    REG32(0xE000E014u) = 168000u - 1;                   /* SysTick 1 kHz */
    REG32(0xE000E018u) = 0;
    REG32(0xE000E010u) = 7;
}

uint32_t board_ms(void) {
    return ms;
}

void board_led(int green, int red) {
    pin_set(PIN_LED_GREEN, green);
    pin_set(PIN_LED_RED, red);
}

int board_ptt(void) {
    return !pin_get(PIN_PTT);
}

void dma1_stream2_handler(void) {
    uint32_t f = DMA_LISR;
    if (f & (1u << 20)) {               /* HTIF2: first half played */
        DMA_LIFCR = 1u << 20;
        free_half = 0;
    }
    if (f & (1u << 21)) {               /* TCIF2: second half played */
        DMA_LIFCR = 1u << 21;
        free_half = 1;
    }
    DMA_LIFCR = 0x3Du << 16;            /* clear the rest of stream 2's flags */
}

void audio_start(uint32_t fs) {
    for (unsigned i = 0; i < 2 * HALF; i++)
        dma_buf[i] = 128;
    pin_mode(PIN_AUDIO_PWM, 2, 2);      /* TIM3_CH3 */
    TIM_PSC(TIM3_BASE) = 0;
    TIM_ARR(TIM3_BASE) = 255;           /* 84 MHz / 256 = 328 kHz carrier */
    TIM_CCR3(TIM3_BASE) = 128;
    TIM_CCMR2(TIM3_BASE) = 6u << 4 | 1u << 3;   /* OC3 PWM mode 1, preload */
    TIM_CCER(TIM3_BASE) |= 1u << 8;     /* CC3E */
    TIM_EGR(TIM3_BASE) = 1;
    TIM_CR1(TIM3_BASE) = 1u << 7 | 1;   /* ARPE, CEN */

    DMA_SCR(AUDIO_DMA) = 0;
    while (DMA_SCR(AUDIO_DMA) & 1) {
    }
    DMA_LIFCR = 0x3Du << 16;
    DMA_SPAR(AUDIO_DMA) = TIM3_BASE + 0x3C;
    DMA_SM0AR(AUDIO_DMA) = (uint32_t)dma_buf;
    DMA_SNDTR(AUDIO_DMA) = 2 * HALF;
    /* channel 1, very high priority, 16-bit both sides, memory increment, circular,
       memory to peripheral, half and full transfer interrupts */
    DMA_SCR(AUDIO_DMA) = 1u << 25 | 3u << 16 | 1u << 13 | 1u << 11 | 1u << 10 | 1u << 8 |
                         1u << 6 | 1u << 4 | 1u << 3;
    NVIC_ISER(0) = 1u << IRQ_DMA1_STREAM2;
    DMA_SCR(AUDIO_DMA) |= 1;

    TIM_PSC(TIM7_BASE) = 0;
    TIM_ARR(TIM7_BASE) = 84000000u / fs - 1;
    TIM_DIER(TIM7_BASE) = 1u << 8;      /* UDE */
    TIM_CR1(TIM7_BASE) = 1;
    fill_pos = 0;
    fill_half = 0;
    free_half = 0xFF;
}

void audio_write(const int16_t *s, unsigned n) {
    while (n--) {
        if (fill_pos == 0) {            /* wait until the DMA has left this half */
            while (free_half != fill_half) {
                __asm__ volatile("wfi");
            }
            free_half = 0xFF;
        }
        dma_buf[fill_half * HALF + fill_pos] = (uint16_t)((*s++ >> 8) + 128);
        if (++fill_pos == HALF) {
            fill_pos = 0;
            fill_half ^= 1;
        }
    }
}

void audio_stop(void) {
    static const int16_t zero[HALF] = {0};
    audio_write(zero, HALF - fill_pos + 2 * HALF);  /* pad, then let both halves play out */
    TIM_CR1(TIM7_BASE) = 0;
    DMA_SCR(AUDIO_DMA) &= ~1u;
    TIM_CCER(TIM3_BASE) &= ~(1u << 8);
}

int host_read_file(const char *path, uint8_t *buf, unsigned max) {
    (void)path;
    (void)buf;
    (void)max;
    return -1;
}

void host_log(const char *s) {
    (void)s;
}

void host_exit(int code) {
    (void)code;
}
