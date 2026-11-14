/* micsend: micrec_tx, then the recording sent as PHY R range packets (Jeff 11-13 19:36Z: live
   sotto 1200 from the UV-380 mic, sent as range packets). Same LED cues as micrec_tx: blinking,
   unplug the cable; dark while the unplug's own PTT send runs; orange (or red), talk; dark, plug
   it back in. After the report it asks for the send (tx_request: 1 W, 440.850 only, then the
   ident), which goes out once the module ends. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 2           /* the signer reads only this file */
#define MR_TX 1
#define MR_SEND 1
#include "micrec.c"
