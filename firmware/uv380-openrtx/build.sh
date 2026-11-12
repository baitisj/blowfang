#!/bin/sh
# Build OpenRTX for the UV-380 (TYT MD-UV380 / MD-UV390 / Retevis RT3S) with blowfang's patch:
# a PAGER mode whose PTT sends the stored sotto 1200 message to a K6 (FFSK pager packets, then
# the Morse ident). Images land in firmware/uv380-openrtx/out/.
#
#   sh firmware/uv380-openrtx/build.sh            the radio: out/openrtx_mduv3x0_wrap.bin (for
#                                                 TYT's updater, radio_tool or dmr.tools), the
#                                                 bare .bin and the ELF
#   sh firmware/uv380-openrtx/build.sh linux      OpenRTX's Linux emulator (out/openrtx_linux)
#
#   BLOWFANG_I2SPROBE=1 sh firmware/uv380-openrtx/build.sh
#                                                 the I2S probe image (i2sprobe.h): PAGER mode
#                                                 receives while idle and runs the probe, its
#                                                 lines to USB serial; out/*_i2sprobe*
#   BLOWFANG_RANGE=1 sh firmware/uv380-openrtx/build.sh
#                                                 the range image: PAGER mode's PTT sends the
#                                                 message as PHY R (docs/k6-range.md: coded
#                                                 600/1200 Hz FFSK at 1200 baud, for the K6's
#                                                 rangerx); out/*_range*
#
# Steps: a fresh clone of OpenRTX at the commit the patch was written against (into
# firmware/uv380-openrtx/build/openrtx, which this script owns and deletes), git apply
# uv380-blowfang.patch, copy blowfang's portable transmitter (c/common: pager_tx, ffsk_tx,
# range_tx, cw_tx), blowfang_message.h and the fixed-point sotto 1200 decoder (the sotto
# submodule, with the wrappers in sotto8k/) into openrtx/src/blowfang, then meson.
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
WORK="$HERE/build/openrtx"
OUT="$HERE/out"

rm -rf "$WORK"
mkdir -p "$HERE/build" "$OUT"
git clone -q "$OPENRTX_REPO" "$WORK"
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
cw_tx.c
cw_tx.h
EOF
while read -r f; do
  cp "$HERE/$f" "$WORK/openrtx/src/blowfang/"
done <<EOF
blowfang_message.h
i2sprobe.c
i2sprobe.h
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

cd "$WORK"
if [ "$TARGET" = linux ]; then
  meson setup build_linux
  meson compile -C build_linux openrtx_linux
  cp build_linux/openrtx_linux "$OUT/openrtx_linux$SUFFIX"
else
  meson setup --cross-file cross_cm4.txt build_cm4
  meson compile -C build_cm4 openrtx_mduv3x0_wrap
  cp build_cm4/openrtx_mduv3x0_wrap "$OUT/openrtx_mduv3x0${SUFFIX}_wrap.bin"
  cp build_cm4/openrtx_mduv3x0_bin "$OUT/openrtx_mduv3x0$SUFFIX.bin"
  cp build_cm4/openrtx_mduv3x0 "$OUT/openrtx_mduv3x0$SUFFIX.elf"
  arm-miosix-eabi-size "$OUT/openrtx_mduv3x0$SUFFIX.elf" | tee "$OUT/size$SUFFIX.txt"
fi
echo "built $TARGET in $OUT"
