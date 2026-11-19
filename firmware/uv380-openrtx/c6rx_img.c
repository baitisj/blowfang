/* c6rx_img.c: the image's M17 and YSF receive (docs/uv380-multimode.md, milestone 5): the body the
   c6sottorx RAM modules prove on the bench (mod/c6rx.h: the C6000's FM tap, m17_rx or ysf_rx,
   and sotto_rx putting the message back together), built into the bench image's flash. While
   PAGER mode sits idle in its M17 or YSF sub-mode, the probe thread calls c6rx_hear with
   ortx_mod.c's own module API table (om_api), then c6rx_take for the message it heard (still in
   the scratch), which becomes the message and plays, as one heard in RNG or FSK does. Receive
   only: no RF out.

   Both receivers are built (the mode is picked at run time); their state, the message being put
   together (C6RX_MAX superframes, 30 s) and the frame buffers sit in the module arena's scratch,
   about 11.3 KB of its 16 KB, so nothing here is a writable static. OpenRTX has its own M17 code:
   c/common's receiver names are renamed c6rx_* here so none can clash with it.

   build.sh copies this file and the mod/ headers it needs into openrtx/src/blowfang/mod/, and
   c/common's receivers into openrtx/c/common/ (where c6rx.h's "../../../c/common/" finds them).
   Only the bench image (BLOWFANG_RANGE and BLOWFANG_I2SPROBE) on the UV-380 builds it.
   SPDX-License-Identifier: MIT */
#include "blowfang_config.h"

#if defined(BLOWFANG_RANGE) && defined(BLOWFANG_I2SPROBE) && defined(PLATFORM_MDUV3x0)
#define m17_callsign c6rx_m17_callsign
#define m17_crc c6rx_m17_crc
#define m17_init c6rx_m17_init
#define m17_push c6rx_m17_push
#define ysf_crc c6rx_ysf_crc
#define ysf_init c6rx_ysf_init
#define ysf_push c6rx_ysf_push
#define srx_conceal c6rx_srx_conceal
#define srx_crc16 c6rx_srx_crc16
#define srx_init c6rx_srx_init
#define srx_m17 c6rx_srx_m17
#define srx_put c6rx_srx_put
#define srx_ysf c6rx_srx_ysf
#include "ortx_mod.h"
#define C6_BITWAIT 3u   /* the tap's 512 bytes a half at about 2 MHz, as c6m17rx */
#include "c6spi.h"
#include "at1846.h"
#include "c6rx.h"

int c6rx_hear(const ortx_api_t *api, unsigned air, uint32_t max_ms, uint8_t *out, unsigned max) {
    uint32_t sz = 0;
    c6rx_state *c = (c6rx_state *)api->scratch(&sz);
    return c6rx_listen(api, air, c, sz, max_ms, out, max);
}

int c6rx_take(const ortx_api_t *api, uint8_t *out, unsigned max) {
    uint32_t sz = 0;
    const c6rx_state *c = (const c6rx_state *)api->scratch(&sz);
    if (sz < sizeof(c6rx_state) || !c->s.got)
        return 0;
    unsigned n = c->s.n < max ? c->s.n : max;
    api->memcpy(out, c->frames, n * SRX_SF);
    return (int)n;
}
#endif
