/* at1846.h: AT1846S registers from a RAM module. On the MD-UV380 the AT1846S hangs on a bit-banged
   I2C (SCL PA8, SDA PC9, address 0x5C/0x5D), which OpenRTX drives with no lock
   (AT1846S_UV3x0.cpp) and leaves idle with SCL high and SDA an output, high. A transfer here runs
   with interrupts off, only after both lines have read idle for about 20 us with SDA an output
   (so never inside one of OpenRTX's transfers, which turn SDA into an input for every ACK);
   otherwise it sleeps 1 ms and tries again.

   What modules use it for: DMR receive needs the AT1846S's raw FM discriminator output (no
   voice filters, no de-emphasis, low frequencies passed), which OpenRTX's FM set-up filters
   (0x58 = 0x9C05, 0x40 = 0x0030). at_dmr_rx() writes RadioFoundry's DMR mode values
   (docs/spec/hardware-dm1701.md 5.5, MIT) after saving what was there; at_restore() puts it back.
   It never touches the frequency, TX or power registers.
   SPDX-License-Identifier: MIT */
#ifndef AT1846_H
#define AT1846_H

#include "ortx_mod.h"

#define AT_FN static __attribute__((unused))

#define AT_REG(a) (*(volatile uint32_t *)(a))
#define AT_GPIOA 0x40020000u
#define AT_GPIOC 0x40020800u
#define AT_SCL 8u                       /* PA8 */
#define AT_SDA 9u                       /* PC9 */
#define AT_ADDR 0x5Cu

AT_FN inline uint32_t at_irq_off(void) {
    uint32_t m;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(m)::"memory");
    return m;
}

AT_FN inline void at_irq_on(uint32_t m) {
    __asm volatile("msr primask, %0" ::"r"(m) : "memory");
}

AT_FN inline void at_wait(unsigned n) {
    for (volatile unsigned i = 0; i < n; i++) {
    }
}

#define AT_HALF 60u                     /* about 2.5 us at 168 MHz */

AT_FN void at_scl(int v) {
    AT_REG(AT_GPIOA + 0x18u) = v ? (1u << AT_SCL) : (1u << (AT_SCL + 16u));
}

AT_FN void at_sda(int v) {
    AT_REG(AT_GPIOC + 0x18u) = v ? (1u << AT_SDA) : (1u << (AT_SDA + 16u));
}

/* SDA's mode: 1 output, 0 input with pull-up (interrupts are already off here) */
AT_FN void at_sda_out(int out) {
    uint32_t mo = AT_REG(AT_GPIOC + 0x00u) & ~(3u << (2 * AT_SDA));
    if (!out) {
        uint32_t pu = AT_REG(AT_GPIOC + 0x0Cu) & ~(3u << (2 * AT_SDA));
        AT_REG(AT_GPIOC + 0x0Cu) = pu | (1u << (2 * AT_SDA));
    }
    AT_REG(AT_GPIOC + 0x00u) = mo | ((out ? 1u : 0u) << (2 * AT_SDA));
}

AT_FN int at_sda_in(void) {
    return (int)((AT_REG(AT_GPIOC + 0x10u) >> AT_SDA) & 1u);
}

AT_FN int at_idle(void) {
    for (unsigned i = 0; i < 8; i++) {
        if (((AT_REG(AT_GPIOA + 0x14u) >> AT_SCL) & 1u) == 0 ||
            ((AT_REG(AT_GPIOA + 0x10u) >> AT_SCL) & 1u) == 0 ||
            ((AT_REG(AT_GPIOC + 0x00u) >> (2 * AT_SDA)) & 3u) != 1u ||
            ((AT_REG(AT_GPIOC + 0x14u) >> AT_SDA) & 1u) == 0)
            return 0;
        at_wait(AT_HALF);
    }
    return 1;
}

AT_FN void at_start(void) {
    at_sda_out(1);
    at_sda(1);
    at_wait(AT_HALF);
    at_scl(1);
    at_wait(AT_HALF);
    at_sda(0);
    at_wait(AT_HALF);
    at_scl(0);
    at_wait(AT_HALF);
}

AT_FN void at_stop(void) {
    at_sda_out(1);
    at_scl(0);
    at_sda(0);
    at_wait(AT_HALF);
    at_scl(1);
    at_wait(AT_HALF);
    at_sda(1);                          /* idle as OpenRTX leaves it: SCL high, SDA output high */
    at_wait(AT_HALF);
}

/* a byte out, then the slave's ACK (0) or NACK (1) */
AT_FN int at_put(uint8_t v) {
    at_sda_out(1);
    for (unsigned b = 0; b < 8; b++) {
        at_scl(0);
        at_sda((v >> (7 - b)) & 1u);
        at_wait(AT_HALF);
        at_scl(1);
        at_wait(AT_HALF);
    }
    at_scl(0);
    at_sda_out(0);
    at_wait(AT_HALF);
    at_scl(1);
    at_wait(AT_HALF);
    int nack = at_sda_in();
    at_scl(0);
    at_sda(1);
    at_sda_out(1);
    at_wait(AT_HALF);
    return nack;
}

AT_FN uint8_t at_get(int ack) {
    uint8_t v = 0;
    at_sda_out(0);
    for (unsigned b = 0; b < 8; b++) {
        at_scl(0);
        at_wait(AT_HALF);
        at_scl(1);
        at_wait(AT_HALF);
        v = (uint8_t)((v << 1) | at_sda_in());
    }
    at_scl(0);
    at_sda(ack ? 0 : 1);
    at_sda_out(1);
    at_wait(AT_HALF);
    at_scl(1);
    at_wait(AT_HALF);
    at_scl(0);
    at_wait(AT_HALF);
    return v;
}

/* one register: write (rd 0) or read (rd 1, into *val). 0, -1 (bus never idle) or -2 (NACK) */
AT_FN int at_xfer(const ortx_api_t *api, uint8_t reg, uint16_t *val, int rd) {
    for (unsigned t = 0; t < 50u; t++) {
        uint32_t m = at_irq_off();
        if (!at_idle()) {
            at_irq_on(m);
            api->sleep_ms(1);
            continue;
        }
        int nack = 0;
        at_start();
        nack |= at_put(AT_ADDR);
        nack |= at_put(reg);
        if (rd) {
            at_start();
            nack |= at_put(AT_ADDR | 1u);
            uint8_t hi = at_get(1), lo = at_get(0);
            *val = (uint16_t)((hi << 8) | lo);
        } else {
            nack |= at_put((uint8_t)(*val >> 8));
            nack |= at_put((uint8_t)*val);
        }
        at_stop();
        at_irq_on(m);
        return nack ? -2 : 0;
    }
    return -1;
}

AT_FN int at_read(const ortx_api_t *api, uint8_t reg, uint16_t *val) {
    return at_xfer(api, reg, val, 1);
}

AT_FN int at_write(const ortx_api_t *api, uint8_t reg, uint16_t val) {
    return at_xfer(api, reg, &val, 0);
}

/* RadioFoundry's DMR receive set (hardware-dm1701.md 5.5, the MD-UV380 has the same chips):
   register, value; `full` 0 writes only the three that shape the audio (filters off, LF pass,
   AF gain), 1 all of them */
typedef struct {
    uint8_t reg[12];
    uint16_t val[12];
    unsigned n;
    int ok;
} at_saved;

AT_FN unsigned at_dmr_set(unsigned i, uint8_t *reg, uint16_t *val) {
    switch (i) {
    case 0: *reg = 0x58, *val = 0x9CDD; return 1;   /* every audio filter off */
    case 1: *reg = 0x40, *val = 0x0031; return 1;   /* bit 0: pass low frequencies (4FSK) */
    case 2: *reg = 0x44, *val = 0x07FF; return 1;   /* AF gain 100 % */
    case 3: *reg = 0x15, *val = 0x1100; return 1;   /* IF tuning */
    case 4: *reg = 0x32, *val = 0x4495; return 1;   /* AGC target */
    case 5: *reg = 0x3C, *val = 0x1B34; return 1;   /* pk_det_th */
    case 6: *reg = 0x3F, *val = 0x29D1; return 1;   /* rssi3_th */
    case 7: *reg = 0x48, *val = 0x19B1; return 1;   /* noise1_th */
    default: return 0;
    }
}

/* the DMR receive values in (3 registers, or 8 with full), the old ones kept in *s: 0 or -1 */
AT_FN int at_dmr_rx(const ortx_api_t *api, at_saved *s, int full) {
    s->n = 0;
    s->ok = 0;
    uint8_t r;
    uint16_t v, old;
    for (unsigned i = 0; at_dmr_set(i, &r, &v) && i < (full ? 8u : 3u); i++) {
        if (at_read(api, r, &old))
            return -1;
        s->reg[s->n] = r, s->val[s->n] = old, s->n++;
    }
    s->ok = 1;
    for (unsigned i = 0; i < s->n; i++) {
        at_dmr_set(i, &r, &v);
        if (at_write(api, r, v))
            return -1;
    }
    return 0;
}

/* The 12.5 kHz receive set (the AT1846S's narrow mode: 0x30 bits 13-12 = 00 and its IF and
   AGC values, as OpenRTX's setBandwidth(_12P5) and RadioFoundry's DMR set write them). PAGER and
   FM leave the AT1846S in 25 kHz mode (the default channel), where its discriminator gives about
   half the level per Hz: the C6000's DMR sync search needs an absolute level, and at standard
   DMR deviation (+-1944 Hz) it never synced (uvdmr-sweep1, 11-19 07:46-07:53Z: only a send at
   1.5x the deviation decoded). at_bw12_restore puts it back, 0x30 by its two bits only (OpenRTX
   owns the rest: RX / TX on). */
typedef struct {
    uint16_t val[9];
    uint16_t bw30;                      /* 0x30 bits 13-12 as they were */
    unsigned n;
    int ok;
} at_bw_saved;

AT_FN unsigned at_bw12_set(unsigned i, uint8_t *reg, uint16_t *val) {
    switch (i) {
    case 0: *reg = 0x15, *val = 0x1100; return 1;   /* IF tuning */
    case 1: *reg = 0x32, *val = 0x4495; return 1;   /* AGC target */
    case 2: *reg = 0x3A, *val = 0x00C3; return 1;
    case 3: *reg = 0x3C, *val = 0x1B34; return 1;   /* pk_det_th */
    case 4: *reg = 0x3F, *val = 0x29D1; return 1;   /* rssi3_th */
    case 5: *reg = 0x48, *val = 0x19B1; return 1;   /* noise1_th */
    case 6: *reg = 0x62, *val = 0x1425; return 1;
    case 7: *reg = 0x65, *val = 0x2494; return 1;
    case 8: *reg = 0x66, *val = 0xEB2E; return 1;
    default: return 0;
    }
}

/* 0 or -1; what it changed is in *b either way */
AT_FN int at_bw12(const ortx_api_t *api, at_bw_saved *b) {
    uint8_t r;
    uint16_t v, old;
    b->n = 0;
    b->ok = 0;
    if (at_read(api, 0x30, &old))
        return -1;
    b->bw30 = old & 0x3000u;
    for (unsigned i = 0; at_bw12_set(i, &r, &v); i++) {
        if (at_read(api, r, &b->val[i]))
            return -1;
        b->n = i + 1u;
        if (at_write(api, r, v))
            return -1;
    }
    b->ok = 1;
    return at_write(api, 0x30, (uint16_t)(old & ~0x3000u));
}

AT_FN void at_bw12_restore(const ortx_api_t *api, const at_bw_saved *b) {
    uint8_t r;
    uint16_t v;
    if (b->ok && at_read(api, 0x30, &v) == 0)
        at_write(api, 0x30, (uint16_t)((v & ~0x3000u) | b->bw30));
    for (unsigned i = b->n; i-- > 0;) {
        at_bw12_set(i, &r, &v);
        at_write(api, r, b->val[i]);
    }
}

AT_FN void at_restore(const ortx_api_t *api, const at_saved *s) {
    if (!s->ok)
        return;
    for (unsigned i = s->n; i-- > 0;)
        at_write(api, s->reg[i], s->val[i]);
}

#endif
