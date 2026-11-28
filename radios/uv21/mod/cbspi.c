/* cbspi: [S12] puts sotto's shape codebook into the UV-21 Pro's SPI flash, once, for the port's
   sotto 1200 decoder (radios/uv21/sotto, -DFX_CB_SPI), which then only reads it. Jeff, 2026-11-28:
   "Codebook in SPI is fine". MIT

   The image is the K6's (c/common/sp_cb.h, radios/uv21/build/sotto/sotto-cb.bin): a 32-byte header,
   then fx_cb0..fx_cb3 packed, 30,720 bytes, 30,752 in all. It goes at CB_BASE, 0x1F0000, the last
   64 KB of the 2 MB chip, inside the area that was blank in the dump (uv21-flash-2e: blank from
   0x14C000 to the end). Nothing outside CB_BASE .. CB_END (32 KB) is ever erased or programmed:
   every erase and program checks its address against that window first. The port itself stays
   READ_ONLY (radios/uv21/fw/src/drivers/norflash.rs).

   Too big for one module (12 KB), so it goes in steps, each its own upload, CB_OP picking one:
     CB_OP=0  check (reads only): the chip's JEDEC ID and status register, how much of the window
              is erased, the CRC-32 of the image's 30,752 bytes there against CB_IMG_CRC, the
              header against CB_HDR. Returns 0 when the whole image is in place, else F_ bits.
     CB_OP=1  erase the window's 8 sectors (4 KB each), then check they read erased.
     CB_OP=2  program part CB_PART of the image into erased flash and read it back:
                0: the header (CB_HDR) and stage 0 (fx_cb0, 10,240 B), image bytes 0 .. 10,271
                1: stage 1 (fx_cb1), bytes 10,272 .. 20,511
                2: stages 2 and 3 (fx_cb2, fx_cb3), bytes 20,512 .. 30,751
              It refuses to program a byte that isn't erased (0xFF), so a part can't land twice.
   The tables come from this tree's c/fixed/fx_tables.c (packed: FX_NO_ARM_DSP), so they are the
   bytes the decoder expects; CB_HDR is sotto-cb.bin's header as 8 little-endian words and
   CB_IMG_CRC the CRC-32 of all of sotto-cb.bin (radios/uv21/sotto/cbspi_job.py makes the job).

   Lines: "cb id=JJJJJJ sr=SS", "cb erased=N/32768 crc=CCCCCCCC want=CCCCCCCC hdr=ok|bad",
   "cb erase ok|fail", "cb part P at=AAAAAA n=N ok|fail". SPI1 (PA5-PA7, mode 3, as the port set
   it up) and its CS PA2 are driven directly; the port's own SPI use is paused while a module runs. */
#define FX_NO_ARM_DSP                    /* the packed codebook: 20 bytes a vector, the K6's image */
#include "uv21_mod.h"
#if defined(CB_OP) && CB_OP == 2
/* the definitions, not just the header: the linker keeps the one or two tables this part writes */
#include "../../../c/fixed/fx_tables.c"
#endif

#ifndef CB_OP
#define CB_OP 0
#endif
#ifndef CB_PART
#define CB_PART 0
#endif
#ifndef CB_IMG_CRC
#define CB_IMG_CRC 0u
#endif

#define CB_BASE 0x1F0000u
#define CB_END 0x1F8000u                 /* the window: 8 sectors */
#define CB_IMG_LEN 30752u
#define CB_HDR_LEN 32u

#define F_IMG 0x01                       /* the image's CRC isn't CB_IMG_CRC */
#define F_HDR 0x02                       /* the header isn't CB_HDR */
#define F_PROT 0x04                      /* the status register has block protection set */
#define F_ERASE 0x08                     /* a sector didn't erase */
#define F_PROG 0x10                      /* a byte didn't program, or wasn't erased before */
#define F_RANGE 0x20                     /* an address outside the window (never sent) */
#define F_BUSY 0x40                      /* the chip stayed busy */
#define F_SET 0x80                       /* a setting is missing */

#define REG(a) (*(volatile uint32_t *)(a))
#define S_STS REG(0x40013008u)
#define S_DT REG(0x4001300Cu)
#define GPIOA_SCR REG(0x48000018u)
#define GPIOA_CLR REG(0x48000028u)

static uint8_t spi(uint32_t b) {
  while (!(S_STS & 2u)) {}              /* TDBE */
  S_DT = (uint8_t)b;
  while (!(S_STS & 1u)) {}              /* RDBF */
  return (uint8_t)S_DT;
}

static void cs(int on) {
  if (on) GPIOA_CLR = 1u << 2;          /* CS PA2, active low */
  else GPIOA_SCR = 1u << 2;
}

static void addr24(uint32_t a) {
  spi(a >> 16);
  spi(a >> 8);
  spi(a);
}

static void rd(uint32_t a, uint8_t *b, uint32_t n) {
  cs(1);
  spi(0x03);
  addr24(a);
  while (n--) *b++ = spi(0);
  cs(0);
}

static uint8_t sr1(void) {
  cs(1);
  spi(0x05);
  uint8_t s = spi(0);
  cs(0);
  return s;
}

#if CB_OP == 0
static uint32_t crc32_add(uint32_t c, const uint8_t *p, uint32_t n) {
  c = ~c;
  while (n--) {
    c ^= *p++;
    for (int k = 0; k < 8; k++) c = c & 1 ? (c >> 1) ^ 0xEDB88320u : c >> 1;
  }
  return ~c;
}
#endif

static void say_kv(const um_api_t *api, const char *head, uint32_t v, int digits) {
  char t[48];
  um_hex(um_put(t, head), v, digits);
  api->say(t);
}

#if CB_OP != 0
static int in_window(uint32_t a, uint32_t n) { return a >= CB_BASE && n <= CB_END - a; }
#endif

#if CB_OP != 0
static int wait_ready(const um_api_t *api, uint32_t ms) {
  uint32_t t0 = api->ms();
  while (sr1() & 1u)
    if (api->ms() - t0 > ms) return 0;
  return 1;
}

static void wren(void) {
  cs(1);
  spi(0x06);
  cs(0);
}
#endif

#if CB_OP == 1
static int erase4k(const um_api_t *api, uint32_t a) {
  if (!in_window(a, 4096) || (a & 4095u)) return F_RANGE;
  wren();
  cs(1);
  spi(0x20);
  addr24(a);
  cs(0);
  return wait_ready(api, 500) ? 0 : F_BUSY;
}
#endif

#if CB_OP == 2
/* n bytes at SPI address a, inside one 256-byte page, onto erased flash; read back */
static int prog_page(const um_api_t *api, uint32_t a, const uint8_t *p, uint32_t n, uint8_t *tmp) {
  if (!in_window(a, n) || ((a & 255u) + n) > 256u) return F_RANGE;
  rd(a, tmp, n);
  for (uint32_t i = 0; i < n; i++)
    if (tmp[i] != 0xFF) return F_PROG;
  wren();
  cs(1);
  spi(0x02);
  addr24(a);
  for (uint32_t i = 0; i < n; i++) spi(p[i]);
  cs(0);
  if (!wait_ready(api, 50)) return F_BUSY;
  rd(a, tmp, n);
  for (uint32_t i = 0; i < n; i++)
    if (tmp[i] != p[i]) return F_PROG;
  return 0;
}

static int prog(const um_api_t *api, uint32_t a, const uint8_t *p, uint32_t n, uint8_t *tmp) {
  while (n) {
    uint32_t k = 256u - (a & 255u);
    if (k > n) k = n;
    int f = prog_page(api, a, p, k, tmp);
    if (f) return f;
    a += k, p += k, n -= k;
  }
  return 0;
}
#endif

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  int f = 0;
  char t[64];
  (void)ram_len;
  cs(0);
  cs(1);
  spi(0x9F);
  uint32_t id = (uint32_t)spi(0) << 16;
  id |= (uint32_t)spi(0) << 8;
  id |= spi(0);
  cs(0);
  uint8_t s = sr1();
  char *p = um_put(t, "cb id=");
  p = um_hex(p, id, 6);
  um_hex(um_put(p, " sr="), s, 2);
  api->say(t);
  if (s & 0x7Cu) f |= F_PROT;            /* BP0-BP4 (or TB/SEC): writes would be ignored */

#if CB_OP == 0
  {
    uint8_t *b = ram;                    /* 256 B */
    uint32_t erased = 0, crc = 0;
    for (uint32_t a = CB_BASE; a < CB_END; a += 256) {
      rd(a, b, 256);
      for (int i = 0; i < 256; i++) erased += b[i] == 0xFF;
      uint32_t off = a - CB_BASE;
      if (off < CB_IMG_LEN) crc = crc32_add(crc, b, CB_IMG_LEN - off < 256 ? CB_IMG_LEN - off : 256);
    }
    int hdr_ok = 0;
#ifdef CB_HDR
    {
      static const uint32_t want[8] = {CB_HDR};
      rd(CB_BASE, b, CB_HDR_LEN);
      hdr_ok = 1;
      for (int i = 0; i < 32; i++)
        if (b[i] != (uint8_t)(want[i >> 2] >> (8 * (i & 3)))) hdr_ok = 0;
    }
#endif
    p = um_put(t, "cb erased=");
    p = um_int(p, (int32_t)erased);
    p = um_put(p, "/32768 crc=");
    p = um_hex(p, crc, 8);
    p = um_put(p, " want=");
    p = um_hex(p, CB_IMG_CRC, 8);
    um_put(p, hdr_ok ? " hdr=ok" : " hdr=bad");
    api->say(t);
    if (crc != CB_IMG_CRC) f |= F_IMG;
    if (!hdr_ok) f |= F_HDR;
  }
#elif CB_OP == 1
  if (!(f & F_PROT)) {
    uint8_t *b = ram;
    for (uint32_t a = CB_BASE; a < CB_END && !f; a += 4096) {
      f |= erase4k(api, a);
      for (uint32_t o = 0; o < 4096 && !f; o += 256) {
        rd(a + o, b, 256);
        for (int i = 0; i < 256; i++)
          if (b[i] != 0xFF) f |= F_ERASE;
      }
    }
  }
  api->say(f ? "cb erase fail" : "cb erase ok");
#elif CB_OP == 2
  if (!(f & F_PROT)) {
    uint8_t *tmp = ram;                  /* 256 B */
    uint32_t at = 0, n = 0;
#if CB_PART == 0
#ifndef CB_HDR
    f |= F_SET;
#else
    static const uint32_t hdr[8] = {CB_HDR};
    uint8_t *h = ram + 256;
    for (int i = 0; i < 32; i++) h[i] = (uint8_t)(hdr[i >> 2] >> (8 * (i & 3)));
    if (!f) f |= prog(api, CB_BASE, h, CB_HDR_LEN, tmp);
    at = CB_HDR_LEN;
    if (!f) f |= prog(api, CB_BASE + at, (const uint8_t *)fx_cb0, sizeof fx_cb0, tmp);
    n = CB_HDR_LEN + sizeof fx_cb0;
#endif
#elif CB_PART == 1
    at = CB_HDR_LEN + sizeof fx_cb0;
    f |= prog(api, CB_BASE + at, (const uint8_t *)fx_cb1, sizeof fx_cb1, tmp);
    n = sizeof fx_cb1;
#elif CB_PART == 2
    at = CB_HDR_LEN + sizeof fx_cb0 + sizeof fx_cb1;
    f |= prog(api, CB_BASE + at, (const uint8_t *)fx_cb2, sizeof fx_cb2, tmp);
    if (!f) f |= prog(api, CB_BASE + at + sizeof fx_cb2, (const uint8_t *)fx_cb3, sizeof fx_cb3, tmp);
    n = sizeof fx_cb2 + sizeof fx_cb3;
#else
    f |= F_SET;
#endif
    p = um_put(t, "cb part ");
    p = um_int(p, CB_PART);
    p = um_put(p, " at=");
    p = um_hex(p, CB_BASE + (CB_PART == 0 ? 0 : at), 6);
    p = um_put(p, " n=");
    p = um_int(p, (int32_t)n);
    um_put(p, f ? " fail" : " ok");
    api->say(t);
  }
#endif
  say_kv(api, "cb f=", (uint32_t)f, 2);
  return f;
}
