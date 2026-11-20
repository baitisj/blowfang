/* m0rt.h: the C runtime pieces a K6 RAM module needs when it includes C written for a whole
   toolchain (sotto's slim encoder): 32-bit division (the Cortex-M0 has none) and memset /
   memcpy / memmove. Include it once, in the module's one C file. Shift-and-subtract division:
   at most 32 steps, about 0.3 us at 96 MHz. SPDX-License-Identifier: MIT */
#ifndef M0RT_H
#define M0RT_H
#include <string.h>

typedef unsigned long long m0rt_u64;

static unsigned m0rt_udiv(unsigned n, unsigned d, unsigned *rem) {
  unsigned q = 0, r = 0;
  int i;
  if (!d) {
    *rem = n;
    return 0xFFFFFFFFu;
  }
  if (n < d) {
    *rem = n;
    return 0;
  }
  for (i = 31; i >= 0; i--) {
    r = (r << 1) | ((n >> i) & 1u);
    if (r >= d) {
      r -= d;
      q |= 1u << i;
    }
  }
  *rem = r;
  return q;
}

unsigned __aeabi_uidiv(unsigned n, unsigned d) {
  unsigned r;
  return m0rt_udiv(n, d, &r);
}

int __aeabi_idiv(int n, int d) {
  unsigned r, q = m0rt_udiv(n < 0 ? 0u - (unsigned)n : (unsigned)n, d < 0 ? 0u - (unsigned)d : (unsigned)d, &r);
  return (n < 0) != (d < 0) ? -(int)q : (int)q;
}

/* quotient in r0, remainder in r1: a 64-bit return in AAPCS */
m0rt_u64 __aeabi_uidivmod(unsigned n, unsigned d) {
  unsigned r, q = m0rt_udiv(n, d, &r);
  return (m0rt_u64)q | ((m0rt_u64)r << 32);
}

m0rt_u64 __aeabi_idivmod(int n, int d) {
  unsigned r, q = m0rt_udiv(n < 0 ? 0u - (unsigned)n : (unsigned)n, d < 0 ? 0u - (unsigned)d : (unsigned)d, &r);
  int qs = (n < 0) != (d < 0) ? -(int)q : (int)q, rs = n < 0 ? -(int)r : (int)r;
  return (m0rt_u64)(unsigned)qs | ((m0rt_u64)(unsigned)rs << 32);
}

void *memset(void *d, int c, size_t n) {
  unsigned char *p = (unsigned char *)d;
  while (n--) *p++ = (unsigned char)c;
  return d;
}

void *memcpy(void *d, const void *s, size_t n) {
  unsigned char *p = (unsigned char *)d;
  const unsigned char *q = (const unsigned char *)s;
  while (n--) *p++ = *q++;
  return d;
}

void *memmove(void *d, const void *s, size_t n) {
  unsigned char *p = (unsigned char *)d;
  const unsigned char *q = (const unsigned char *)s;
  if (p < q) {
    while (n--) *p++ = *q++;
  } else {
    p += n;
    q += n;
    while (n--) *--p = *--q;
  }
  return d;
}
/* the names the compiler calls for struct copies and clears (ARM run-time ABI: memset's
   arguments in another order) */
void __aeabi_memcpy(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy8(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memmove(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memset(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memclr(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr8(void *d, size_t n) { memset(d, 0, n); }
#endif
