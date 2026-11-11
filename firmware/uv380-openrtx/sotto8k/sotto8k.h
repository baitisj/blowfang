/* What every sotto8k_*.c wrapper sets before including a sotto decoder source: the UV-380's
 * codec flags (sotto VARIANT=v3.7).
 * SPDX-License-Identifier: MIT */
#ifndef SOTTO8K_H
#define SOTTO8K_H

#define SOTTO_V36 1
#define SOTTO_PITCH_THIRD 1

/* OpenRTX's arm-miosix-eabi GCC 9 has no SMLAD / SMLALD intrinsics in arm_acle.h; sotto's
 * fx.h then takes the same instructions as inline assembly (FX_ARM_DSP_ASM) by itself. */

#endif
