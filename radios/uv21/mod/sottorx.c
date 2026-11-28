/* sottorx: [S12] does the UV-21 Pro's BK4829 receive the K6 voice pager's sotto packets? A
   receive-only RAM module that arms the chip's FSK modem as the K6 pager does for its default PHY
   (sottorx_core.h, which the port's built-in receiver uses too), listens on SR_HZ for SR_S seconds,
   and checks each packet the way c/k6/sotto_pager.c does: PN9 de-whitening, the magic byte,
   CRC-16/CCITT-FALSE over all but the last two bytes. MIT

     sr arm hz=HHHHHHHHH 2b=XXXX                     the setup
     sr pk N ok|crc|short magic=MM seq=SSSS tot=TTTT rssi=-NN.N swap=0|1 w=WW
                                                     each packet (seq and total from its header)
     sr end syncs=N pk=N ok=N a9=N bad=N short=N    the totals; returns ok (good packets)

   Settings: SR_HZ (440850000: the K6 pager's channel), SR_S (60), SR_DIAG (1: a status line a
   second, "sr t=S DBM 0c= 0b= 59= 02= s=SYNCS"), and sottorx_core.h's SR_LEN
   (128; 64 for profile L), SR_58, SR_72, SR_BAUD, SR_2B_OR, SR_THR. The K6 pager's D/2400 rung
   (direct 2-FSK, which the RT-950's BK4829 decodes; it never decoded FFSK, docs/rt950-
   characteristics.md): SR_58=0x00E9 SR_72=0x60CB SR_BAUD=2400 SR_2B_OR=0x0400. Writes only the RF chip's receive registers (each
   through the API's receive-only filter); it leaves the FSK modem off at the end, and SURF retunes
   when the module returns. */
#include "sottorx_core.h"

#ifndef SR_HZ
#define SR_HZ 440850000u
#endif
#ifndef SR_S
#define SR_S 60
#endif
#ifndef SR_DIAG
#define SR_DIAG 1
#endif

typedef struct {
  uint32_t pk, ok, a9, bad, shorts;
} counts_t;

static int magic_ok(uint8_t m) {
  m &= (uint8_t)~0x10u;                  /* parity packets: magic | 0x10 */
  return m == 0xA7 || m == 0xA9 || m == 0xAC || m == 0xAD;
}

static void report(const um_api_t *api, counts_t *c, const srx_t *s, uint8_t *tmp) {
  char t[112];
  int good = 0, swap = 0;
  c->pk++;
  for (int i = 0; i < SR_LEN; i++) tmp[i] = 0;
  if (s->got < SR_LEN) {
    c->shorts++;
  } else if (srx_check(s->raw, tmp, SR_LEN, 0)) {
    good = 1;
  } else if (srx_check(s->raw, tmp, SR_LEN, 1)) {
    good = 1, swap = 1;
  } else {
    srx_check(s->raw, tmp, SR_LEN, 0);
    c->bad++;
  }
  if (good) {
    c->ok++;
    if ((tmp[0] & (uint8_t)~0x10u) == 0xA9) c->a9++;
  }
  char *p = um_put(t, "sr pk ");
  p = um_int(p, (int32_t)c->pk);
  p = um_put(p, s->got < SR_LEN ? " short" : good ? (magic_ok(tmp[0]) ? " ok" : " ok?") : " crc");
  p = um_hex(um_put(p, " magic="), tmp[0], 2);
  p = um_hex(um_put(p, " seq="), (uint32_t)tmp[2] << 8 | tmp[3], 4);
  p = um_hex(um_put(p, " tot="), (uint32_t)tmp[4] << 8 | tmp[5], 4);
  p = um_half_db(um_put(p, " rssi="), um_rssi2(api));
  p = um_int(um_put(p, " swap="), swap);
  um_int(um_put(p, " w="), s->got / 2);
  api->say(t);
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  char t[96];
  counts_t c = {0, 0, 0, 0, 0};
  if (ram_len < sizeof(srx_t) + SR_LEN + 8) return -1;
  srx_t *s = (srx_t *)ram;
  uint8_t *tmp = ram + ((sizeof(srx_t) + 7) & ~7u);

  srx_arm(api, s, SR_HZ);
  char *p = um_put(t, "sr arm hz=");
  p = um_int(p, (int32_t)SR_HZ);
  um_hex(um_put(p, " 2b="), s->r2b, 4);
  api->say(t);

  uint32_t t0 = api->ms(), t_end = t0 + (uint32_t)SR_S * 1000u, t_diag = t0;
  while ((int32_t)(api->ms() - t_end) < 0) {
    if (srx_poll(api, s)) report(api, &c, s, tmp);
#if SR_DIAG
    /* once a second: the level, the chip's status and IRQ registers, the FSK setup as it reads
       back (SR_DIAG=0 leaves it out) */
    if (api->ms() - t_diag >= 1000u) {
      t_diag = api->ms();
      /* (the console takes 64 characters a line) */
      p = um_put(t, "sr t=");
      p = um_int(p, (int32_t)((t_diag - t0) / 1000u));
      p = um_half_db(um_put(p, " "), um_rssi2(api));
      p = um_hex(um_put(p, " 0c="), api->fd_read(0x0C), 4);
      p = um_hex(um_put(p, " 0b="), api->fd_read(0x0B), 4);
      p = um_hex(um_put(p, " 59="), api->fd_read(0x59), 4);
      p = um_hex(um_put(p, " 02="), api->fd_read(0x02), 4);
      um_int(um_put(p, " s="), (int32_t)s->syncs);
      api->say(t);
    }
#endif
    api->delay_us(500);
  }
  srx_off(api);
  p = um_put(t, "sr end syncs=");
  p = um_int(p, (int32_t)s->syncs);
  p = um_int(um_put(p, " pk="), (int32_t)c.pk);
  p = um_int(um_put(p, " ok="), (int32_t)c.ok);
  p = um_int(um_put(p, " a9="), (int32_t)c.a9);
  p = um_int(um_put(p, " bad="), (int32_t)c.bad);
  um_int(um_put(p, " short="), (int32_t)c.shorts);
  api->say(t);
  return (int)c.ok;
}
