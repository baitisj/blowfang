/* One source of the fixed-point sotto 1200 encoder (8 kHz) in OpenRTX: sotto's
 * c/fixed/sotto_fx_enc.c, built with the UV-380's codec flags (sotto VARIANT=v3.7, set in sotto8k.h),
 * which meson can't give one file. build.sh copies this here and
 * sotto/c (the submodule) into openrtx/src/blowfang/sotto.
 * SPDX-License-Identifier: MIT */
#include "sotto8k.h"
#include "sotto8k_enc_arena.h"    /* calloc, free: a static arena (CCM on the radio) */
#include "sotto/fixed/sotto_fx_enc.c"
