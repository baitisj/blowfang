/* k6_uldiv: a small __aeabi_uldivmod for the K6 (thumbv6m): shift and subtract, a bit a step,
   in place of compiler_builtins' 1.3 KB one. Exact (the same quotient and remainder);
   slower, but only sotto's decoder (once a frame) and EXPLORE's load meter divide 64-bit numbers.
   Checked against Python on 5,000 cases in an emulated M0 (tests/test_k6_lz4.py runs it too).
   SPDX-License-Identifier: MIT */
#include <stdint.h>

uint64_t k6_udivmod64(uint64_t n, uint64_t d, uint64_t *rem) {
  /* 32-bit halves only, so no 64-bit shift helpers get pulled in. n shifts left a bit a step;
     the quotient bits fill in from its bottom as the dividend bits leave the top. */
  uint32_t nh = (uint32_t)(n >> 32), nl = (uint32_t)n;
  uint32_t dh = (uint32_t)(d >> 32), dl = (uint32_t)d;
  uint32_t rh = 0, rl = 0, top, c;
  int i;
  for (i = 64; i; i--) {
    top = rh >> 31;
    rh = rh << 1 | rl >> 31;
    rl = rl << 1 | nh >> 31;
    nh = nh << 1 | nl >> 31;
    nl <<= 1;
    if (top || rh > dh || (rh == dh && rl >= dl)) {
      c = rl < dl;
      rl -= dl;
      rh -= dh + c;
      nl |= 1;
    }
  }
  *rem = (uint64_t)rh << 32 | rl;
  return (uint64_t)nh << 32 | nl;
}

/* AEABI: n in r0:r1, d in r2:r3; the quotient back in r0:r1, the remainder in r2:r3 */
__attribute__((naked)) void __aeabi_uldivmod(void) {
  __asm volatile(
      "push {r4, lr}\n\t"
      "sub sp, #16\n\t"
      "add r4, sp, #8\n\t"
      "str r4, [sp]\n\t"
      "bl k6_udivmod64\n\t"
      "ldr r2, [sp, #8]\n\t"
      "ldr r3, [sp, #12]\n\t"
      "add sp, #16\n\t"
      "pop {r4, pc}\n\t");
}
