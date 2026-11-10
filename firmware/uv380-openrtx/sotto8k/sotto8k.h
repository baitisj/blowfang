/* What every sotto8k_*.c wrapper sets before including a sotto decoder source: the UV-380's
 * codec flags (sotto VARIANT=v3.7) and a work-around for OpenRTX's compiler.
 * SPDX-License-Identifier: MIT */
#ifndef SOTTO8K_H
#define SOTTO8K_H

#define SOTTO_V36 1
#define SOTTO_PITCH_THIRD 1

/* OpenRTX's arm-miosix-eabi GCC 9 has no __smlad / __smlald / __smlsld in arm_acle.h (GCC 10
 * added them), so the link fails. Keep sotto's Cortex-M4 code (FX_ARM_DSP: wide products,
 * unpacked codebook) but take fx.h's portable C for those three, the same integers. */
#if defined(__ARM_FEATURE_DSP) && defined(__GNUC__) && !defined(__clang__) && __GNUC__ < 10
#define FX_ARM_DSP
#undef __ARM_FEATURE_DSP
#endif

#endif
