/* UV-21 Pro board pins (board UV21NRFB-YP07, stock firmware V0.07) for the bring-up image.
 *
 * Every pin the image drives is listed here. A subsystem whose pins aren't defined is compiled
 * out, so an unknown pin is never driven: a wrong output could fight another chip or key
 * something. Inputs (the pin census, ADC) are safe whatever is on the pin.
 *
 * Sources: "dump" = read from this radio's stock firmware (dump job uv21-flash-1, 2026-11-23,
 * not yet tried on the radio); "run" = proven on this radio by an image of ours. This board is
 * NOT wired like the UV-5RM's 5RHNRFB-YP07 despite the name: the power latch, the SPI flash's chip
 * select, the RF bus, the LCD's control pins and the keys all moved.
 *
 * SPDX-License-Identifier: MIT */
#ifndef UV21_BOARD_H
#define UV21_BOARD_H

#define PORT_A 0
#define PORT_B 1
#define PORT_C 2
#define PORT_F 5
#define PIN(port, bit) ((port) << 4 | (bit))
#define BOARD_NAME "uv21"

/* dump. (The dumper drove PA8 high as its SPI chip select, which is why it stayed on.) */
#define POWER_LATCH PIN(PORT_A, 8)          /* high = stay on */
/* dump: high = switched off, so drop the latch */
#define POWER_SENSE PIN(PORT_A, 15)
/* run: the bootloader and the dumper talk on it (USART1, AF1) */
#define UART_TX PIN(PORT_A, 9)
#define UART_RX PIN(PORT_A, 10)             /* also PTT, active low: a press reads as a break */
#define UART_AF 1

/* RF chip: BK4829 (run: register 0 reads 0x4829, bring-up 11-23), dump: bit-banged 3-wire. SCN low frames a transaction,
 * 8-bit address (bit 7 = read), 16-bit data, MSB first. SDA is shared with an I2C device at 0x60
 * (SCL PA4, left alone here). The transmitter's PA enables are the RF chip's GPIO2/GPIO3, so with
 * reads only, nothing here can key it. */
#define RF_SCN PIN(PORT_B, 3)
#define RF_SCK PIN(PORT_B, 5)
#define RF_SDA PIN(PORT_B, 6)

/* SPI flash, dump: SPI1 pins (mode 3 in stock), bit-banged here for a JEDEC ID read only */
#define NOR_CS PIN(PORT_A, 2)
#define NOR_SCK PIN(PORT_A, 5)
#define NOR_MISO PIN(PORT_A, 6)
#define NOR_MOSI PIN(PORT_A, 7)

/* LCD, dump: SPI2 pins, write-only in stock (ST7735S-like, inferred); bit-banged here so the ID
 * read can be tried on SDA. Backlight pin not found in the dump (always on, or a timer output). */
#define LCD_CS PIN(PORT_B, 12)
#define LCD_RST PIN(PORT_B, 8)
#define LCD_DC PIN(PORT_C, 15)              /* low = command */
#define LCD_SCK PIN(PORT_B, 13)
#define LCD_SDA PIN(PORT_B, 15)
#define LCD_W 128                           /* portrait (the 5RM is 160x128 landscape) */
#define LCD_H 160
#define LCD_MADCTL 0x00                     /* stock's value */

/* Keys, dump: columns driven low one at a time, rows read with pull-ups. Each press prints as
 * matrix bit (column * 4 + row). The three side keys ground a row themselves (they read with no
 * column driven), so they print as side bit (row). */
#define KEY_COLS PIN(PORT_B, 0), PIN(PORT_B, 4), PIN(PORT_B, 9), PIN(PORT_B, 10), PIN(PORT_B, 11)
#define KEY_ROWS PIN(PORT_C, 13), PIN(PORT_C, 14), PIN(PORT_F, 6), PIN(PORT_B, 14)
/* run (bring-up press test, 11-23 16:35Z, assuming the presses came in the order asked): names
 * by matrix bit. 0 = not seen yet (column PB0, and PB11 which the first image couldn't print). */
#define KEY_NAMES \
    0, 0, 0, 0,                             /* PB0: rows PC13 PC14 PF6 PB14 */ \
    "EXIT", "2", "MENU", "4",               /* PB4 */ \
    "6", "7", "DOWN", "8",                  /* PB9 */ \
    "1", "3", "UP", "5",                    /* PB10 */ \
    0, 0, 0, 0                              /* PB11 */
#define SIDE_NAMES 0, "SIDE TOP", "SIDE BOT", "SIDE MID"   /* rows PC13, PC14, PF6, PB14 */

/* ADC channels to report (channel n = PA n for 0-7, PB0 = 8, PB1 = 9): the UV-5RM's VOX and
 * battery channels, which no other function here uses. Unconfirmed: reading them is harmless. */
#define ADC_CHANNELS 0, 1

/* Not driven by this image: PA13/PA14 (SWD here; stock uses them for the receive-band switch),
 * PA11 (audio PWM), PB2 (audio amplifier, inferred), PB7 (flashlight: the bootloader toggles it on
 * each frame in flash mode), PA4 (I2C SCL). */

#endif
