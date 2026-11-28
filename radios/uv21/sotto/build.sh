#!/bin/sh
# [S12] sotto 1200 receive and decode for the UV-21 Pro (AT32F421, Cortex-M4 at 120 MHz, no FPU):
# the K6 voice pager's library (c/k6/sotto_pager.c with -DSP_LR, the fixed-point sotto 1200
# decoder in c/fixed) built for thumbv7em, plus a bench ELF that radios/uv21/sotto/bench_uv21.py
# runs in the Cortex-M4 emulator (c/fixed/cm4/run.py) to time it and check its output.
#
#   sh radios/uv21/sotto/build.sh            radios/uv21/build/sotto/{libsotto_uv21.a,bench.elf,
#                                            sotto-cb.bin,size.txt}
#
# Why these flags (docs/uv21-sotto1200.md has the measurements):
# - The same codec flags as the K6's sotto 1200 SPI build (tools/k6_build.sh K6_CODEC=1200
#   K6_CB=spi), so the UV-21 plays bit for bit what the K6 plays: -DSOTTO_V36 -DSOTTO_PITCH_THIRD
#   -DFX_CS512_CONST -DFX_MP_ROWGEN -DSP_LR, the decoder's pool in the firmware's arena
#   (-DSP_DEC_POOL), synth's 2 KB of arrays in that pool and not on the stack (-DFX_SYNTH_STATE:
#   port 21 has about 2.5 KB of stack spare), and the yield hook (-DFX_YIELD_HOOK) so the radio
#   can empty the RF chip's 8-word FSK FIFO while a frame decodes.
# - The codebook (30,720 B) is read from SPI flash a vector at a time (-DFX_CB_SPI, c/common/sp_cb.c):
#   with it in the MCU's flash the decoder links at 44.3 KB, and port 21 leaves 35.3 KB of the
#   60 KB app area. The image format is the K6's (sotto-cb.bin, made here from fx_tables.c); where
#   it goes in the UV-21's SPI flash is the firmware's business (sp_flash_read maps SP_CB_ADDR),
#   and nothing here writes SPI flash: the UV-21's norflash driver stays READ_ONLY.
# - -DFX_NO_ARM_DSP -DFX_WIDE_MUL: fx.h turns FX_ARM_DSP on for any ARMv7E-M, and with it the
#   codebook unpacked (40 B a vector, a 61 KB image) for the encoder's SMLAD search, which a
#   decoder never runs. This keeps the K6's packed 20-byte vectors (the same SPI image) and
#   still takes 32 x 32 products whole (SMULL / SMLAL), the same integers either way.
# - -Oz throughout: 120 MHz is far more than the decoder needs (bench_uv21.py), and flash is
#   what's short.
# Needs clang and ld.lld (ATfE or a distribution's), and a host cc for the codebook image.
# SPDX-License-Identifier: MIT
set -e
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
OUT=${UV21_SOTTO_OUT:-"$ROOT/radios/uv21/build/sotto"}
CC=${UV21_CC:-clang}
AR=${UV21_AR:-llvm-ar}
HOSTCC=${HOSTCC:-cc}
mkdir -p "$OUT"

# the codebook image and its CRC, from this tree's fx_tables.c (the K6's c/k6/cb_image.c)
$HOSTCC -O2 -std=c99 -I"$ROOT/c" "$ROOT/c/k6/cb_image.c" "$ROOT/c/common/sp_cb.c" "$ROOT/c/fixed/fx_tables.c" \
  -o "$OUT/cb_image"
"$OUT/cb_image" "$OUT/sotto-cb.bin" >/dev/null
CRC=$("$OUT/cb_image" -c)

RES=$("$CC" -print-resource-dir)
CFLAGS="--target=thumbv7em-none-eabi -mcpu=cortex-m4 -mthumb -mfloat-abi=soft -nostdinc
  -isystem $ROOT/c/fixed/cm4/inc -isystem $RES/include -ffunction-sections -fdata-sections -std=c99
  -ffreestanding -fno-builtin-calloc -fno-builtin-free -Wall -Wno-unused-function -Werror
  -DSOTTO_V36 -DSOTTO_PITCH_THIRD -DFX_CS512_CONST -DFX_MP_ROWGEN -DFX_SYNTH_STATE -DFX_YIELD_HOOK
  -DSP_LR -DSP_DEC_POOL -DFX_CB_SPI -DSP_CB_CRC=$CRC -DFX_NO_ARM_DSP -DFX_WIDE_MUL -I$ROOT/c -Oz"

$CC $CFLAGS -c "$ROOT/c/k6/sotto_pager.c" -o "$OUT/sotto_pager.o"
# the decoder's hot code (UV21_HOT_OPT, -Oz by default; bench_uv21.py times the choice)
HOT=${UV21_HOT_OPT:--Oz}
$CC $CFLAGS $HOT -Dcalloc=sp_calloc -Dfree=sp_free -c "$ROOT/c/fixed/sotto_fx_dec.c" -o "$OUT/sotto_fx_dec.o"
# sotto_fx_lr.c at -Os, never -Oz: ATfE clang 22's -Oz build of it for thumbv7em plays a different
# signal from slot 53 of a 70-frame test message on (tests/test_uv21_sotto.py; -O1, -Os and -O2 are
# bit-exact, -fwrapv and -fno-strict-aliasing don't help, and a UBSan host build is clean, so it
# looks like the compiler's). The K6's M0 -Oz build of it is bit-exact. -Os costs 42 bytes.
$CC $CFLAGS ${UV21_LR_OPT:--Os} -Dcalloc=sp_calloc -Dfree=sp_free -c "$ROOT/c/fixed/sotto_fx_lr.c" -o "$OUT/sotto_fx_lr.o"
$CC $CFLAGS $HOT -c "$ROOT/c/fixed/fx_math.c" -o "$OUT/fx_math.o"
$CC $CFLAGS -c "$ROOT/c/fixed/fx_tables.c" -o "$OUT/fx_tables.o"
$CC $CFLAGS -c "$ROOT/c/common/sp_cb.c" -o "$OUT/sp_cb.o"
$CC $CFLAGS -c "$ROOT/c/common/sp_vlr.c" -o "$OUT/sp_vlr.o"
LIBOBJS="$OUT/sotto_pager.o $OUT/sotto_fx_dec.o $OUT/sotto_fx_lr.o $OUT/fx_math.o $OUT/fx_tables.o $OUT/sp_cb.o $OUT/sp_vlr.o"
# the memcpy family and the 64-bit divide the firmware links with the library (the bench links
# the Cortex-M4 bench's shim.c and divmod.s instead)
$CC $CFLAGS -fno-builtin -c "$ROOT/c/k6/k6_mem.c" -o "$OUT/k6_mem.o"
$CC $CFLAGS -c "$ROOT/c/k6/k6_uldiv.c" -o "$OUT/k6_uldiv.o"
# the port's receiver and player around the library (uv21_sotto.c: the BK4829 packet receiver
# from radios/uv21/mod/sottorx_core.h, TMR1 CH4 PWM with DMA): in the archive the firmware links,
# not in the bench, which drives the library directly
# its PHY: the K6 pager's D/2400 rung (direct 2-FSK, REG_58 0x00E9, REG_72 0x60CB, RX HPF off),
# the one the BK4829 decodes: job s12rx3 got 41 of 44 packets from the Pluto at -81 and -91 dBm,
# where F/1800 (FFSK, the K6's default) never synced (s12rx2b)
RXPHY="-DSR_58=0x00E9 -DSR_72=0x60CB -DSR_BAUD=2400 -DSR_2B_OR=0x0400"
$CC $CFLAGS $RXPHY -I"$ROOT/radios/uv21/mod" -c "$ROOT/radios/uv21/sotto/uv21_sotto.c" -o "$OUT/uv21_sotto.o"
rm -f "$OUT/libsotto_uv21.a"
$AR rcs "$OUT/libsotto_uv21.a" $LIBOBJS "$OUT/uv21_sotto.o" "$OUT/k6_mem.o" "$OUT/k6_uldiv.o"

# the bench: the library with the K6 bench's stand-in SPI flash (c/k6/flash_bench.c serves
# sotto-cb.bin as if it sat at SP_CB_ADDR) and the Cortex-M4 bench's run-time helpers
python3 -c 'import sys; d = open(sys.argv[1], "rb").read(); print("const unsigned char sp_cb_bench_image[%d] = {%s};" % (len(d), ",".join(map(str, d))))' \
  "$OUT/sotto-cb.bin" > "$OUT/cb_bench_image.c"
$CC $CFLAGS -c "$OUT/cb_bench_image.c" -o "$OUT/cb_bench_image.o"
$CC $CFLAGS -fno-builtin -c "$ROOT/c/k6/flash_bench.c" -o "$OUT/flash_bench.o"
$CC $CFLAGS -fno-builtin -c "$ROOT/c/fixed/cm4/shim.c" -o "$OUT/shim.o"
$CC --target=thumbv7em-none-eabi -mcpu=cortex-m4 -c "$ROOT/c/fixed/cm4/divmod.s" -o "$OUT/divmod.o"
# the 64-bit shifts -Oz leaves as calls (__aeabi_llsl, llsr, lasr; the port gets them from
# Rust's compiler_builtins): the Cortex-M0 bench's portable C ones
$CC $CFLAGS -fno-builtin -c "$ROOT/c/fixed/cm0/rt.c" -o "$OUT/rt.o"
SYMS="sp_init sp_packet sp_status sp_play_start sp_play_frame sp_play_pwm sp_pcm_to_pwm sp_auto_gain_q8
  sp_pcm_hash sp_static_bytes sp_set_yield sp_yield_probe sp_set_stream sp_stream_ready sp_stream_end
  sp_cb_setup sp_cb_ready sp_dec_pool sp_dec_need heap_used"
U=$(for s in $SYMS; do printf -- '-u %s ' "$s"; done)
ld.lld -T "$ROOT/c/fixed/cm4/link.ld" --gc-sections -e sp_init $U \
  $LIBOBJS "$OUT/cb_bench_image.o" "$OUT/flash_bench.o" "$OUT/shim.o" "$OUT/rt.o" "$OUT/divmod.o" -o "$OUT/bench.elf"

# what the firmware would link: the library's entry points alone (no bench image, no shim), so
# the flash the port pays for sotto 1200, codebook excluded
cat > "$OUT/fit.ld" <<'EOF'
MEMORY { FLASH (rx) : ORIGIN = 0x08001000, LENGTH = 1M
         RAM (rwx)  : ORIGIN = 0x20000000, LENGTH = 1M }
SECTIONS {
  .text : { *(.text*) *(.rodata*) } > FLASH
  .data : { *(.data*) } > RAM AT > FLASH
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
  /DISCARD/ : { *(.ARM.exidx*) *(.comment) }
}
EOF
printf 'void sp_flash_read(unsigned a, void *b, unsigned n) { (void)a, (void)b, (void)n; }\n' > "$OUT/fit_flash.c"
printf 'void sp_flash_erase4k(unsigned a) { (void)a; }\n' >> "$OUT/fit_flash.c"
printf 'void sp_flash_write(unsigned a, const void *b, unsigned n) { (void)a, (void)b, (void)n; }\n' >> "$OUT/fit_flash.c"
$CC $CFLAGS -c "$OUT/fit_flash.c" -o "$OUT/fit_flash.o"
FIT="sp_init sp_packet sp_status sp_play_start sp_play_pwm sp_auto_gain_q8 sp_pcm_hash sp_set_yield
  sp_set_stream sp_stream_ready sp_stream_end sp_cb_setup sp_cb_ready sp_dec_pool sp_dec_need sp_missing
  uv21_sotto"
UF=$(for s in $FIT; do printf -- '-u %s ' "$s"; done)
ld.lld -T "$OUT/fit.ld" --gc-sections -e sp_init $UF "$OUT/fit_flash.o" "$OUT/libsotto_uv21.a" "$OUT/rt.o" -o "$OUT/fit.elf"
FLASH=$(llvm-size -A "$OUT/fit.elf" | awk '$1 == ".text" || $1 == ".data" { s += $2 } END { print s }')
BSS=$(llvm-size -A "$OUT/fit.elf" | awk '$1 == ".bss" { print $2 }')
printf 'flash=%s bss=%s codebook_crc=%s\n' "$FLASH" "$BSS" "$CRC" | tee "$OUT/size.txt"
