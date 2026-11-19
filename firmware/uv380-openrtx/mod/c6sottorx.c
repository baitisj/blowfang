/* c6sottorx: sotto heard as M17 or YSF on the unmodified UV-380, put back together as a message
   (docs/uv380-multimode.md, milestone 5): mod/c6rx.h as a RAM module, to prove on the bench what
   the image's PAGER M17 / YSF receive will run from flash. Receive only (no RF out).
   It listens for RX_S seconds; each message it hears is reported as c6rx.h's "rx AIR msg ..." line
   (superframes, how many came, the sender, the first and last superframe to check against what
   was sent), then it listens again. c6sottorx_m17.c and c6sottorx_ysf.c pick the mode (a module
   has room for one receiver: m17_rx alone is 6.6 KB of state).
   Lines: "sottorx start air= s= scratch= need=", the rx lines, "end msgs=N".
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#ifndef C6_BITWAIT
#define C6_BITWAIT 3u   /* the tap's 512 bytes a half at about 2 MHz, as c6m17rx */
#endif
#include "c6spi.h"
#include "at1846.h"

#ifndef RX_AIR
#define RX_AIR 1
#endif
#ifndef RX_S
#define RX_S 150u
#endif
#define C6RX_HAS_M17 (RX_AIR == 1)
#define C6RX_HAS_YSF (RX_AIR == 2)
#ifndef C6RX_MAX
#define C6RX_MAX 300u   /* 12 s of sotto: the module's scratch is what the arena has after the code */
#endif
#define C6RX_ST 1
#include "c6rx.h"

int uv_main(const ortx_api_t *api) {
    char line[120];
    uint32_t sz = 0;
    c6rx_state *c = (c6rx_state *)api->scratch(&sz);
    api->fmt(line, sizeof line, "sottorx start air=%s s=%u scratch=%lu need=%u", c6rx_name(RX_AIR), RX_S,
             (unsigned long)sz, (unsigned)sizeof(c6rx_state));
    api->say(line);
    uint32_t t0 = api->ms();
    unsigned msgs = 0;
    while (api->ms() - t0 < RX_S * 1000u && !api->stop()) {
        int n = c6rx_listen(api, RX_AIR, c, sz, RX_S * 1000u - (api->ms() - t0), 0, 0);
        if (n < 0)
            return -1;
        if (n > 0)
            msgs++;
    }
    api->fmt(line, sizeof line, "end msgs=%u", msgs);
    api->say(line);
    return (int)msgs;
}
