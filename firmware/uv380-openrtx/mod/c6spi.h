/* c6spi.h: every HR_C6000 space from a RAM module, with no reflash. The image's own c6000_read and
   c6000_write reach only the system parameter table (OPMode 100, "page 4"); the modem's other
   spaces (the auxiliary table with the sync pattern and tone tables, the TX and RX RAMs where DMR
   payloads and the demodulator's soft values live, 11-bit addresses) need the U_SPI framing
   itself, so this header drives it, as OpenRTX's own bit-banged port does (hwconfig.c, MD-UV3x0):
   PE2 CS, PE3 CLK, PE4 MOSI, PE5 MISO, each bit CLK high, MOSI, CLK low, MISO sampled.

   Sharing the port with OpenRTX. OpenRTX takes a mutex a module can't reach, and keeps CS low
   for a whole transaction. A transaction here runs with interrupts off (so nothing preempts it
   halfway) and only while CS is high (so it never cuts into one of OpenRTX's: a module thread
   that woke while OpenRTX's thread was inside a transaction finds CS low, sleeps 1 ms to let it
   finish, and tries again). Bursts are cut into C6_CHUNK-byte transactions so interrupts are
   never off for long (about 150 us for 32 bytes).

   Framing (RadioFoundry docs/spec/hrc6000-registers.md 1.1-1.2, MIT; the HR_C6000 manual 4.4):
   command byte bit 7 read, bit 6 an 11-bit address, bits 2-0 the space; the address, low byte
   first when extended; then the data, the address counting up while CS stays low.

     space 1  auxiliary parameters (sync pattern 0x04-0x09, CRC masks 0x10-, tones 0x11A-)
     space 2  write: TX RAM, read: RX RAM
     space 3  prompt tone FIFO
     space 4  system parameters (what api->c6000_read reads)
     space 6  write: RX RAM, read: TX RAM

   Header only, no statics (RAM modules can't have writable ones).
   SPDX-License-Identifier: MIT */
#ifndef C6SPI_H
#define C6SPI_H

#include "ortx_mod.h"

#define C6_FN static __attribute__((unused))

#define C6_REG(a) (*(volatile uint32_t *)(a))
#define C6_GPIOE 0x40021000u
#define C6_ODR 0x14u
#define C6_IDR 0x10u
#define C6_BSRR 0x18u
#define C6_CS 2u
#define C6_CLK 3u
#define C6_MOSI 4u
#define C6_MISO 5u
#define C6_CHUNK 32u            /* data bytes in one transaction */
#define C6_TRIES 50u            /* 1 ms waits for CS before giving up */

#define C6_AUX 1u
#define C6_DATA 2u              /* write TX RAM, read RX RAM */
#define C6_SOUND 3u
#define C6_CFG 4u
#define C6_DATA_R 6u            /* write RX RAM, read TX RAM */
#ifndef C6_BITWAIT
#define C6_BITWAIT 8u           /* wait-loop turns a half clock: 8 is about 0.9 MHz on the radio
                                   (c6m17rx 11-18: 7 ms a 512-byte half); 3 about 2 MHz */
#endif
#ifndef C6_ON_BUSY
#define C6_ON_BUSY ((void)0)    /* a module may count the 1 ms waits for OpenRTX's transactions */
#endif

C6_FN inline uint32_t c6_irq_off(void) {
    uint32_t m;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(m)::"memory");
    return m;
}

C6_FN inline void c6_irq_on(uint32_t m) {
    __asm volatile("msr primask, %0" ::"r"(m) : "memory");
}

C6_FN inline void c6_wait(unsigned n) {
    for (volatile unsigned i = 0; i < n; i++) {
    }
}

/* one byte out and in, MSB first; C6_BITWAIT sets the clock, under the manual's 4 MHz (OpenRTX runs
   6.5) */
C6_FN uint8_t c6_byte(uint8_t v) {
    uint8_t in = 0;
    for (unsigned b = 0; b < 8; b++) {
        C6_REG(C6_GPIOE + C6_BSRR) = 1u << C6_CLK;
        C6_REG(C6_GPIOE + C6_BSRR) = (v & 0x80u) ? (1u << C6_MOSI) : (1u << (C6_MOSI + 16u));
        v = (uint8_t)(v << 1);
        c6_wait(C6_BITWAIT);
        C6_REG(C6_GPIOE + C6_BSRR) = 1u << (C6_CLK + 16u);
        c6_wait(C6_BITWAIT);
        in = (uint8_t)((in << 1) | ((C6_REG(C6_GPIOE + C6_IDR) >> C6_MISO) & 1u));
    }
    return in;
}

/* One transaction: space sp, address a (11 bits; extended framing when a > 0xFF or ext), n data
   bytes out of `out` (NULL: 0xFF) and into `in` (NULL: dropped). Interrupts off throughout; only
   starts while CS is high. 0, or -1: CS stayed low (OpenRTX inside a transaction) for every try. */
C6_FN int c6_xfer(const ortx_api_t *api, unsigned sp, unsigned a, int read, int ext,
                   const uint8_t *out, uint8_t *in, unsigned n) {
    uint8_t cmd = (uint8_t)((read ? 0x80u : 0u) | (sp & 7u));
    if (a > 0xFFu)
        ext = 1;
    if (ext)
        cmd |= 0x40u;
    for (unsigned t = 0; t < C6_TRIES; t++) {
        uint32_t m = c6_irq_off();
        if (((C6_REG(C6_GPIOE + C6_ODR) >> C6_CS) & 1u) == 0) {
            c6_irq_on(m);
            C6_ON_BUSY;
            api->sleep_ms(1);
            continue;
        }
        C6_REG(C6_GPIOE + C6_BSRR) = 1u << (C6_CS + 16u);
        c6_byte(cmd);
        c6_byte((uint8_t)a);
        if (ext)
            c6_byte((uint8_t)((a >> 8) & 7u));
        for (unsigned i = 0; i < n; i++) {
            uint8_t v = c6_byte(out ? out[i] : 0xFFu);
            if (in)
                in[i] = v;
        }
        c6_wait(40);            /* OpenRTX: 2 us before and after CS goes high (RT3s) */
        C6_REG(C6_GPIOE + C6_BSRR) = 1u << C6_CS;
        c6_wait(40);
        c6_irq_on(m);
        return 0;
    }
    return -1;
}

/* n bytes from space sp at address a, in C6_CHUNK-byte transactions: 0 or -1 */
C6_FN int c6_read(const ortx_api_t *api, unsigned sp, unsigned a, uint8_t *buf, unsigned n) {
    for (unsigned o = 0; o < n; o += C6_CHUNK) {
        unsigned k = n - o < C6_CHUNK ? n - o : C6_CHUNK;
        if (c6_xfer(api, sp, a + o, 1, a + n > 0x100u, 0, buf + o, k))
            return -1;
    }
    return 0;
}

C6_FN int c6_write(const ortx_api_t *api, unsigned sp, unsigned a, const uint8_t *buf, unsigned n) {
    for (unsigned o = 0; o < n; o += C6_CHUNK) {
        unsigned k = n - o < C6_CHUNK ? n - o : C6_CHUNK;
        if (c6_xfer(api, sp, a + o, 0, a + n > 0x100u, buf + o, 0, k))
            return -1;
    }
    return 0;
}

/* the system parameter table through this port (the module's own, no OpenRTX lock): -1 on a
   port that stayed busy reads as 0x100 */
C6_FN int c6_r(const ortx_api_t *api, unsigned reg) {
    uint8_t v;
    return c6_xfer(api, C6_CFG, reg, 1, 0, 0, &v, 1) ? 0x100 : v;
}

C6_FN void c6_w(const ortx_api_t *api, unsigned reg, unsigned val) {
    uint8_t v = (uint8_t)val;
    c6_xfer(api, C6_CFG, reg, 0, 0, &v, 0, 1);
}

/* registers a module changes, kept so it can put them back: regs[] names them (end with 0) */
typedef struct {
    uint8_t reg[40];
    uint8_t val[40];
    unsigned n;
} c6_saved;

C6_FN void c6_save(const ortx_api_t *api, c6_saved *s, const uint8_t *regs) {
    s->n = 0;
    for (; *regs && s->n < sizeof s->reg; regs++, s->n++) {
        s->reg[s->n] = *regs;
        s->val[s->n] = api->c6000_read(*regs);
    }
}

/* back in reverse order (a mode register written first comes back last) */
C6_FN void c6_restore(const ortx_api_t *api, const c6_saved *s) {
    for (unsigned i = s->n; i-- > 0;)
        api->c6000_write(s->reg[i], s->val[i]);
}

/* hex of n bytes into out (2n+1 chars) */
C6_FN void c6_hex(char *out, const uint8_t *b, unsigned n) {
    const char *d = "0123456789abcdef";
    for (unsigned i = 0; i < n; i++) {
        out[2 * i] = d[b[i] >> 4];
        out[2 * i + 1] = d[b[i] & 15u];
    }
    out[2 * n] = 0;
}

#endif
