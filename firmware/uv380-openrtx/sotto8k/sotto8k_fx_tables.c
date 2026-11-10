/* One source of the fixed-point sotto 1200 decoder (8 kHz) in OpenRTX: sotto's
 * c/fixed/fx_tables.c, built with the UV-380's codec flags (sotto VARIANT=v3.7: -DSOTTO_V36
 * -DSOTTO_PITCH_THIRD), which meson can't give one file. build.sh copies this here and
 * sotto/c (the submodule) into openrtx/src/blowfang/sotto.
 * SPDX-License-Identifier: MIT */
#define SOTTO_V36 1
#define SOTTO_PITCH_THIRD 1
#include "sotto/fixed/fx_tables.c"
