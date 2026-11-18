/* Codec2's memory from the sotto encoder's static arena first, the heap after (11-18).
 *
 * codec2_create(CODEC2_MODE_3200) takes about 31 KB in 11 blocks (measured on the host: the
 * largest 5.4 KB), and OpenRTX's decoder thread takes a 16 KB stack from the heap beside it. With
 * a RAM module loaded the UV-380's heap has about 40 KB left, so the first Codec2 call to the
 * speaker (c6m17rx, 11-18 10:33Z) ran the heap dry inside codec2_create, which doesn't check its
 * mallocs, and the radio reset right after "spk start". The sotto encoder's arena
 * (sotto8k/sotto8k_enc_arena.h, 25 KB in CCM) is idle then: the receiver gives it back before
 * every USB command and module, and the encoder only runs when a module or a recording asks.
 * Nothing reads Codec2's state by DMA (the UV-380 speaker is fed by the CPU over SPI), so CCM
 * suits it. What doesn't fit there comes from the heap as before.
 *
 * build.sh copies this into OpenRTX's codec2 packagefiles; the patch puts it in front of every
 * Codec2 source with -include, so their malloc, calloc and free come here.
 * SPDX-License-Identifier: MIT */
#ifndef C2_ALLOC_H
#define C2_ALLOC_H

#include <stdlib.h>

void *c2_malloc(size_t n);
void *c2_calloc(size_t n, size_t size);
void c2_free(void *p);

#define malloc(n) c2_malloc(n)
#define calloc(n, size) c2_calloc(n, size)
#define free(p) c2_free(p)

#endif
