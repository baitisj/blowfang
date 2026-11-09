# K6 compilers: is clang the best choice?

Study, 2026-11-09. Since then, CI's M0 builds use the **Arm Toolchain for Embedded 22.1.0**,
with the size files at `-Oz` (provisional, Jeff 2026-11-09). That covers the C library, the
RAM modules and the tests' module builds. `ifpager.c` and `xdmr.c` stay at `-Os`, as their
emulator timing tests build them. `tools/k6_cc/bin/clang` sends every `--target=thumb...`
compile to ATfE's clang (`K6_ATFE`), and host compiles to the next clang on `PATH`.
`k6-firmware.yml` downloads ATfE once and caches it. To build the same way by hand:

    export K6_ATFE=/path/to/ATfE-22.1.0-Linux-x86_64/bin PATH=$PWD/tools/k6_cc/bin:$PATH

Without that, the builds use whatever clang is on `PATH`, as before. `tools/k6_cc/compare.sh`
repeats the comparison below, and the workflow `K6 compiler study`
(`.github/workflows/k6-compilers.yml`) runs it in CI by hand or when the study's files
change. Nothing was flashed to a radio.

## What the compiler can change

An image has two halves:

- **Aura**, the Rust firmware (drivers, UI, launcher), built by `rustc` at `opt-level = "z"`
  with fat LTO. That's about 53 KB of the pager images and 68 KB of EXPLORE. `rustc` only has
  an LLVM back end that works for this (the GCC back end and gccrs aren't ready for
  production firmware), so no C compiler choice touches it.
- **`libsotto_k6.a`**, the C library (`tools/k6_build.sh`): the sotto decoders, the pager,
  the IF receiver, EXPLORE's engine. That's about 31-53 KB including tables. CI builds it with
  Ubuntu 24.04's **clang 18.1.3** and `ld.lld`, for
  `--target=thumbv6m-none-eabi -mcpu=cortex-m0 -mthumb -mfloat-abi=soft`,
  `-ffunction-sections -fdata-sections`. The level is set per file: `-O2` for the decoder's
  hot files (`fx_math.c`, `sotto_fx_dec.c`, the hiss layer without unrolling), `-Os` for the
  pager, the IF receiver and xdmr, and `-Oz` for EXPLORE's engine.

The RAM modules are built with clang too, with `-fropi` (position-independent code and
constants). gcc has no ROPI mode for Thumb-1, so the modules would stay on clang whatever the
library uses.

## Compilers tried

- **clang 18.1.3**: today's build, the baseline.
- **clang 18 with -Oz**: the same, with the `-Os` files at `-Oz`.
- **Arm Toolchain for Embedded (ATfE) 22.1.0**: Arm's LLVM build for embedded, which is clang
  22 plus Arm's M-profile tuning and its own C libraries. The library is freestanding (Rust's
  `compiler_builtins` supplies `memcpy` and the division helpers), so only the compiler counts
  here.
- **ATfE with -Oz**: the same, with the `-Os` files at `-Oz`.
- **arm-none-eabi-gcc 13.2.1** (Ubuntu's `gcc-arm-none-eabi`), with the same per-file levels.
  It needs libgcc's Thumb-1 switch-table helpers (`__gnu_thumb1_case_*`) linked beside the
  library.
- **gcc/clang mix**: gcc for the `-Os`/`-Oz` files, clang for the `-O2` ones.
- **gcc with -O3** on the hot files.
- **ATfE with cross-language LTO**: C bitcode plus `-C linker-plugin-lto`, so the C and the
  Rust are optimised together. ATfE 22 matches rustc 1.97's LLVM 22.
- **ATfE with -Oz, decoder not unrolled**: the hot files at `-O2 -fno-unroll-loops`.

Not tried:

- **Arm Compiler 6** and **IAR** are commercial, with no licence here. Arm Compiler 6 is
  also clang-based, and Arm is replacing it with ATfE.
- **Newer gcc** (Arm GNU Toolchain 14/15): the download host is blocked from the cloud
  sessions.

## Results

These are local builds: rustc 1.97 and the same Aura commit for every column. Local builds
come out about 0.6-1 KB bigger than CI's, so compare the columns with each other, not with
CI's numbers.

### Flash

Bytes of the 122,880 available, with the change against clang 18.

| image | clang 18 | clang 18 -Oz | ATfE 22 | ATfE 22 -Oz | gcc 13 | gcc/clang mix | gcc -O3 hot | ATfE x-LTO | ATfE -Oz, no unroll |
|---|---|---|---|---|---|---|---|---|---|
| pager-only | 108,728 | −1,116 | −944 | **−2,044** | −5,244 | −876 | −3,712 | −8 | −7,288 |
| pager-lr | 109,700 | −1,132 | −964 | **−2,124** | −5,228 | −940 | −3,792 | −32 | −7,252 |
| pager-vlr | 113,716 | −1,528 | −1,144 | **−2,656** | −5,744 | −1,456 | −4,308 | −212 | −7,784 |
| pager-vlrh (hiss) | 123,744 | −1,552 | −1,260 | **−2,744** | −5,316 | −1,512 | +5,468 | −324 | −8,024 |
| explore | 118,780 | −1,312 | −920 | **−2,208** | −2,084 | −2,084 | −2,084 | +20 | −2,192 |
| pager-vlr-rom | 118,472 | −1,428 | −1,044 | **−2,320** | −5,656 | −1,368 | −4,220 | −144 | −7,448 |
| pager-vlri | 119,132 | −1,448 | −1,060 | **−2,360** | −5,656 | −1,368 | −4,220 | −100 | −7,488 |

Static RAM moves by 8-212 bytes at most, and only for EXPLORE (gcc saves 212 B there). The
deepest direct-call stack is within 40 B of clang's for ATfE. With gcc it's up to 272 B
deeper in the voice images, and 104 B shallower in EXPLORE.

### Speed

The decoder on the emulated M0 (`c/k6/bench_k6.py`: 96 MHz, two flash wait states), in MHz
per 20 ms frame, mean / worst. The input is librispeech_251_m (12 s). The PCM output was
bit-identical to clang 18's in every column.

| codec | clang 18 | clang 18 -Oz | ATfE 22 | ATfE 22 -Oz | gcc 13 | gcc/clang mix | gcc -O3 hot | ATfE -Oz, no unroll |
|---|---|---|---|---|---|---|---|---|
| v3.7 (2400) | 58.5 / 89.6 | 58.6 / 89.6 | 54.8 / 83.6 | **54.8 / 83.6** | 66.9 / 97.3 | 58.5 / 89.6 | 66.7 / 97.1 | 64.5 / 100.0 |
| sotto 1200 | 58.5 / 83.3 | 58.6 / 83.4 | 54.7 / 77.7 | **54.8 / 77.8** | 67.1 / 91.7 | 58.6 / 83.4 | 66.7 / 91.3 | 64.4 / 92.4 |
| sotto 600 | 57.9 / 82.7 | 58.0 / 82.7 | 54.2 / 77.2 | **54.3 / 77.3** | 66.2 / 90.8 | 58.0 / 82.8 | 66.0 / 90.8 | 63.7 / 91.8 |
| sotto 400 | 57.8 / 92.3 | 57.8 / 92.4 | 54.1 / 86.1 | **54.2 / 86.2** | 66.1 / 100.4 | 57.9 / 92.4 | 66.0 / 100.4 | 63.6 / 102.7 |
| hiss 1200 | 71.9 / 142.4 | 72.0 / 142.5 | 67.4 / 133.6 | **67.5 / 133.6** | 81.5 / 163.1 | 72.0 / 142.6 | 80.2 / 159.5 | 76.8 / 156.5 |
| hiss 400 | 72.2 / 146.8 | 72.3 / 147.0 | 67.7 / 137.8 | **67.7 / 137.9** | 81.7 / 171.2 | 72.3 / 147.1 | 80.4 / 163.2 | 77.1 / 165.0 |

### RAM modules

All 74 modules, built at their own `-Os`/`-Oz`:

- clang 18: 251,834 B in total.
- ATfE 22: 250,394 B, which is 1,440 B less.

The GROUNDWAVE modules each lose about 320 B. `gw` goes from 11,681 to 11,357 B, against the
11,752 B module limit. `dmrrx` grows by 224 B and `aprsclk` by 88 B.

## What it means

- **gcc lives up to its size reputation, but not on speed.** It's 5-6 KB smaller on the
  voice images, but the decoder needs 14% more cycles, and `-O3` doesn't win them back. In the
  worst frames v3.7 and sotto 400 go past the 96 MHz clock (97-100 MHz). Most of gcc's saving
  is in the `-O2` decoder files, because gcc unrolls far less than clang. On the `-Os`/`-Oz`
  files alone gcc beats clang by about 0.9-2 KB (the gcc/clang mix), which is about what
  clang's own `-Oz` gives.
- **The size is mostly in loop unrolling.** clang with the decoder not unrolled is 7-8 KB
  smaller than today's build, smaller even than gcc. It's still faster than gcc, but 10% slower
  than today, and its worst frames go past 96 MHz too. That's a trade-off between speed and
  size, not a choice of compiler. The middle ground would be to keep unrolling only in the few
  loops that dominate the cycles (`fx_oa_block` and the synthesis), and turn it off in the rest
  of the decoder.
- **ATfE 22 wins on both.** It's about 1 KB smaller and about 6% faster than clang 18, with the
  same flags and the same PCM. Most of that is probably LLVM 18 → 22 rather than Arm's own
  tuning (a stock clang 22 wasn't tried). With the `-Os` files at `-Oz` it gives 2.0-2.7 KB
  back on every image, and the hiss image fits with room to spare.
- **Cross-language LTO doesn't pay** (−8 to −324 B). The C library has few calls across
  files, and its hot code is already `-O2`.

## Recommendation

1. **Free and safe now:** `-Os` → `-Oz` for the size-tuned C files with today's clang. That
   gives 1.1-1.6 KB per image and the same speed.
2. **Next:** build the C library with ATfE 22 (or any clang 22), together with step 1. That's
   2.0-2.7 KB per image, a decoder about 6% faster and modules 1.4 KB smaller. CI would
   download the 190 MB ATfE tarball from GitHub (the study workflow already does), or use
   apt.llvm.org's clang-22. Before switching: run the full host tests and a radio check of the
   images.
3. **Not gcc:** it's slower where the time goes, needs libgcc helpers, and can't build the
   ROPI modules.
4. **If flash gets tight again:** selective unrolling in the decoder, worth up to about 5 KB
   more.
