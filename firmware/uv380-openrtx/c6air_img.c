/* c6air_img.c: the image's M17, YSF and DMR sends (docs/uv380-multimode.md, milestone 4): the
   body the c6sotto RAM modules proved on air 11-18 (mod/c6air.h: the shared HR_C6000 core, the
   M17 and YSF framers, the DMR sotto voice call), built into the bench image's flash. PAGER mode's
   M17 / YSF / DMR sub-modes (macro key 4) and the "air" USB command call c6air_send with
   ortx_mod.c's own module API table (om_api), so the image keys exactly as the modules did:
   api->tx_tone, the Morse ident first, the transmit guard, 441.850 or 440.850 MHz only.

   build.sh copies this file and the mod/ headers it needs into openrtx/src/blowfang/mod/, and
   c/common's core and framers into openrtx/c/common/ (where c6air.h's "../../../c/common/"
   finds them). Only the bench image (BLOWFANG_RANGE and BLOWFANG_I2SPROBE) on the UV-380 builds it.
   SPDX-License-Identifier: MIT */
#include "blowfang_config.h"

#if defined(BLOWFANG_RANGE) && defined(BLOWFANG_I2SPROBE) && defined(PLATFORM_MDUV3x0)
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "c6key.h"
#include "w25sec.h"
#include "c6vspi.h"
#include "c6air.h"

int c6air_send(const ortx_api_t *api, unsigned air, unsigned src, unsigned dry) {
    return c6air_run(api, air, src, dry);
}
#endif
