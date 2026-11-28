/* wsprobe: [S12] how much the AT32F421's flash wait states cost real code, to calibrate the sotto
   1200 decoder's cycle bench (radios/uv21/sotto/bench_uv21.py), whose flash model charges every
   fetch 1 + 3 cycles a word and so brackets the decoder between 32 % (no wait states) and 99 % (that
   model) of the 120 MHz budget for its worst 40 ms slot. MIT

   The same machine code is timed running from flash and from RAM: the port's own
   radio_common::wear_leveled::crc16_xmodem (60 bytes, position independent: relative branches
   only, no literal pool; at WP_AT in port 21, 0c02e0a, build f4a05a4e), checked by its CRC-32
   first and copied into the module's RAM. Its bit loop takes a backward branch every 6
   instructions, a hard case for a prefetch buffer. Then a word-read loop over WP_RD_LEN bytes of
   the app area and of RAM (the decoder's tables and literal pools sit in flash). Cycles come from
   the core's DWT cycle counter (DEMCR.TRCENA, DWT_CTRL.CYCCNTENA), with interrupts off for each
   timed run (each under 1 ms), the fastest of WP_RUNS. Nothing is written but the DWT enables
   and the module's own RAM; the RF chip and the screen aren't touched.

     ws psr=0153 cyc=1                       FLASH_PSR, and whether DWT counts
     ws crc n=1024 flash=CCCC ram=CCCC        cycles for crc16 over n bytes, same result both ways
     ws rd n=4096 flash=CCCC ram=CCCC         cycles for the word-read loop
   Returns 0, or 1 (the function at WP_AT isn't port 21's), 2 (no cycle counter), 3 (the two
   copies disagree). */
#include "uv21_mod.h"

#ifndef WP_AT
#define WP_AT 0x08003958u               /* crc16_xmodem in port 21 (images/uv21-clk-0c02e0a) */
#endif
#ifndef WP_CRC
#define WP_CRC 0x7744f62cu              /* CRC-32 of its 60 bytes */
#endif
#define WP_LEN 60
#ifndef WP_N
#define WP_N 1024
#endif
#ifndef WP_RD_LEN
#define WP_RD_LEN 4096
#endif
#ifndef WP_RUNS
#define WP_RUNS 8
#endif

#define REG(a) (*(volatile uint32_t *)(a))
#define FLASH_PSR 0x40022000u
#define DEMCR 0xE000EDFCu
#define DWT_CTRL 0xE0001000u
#define DWT_CYCCNT 0xE0001004u
#define APP_FLASH 0x08001000u
#define NI __attribute__((noinline))

typedef uint16_t (*crc_fn)(const uint8_t *p, uint32_t n);

static uint32_t crc32(const uint8_t *p, uint32_t n) {
  uint32_t c = ~0u;
  while (n--) {
    c ^= *p++;
    for (int k = 0; k < 8; k++) c = c & 1 ? (c >> 1) ^ 0xEDB88320u : c >> 1;
  }
  return ~c;
}

static inline uint32_t irq_off(void) {
  uint32_t m;
  __asm volatile("mrs %0, primask\n cpsid i" : "=r"(m)::"memory");
  return m;
}

static inline void irq_restore(uint32_t m) { __asm volatile("msr primask, %0" ::"r"(m) : "memory"); }

static NI uint32_t time_crc(crc_fn f, const uint8_t *buf, uint16_t *out) {
  uint32_t best = ~0u;
  for (int r = 0; r < WP_RUNS; r++) {
    uint32_t m = irq_off();
    uint32_t t0 = REG(DWT_CYCCNT);
    uint16_t v = f(buf, WP_N);
    uint32_t dt = REG(DWT_CYCCNT) - t0;
    irq_restore(m);
    *out = v;
    if (dt < best) best = dt;
  }
  return best;
}

/* the word-read loop, unrolled by 4 so the loop's own branch weighs little against the reads */
static NI uint32_t sum_words(const volatile uint32_t *p, uint32_t words) {
  uint32_t s = 0;
  for (uint32_t i = 0; i < words; i += 4) s += p[i] + p[i + 1] + p[i + 2] + p[i + 3];
  return s;
}

static NI uint32_t time_rd(const volatile uint32_t *p) {
  uint32_t best = ~0u;
  for (int r = 0; r < WP_RUNS; r++) {
    uint32_t m = irq_off();
    uint32_t t0 = REG(DWT_CYCCNT);
    (void)sum_words(p, WP_RD_LEN / 4);
    uint32_t dt = REG(DWT_CYCCNT) - t0;
    irq_restore(m);
    if (dt < best) best = dt;
  }
  return best;
}

static void line3(const um_api_t *api, const char *head, uint32_t n, uint32_t a, uint32_t b) {
  char t[80];
  char *p = um_put(t, head);
  p = um_put(p, " n=");
  p = um_int(p, (int32_t)n);
  p = um_put(p, " flash=");
  p = um_int(p, (int32_t)a);
  p = um_put(p, " ram=");
  um_int(p, (int32_t)b);
  api->say(t);
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  char t[64];
  uint8_t *code = ram;                          /* 64 B, 8-aligned: the copy of the function */
  uint8_t *buf = ram + 64;                      /* WP_N bytes to checksum */
  uint8_t *rdbuf = ram + 64 + WP_N;             /* WP_RD_LEN bytes of RAM to read */
  if (ram_len < 64 + WP_N + WP_RD_LEN) return 4;

  REG(DEMCR) |= 1u << 24;                       /* TRCENA */
  REG(DWT_CYCCNT) = 0;
  REG(DWT_CTRL) |= 1u;                          /* CYCCNTENA */
  uint32_t c0 = REG(DWT_CYCCNT);
  api->delay_us(100);
  int counts = REG(DWT_CYCCNT) != c0;
  char *p = um_put(t, "ws psr=");
  p = um_hex(p, REG(FLASH_PSR), 4);
  p = um_put(p, " cyc=");
  um_int(p, counts);
  api->say(t);
  if (!counts) return 2;

  if (crc32((const uint8_t *)WP_AT, WP_LEN) != WP_CRC) {
    api->say("ws crc16 at WP_AT is not port 21's: stop");
    return 1;
  }
  for (int i = 0; i < WP_LEN; i++) code[i] = ((const uint8_t *)WP_AT)[i];
  for (int i = 0; i < WP_N; i++) buf[i] = (uint8_t)(i * 37 + 11);
  for (int i = 0; i < WP_RD_LEN; i++) rdbuf[i] = (uint8_t)i;
  __asm volatile("dsb\n isb" ::: "memory");

  uint16_t vf, vr;
  uint32_t cf = time_crc((crc_fn)(WP_AT | 1u), buf, &vf);
  uint32_t cr = time_crc((crc_fn)((uint32_t)code | 1u), buf, &vr);
  line3(api, "ws crc", WP_N, cf, cr);
  uint32_t rf = time_rd((const volatile uint32_t *)APP_FLASH);
  uint32_t rr = time_rd((const volatile uint32_t *)rdbuf);
  line3(api, "ws rd", WP_RD_LEN, rf, rr);
  return vf == vr ? 0 : 3;
}
