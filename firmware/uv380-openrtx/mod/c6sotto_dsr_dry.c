/* c6sotto DRY (no keying), DSotto raw: the radio's sotto message as DSR120 frames through the
   shared HR_C6000 core (c6sotto.c, docs/uv380-dsotto-raw.md).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the ident first and the transmit guard */
#define SOTTO_AIR 4
#define TX_DRY 1
#include "c6sotto.c"
