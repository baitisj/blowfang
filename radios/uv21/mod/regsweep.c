/* regsweep: one BK4829 register through a list of values, the level of a known signal and of the
   noise beside it at each (the operator, 2026-11-24: register trials from RAM modules, automated).
   Built with -D settings (modem/uv21mod.py, the bench's uv21.mod step):
     SW_REG     the register, e.g. 0x1F
     SW_VALS    its values, e.g. 0x1657,0xC65A (up to 32)
     SW_HZ      the signal's frequency, Hz (the Pluto's test carrier)
     SW_OFF_HZ  where the noise is read, Hz from SW_HZ (default 100 kHz above)
     SW_N       reads averaged per level (default 16), SW_GAP_US apart (default 2000)
     SW_SETTLE_MS  after each write and tune (default 50)
   Each value says "sw reg=1f val=1657 sig=-87.5 noise=-121.0 snr=33.5"; the register gets its old
   value back at the end, and the radio its frequency. Receive only, as every module. MIT */
#include "uv21_mod.h"

#ifndef SW_REG
#error "SW_REG: the register (-DSW_REG=0x1F)"
#endif
#ifndef SW_VALS
#error "SW_VALS: its values (-DSW_VALS=0x1657,0xC65A)"
#endif
#ifndef SW_HZ
#error "SW_HZ: the signal's frequency in Hz"
#endif
#ifndef SW_OFF_HZ
#define SW_OFF_HZ 100000
#endif
#ifndef SW_N
#define SW_N 16
#endif
#ifndef SW_GAP_US
#define SW_GAP_US 2000
#endif
#ifndef SW_SETTLE_MS
#define SW_SETTLE_MS 50
#endif

static int level2(const um_api_t *api, uint32_t hz) {
  int i, sum = 0;
  api->tune(hz, 1);
  api->delay_us(SW_SETTLE_MS * 1000u);
  for (i = 0; i < SW_N; i++) {
    sum += um_rssi2(api);
    api->delay_us(SW_GAP_US);
  }
  return (sum + (sum < 0 ? -SW_N / 2 : SW_N / 2)) / SW_N;
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  static const uint16_t vals[] = {SW_VALS};
  const unsigned nv = sizeof vals / sizeof vals[0];
  uint16_t old = api->fd_read(SW_REG);
  char line[80];
  char *p;
  unsigned i;
  (void)ram;
  (void)ram_len;
  p = um_put(line, "sw start reg=");
  p = um_hex(p, SW_REG, 2);
  p = um_put(p, " old=");
  p = um_hex(p, old, 4);
  p = um_put(p, " khz=");
  um_int(p, (int32_t)(SW_HZ / 1000));
  api->say(line);
  for (i = 0; i < nv && i < 32; i++) {
    int sig, noise;
    api->fd_write(SW_REG, vals[i]);
    sig = level2(api, SW_HZ);
    noise = level2(api, SW_HZ + SW_OFF_HZ);
    p = um_put(line, "sw reg=");
    p = um_hex(p, SW_REG, 2);
    p = um_put(p, " val=");
    p = um_hex(p, vals[i], 4);
    p = um_put(p, " sig=");
    p = um_half_db(p, sig);
    p = um_put(p, " noise=");
    p = um_half_db(p, noise);
    p = um_put(p, " snr=");
    um_half_db(p, sig - noise);
    api->say(line);
  }
  api->fd_write(SW_REG, old);
  api->tune(SW_HZ, 1);
  api->say("sw done");
  return (int)i;
}
