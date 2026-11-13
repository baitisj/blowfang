/* UV-380 I2S probe: does the radio's received audio reach the MCU without a hardware mod?
 *
 * On the MD-UV380 the AT1846S's FM audio goes to the HR_C6000 (RX_AUDIO_MUX, PD9, high), whose
 * codec digitises it and plays it to the speaker (OpenRTX: "Codec enabled, LineIn1, LineOut2,
 * I2S slave mode", and its ADC-to-DAC passthrough). The codec also has an I2S port. The stock
 * MD-380 firmware talks to its C5000 over SPI3/I2S3 (PA15, PC10, PC11, PC12, none of them in
 * OpenRTX's pin map for this radio). If those samples can be clocked out, the UV-380 can receive
 * K6 packets with no mod. OpenRTX's own route needs a wire from the C6000 to PC3.
 *
 * One pass, each step a line (i2sp_ops.say, the image sends them to USB serial):
 *   1. census: each candidate pin as an input with pull-up then pull-down. Does it follow the
 *      pulls (nothing drives it), sit high or low (driven), or toggle (edges a ms)? Done with the
 *      codec as OpenRTX sets it (E0 c9) and with bit 0 cleared (c8: perhaps I2S master).
 *   2. capture: if the C6000 clocks a port, the MCU listens as an I2S slave (it drives nothing).
 *      If no one drives a port's clock and word select, the MCU clocks it as master and only
 *      listens on SD and ext SD. Each capture's samples are summed up: words that came, nonzero,
 *      changes, mean, RMS, and the strongest of 31 bands (fs / 64 wide) and its share of the
 *      power. A tone sent by the IC-7100 (PSK31 "AG7EW TEST" at 1000 Hz, or PHY R's 600/1200 Hz)
 *      shows up there.
 *   3. PC3 (OpenRTX's RX audio input, ADC2 channel 13) through OpenRTX's own input stream, as a
 *      baseline: on a radio with no mod it should hold nothing.
 * Everything touched is put back after each step: pins, the C6000's registers, DMA streams.
 *
 * The analysis (i2sp_analyse, i2sp_format) is plain C and runs in the tests on the host.
 * SPDX-License-Identifier: MIT */
#ifndef I2SPROBE_H
#define I2SPROBE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    void (*say)(void *ctx, const char *line);                /* one line, no newline */
    void (*c6000_write)(void *ctx, uint8_t reg, uint8_t val); /* configuration registers */
    uint8_t (*c6000_read)(void *ctx, uint8_t reg);
    void (*sleep_ms)(void *ctx, unsigned ms);
    int (*stop)(void *ctx);                                  /* nonzero: end the pass now */
    /* PC3 through OpenRTX's input stream: n samples at fs into buf, returns how many came */
    unsigned (*adc_capture)(void *ctx, int16_t *buf, unsigned n, unsigned fs);
    void *ctx;
} i2sp_ops;

#define I2SP_BINS 31

typedef struct {
    uint32_t n;        /* samples */
    uint32_t nz;       /* nonzero */
    uint32_t changes;  /* samples unlike the one before */
    int32_t mean;
    uint32_t rms;      /* about the mean */
    uint32_t peak_hz;  /* the strongest band's middle, fs k / 64, k = 1..31 (first 1024 samples) */
    uint32_t peak_pm;  /* its share of the power about the mean, per mille */
} i2sp_stats;

/* n samples x[0], x[stride], ... taken at fs a second */
void i2sp_analyse(const int16_t *x, unsigned n, unsigned stride, unsigned fs, i2sp_stats *s);
/* " tag:nz=.. ch=.. m=.. r=.. pk=..Hz/..": appends to buf (size bytes in all), returns its length */
unsigned i2sp_format(char *buf, unsigned size, const char *tag, const i2sp_stats *s);

/* One whole pass (the radio only; elsewhere it says that it can't run). */
void i2sp_pass(const i2sp_ops *ops);

/* One command line from USB serial (the probe image reads them between passes), so hamlet can
 * experiment with the I2S port with no reflash. Each command ends with a line "ok ..." or
 * "err ...". Commands:
 *   help                       this list
 *   pass                       one whole pass now
 *   auto 0|1                   passes every few seconds off / on (returns I2SP_AUTO_OFF / _ON)
 *   c6r RR | c6w RR VV         a C6000 configuration register (hex)
 *   rd ADDR [N]                N 32-bit words (hex, up to 1024, 8 a line) from flash, RAM,
 *                              peripherals or the core's registers
 *   wr ADDR VAL                a 32-bit write, only to the I2S ports' registers (SPI2, SPI3,
 *                              I2S2ext, I2S3ext), DMA1's LIFCR and its streams 0, 2 and 3
 *                              (i2sp_wr_allowed): nothing that can key the radio
 *   pin NAME MODE [PULL [AF]]  a probe pin (PA15 PC10 PC11 PC12 PB12-15 PC2 PB10 PC3):
 *                              MODE in|out|af|an, PULL none|up|down, AF 0-15
 *   clk on|off                 DMA1, SPI2 and SPI3 clocks and PLLI2S
 *   cap PORT s|m FMT FS        one capture on SPI3 or SPI2, as slave or master, FMT P16 P32 M32
 *                              S16, its usual line
 *   raw [a|b] [N]              the last capture's first N halfwords (SD, or ext SD with b)
 *   buf                        the capture buffers' addresses (for DMA set up by hand)
 * Returns 0, or I2SP_AUTO_ON / I2SP_AUTO_OFF for "auto", or -1 for an error. */
#define I2SP_AUTO_OFF 1
#define I2SP_AUTO_ON 2
int i2sp_command(const i2sp_ops *ops, const char *line);

/* may "wr" write the 32-bit word at addr? (host and radio) */
int i2sp_wr_allowed(uint32_t addr);

#ifdef __cplusplus
}
#endif

#endif
