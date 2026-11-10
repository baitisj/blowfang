#!/bin/sh
# Build the main K6 images with several compilers for the C library (Aura's Rust stays on rustc)
# and compare flash, RAM, stack and the decoder's emulated speed. A study, not the build: CI's
# images stay on clang (k6-firmware.yml). docs/k6-compilers.md has the results.
#
#   sh tools/k6_cc/compare.sh OUT [CONFIG ...]     results in OUT/CONFIG/, table in OUT/table.txt
#
# CONFIG (default: all of them):
#   clang        the build as CI runs it (clang from PATH, the per-file -O2 / -Os / -Oz)
#   clang-Oz     the same, with the -Os files at -Oz
#   atfe         the Arm Toolchain for Embedded's clang ($ATFE: its bin directory)
#   atfe-Oz      atfe, with the -Os files at -Oz
#   atfe-Oz-nu   atfe-Oz, with the -O2 (decoder) files not unrolled (-fno-unroll-loops)
#   gcc          arm-none-eabi-gcc for every file (plus libgcc's Thumb-1 switch helpers)
#   gcc-hybrid   gcc for the -Os / -Oz files, clang for the -O2 (decoder) files
# BENCH=1 also times the v3.7 decoder on the emulated M0 (c/k6/bench_k6.py; needs unicorn,
# capstone and make -C sotto/c VARIANT=v3.7 fixed) on BENCH_IN (default: codec2's ve9qrp_10s.raw).
# SPDX-License-Identifier: MIT
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd); shift
# the configs, one per line
ALL=$(cat <<'EOF2'
clang
clang-Oz
atfe
atfe-Oz
atfe-Oz-nu
gcc
gcc-hybrid
EOF2
)
CONFIGS=${*:-$ALL}
BENCH_IN=${BENCH_IN:-$ROOT/sotto/codec/codec2/raw/ve9qrp_10s.raw}

gcc_extra() {
  # the libgcc members gcc's code calls that Rust's compiler_builtins doesn't have, one object each
  # so the linker pulls only what's used
  d="$OUT/libgcc"; mkdir -p "$d"
  lg=$(arm-none-eabi-gcc -mcpu=cortex-m0 -mthumb -print-libgcc-file-name)
  (cd "$d" && arm-none-eabi-ar x "$lg" \
    _thumb1_case_sqi.o \
    _thumb1_case_uqi.o \
    _thumb1_case_shi.o \
    _thumb1_case_uhi.o \
    _thumb1_case_si.o \
    _clzdi2.o)
  (cd "$d" && arm-none-eabi-ld -r \
    _thumb1_case_sqi.o \
    _thumb1_case_uqi.o \
    _thumb1_case_shi.o \
    _thumb1_case_uhi.o \
    _thumb1_case_si.o \
    -o thumb1case.o)
  echo "$d/thumb1case.o $d/_clzdi2.o"
}

for c in $CONFIGS; do
  case $c in
    clang) set -- ;;
    clang-Oz) set -- K6_CC="$HERE/mux" COLD_OS=-Oz ;;
    atfe) set -- K6_CC="$ATFE/clang" K6_AR="$ATFE/llvm-ar" ;;
    atfe-Oz) set -- K6_CC="$HERE/mux" HOT_CC="$ATFE/clang" COLD_CC="$ATFE/clang" COLD_OS=-Oz K6_AR="$ATFE/llvm-ar" ;;
    atfe-Oz-nu) set -- K6_CC="$HERE/mux" HOT_CC="$ATFE/clang" COLD_CC="$ATFE/clang" COLD_OS=-Oz HOT_EXTRA=-fno-unroll-loops K6_AR="$ATFE/llvm-ar" ;;
    gcc) set -- K6_CC="$HERE/gcc-k6" K6_AR=arm-none-eabi-ar K6_OBJCOPY=arm-none-eabi-objcopy K6_LIBEXTRA="$(gcc_extra)" ;;
    gcc-hybrid) set -- K6_CC="$HERE/mux" COLD_CC="$HERE/gcc-k6" K6_LIBEXTRA="$(gcc_extra)" ;;
    *) echo "unknown config $c" >&2; exit 1 ;;
  esac
  R="$OUT/$c"; rm -rf "$R"; mkdir -p "$R/lib"
  echo "== $c"
  rm -rf "$ROOT/firmware/k6-aura/out" "$ROOT"/c/k6/build*
  # FULL, INSTALL and TABLE images are reports or one-off installers; the modules are clang's (-fropi)
  env FULL=0 INSTALL=0 TABLE=0 MODULES=0 AURA_BASELINE=0 SOTTO_BUILD=cc-compare "$@" \
    sh "$ROOT/firmware/k6-aura/build.sh" > "$R/log.txt" 2>&1 || echo "(build.sh stopped: an image didn't fit)" | tee -a "$R/log.txt"
  cp "$ROOT"/firmware/k6-aura/out/size-*.txt "$R/" 2>/dev/null || true
  for d in "$ROOT"/c/k6/build*; do [ -f "$d/bench.elf" ] && cp "$d/bench.elf" "$R/lib/$(basename "$d").elf"; done
  grep -h -E '^== |^flash ' "$R"/size-*.txt || true
  if [ "${BENCH:-0}" = 1 ] && [ -f "$R/lib/build-cbs.elf" ]; then
    python3 "$ROOT/c/k6/bench_k6.py" "$R/lib/build-cbs.elf" "$BENCH_IN" --pcm "$R/pcm-v37.raw" --json "$R/bench-v37.json" > "$R/bench-v37.log" 2>&1 ||
      echo "bench failed: $R/bench-v37.log"
  fi
done
python3 "$HERE/table.py" "$OUT" $CONFIGS | tee "$OUT/table.txt"
