/* CCM-first bump allocator for the sotto codec; see sotto_arena.h.
 * SPDX-License-Identifier: MIT */
#include "sotto_arena.h"

#include <stdint.h>
#include <string.h>

#define CCM_ARENA (60u * 1024u)         /* of the 64 KB CCM; the rest is left for other .ccm data */

static uint8_t ccm[CCM_ARENA] __attribute__((section(".ccm"), aligned(8)));
extern uint8_t _heap_start[], _heap_end[];  /* sections.ld: SRAM between .bss and the stack */

static unsigned ccm_used, sram_used, peak, live, failed;

static void *take(uint8_t *base, unsigned size, unsigned *used, unsigned n) {
    if (n > size - *used)
        return NULL;
    void *p = base + *used;
    *used += n;
    return p;
}

void *sotto_arena_calloc(size_t n, size_t size) {
    if (size && n > (size_t)-1 / size) {
        failed++;
        return NULL;
    }
    unsigned bytes = (unsigned)((n * size + 7u) & ~(size_t)7u);
    unsigned sram_size = (unsigned)(_heap_end - _heap_start);
    void *p = take(ccm, CCM_ARENA, &ccm_used, bytes);
    if (!p)
        p = take(_heap_start, sram_size, &sram_used, bytes);
    if (!p) {
        failed++;
        return NULL;
    }
    memset(p, 0, bytes);                /* CCM is NOLOAD: nothing zeroes it at start-up */
    live++;
    if (ccm_used + sram_used > peak)
        peak = ccm_used + sram_used;
    return p;
}

void sotto_arena_free(void *p) {
    if (!p || !live)
        return;
    if (--live == 0)
        ccm_used = sram_used = 0;
}

void sotto_arena_get_stats(sotto_arena_stats *s) {
    s->ccm_used = ccm_used;
    s->ccm_size = CCM_ARENA;
    s->sram_used = sram_used;
    s->sram_size = (unsigned)(_heap_end - _heap_start);
    s->peak = peak;
    s->live = live;
    s->failed = failed;
}
