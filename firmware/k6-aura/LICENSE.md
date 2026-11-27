# Licence of this directory

The K6 firmware these scripts build is [`radios/k6/fw/`](../../radios/k6/fw/README.md): Aura
([sophiel-meow/bf-k61-firmware-aura](https://github.com/sophiel-meow/bf-k61-firmware-aura)) with
blowfang's changes. Aura is licensed under the GNU General Public License, version 3, and so is
`radios/k6/fw/` (GPL-3.0-only, full text in [`radios/k6/fw/LICENSE`](../../radios/k6/fw/LICENSE)),
except Aura's register crate `radios/k6/fw/kd32f328-pac/`, which is Apache-2.0 as upstream
([its LICENSE](../../radios/k6/fw/kd32f328-pac/LICENSE)). Until 2026-11-27 blowfang's changes lived
here as `aura-pager.patch`, under the same licence.

The firmware images `build.sh` produces link Aura with blowfang's C library, so those images are
GPL-3.0 as well. `build.sh` and `README.md` in this directory, and the C library itself
(`c/k6/`), are MIT like the rest of blowfang (`LICENSE` at the top of the repository).
