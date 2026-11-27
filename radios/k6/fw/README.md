# The K6 firmware crate

This is [Aura](https://github.com/sophiel-meow/bf-k61-firmware-aura) (Rust, GPL-3, commit
`edf3b98`, 2026-11-02), the open firmware for the Baofeng UV-K6 / K61 family, with blowfang's
changes: the sotto voice pager (VOICE MSG), EXPLORE, the RAM module loader and arena, and the
`pager-only` images built around them. `UPSTREAM-README.md` is Aura's own README. Everything here
is GPL-3.0 (`LICENSE`) except `kd32f328-pac/`, Aura's register crate, which is Apache-2.0 as
upstream. The rest of blowfang stays MIT.

Build it with `sh firmware/k6-aura/build.sh`, which builds sotto's C library into `lib/` and runs
`cargo build --release` here for each image (`firmware/k6-aura/README.md` lists them). Until
2026-11-27 these changes were `firmware/k6-aura/aura-pager.patch`, applied to a fresh clone of
Aura at each build; the sources here are that clone with the patch applied, file for file,
apart from `build.rs`'s `K6_FLASH_LEN` and `.gitignore`.
Aura's `tools/`, `docs/`, `assets/`, `Makefile` and Nix files are not carried: clone Aura for its
flashing tools.

## What blowfang changes

- New: `src/app/pager.rs` and `src/app/pager/` (VOICE MSG, EXPLORE, the module loader),
  `src/ui/pager.rs`, `src/ui/glyph.rs`, `src/out.rs`, and in `src/hal/`: `arena.rs`, `light.rs`,
  `pwm_audio.rs`, `ram0.rs`, `txdog.rs`.
- Changed: the launcher, keys, screen, power, storage and radio wiring (`src/app/`, `src/device/`,
  `src/ui/`, `src/main.rs`), two raw-register helpers in `src/drivers/fd6818.rs`, the UART and
  uptime HAL, and `Cargo.toml` (the `pager-only`, `lean`, `explore` and related features).
- `build.rs` writes `memory.x` from `memory.x.in` (Aura's `memory.x` is gone) and links
  `lib/libsotto_k6.a`; `K6_FLASH_LEN` opens the flash region for build.sh's size reports without
  touching the tracked file. The boot screen's version string (`GIT_VERSION`) is blowfang's
  commit, `-dirty` when the checkout has changes (`git status`).

To see the whole change against Aura:

```sh
git clone https://github.com/sophiel-meow/bf-k61-firmware-aura /tmp/aura
git -C /tmp/aura checkout edf3b98c3528f909b0e0f4dda80142a70c194ff0
diff -ru --exclude=.git --exclude=target --exclude=lib /tmp/aura radios/k6/fw
```
