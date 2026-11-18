/* c6_core: HR_C6000 profiles and transmit engines (c6_core.h).
   SPDX-License-Identifier: MIT */
#include "c6_core.h"

/* A profile is one flat byte array (no pointers, so -fropi RAM modules can hold it):
     nsave, the registers to save (put back in reverse order),
     nw, then nw writes of (reg, val, how): how 0 writes val, how C6C_A_* writes args[how],
     how OR_SAVED writes the register's value when the profile started | val. */
#define OR_SAVED 0x40u

/* The physical layer mode (RadioFoundry modem-hrc6000.md 9.1-9.2, MIT; on air from the RAM
   modules c6phytx 11-16, c6m17tx 11-17, c6ysftx 11-18): the FM chain off, two-point modulation
   at the scaled DMR pair, 0x10 = 0x66 before 0x40 = 0xE3 (the engine writes 0x40). */
const uint8_t C6C_FSK4_TX[] = {
    12, 0x10, 0x01, 0x11, 0x20, 0x21, 0x45, 0x46, 0x60, 0x5F, 0x81, 0x40, 0x41,
    12,
    0x60, 0x00, 0,                      /* the FM chain off: the modem modulates */
    0x01, 0xF0, 0,                      /* two-point TX */
    0x45, 0x00, C6C_A_I,
    0x46, 0x00, C6C_A_Q,
    0x10, 0x00, C6C_A_MODE,             /* before 0x40 = 0xE3 */
    0x11, 0x80, 0,
    0x20, 0x00, 0,
    0x21, 0x02, 0,
    0x5F, 0xF0, 0,
    0x81, 0x1F, 0,
    0x83, 0xFF, 0,
    0x41, 0x00, 0,
};

/* A DMR voice call with the payload over V_SPI (c6dvtx, 11-17: 60/60 blocks bit exact): DMR
   Tier II layer 2, the colour code, vocoder select 0x21 (voice over V_SPI under MCU control),
   impolite access, the encode buffer cleared; the engine writes the LC, 0x22 and 0x40. */
const uint8_t C6C_DMR_VOICE_TX[] = {
    16, 0x10, 0x01, 0x11, 0x1F, 0x20, 0x21, 0x45, 0x46, 0x60, 0x5F, 0x81, 0x26, 0x50, 0x40, 0x41, 0x06,
    14,
    0x60, 0x00, 0,
    0x01, 0xF0, 0,
    0x45, 0x00, C6C_A_I,
    0x46, 0x00, C6C_A_Q,
    0x10, 0x6E, 0,                      /* DMR, Tier II, layer 2 */
    0x11, 0x80, 0,
    0x1F, 0x00, C6C_A_CC,
    0x06, 0x21, 0,                      /* voice over V_SPI under MCU control */
    0x20, 0x00, 0,                      /* impolite: send whatever is on the channel */
    0x21, 0x02, 0,                      /* and clear the encode buffer */
    0x5F, 0xF0, 0,
    0x81, 0x1F, 0,
    0x83, 0xFF, 0,
    0x41, 0x00, 0,
};

/* The 8 kHz FM tap for software 4FSK receive (c6m17rx, 11-18: M17 0 bits wrong to -65 dB):
   FM receive, the FM chain's own output off, receive audio on, the RX RAM's half flags
   (0x81 bit 1), the tap filter 0x34 (0x1C: flat 10 Hz-3.4 kHz, DC coupled). The AT1846S's DMR
   receive set and RX_AUDIO_MUX (PD9) are not the C6000's: the receiver sets those. */
const uint8_t C6C_FMTAP_RX[] = {
    5, 0x10, 0x60, 0x26, 0x81, 0x34,
    6,
    0x10, 0x80, 0,
    0x60, 0x00, 0,
    0x26, 0xFD, 0,
    0x81, 0x02, OR_SAVED,
    0x34, 0x00, C6C_A_V34,
    0x83, 0xFF, 0,
};

void c6c_apply(const c6c_bus *b, const uint8_t *p, const uint8_t args[C6C_A_N], c6c_saved *sv) {
    unsigned ns = p[0];
    sv->n = 0;
    for (unsigned i = 0; i < ns && sv->n < C6C_SAVE_MAX; i++, sv->n++) {
        sv->reg[sv->n] = p[1 + i];
        sv->val[sv->n] = b->rd(b->ctx, p[1 + i]);
    }
    const uint8_t *w = p + 1 + ns;
    unsigned nw = *w++;
    for (unsigned i = 0; i < nw; i++, w += 3) {
        uint8_t reg = w[0], val = w[1], how = w[2];
        if (how == OR_SAVED) {
            uint8_t old = 0;
            unsigned k = 0;
            for (; k < sv->n && sv->reg[k] != reg; k++) {
            }
            old = k < sv->n ? sv->val[k] : b->rd(b->ctx, reg);
            val = (uint8_t)(old | val);
        } else if (how && how < C6C_A_N) {
            val = args[how];
        }
        b->wr(b->ctx, reg, val);
    }
}

void c6c_restore(const c6c_bus *b, const c6c_saved *sv) {
    for (unsigned i = sv->n; i-- > 0;)
        b->wr(b->ctx, sv->reg[i], sv->val[i]);
}

uint8_t c6c_dev(unsigned cal, unsigned pct) {
    unsigned v = cal * pct / 100u;
    return (uint8_t)(v < 0x08u ? 0x08u : v > 0xF8u ? 0xF8u : v);
}

/* ---- the 4FSK stream ---- */

static void fill(uint8_t *s, uint8_t a, uint8_t b) {
    for (unsigned i = 0; i < C6C_SLOT; i += 2u) {
        s[i] = a;
        s[i + 1u] = b;
    }
}

static int put_half(const c6c_bus *b, const uint8_t *s, unsigned half) {
    return b->ram_wr(b->ctx, C6C_SPACE_TXRAM, half ? 0x24u : 0x00u, s, C6C_SLOT);
}

static unsigned same(const uint8_t *a, const uint8_t *b) {
    unsigned n = 0;
    while (n < C6C_SLOT && a[n] == b[n])
        n++;
    return n;
}

static void zero_stats(c6c_stats *st) {
    uint8_t *p = (uint8_t *)st;
    for (unsigned i = 0; i < sizeof *st; i++)
        p[i] = 0;
}

int c6c_fsk4_tx(const c6c_bus *b, const c6c_fsk4 *p, c6c_pull pull, void *src, c6c_stats *st) {
    uint8_t args[C6C_A_N] = {0};
    uint8_t slot[C6C_SLOT];
    c6c_saved sv;
    zero_stats(st);
    args[C6C_A_I] = p->i45;
    args[C6C_A_Q] = p->q46;
    args[C6C_A_MODE] = p->mode;
    c6c_apply(b, C6C_FSK4_TX, args, &sv);

    for (unsigned i = 0; i < C6C_SLOT; i++)
        slot[i] = p->fill;
    int err = put_half(b, slot, 0) | put_half(b, slot, 1);   /* the preamble until our first slot */
    b->edges(b->ctx);                                       /* old edges out */
    b->wr(b->ctx, 0x40, 0xE3);                              /* TX and RX, active timing */

    unsigned dry = 0;
    uint32_t t0 = b->ms(b->ctx), last = 0;
    while (!b->stop(b->ctx)) {
        uint32_t now = b->ms(b->ctx);
        unsigned e = b->edges(b->ctx);
        st->rftx += (e & C6C_EDGE_RFTX) != 0;
        if (e & C6C_EDGE_SLOT) {
            if (st->edges == 0)
                st->first_ms = now - t0;
            else if (now - last > C6C_SLOT_MS + 3u) {
                st->late++;
                st->lost += (now - last + C6C_SLOT_MS / 2u) / C6C_SLOT_MS - 1u;
            }
            st->edges++;
            last = now;
            if (dry && ++st->tail > 2u) {
                st->done = 1;
                break;                                      /* the data and two pad slots are out */
            }
            if (now + C6C_SLOT_MS >= p->t_end)
                break;
            unsigned got = dry ? 0u : pull(src, slot, C6C_SLOT);
            st->bytes += got;
            if (got < C6C_SLOT) {
                dry = 1;
                for (unsigned i = got; i < C6C_SLOT; i++)
                    slot[i] = ((i - got) & 1u) ? p->pad1 : p->pad0;
            }
            err |= put_half(b, slot, (st->edges - 1u + p->half) & 1u);
            b->wr(b->ctx, 0x41, 0x80);                      /* send the next slot */
            st->slots++;
            if (st->edges == 1u && b->ram_rd) {
                uint8_t back[C6C_SLOT];
                if (!b->ram_rd(b->ctx, C6C_SPACE_TXRAM_R, 0x00, back, C6C_SLOT))
                    st->chk0 = same(back, slot);
                if (!b->ram_rd(b->ctx, C6C_SPACE_TXRAM_R, 0x24, back, C6C_SLOT))
                    st->chk1 = same(back, slot);
            }
        }
        if (now - t0 > 400u && st->edges == 0)
            break;                                          /* no slot clock on PC0 */
        if (now >= p->t_end)
            break;
        b->sleep_ms(b->ctx, 1);
    }

    fill(slot, p->pad0, p->pad1);                           /* pad in both halves for the end */
    put_half(b, slot, 0);
    put_half(b, slot, 1);
    b->wr(b->ctx, 0x41, 0x00);
    b->wr(b->ctx, 0x40, 0x43);
    c6c_restore(b, &sv);
    b->wr(b->ctx, 0x83, 0xFF);
    st->spi_err = err != 0;
    return st->done && !st->late && !err ? 0 : -1;
}

/* ---- the DMR voice call ---- */

static void put_voice(const c6c_bus *b, const uint8_t *pay) {
    if (b->vspi)
        b->vspi(b->ctx, pay, C6C_DV_BYTES);
    else
        b->ram_wr(b->ctx, C6C_SPACE_SOUND, 0x00, pay, C6C_DV_BYTES);
}

int c6c_dmr_tx(const c6c_bus *b, const c6c_dmr *p, c6c_voice voice, void *src, c6c_stats *st) {
    uint8_t args[C6C_A_N] = {0};
    c6c_saved sv;
    zero_stats(st);
    args[C6C_A_I] = p->i45;
    args[C6C_A_Q] = p->q46;
    args[C6C_A_CC] = (uint8_t)(p->cc << 4);
    c6c_apply(b, C6C_DMR_VOICE_TX, args, &sv);
    {
        /* the call's LC: group call (FLCO 0), FID 0, no options; the chip adds the RS parity */
        uint8_t lc[12] = {0, 0, 0, (uint8_t)(p->dst >> 16), (uint8_t)(p->dst >> 8), (uint8_t)p->dst,
                          (uint8_t)(p->radio >> 16), (uint8_t)(p->radio >> 8), (uint8_t)p->radio, 0, 0, 0};
        b->ram_wr(b->ctx, C6C_SPACE_TXRAM, 0x00, lc, 12);
    }
    b->wr(b->ctx, 0x22, p->enc);
    b->edges(b->ctx);
    b->wr(b->ctx, 0x40, 0xE3);

    unsigned step = 0, ours = 1, quiet = 0, nsteps = p->nhdr + p->nvoice + 1u;
    uint8_t pay[C6C_DV_BYTES];
    uint32_t t0 = b->ms(b->ctx), last = 0;
    while (!b->stop(b->ctx)) {
        uint32_t now = b->ms(b->ctx);
        unsigned e = b->edges(b->ctx);
        st->rftx += (e & C6C_EDGE_RFTX) != 0;
        uint8_t f = b->rd(b->ctx, 0x82);
        if (f) {
            st->f82 |= f;
            b->wr(b->ctx, 0x83, f);
        }
        if (e & C6C_EDGE_SLOT) {
            if (st->edges == 0)
                st->first_ms = now - t0;
            else if (now - last > 33u)
                st->late++;
            st->edges++;
            last = now;
            ours ^= 1u;
            if (!ours) {
                b->wr(b->ctx, 0x41, 0x00);
            } else if (step < nsteps && now + 60u < p->t_end) {
                if (step < p->nhdr) {
                    b->wr(b->ctx, 0x50, 0x10);              /* voice LC header */
                    st->hdr++;
                } else if (step < p->nhdr + p->nvoice) {
                    unsigned k = step - p->nhdr;
                    voice(src, k, pay);
                    put_voice(b, pay);
                    b->wr(b->ctx, 0x50, (uint8_t)(0x08u | ((k % 6u) << 4)));
                    st->voice++;
                } else {
                    for (unsigned i = 0; i < C6C_DV_BYTES; i++)
                        pay[i] = 0;
                    put_voice(b, pay);
                    b->wr(b->ctx, 0x50, 0x20);              /* terminator with LC */
                    st->term++;
                }
                b->wr(b->ctx, 0x41, 0x80);
                step++;
            } else {
                b->wr(b->ctx, 0x41, 0x00);
                if (++quiet >= 2u) {
                    st->done = step >= nsteps;
                    break;                                  /* the last burst is out */
                }
            }
        }
        if (now - t0 > 400u && st->edges == 0)
            break;
        if (now >= p->t_end)
            break;
        b->sleep_ms(b->ctx, 1);
    }

    b->wr(b->ctx, 0x41, 0x00);
    b->wr(b->ctx, 0x22, 0x40);                              /* encode stop */
    b->wr(b->ctx, 0x40, 0x43);
    c6c_restore(b, &sv);
    b->wr(b->ctx, 0x83, 0xFF);
    return st->done && !st->late ? 0 : -1;
}
