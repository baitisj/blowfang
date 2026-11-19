#!/bin/sh
# Build OpenRTX for the UV-380 (TYT MD-UV380 / MD-UV390 / Retevis RT3S) with blowfang's patch:
# a PAGER mode whose PTT sends the stored sotto 1200 message to a K6 (FFSK pager packets, then
# the Morse ident). Images land in firmware/uv380-openrtx/out/.
#
#   sh firmware/uv380-openrtx/build.sh            the radio: out/openrtx_mduv3x0_wrap.bin (for
#                                                 TYT's updater, radio_tool or dmr.tools), the
#                                                 bare .bin and the ELF
#   sh firmware/uv380-openrtx/build.sh mduv3x0p   the MD-UV380/390 PLUS (10 W, Jeff's radio):
#                                                 out/openrtx_mduv3x0p_wrap.bin. The Plus swaps two
#                                                 pins (backlight PC6, PA band select PD8); the plain
#                                                 image on a Plus leaves the screen dark and drives
#                                                 the backlight PWM into the PA band select, so
#                                                 never flash a plain image on a Plus
#   sh firmware/uv380-openrtx/build.sh linux      OpenRTX's Linux emulator (out/openrtx_linux)
#
#   BLOWFANG_I2SPROBE=1 sh firmware/uv380-openrtx/build.sh
#                                                 the I2S probe image (i2sprobe.h): PAGER mode
#                                                 receives while idle and runs the probe, its
#                                                 lines to USB serial, and takes signed RAM
#                                                 modules over USB serial (ortx_mod.h);
#                                                 out/*_i2sprobe*
#   BLOWFANG_RANGE=1 sh firmware/uv380-openrtx/build.sh
#                                                 the range image: PAGER mode's PTT sends the
#                                                 message as PHY R (docs/k6-range.md: coded
#                                                 600/1200 Hz FFSK at 1200 baud, for the K6's
#                                                 rangerx); out/*_range*
#
# Steps: a fresh clone of OpenRTX at the commit the patch was written against (into
# firmware/uv380-openrtx/build/openrtx, which this script owns and deletes), git apply
# uv380-blowfang.patch, copy blowfang's portable transmitter and receiver (c/common: pager_tx, ffsk_tx,
# range_tx, cw_tx, range_rx, pager_rx, fsk_rx), the module checker (c/common/mod_core), blowfang_message.h, the probe and the
# module loader (i2sprobe, ortx_mod), the HR_C6000 core with the M17/YSF/DMR framers (c6air_img.c
# and mod/c6air.h: the bench image's M17, YSF and DMR sends) and the fixed-point sotto 1200
# decoder (the sotto submodule, with the wrappers in sotto8k/) into openrtx/src/blowfang, then meson.
#
# Needs: what OpenRTX's own build needs (openrtx.org, compiling.md). The radio build uses
# OpenRTX's arm-miosix-eabi toolchain; CI runs it in OpenRTX's published devcontainer image.
# The Linux build needs meson, libsdl2-dev, libcodec2-dev, libreadline-dev, libusb-1.0-0-dev,
# cmake (meson builds radio_tool from source).
#
# The patch is GPL-3.0-or-later, like OpenRTX; the blowfang sources it adds are MIT.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OPENRTX_REPO=${OPENRTX_REPO:-https://github.com/OpenRTX/OpenRTX}
OPENRTX_REV=20b4088d216b63aa9ae060f87d3b125c4accdfe8     # master, 2026-11-07
TARGET=${1:-mduv3x0}
if [ "$TARGET" = mduv3x0p ]; then
  # OpenRTX's mduv3x0-plus branch: master 20b4088 plus "Add MD-UV3x0 Plus target" (PR #300,
  # issue #275: target mduv3x0p, PLATFORM_MDUV3x0P swaps PA_SEL_SW and LCD_BKLIGHT in pinmap.h)
  OPENRTX_REV=f969eff04220106ace6edccbeef91632cb486856   # mduv3x0-plus, 2026-11-09
fi
WORK="$HERE/build/openrtx"
OUT="$HERE/out"

rm -rf "$WORK"
mkdir -p "$HERE/build" "$OUT"
git clone -q "$OPENRTX_REPO" "$WORK"
git -C "$WORK" fetch -q origin "$OPENRTX_REV" 2>/dev/null || true
git -C "$WORK" checkout -q "$OPENRTX_REV"
git -C "$WORK" apply "$HERE/uv380-blowfang.patch"

mkdir -p "$WORK/openrtx/src/blowfang"
while read -r f; do
  cp "$ROOT/c/common/$f" "$WORK/openrtx/src/blowfang/"
done <<EOF
pager_tx.c
pager_tx.h
ffsk_tx.c
ffsk_tx.h
range_tx.c
range_tx.h
range_rx.c
range_rx.h
pager_rx.c
pager_rx.h
fsk_rx.c
fsk_rx.h
cw_tx.c
cw_tx.h
mod_core.c
mod_core.h
EOF
while read -r f; do
  cp "$HERE/$f" "$WORK/openrtx/src/blowfang/"
done <<EOF
blowfang_message.h
i2sprobe.c
i2sprobe.h
ortx_mod.c
ortx_mod.h
pager_rec.c
pager_rec.h
pager_listen.c
pager_listen.h
mod/rxaudio.h
EOF
# the HR_C6000 core and framers for the image's M17, YSF and DMR sends (c6air_img.c, milestone 4)
# and its M17 and YSF receive (c6rx_img.c, milestone 5): mod/c6air.h and mod/c6rx.h are the RAM
# modules' bodies, and find c/common at "../../../c/common/"
mkdir -p "$WORK/openrtx/src/blowfang/mod" "$WORK/openrtx/c/common"
cp "$HERE/c6air_img.c" "$WORK/openrtx/src/blowfang/mod/"
cp "$HERE/c6rx_img.c" "$WORK/openrtx/src/blowfang/mod/"
while read -r f; do
  cp "$HERE/mod/$f" "$WORK/openrtx/src/blowfang/mod/"
done <<EOF
at1846.h
c6air.h
c6bus_mod.h
c6key.h
c6rx.h
c6slot.h
c6spi.h
c6vspi.h
w25sec.h
EOF
while read -r f; do
  cp "$ROOT/c/common/$f" "$WORK/openrtx/c/common/"
done <<EOF
c6_core.c
c6_core.h
dmr_sotto.h
dmr_voice.h
dsr_tx.c
dsr_tx.h
m17_rx.c
m17_rx.h
m17_tx.c
m17_tx.h
sotto_rx.c
sotto_rx.h
ysf_rx.c
ysf_rx.h
ysf_tx.c
ysf_tx.h
EOF
SUFFIX=
{
  echo "/* written by build.sh */"
  if [ "${BLOWFANG_I2SPROBE:-0}" = 1 ]; then
    echo "#define BLOWFANG_I2SPROBE 1"
    SUFFIX=_i2sprobe
  fi
  if [ "${BLOWFANG_RANGE:-0}" = 1 ]; then
    echo "#define BLOWFANG_RANGE 1"
    SUFFIX=${SUFFIX}_range
  fi
} > "$WORK/openrtx/src/blowfang/blowfang_config.h"

# the fixed-point sotto 1200 decoder (8 kHz) from the sotto submodule, for the speaker preview:
# sotto/c's headers and c/fixed, and the wrappers that build its sources with the codec flags
SOTTO="$ROOT/sotto/c"
if [ ! -f "$SOTTO/fixed/sotto_fx_lr.c" ]; then
  echo "build.sh: no sotto/c/fixed: git submodule update --init sotto" >&2
  exit 1
fi
mkdir -p "$WORK/openrtx/src/blowfang/sotto/fixed"
cp "$SOTTO"/*.h "$SOTTO"/*.inc "$WORK/openrtx/src/blowfang/sotto/"
cp "$SOTTO"/fixed/*.h "$SOTTO"/fixed/*.inc "$SOTTO"/fixed/*.c "$WORK/openrtx/src/blowfang/sotto/fixed/"
cp "$HERE"/sotto8k/*.c "$HERE"/sotto8k/*.h "$WORK/openrtx/src/blowfang/"
# Codec2's malloc goes to the sotto encoder's arena first (c2_alloc.h): meson puts packagefiles
# over the codec2 checkout at setup, and the patch's codec2 meson.build includes it everywhere
cp "$HERE/c2_alloc.h" "$WORK/subprojects/packagefiles/codec2/"

cd "$WORK"
if [ "$TARGET" = linux ]; then
  meson setup build_linux
  meson compile -C build_linux openrtx_linux
  cp build_linux/openrtx_linux "$OUT/openrtx_linux$SUFFIX"
else
  if [ "$TARGET" = mduv3x0p ]; then
    # the swap must be in the tree, or this would be a plain image under a Plus name
    grep -q 'define LCD_BKLIGHT GPIOC,6' platform/targets/MD-UV3x0/pinmap.h
  fi
  T=$TARGET
  meson setup --cross-file cross_cm4.txt build_cm4
  meson compile -C build_cm4 "openrtx_${T}_wrap"
  cp "build_cm4/openrtx_${T}_wrap" "$OUT/openrtx_$T${SUFFIX}_wrap.bin"
  cp "build_cm4/openrtx_${T}_bin" "$OUT/openrtx_$T$SUFFIX.bin"
  cp "build_cm4/openrtx_$T" "$OUT/openrtx_$T$SUFFIX.elf"
  arm-miosix-eabi-size "$OUT/openrtx_$T$SUFFIX.elf" | tee "$OUT/size_$T$SUFFIX.txt"
  # RAM by section (docs/uv380-multimode.md, "Budget"): .data/.bss are in the 64 KB CCM, .bss2
  # in the 128 KB SRAM, the heap is what SRAM has left after .bss2; flash is 868,352 bytes from
  # 0x0800C000. Then the 30 largest symbols in RAM, so a PR shows what its RAM costs.
  arm-miosix-eabi-size -A -x "$OUT/openrtx_$T$SUFFIX.elf" | tee -a "$OUT/size_$T$SUFFIX.txt"
  echo "largest RAM symbols (size, type, name):" | tee -a "$OUT/size_$T$SUFFIX.txt"
  arm-miosix-eabi-nm -S -t d --size-sort "$OUT/openrtx_$T$SUFFIX.elf" \
    | awk '$3 ~ /^[bBdD]$/ { printf "%8d %s %s\n", $2, $3, $4 }' \
    | tail -n 30 | tee -a "$OUT/size_$T$SUFFIX.txt"
fi
echo "built $TARGET in $OUT"
