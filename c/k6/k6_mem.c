/* memcpy, memmove, memset and memcmp for the K6 images, with the EABI names the compiler calls
   (__aeabi_memcpy4, __aeabi_memclr, ...): 268 bytes in place of Rust compiler_builtins' 1.7 KB.
   Words when both pointers are 4-aligned, bytes otherwise; the same memcpy as the decoder's
   cycle bench (c/fixed/cm4/shim.c), so bench_k6.py's timings hold. In the library archive only
   (tools/k6_build.sh), not the bench link, which has shim.c's. SPDX-License-Identifier: MIT */
typedef unsigned int size_t;
typedef unsigned int u32;
typedef unsigned char u8;

void *memcpy(void *d, const void *s, size_t n) {
  u8 *p = d;
  const u8 *q = s;
  if ((((u32)p | (u32)q) & 3) == 0)
    for (; n >= 4; n -= 4, p += 4, q += 4) *(u32 *)p = *(const u32 *)q;
  while (n--) *p++ = *q++;
  return d;
}

void *memmove(void *d, const void *s, size_t n) {
  u8 *p = d;
  const u8 *q = s;
  if (p <= q || p >= q + n) return memcpy(d, s, n);
  while (n--) p[n] = q[n];
  return d;
}

void *memset(void *d, int c, size_t n) {
  u8 *p = d;
  u32 w = (u8)c * 0x01010101u;
  if (((u32)p & 3) == 0)
    for (; n >= 4; n -= 4, p += 4) *(u32 *)p = w;
  while (n--) *p++ = (u8)c;
  return d;
}

int memcmp(const void *a, const void *b, size_t n) {
  const u8 *p = a, *q = b;
  for (; n; n--, p++, q++)
    if (*p != *q) return *p - *q;
  return 0;
}

void __aeabi_memcpy(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, size_t n) __attribute__((alias("__aeabi_memcpy")));
void __aeabi_memcpy8(void *d, const void *s, size_t n) __attribute__((alias("__aeabi_memcpy")));
void __aeabi_memmove(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, size_t n) __attribute__((alias("__aeabi_memmove")));
void __aeabi_memmove8(void *d, const void *s, size_t n) __attribute__((alias("__aeabi_memmove")));
void __aeabi_memset(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset4(void *d, size_t n, int c) __attribute__((alias("__aeabi_memset")));
void __aeabi_memset8(void *d, size_t n, int c) __attribute__((alias("__aeabi_memset")));
void __aeabi_memclr(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, size_t n) __attribute__((alias("__aeabi_memclr")));
void __aeabi_memclr8(void *d, size_t n) __attribute__((alias("__aeabi_memclr")));
