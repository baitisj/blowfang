/* c6core_host: c6_core's engines against a fake HR_C6000, for tests/test_uv380_c6core.py.

   The fake: 256 configuration registers (each starts as reg ^ 0x5A, so a register the engine
   fails to put back shows), a 72-byte TX RAM (halves 0x00 and 0x24), and a slot clock that
   starts 7 ms after 0x40 = 0xE3 and ticks every 30 ms. At each slot edge the chip starts
   sending the half the engine isn't meant to write (edge e: half e & 1), so the "air" is that
   half's bytes, slot after slot: what the RAM modules found on the real chip (11-16 run 3).

     c6core_host fsk4 m17 SRC < SOTTO     the M17 sotto stream: air bytes on stdout
     c6core_host fsk4 ysf SRC < SOTTO     the YSF sotto stream
     c6core_host fsk4 m17 SRC T_END_MS    with an early deadline
     c6core_host dmr NVOICE               a DMR call: one line per burst on stdout
   Lines on stderr: stats, then "changed" and every register not as it started.
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "c6_core.h"
#include "m17_tx.h"
#include "ysf_tx.h"

typedef struct {
    uint8_t reg[256], ram[72], last_vspi[C6C_DV_BYTES];
    uint32_t now, start;                /* ms; slot clock start (0: off) */
    unsigned edge, pending, vspi_n;
    int dmr;
    uint8_t air[1u << 16];
    unsigned nair;
} fake;

static void tick(fake *f) {
    f->now++;
    if (!f->start || f->now < f->start)
        return;
    if ((f->now - f->start) % C6C_SLOT_MS)
        return;
    f->edge++;
    f->pending |= C6C_EDGE_SLOT;
    if (f->dmr) {
        if (f->reg[0x41] & 0x80u) {
            printf("burst %u 50=%02x pay=", f->edge, f->reg[0x50]);
            for (unsigned i = 0; i < C6C_DV_BYTES; i++)
                printf("%02x", f->last_vspi[i]);
            printf("\n");
        }
    } else if (f->reg[0x40] == 0xE3 && f->nair + C6C_SLOT <= sizeof f->air) {
        memcpy(f->air + f->nair, f->ram + ((f->edge & 1u) ? 0x24u : 0u), C6C_SLOT);
        f->nair += C6C_SLOT;
    }
}

static void wr(void *c, uint8_t r, uint8_t v) {
    fake *f = c;
    if (r == 0x83) {
        f->reg[0x82] &= (uint8_t)~v;    /* write 1 to clear */
        return;
    }
    f->reg[r] = v;
    if (r == 0x40) {
        if (v == 0xE3 && !f->start)
            f->start = f->now + 7u;
        else if (v != 0xE3)
            f->start = 0;
    }
}

static uint8_t rd(void *c, uint8_t r) {
    return ((fake *)c)->reg[r];
}

static int ram_wr(void *c, unsigned space, unsigned addr, const uint8_t *d, unsigned n) {
    fake *f = c;
    if (space == C6C_SPACE_TXRAM && addr + n <= sizeof f->ram)
        memcpy(f->ram + addr, d, n);
    return 0;
}

static int ram_rd(void *c, unsigned space, unsigned addr, uint8_t *d, unsigned n) {
    fake *f = c;
    if (space != C6C_SPACE_TXRAM_R || addr + n > sizeof f->ram)
        return -1;
    memcpy(d, f->ram + addr, n);
    return 0;
}

static int vspi(void *c, const uint8_t *d, unsigned n) {
    fake *f = c;
    memcpy(f->last_vspi, d, n);
    f->vspi_n++;
    return 0;
}

static unsigned edges(void *c) {
    fake *f = c;
    unsigned e = f->pending;
    f->pending = 0;
    return e;
}

static uint32_t ms(void *c) {
    return ((fake *)c)->now;
}

static void sleep_ms(void *c, unsigned n) {
    for (unsigned i = 0; i < n; i++)
        tick(c);
}

static int stop(void *c) {
    (void)c;
    return 0;
}

static fake F;
static uint8_t in[1u << 16];

static unsigned m17_pull(void *s, uint8_t *o, unsigned n) {
    return m17t_sotto_read(s, o, n);
}

static unsigned ysf_pull(void *s, uint8_t *o, unsigned n) {
    return ysft_sotto_read(s, o, n);
}

static void voice(void *s, unsigned k, uint8_t pay[C6C_DV_BYTES]) {
    (void)s;
    for (unsigned i = 0; i < C6C_DV_BYTES; i++)
        pay[i] = (uint8_t)(k * 16u + i);
}

static void report(const c6c_stats *st, int r) {
    fprintf(stderr, "r=%d edges=%u slots=%u late=%u lost=%u tail=%u bytes=%lu chk=%u/%u done=%d hdr=%u voice=%u term=%u\n",
            r, st->edges, st->slots, st->late, st->lost, st->tail, (unsigned long)st->bytes,
            st->chk0, st->chk1, st->done, st->hdr, st->voice, st->term);
    fprintf(stderr, "changed");
    for (unsigned i = 0; i < 256u; i++)
        if (F.reg[i] != (uint8_t)(i ^ 0x5Au))
            fprintf(stderr, " %02x=%02x", i, F.reg[i]);
    fprintf(stderr, "\n");
}

int main(int argc, char **argv) {
    for (unsigned i = 0; i < 256u; i++)
        F.reg[i] = (uint8_t)(i ^ 0x5Au);
    F.now = 1000;
    c6c_bus b = {&F, wr, rd, ram_wr, ram_rd, vspi, edges, ms, sleep_ms, stop};
    c6c_stats st;
    if (argc >= 4 && !strcmp(argv[1], "fsk4")) {
        unsigned n = (unsigned)fread(in, 1, sizeof in, stdin) / 6u;
        c6c_fsk4 p = {c6c_dev(0x7F, 123), c6c_dev(0x36, 123), 0x66, 0x77, 0x55, 0x5D, 0, 0};
        p.t_end = F.now + (argc >= 5 ? (uint32_t)atoi(argv[4]) : 100000u);
        int r;
        if (!strcmp(argv[2], "m17")) {
            m17t_sotto s;
            if (m17t_sotto_begin(&s, in, n, argv[3], "", 0))
                return 2;
            r = c6c_fsk4_tx(&b, &p, m17_pull, &s, &st);
        } else {
            ysft_sotto s;
            p.i45 = c6c_dev(0x7F, 140);
            p.q46 = c6c_dev(0x36, 140);
            p.pad0 = p.pad1 = 0x77;
            if (ysft_sotto_begin(&s, in, n, "ALL", argv[3], 0))
                return 2;
            r = c6c_fsk4_tx(&b, &p, ysf_pull, &s, &st);
        }
        fwrite(F.air, 1, F.nair, stdout);
        report(&st, r);
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "dmr")) {
        F.dmr = 1;
        c6c_dmr p = {0x7F, 0x36, 1, 0x86, 9, 1, 3, (unsigned)atoi(argv[2]), F.now + 100000u};
        int r = c6c_dmr_tx(&b, &p, voice, NULL, &st);
        report(&st, r);
        return 0;
    }
    fprintf(stderr, "usage: see c6core_host.c\n");
    return 2;
}
