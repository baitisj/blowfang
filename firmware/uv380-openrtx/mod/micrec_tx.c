/* micrec_tx: micrec with MR_TX. In receive the codec input carries the UV-380's own receiver,
   whatever PD9 and register 26 say (11-14 03:12Z: a station's data and CW ident, no voice), so
   this records while the radio transmits FM from the mic (tx_mic: 1 W, 440.850 only, then the
   ident), when the receiver is off. Same LED cues: blinking, unplug the cable; steady, talk;
   off (after the ident), plug it back in. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 2           /* the signer reads only this file */
#define MR_TX 1
#include "micrec.c"
