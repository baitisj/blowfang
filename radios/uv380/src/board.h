/* UV-380 board layer: what the portable code (c/common, the sotto codec, the apps) may call.
 *
 * Two implementations: board_radio.c (the real radio, STM32F405 at 168 MHz) and board_qemu.c
 * (QEMU's netduinoplus2, an STM32F405 with no radio around it: audio goes to a file on the host
 * through semihosting). Everything RF-side (AT1846S, HR_C6000, PA) is in rf.h and is not written
 * yet: the radio build cannot transmit.
 *
 * Pin facts are from the OpenRTX project's MD-UV380 hardware notes (openrtx.org,
 * hardware/mduv380.md and platform/targets/MD-UV3x0/pinmap.h), checked 2026-11-10.
 *
 * SPDX-License-Identifier: MIT */
#ifndef UV380_BOARD_H
#define UV380_BOARD_H

#include <stdint.h>

enum { PORT_A, PORT_B, PORT_C, PORT_D, PORT_E };

/* pins as port << 4 | pin */
#define PIN(port, n) ((uint8_t)((port) << 4 | (n)))
#define PIN_PWR_HOLD PIN(PORT_A, 7)     /* high keeps the radio powered */
#define PIN_LED_GREEN PIN(PORT_E, 0)
#define PIN_LED_RED PIN(PORT_E, 1)
#define PIN_PTT PIN(PORT_E, 11)         /* active low */
#define PIN_PTT_EXT PIN(PORT_E, 12)
#define PIN_AUDIO_PWM PIN(PORT_C, 8)    /* TIM3_CH3 (AF2): the MCU's audio into the HR_C6000 line in */
#define PIN_SPK_MUTE PIN(PORT_B, 8)     /* low unmutes */
#define PIN_AUDIO_AMP PIN(PORT_B, 9)    /* speaker amplifier power */
#define PIN_MIC_PWR PIN(PORT_A, 13)     /* mic preamp power; also SWDIO */
#define PIN_TX_PA_EN PIN(PORT_C, 5)
#define PIN_RF_APC_SW PIN(PORT_C, 4)
#define PIN_PA_SEL PIN(PORT_C, 6)       /* low VHF, high UHF */
#define PIN_VHF_LNA PIN(PORT_A, 5)
#define PIN_UHF_LNA PIN(PORT_A, 2)
#define PIN_C6000_PDN PIN(PORT_E, 6)

void board_init(void);
uint32_t board_ms(void);                /* milliseconds since board_init */
void board_led(int green, int red);
int board_ptt(void);                    /* 1 while the PTT key is held */

/* Sample output to the MCU -> RF path (TIM3 PWM on PC8, paced by TIM7 + DMA1 stream 2). fs must
 * divide 84 MHz (48000 does). audio_write blocks until the samples are queued; audio_stop waits
 * for the last of them to play out. */
void audio_start(uint32_t fs);
void audio_write(const int16_t *s, unsigned n);
void audio_stop(void);

/* QEMU only (semihosting); the radio build returns -1. */
int host_read_file(const char *path, uint8_t *buf, unsigned max);
int host_read_at(const char *path, uint8_t *buf, unsigned off, unsigned n);   /* bytes read, -1 if no file */
void host_log(const char *s);
void host_exit(int code);

#endif
