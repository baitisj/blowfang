/* Memory for the sotto codec on the UV-380: CCM first, then SRAM.
 *
 * The fixed-point sotto calls calloc and free only in its create and destroy functions. The
 * Makefile builds the sotto sources with -Dcalloc=sotto_arena_calloc -Dfree=sotto_arena_free, so
 * their state lands here instead of newlib's heap: a bump allocator over the 64 KB CCM (CPU only,
 * zero wait, no DMA, which the codec's state never needs), falling back to the SRAM between .bss
 * and the main stack. Only one encoder or decoder is alive at a time on a half-duplex radio. When
 * every block has been freed both regions start again from empty.
 *
 * The 8 kHz sotto 1200 encoder (24.4 KB) and decoder (1.2 KB) fit the CCM alone, and so does
 * the 16 kHz encoder (55.6 KB) when the rest of the CCM leaves room; otherwise its later blocks
 * go to SRAM.
 *
 * SPDX-License-Identifier: MIT */
#ifndef UV380_SOTTO_ARENA_H
#define UV380_SOTTO_ARENA_H

#include <stddef.h>

void *sotto_arena_calloc(size_t n, size_t size);
void sotto_arena_free(void *p);

typedef struct {
    unsigned ccm_used, ccm_size;        /* bytes */
    unsigned sram_used, sram_size;
    unsigned peak;                      /* most bytes in use at once, both regions */
    unsigned live;                      /* blocks not freed yet */
    unsigned failed;                    /* allocations that did not fit */
} sotto_arena_stats;

void sotto_arena_get_stats(sotto_arena_stats *s);

#endif
