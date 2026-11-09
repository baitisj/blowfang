# blowfang

Alternative firmware for radios. The first radio is the Baofeng UV-K6 / K61 (KD32F328 Cortex-M0,
FD6818 RF chip): Aura, the open Rust firmware, with a sotto voice pager, an IF receiver and
softmodem, RAM modules loaded over the air (FT8, WSPR, AIS, APRS clock, DMR detect, CW ID, Morse,
band surf, the signal explorer, MIDI, GROUNDWAVE) and the host tools that build, flash and bench
them.

```sh
git clone --recurse-submodules https://github.com/baitisj/blowfang
```

## Where things are

- `c/k6/`: the K6 C library (pager reassembly and playback, IF receiver, module loader) and its host builds
- `c/k6/mod/`: the RAM modules, one file each; `docs/k6-modules.md` is the module API (level 8)
- `firmware/k6-aura/`: the patch on Aura and `build.sh`, which builds the radio images
- `modem/`: the Python side: K6 packets and FSK (`modem.k6`), modules (`k6mod`), PSK31, APRS, CI-V PTT
- `sim/k6if/`, `sim/k6digi/`: models of the K6's IF path and digital modes
- `tools/k6_*`: bench tools (`k6_update.py` flashes, `k6_bench.py` owns the PTT lock and CW ID keeper)
- `tests/`: host and emulated Cortex-M0 tests; `tests/data/k6/air/` holds on-air logs replayed in CI
- `docs/k6-*.md`: design notes and bench results, one per subsystem

## The sotto codec

The voice codec lives in [baitisj/sotto](https://github.com/baitisj/sotto) and is a git
submodule at `sotto/`, pinned to one sotto commit. The K6 build compiles sotto's fixed-point
sources itself with its own flags, so `c/fixed` and the few `c/sotto_*` headers it uses are
symlinks into the submodule:

- `c/fixed`
- `c/sotto.h`
- `c/sotto_internal.h`
- `c/sotto_lr_dec.inc`
- `c/sotto_lr_tables.h`
- `c/sotto_vlr.h`
- `c/sotto_vlr_dec.inc`
- `c/sotto_vlr_tables.c`
- `c/sotto_vlr_tables.h`

To take a newer sotto: `git -C sotto checkout <commit>`, then commit the `sotto` pointer. The
weekly CI run also tests against sotto main, so a codec change that breaks the K6 shows up early.
Where the docs say `c/sotto_enc` or `c/sotto_dec`, build them with `make -C sotto` and run
`sotto/c/sotto_enc`.

## Build and test

```sh
make -C sotto/c VARIANT=v3.7 fixed PYTHON=false   # the codec's host decoders the tests use
make -C c/k6                                       # the K6 library's host builds
python -m pytest -q -n auto tests/                 # needs numpy scipy soundfile pytest pytest-xdist unicorn capstone (CI adds pytest-split)
sh firmware/k6-aura/build.sh                       # the radio images (Rust + clang/lld); see its README
```

CI (`.github/workflows/k6-firmware.yml`) runs the same and keeps each image as an artifact.

### Iterating quickly

While working on one area, run only its test file; most finish in seconds:

```sh
python -m pytest -q tests/test_k6_air.py tests/test_k6_air_cases.py   # on-air recordings, about 2 s
python -m pytest -q -n auto tests/test_k6_ifpager.py                  # an emulated-M0 module, about 1 min
python -m pytest -q -k "not on_m0 and not emulated" tests/test_k6_ifrx.py   # skip the emulated sessions
```

CI splits the host tests into four shards that run side by side, so a full run takes about
2.5 minutes. Each shard's share comes from `tests/durations.json` (pytest-split). A new test
counts as average until that file is refreshed, so after adding a test that takes more than
about 20 s, refresh it:

```sh
python -m pytest -q -n auto --store-durations --durations-path tests/durations.json \
    $(grep -o 'tests/test_[a-z0-9_]*\.py' .github/workflows/k6-firmware.yml)
```

## History

This repository was split out of baitisj/sotto on 2026-11-09 with `git filter-repo`, keeping
every commit that touched these files. Pull requests up to #173 are in
[baitisj/sotto](https://github.com/baitisj/sotto/pulls?q=is%3Apr). Commit IDs changed in the
split: images flashed before it report sotto commits as their build ID, and
`docs/sotto-commit-map.txt` maps each old sotto commit to its blowfang commit.

## Never committed here

- K6 bootloader dumps (`boot.bin`)
- Yaesu firmware or decrypted images
- module signing keys (`k6-mod.key`)
- built images (`.bin`, `.elf`, `libsotto_k6.a`): CI keeps them as artifacts

The *Forbidden files* workflow fails a push or PR that adds one.

## Licence

MIT (`LICENSE`), except `firmware/k6-aura/aura-pager.patch`, which is GPL-3.0 like Aura, the
firmware it patches (`firmware/k6-aura/LICENSE.md`). The Aura images CI builds are GPL-3.0 for the
same reason. `c/k6/ft8/ft8k_tables.h` is generated from ft8_lib (MIT).
