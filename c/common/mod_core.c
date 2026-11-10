/* mod_core: check and run a signed RAM module (see mod_core.h). Ported from the K6's loader
 * (c/k6/k6_mod.c): same image, same SipHash-2-4 tag, same key placeholder, same replay rule.
 * SPDX-License-Identifier: MIT */
#include "mod_core.h"

#include <stddef.h>

/* The key: -DMC_KEY=0x12,0x34,... or the K6's placeholder, which `python -m modem.k6 keyimage`
   overwrites in a built image. Read through a volatile pointer so the compiler can't fold it
   into the code, where keyimage couldn't find it; the placeholder is also kept inverted, so its
   plain text is in the image once. */
#define MC_PLACEHOLDER 's', 'o', 't', 't', 'o', '-', 'K', '6', 'M', 'O', 'D', '-', 'k', 'e', 'y', '!'
#define X(c) (uint8_t)((c) ^ 0xFF)
static const uint8_t placeholder_inv[16] = {
    X('s'),
    X('o'),
    X('t'),
    X('t'),
    X('o'),
    X('-'),
    X('K'),
    X('6'),
    X('M'),
    X('O'),
    X('D'),
    X('-'),
    X('k'),
    X('e'),
    X('y'),
    X('!'),
};
#ifdef MC_KEY
static const uint8_t key_store[16] = {MC_KEY};
#else
__attribute__((used)) static const uint8_t key_store[16] = {MC_PLACEHOLDER};
#endif

static uint32_t last_serial;
static uint8_t armed;

static uint64_t le64(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--)
        v = v << 8 | p[i];
    return v;
}

#define ROTL(x, b) (((x) << (b)) | ((x) >> (64 - (b))))
static void rounds(uint64_t *v, int n) {
    while (n--) {
        v[0] += v[1], v[1] = ROTL(v[1], 13), v[1] ^= v[0], v[0] = ROTL(v[0], 32);
        v[2] += v[3], v[3] = ROTL(v[3], 16), v[3] ^= v[2];
        v[0] += v[3], v[3] = ROTL(v[3], 21), v[3] ^= v[0];
        v[2] += v[1], v[1] = ROTL(v[1], 17), v[1] ^= v[2], v[2] = ROTL(v[2], 32);
    }
}

uint64_t mc_siphash(const uint8_t k[16], const uint8_t *m, unsigned n) {
    uint64_t k0 = le64(k), k1 = le64(k + 8), b = (uint64_t)n << 56, mi;
    uint64_t v[4];
    unsigned i, j;
    v[0] = k0 ^ 0x736f6d6570736575ULL, v[1] = k1 ^ 0x646f72616e646f6dULL;
    v[2] = k0 ^ 0x6c7967656e657261ULL, v[3] = k1 ^ 0x7465646279746573ULL;
    for (i = 0; i + 8 <= n; i += 8) {
        mi = le64(m + i);
        v[3] ^= mi, rounds(v, 2), v[0] ^= mi;
    }
    for (j = 0; i + j < n; j++)
        b |= (uint64_t)m[i + j] << (8 * j);
    v[3] ^= b, rounds(v, 2), v[0] ^= b;
    v[2] ^= 0xff, rounds(v, 4);
    return v[0] ^ v[1] ^ v[2] ^ v[3];
}

static int tag_check(const uint8_t *a, unsigned code) {
    const volatile uint8_t *kp = key_store;
    uint8_t key[16];
    int same = 1;
    for (int i = 0; i < 16; i++)
        key[i] = kp[i], same &= key[i] == (uint8_t)~placeholder_inv[i];
    if (same)
        return MC_NO_KEY;
    if (mc_siphash(key, a, MC_HDR + code) != le64(a + MC_HDR + code))
        return MC_BAD_TAG;
    return 0;
}

int mc_check(const uint8_t *a, unsigned n, const char magic[3], unsigned level) {
    armed = 0;
    if (n < MC_HDR)
        return MC_BAD_HDR;
    unsigned code = a[4] | a[5] << 8, entry = a[6] | a[7] << 8;
    uint32_t serial = (uint32_t)a[8] | (uint32_t)a[9] << 8 | (uint32_t)a[10] << 16 |
                      (uint32_t)a[11] << 24;
    if (a[0] != (uint8_t)magic[0] || a[1] != (uint8_t)magic[1] || a[2] != (uint8_t)magic[2] ||
        a[3] != MC_ABI || a[12] > level || a[13] != 0 || MC_HDR + code + MC_TAG > n ||
        entry >= code || (entry & 1))
        return MC_BAD_HDR;
    int t = tag_check(a, code);
    if (t)
        return t;
    if (serial <= last_serial)
        return MC_OLD;
    last_serial = serial;
    armed = 1;
    return MC_READY;
}

int mc_run(const uint8_t *a, const void *api) {
    if (!armed)
        return -100;
    armed = 0;
#if defined(__thumb__)
    typedef int (*entry_t)(const void *);
    entry_t f = (entry_t)((uintptr_t)(a + MC_HDR + (a[6] | a[7] << 8)) | 1u);
    __asm__ volatile("dsb\n\tisb" ::: "memory");     /* the code just written is what runs */
    return f(api);
#else
    (void)a, (void)api;
    return -100;
#endif
}

uint32_t mc_serial(void) {
    return last_serial;
}
