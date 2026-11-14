/* micsend_k6: micsend with the send in the K6's pager modem (PHY F, FFSK 1200/1800 Hz at 1800
   baud) instead of PHY R, so a K6 in VOICE MSG on 440.850 plays the UV-380's mic as it arrives
   (Jeff 11-14 09:10Z). Needs an image with the "phy" command; same LED cues as micsend.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 2           /* the signer reads only this file */
#define MR_TX 1
#define MR_SEND 1
#define MR_PHYF 1
#include "micrec.c"
