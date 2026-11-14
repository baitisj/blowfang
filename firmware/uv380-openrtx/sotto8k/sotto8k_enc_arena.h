/* The sotto 1200 encoder's state from a static arena, not the heap: the UV-380's heap (the
 * 128 KB RAM after the framebuffer and the probe's buffers) has about 40 KB left once the module
 * arena is taken, which the encoder's 24 KB and its thread's 16 KB stack overran (11-14 01:27Z:
 * the radio reset as usbenc started). The arena is in .bss, the 64 KB CCM on the radio, which
 * the encoder can use (no DMA reads it) and where about 37 KB was free. Included by the encoder
 * wrappers only (sotto8k_fx_enc.c, sotto8k_fx_lr_enc.c), before the sotto source, so their
 * calloc and free come here; the decoder keeps the heap. sotto8k_fx_lr_enc.c defines it.
 * SPDX-License-Identifier: MIT */
#ifndef SOTTO8K_ENC_ARENA_H
#define SOTTO8K_ENC_ARENA_H

#include <stddef.h>

/* the encoder with its core and FFT scratch on the M4: 2276 + 18020 + 4096 bytes (11-14) */
#ifndef SOTTO8K_ENC_ARENA
#define SOTTO8K_ENC_ARENA 25600u
#endif

void *sotto8k_enc_calloc(size_t n, size_t size);
void sotto8k_enc_free(void *p);

#ifdef SOTTO8K_ENC_ARENA_IMPL
#include <stdint.h>
#include <string.h>

static uint8_t enc_arena[SOTTO8K_ENC_ARENA] __attribute__((aligned(8)));
static unsigned enc_used, enc_live;

/* a bump allocator: everything goes back at once when the last block is freed (an encoder is
   made and destroyed whole) */
void *sotto8k_enc_calloc(size_t n, size_t size) {
    if (size && n > (size_t)-1 / size)
        return NULL;
    size_t bytes = (n * size + 7u) & ~(size_t)7u;
    if (bytes > SOTTO8K_ENC_ARENA - enc_used)
        return NULL;
    void *p = enc_arena + enc_used;
    enc_used += (unsigned)bytes;
    enc_live++;
    memset(p, 0, bytes);
    return p;
}

void sotto8k_enc_free(void *p) {
    if (p == NULL || enc_live == 0)
        return;
    if (--enc_live == 0)
        enc_used = 0;
}
#endif

#define calloc sotto8k_enc_calloc
#define free sotto8k_enc_free

#endif
