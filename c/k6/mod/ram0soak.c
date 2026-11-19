/* ram0soak: can the core keep received messages in the bootloader's RAM? (2026-11-19, FuSotto
   playback, docs/k6-sotto-play.md). 0x20000000-0x20001027 is the bootloader's own RAM, below
   Aura's region. Once Aura runs, only hal::txdog's vector table (0x20000000-0x200000BF, while
   keyed) and the bootloader's UserVectors (0x20001000-0x20001027, SysTick and USART1 go through
   them) are used there: `loram` saw nothing written in a second of Aura running. But a write
   check can't see a read: if the bootloader's interrupt code read a flag in 0x20000100-0x20000FFF,
   overwriting it could hang the radio. Jeff (2026-11-19): make it safe, so that nobody has to
   reboot the radio by hand.

   - The independent watchdog (IWDG, 0x40003000, as the STM32F030's) is armed first, about 6 s,
     and fed while the module runs. A hang anywhere, interrupts included, resets the radio.
   - Before each step the module leaves txdog's crash note (0x20005FF0, the only RAM a reset
     keeps) saying which block it is on, so a watchdog reset's next boot prints
       K6 FAULT last boot pc=0a30000N lr=0a3050ac
     (N: the block being filled, 0x100 bytes each; f is 0x20000F00-0x20000FFF).
   - It fills from the top down, a block at a time (the far end from the bootloader's own
     0x20000000-0x2000046F first), holds each for a second with interrupts running, then reads
     back everything filled so far:   r0 b=N bad=0
   - Then it holds the whole range for HOLD_S seconds, read back every second, and reads the two
     ends it never writes (txdog's table 0x20000000-0x200000FF and the UserVectors) before and
     after:   r0 done bad=0 first=00000000 bm=0000 lo=same uv=same
   - The IWDG can't be stopped once started, so the module ends with a planned reset
     (r0 reset), the note cleared first. The bootloader clears this RAM at every reset, as before.
   API level 2 (ms, key_down). Returns only if the IWDG doesn't arm (r0 no iwdg).

   2026-11-19, for the core's buffers there (docs/k6-ram0.md):
   - It first maps what is in the range before it writes anything, read only:
       r0 map nz=NNN first=AAAAAAAA last=AAAAAAAA bm=BBBB
     (bm, not blk: the bench logger, modem.k6_harness, reads any blk= as decimal numbers and
     stopped on the first map, blk=f806, 2026-11-19 k6-ram0zero1)
     nz: the words that aren't 0 (the bootloader clears this RAM at reset, then uses
     0x20000000-0x2000046F before it starts Aura: what it left), then up to MAP_W of them,
     three to a line:   r0 w 0100=VVVVVVVV 0104=VVVVVVVV ...
   - On an image with module API level 12 or more the core's own buffers live there
     (hal::ram0): it maps and says "r0 refused: core RAM0", writes nothing, returns -2.
   - R0_ZERO (the ram0zero module) fills with zeros instead of the pattern: the core clears
     its buffers there at every boot, and the 2026-11-04 store that hung at boot was cleared
     the same way. A bootloader flag read as "not 0" would only show with zeros. A word that
     anything else writes shows as bad, as before.
   SPDX-License-Identifier: MIT */
#include "../k6_mod.h"
#ifdef R0_ZERO
K6_MODULE_NAME("RAM0 ZERO");
#else
K6_MODULE_NAME("RAM0 SOAK");
#endif
#define K6_MODULE_LEVEL 2
#ifndef MAP_W
#define MAP_W 12u                    /* non-zero words said in full */
#endif

#define LO 0x20000100u
#define HI 0x20001000u
#define UV 0x20001000u               /* the bootloader's UserVectors, 10 words */
#ifndef SEED
#define SEED 0x1234abcdu
#endif
#ifndef HOLD_S
#define HOLD_S 60u
#endif
#ifndef STEP_MS
#define STEP_MS 1000u
#endif

#define IWDG_KR (*(volatile uint32_t *)0x40003000u)
#define IWDG_PR (*(volatile uint32_t *)0x40003004u)
#define IWDG_RLR (*(volatile uint32_t *)0x40003008u)
#define IWDG_SR (*(volatile uint32_t *)0x4000300Cu)
#define RCC_CSR (*(volatile uint32_t *)0x40021024u)
#define AIRCR (*(volatile uint32_t *)0xE000ED0Cu)
#define NOTE ((volatile uint32_t *)0x20005FF0u)
#define NOTE_FAULT 0xFA170001u        /* hal::txdog's: the next boot prints pc and lr */
#define MARK_PC 0x0A300000u
#define MARK_LR 0x0A3050ACu

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hex(char *p, uint32_t v, int digits) {
  const char *x = "0123456789abcdef";
  while (digits--) *p++ = x[(v >> (4 * digits)) & 15];
  return p;
}

#ifdef R0_ZERO
static uint32_t pat(uint32_t a) {
  (void)a;
  return 0;
}
#else
static uint32_t pat(uint32_t a) { return (a * 2654435761u) ^ SEED; }
#endif

static void feed(void) { IWDG_KR = 0xAAAAu; }

static void mark(uint32_t blk) {
  NOTE[1] = MARK_PC | blk;
  NOTE[2] = MARK_LR;
  NOTE[0] = NOTE_FAULT;
}

static uint32_t sum(uint32_t a, uint32_t b) {
  uint32_t s = 0;
  for (; a < b; a += 4) s = (s << 1 | s >> 31) ^ *(volatile uint32_t *)a;
  return s;
}

/* words from LO up to `to` that lost the pattern; first and the blocks with one */
static uint32_t check(uint32_t to, uint32_t *first, uint32_t *blk) {
  uint32_t a, bad = 0;
  for (a = LO; a < to; a += 4)
    if (*(volatile uint32_t *)a != pat(a)) {
      if (!bad++) *first = a;
      *blk |= 1u << ((a - 0x20000000u) >> 8);
    }
  return bad;
}

/* what is in LO..HI before anything is written: the words that aren't 0 */
static void map(const k6_api_t *api) {
  char line[64], *p = line;
  uint32_t a, v, nz = 0, first = 0, last = 0, blk = 0, k = 0;
  for (a = LO; a < HI; a += 4)
    if (*(volatile uint32_t *)a) {
      if (!nz++) first = a;
      last = a;
      blk |= 1u << ((a - 0x20000000u) >> 8);
    }
  p = hex(put(line, "r0 map nz="), nz, 3);
  p = hex(put(p, " first="), first, 8);
  p = hex(put(p, " last="), last, 8);
  p = hex(put(p, " bm="), blk, 4);
  *p = 0;
  api->say(line);
  for (a = LO; a < HI && k < MAP_W; a += 4) {
    v = *(volatile uint32_t *)a;
    if (!v) continue;
    if (k % 3u == 0) p = put(line, "r0 w");
    p = hex(put(p, " "), a & 0xFFFFu, 4);
    p = hex(put(p, "="), v, 8);
    if (++k % 3u == 0) {
      *p = 0;
      api->say(line);
    }
  }
  if (k % 3u) {
    *p = 0;
    api->say(line);
  }
}

static void dwell(const k6_api_t *api, uint32_t ms) {
  uint32_t t0 = api->ms();
  while (api->ms() - t0 < ms) {
    feed();
    api->delay_us(2000);
  }
}

int k6_main(const k6_api_t *api) {
  char line[64], *p;
  uint32_t b, a, bad = 0, first = 0, blk = 0, s, lo0, uv0, t;
  int quit = 0;

  p = hex(put(line, "r0 start csr="), RCC_CSR, 8);
  *p = 0;
  api->say(line);
  map(api);
  if (api->level >= 12) {             /* the core's buffers are there now (hal::ram0) */
    api->say("r0 refused: core RAM0");
    return -2;
  }
  IWDG_KR = 0xCCCCu;                  /* start (and the LSI with it) */
  IWDG_KR = 0x5555u;                  /* PR and RLR writable */
  IWDG_PR = 4u;                       /* LSI / 64: 1.6 ms a count at 40 kHz */
  IWDG_RLR = 3750u;                   /* about 6 s (4.8-8 s over the LSI's 30-50 kHz) */
  for (t = 0; IWDG_SR & 3u; t++) {     /* PVU, RVU: the new values reach the LSI domain */
    if (t > 200000u) {
      api->say("r0 no iwdg");
      return -1;
    }
  }
  feed();
  lo0 = sum(0x20000000u, LO);
  uv0 = sum(UV, UV + 40u);

  for (b = (HI - LO) >> 8; b-- > 0;) {        /* blocks 14..0: 0x20000F00 down to 0x20000100 */
    uint32_t base = LO + (b << 8);
    mark((base - 0x20000000u) >> 8);
    for (a = base; a < base + 256u; a += 4) *(volatile uint32_t *)a = pat(a);
    dwell(api, STEP_MS);
    s = 0;
    for (a = base; a < HI; a += 4) s += *(volatile uint32_t *)a != pat(a);
    p = hex(put(line, "r0 b="), (base - 0x20000000u) >> 8, 1);
    p = hex(put(p, " bad="), s, 3);
    *p = 0;
    api->say(line);
    bad += s;
    if (api->key_down()) {
      quit = 1;
      break;
    }
  }
  if (!quit) {
    mark(0);
    for (t = 0; t < HOLD_S && !api->key_down(); t++) {
      dwell(api, 1000u);
      if (check(HI, &first, &blk) && !bad) bad = 1;
    }
    bad = check(HI, &first, &blk);
  }
  p = hex(put(line, "r0 done bad="), bad, 3);
  p = hex(put(p, " first="), first, 8);
  p = hex(put(p, " bm="), blk, 4);
  p = put(p, sum(0x20000000u, LO) == lo0 ? " lo=same" : " lo=CHANGED");
  p = put(p, sum(UV, UV + 40u) == uv0 ? " uv=same" : " uv=CHANGED");
  *p = 0;
  api->say(line);
  NOTE[0] = 0;
  api->say("r0 reset");
  dwell(api, 200u);                   /* the serial line out */
  AIRCR = 0x05FA0004u;                /* SYSRESETREQ: the IWDG can't be stopped */
  for (;;) {
  }
}
