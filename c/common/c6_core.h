/* c6_core: one HR_C6000 layer for every digital mode on the UV-380 (docs/uv380-multimode.md):
   the chip set-ups as const profile tables (Jeff 11-18: "initializer routines that live on
   flash"), apply and restore, and two transmit engines: fsk4_tx for every 4800 symbol/s 4FSK
   stream through the chip's physical layer mode (M17, YSF, the K6's flat FSK), and dmr_tx for a
   DMR voice call whose bursts the caller fills (sotto blocks, dmr_sotto.h).

   Portable C with no OpenRTX in it: the chip is reached through a c6c_bus the caller fills in.
   The image fills it from OpenRTX's own HR_C6000 driver and chip-select lock; a RAM module fills
   it from c6spi.h / c6slot.h / c6vspi.h (mod/c6bus_mod.h); the host tests fill it with a fake
   chip (tests/test_uv380_c6core.py). The engines are the loops the RAM modules proved on air
   (c6m17tx, c6ysftx 11-17/18: 0 bits wrong; c6dvtx 11-17: 60/60 blocks), moved here unchanged.

   Profiles hold no pointers (RAM modules are built -fropi): a profile is one flat const byte
   array, see c6_core.c. Nothing here keys the radio: the caller keys (the Morse ident AG7EW
   first, under the transmit guard) and gives the engine the moment it must be done by.
   SPDX-License-Identifier: MIT */
#ifndef C6_CORE_H
#define C6_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define C6C_EDGE_SLOT 1u                /* PC0 TIME_SLOT_INTER: a 30 ms slot boundary */
#define C6C_EDGE_SYS 2u                 /* PC1 SYS_INTER */
#define C6C_EDGE_RFTX 4u                /* PC2 RF_TX_INTER */

#define C6C_SPACE_TXRAM 2u              /* write: TX RAM */
#define C6C_SPACE_SOUND 3u              /* page 3 */
#define C6C_SPACE_TXRAM_R 6u            /* read: TX RAM */

#define C6C_SLOT 36u                    /* bytes a TX RAM half: one 30 ms slot of 4FSK */
#define C6C_SLOT_MS 30u
#define C6C_DV_BYTES 27u                /* a DMR voice burst's payload */

typedef struct {
    void *ctx;
    void (*wr)(void *ctx, uint8_t reg, uint8_t val);              /* configuration register */
    uint8_t (*rd)(void *ctx, uint8_t reg);
    /* n bytes into a space (C6C_SPACE_*) from addr: 0, or -1 (the bus was busy) */
    int (*ram_wr)(void *ctx, unsigned space, unsigned addr, const uint8_t *d, unsigned n);
    /* read back (NULL: the engines skip the read-back check) */
    int (*ram_rd)(void *ctx, unsigned space, unsigned addr, uint8_t *d, unsigned n);
    /* a DMR voice burst's 27 bytes over V_SPI (NULL: page 3 over the control port instead) */
    int (*vspi)(void *ctx, const uint8_t *d, unsigned n);
    unsigned (*edges)(void *ctx);       /* C6C_EDGE_* seen since the last call */
    uint32_t (*ms)(void *ctx);
    void (*sleep_ms)(void *ctx, unsigned ms);
    int (*stop)(void *ctx);             /* 1: end now (PTT, mode change, Ctrl-C) */
} c6c_bus;

/* ---- profiles ---- */

/* the values a profile takes at run time */
enum {
    C6C_A_I = 1,                        /* 0x45: the DMR modulation pair, scaled */
    C6C_A_Q,                            /* 0x46 */
    C6C_A_MODE,                         /* 0x10 */
    C6C_A_CC,                           /* 0x1F: colour code << 4 */
    C6C_A_ENC,                          /* 0x22: vocoder encode start */
    C6C_A_V34,                          /* 0x34: the FM tap's filter */
    C6C_A_N
};

#define C6C_SAVE_MAX 20u
typedef struct {
    uint8_t reg[C6C_SAVE_MAX], val[C6C_SAVE_MAX];
    unsigned n;
} c6c_saved;

/* the profiles (c6_core.c has each one's registers and where they were proven) */
extern const uint8_t C6C_FSK4_TX[];     /* physical layer mode: TX RAM as 4FSK */
extern const uint8_t C6C_DMR_VOICE_TX[];/* DMR voice call, payload over V_SPI */
extern const uint8_t C6C_FMTAP_RX[];    /* the 8 kHz FM tap for software 4FSK receive */

/* saves the profile's registers into sv, then writes its set-up; args[C6C_A_*] */
void c6c_apply(const c6c_bus *b, const uint8_t *profile, const uint8_t args[C6C_A_N],
               c6c_saved *sv);
/* writes the saved registers back, in reverse order */
void c6c_restore(const c6c_bus *b, const c6c_saved *sv);
/* a calibration gain times pct percent, kept within 0x08-0xF8 */
uint8_t c6c_dev(unsigned cal, unsigned pct);

/* ---- transmit engines ---- */

typedef struct {
    unsigned edges, slots, late, lost, rftx, tail, spi_err;
    unsigned chk0, chk1;                /* first slot: bytes of each half that read back */
    uint32_t first_ms, bytes;           /* first edge after start; bytes the source gave */
    uint8_t f82;                        /* 0x82 flags seen (dmr_tx) */
    unsigned hdr, voice, term;          /* dmr_tx bursts */
    int done;                           /* the source ran dry and its tail went out */
} c6c_stats;

/* the next bytes of the transmission: how many (0: it is all out) */
typedef unsigned (*c6c_pull)(void *src, uint8_t *out, unsigned max);

typedef struct {
    uint8_t i45, q46;                   /* scaled modulation pair (c6c_dev) */
    uint8_t mode;                       /* 0x10: 0x66 */
    uint8_t fill;                       /* both halves before the first slot: the preamble */
    uint8_t pad0, pad1;                 /* after the end, alternating (M17 EOT 55 5D, YSF 77 77) */
    uint8_t half;                       /* TX_HALF: which half the first edge writes (0) */
    uint32_t t_end;                     /* bus ms: stop writing slots by then */
} c6c_fsk4;

/* the 4FSK stream (c6m17tx / c6ysftx's loop): profile C6C_FSK4_TX, both halves fill, then at
   each slot edge the next 36 bytes into the half the chip isn't sending, until the source is
   dry and two pad slots are out, t_end, stop(), or 400 ms with no edge. The registers are put
   back before it returns. 0 if the whole source went out with no late slot, else -1. */
int c6c_fsk4_tx(const c6c_bus *b, const c6c_fsk4 *p, c6c_pull pull, void *src, c6c_stats *st);

/* a voice burst's 27 bytes: burst k of n */
typedef void (*c6c_voice)(void *src, unsigned k, uint8_t pay[C6C_DV_BYTES]);

typedef struct {
    uint8_t i45, q46;
    uint8_t cc;                         /* colour code */
    uint8_t enc;                        /* 0x22 encode start: 0x86 */
    uint32_t dst, radio;                /* the LC: group call to dst from radio (24 bits) */
    unsigned nhdr, nvoice;              /* LC headers, voice bursts (whole superframes of 6) */
    uint32_t t_end;
} c6c_dmr;

/* the DMR voice call (c6dvtx's loop): profile C6C_DMR_VOICE_TX, the LC in page 2, then on every
   other slot edge a header, a voice burst (voice() fills it) or the terminator. 0 if every
   burst went out, else -1. */
int c6c_dmr_tx(const c6c_bus *b, const c6c_dmr *p, c6c_voice voice, void *src, c6c_stats *st);

#ifdef __cplusplus
}
#endif

#endif
