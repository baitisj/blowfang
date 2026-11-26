# RT-950 Pro

blowfang on the Radtel RT-950 Pro: an Artery AT32F403A (Cortex-M4F, 1 MB flash, 96 KB SRAM) and
two BK4829 transceivers. The plan, and why two chips matter, are in
[docs/rt950-plan.md](../../docs/rt950-plan.md); what the radio can and can't do, with measurements, is in
[docs/rt950-characteristics.md](../../docs/rt950-characteristics.md). **No blowfang code has run on an RT-950 Pro yet.**
Andrew has one and Jeff has one (2026-11-16); everything is built to compile for the radio and tested on a PC against
a model of its two chips.

## Licence: GPL-3

Everything under `radios/rt950/` is GPL-3.0-or-later ([LICENSE](LICENSE)), Jeff's choice of
2026-11-16, so it can build on the GPL-3 base firmware below. The rest of blowfang stays MIT.
The RT-950 code includes two MIT files from `c/common/` (`surf_core.inc`, which is SURF's shared
half, and the polar-TX code); MIT code can go into a GPL-3 program, and those files stay MIT so the
K6 and the UV-380 can keep using them.

## Base firmware: jaymart1983's

[jaymart1983/Radtel-RT950-Pro-Firmware](https://github.com/jaymart1983/Radtel-RT950-Pro-Firmware),
GPL-3, pinned at commit `965eae2` (2026-09-15) in `.github/workflows/rt950.yml`. It started as a
fork of [Hertzz58's](https://github.com/Hertzz58/Radtel-RT950-Pro-Firmware) and is the better base
(compared 2026-11-16; Jeff agreed it is further along):

| | jaymart1983 | Hertzz58 |
|---|---|---|
| licence | GPL-3 | GPL-3 |
| on a radio | receives with audio, screen, keys, encoder, SPI flash, Bluetooth flashing, in-app updater (a test ladder, each rung flashed and checked) | boot screen, LEDs, LCD, GPIO; the rest written but untested |
| BK4829 driver | fixed by measurement: frequency registers in 10 Hz units (Hertzz58 wrote raw Hz), RSSI in REG_67 (not 0x0C), band relay and LNA set on every tune | as first written |
| two chips | chip 0 receives with audio, chip 1 held off the audio bus | both driven, untested |
| Radtel's images | not in the repository | firmware images and the bootloader committed under `binary/` (we may not redistribute those) |
| last commit | 2026-09-15 | 2026-04-06 |

Neither transmits yet. jaymart1983's keeps TX for a later rung, after receive is trusted.

## What is here

- `src/rt_api.h`: what the apps need from the radio: register access, tune, RSSI in dBm, front end
  off, speaker, keys, a frame to draw.
- `src/surf2.c`: two-chip SURF. Chip 1 sweeps all the time; chip 0 listens with audio; the sweep
  skips a guard band round chip 0 and chip 0's level fills the bins there. The scale, bars and
  caret logic are the K6's, shared through [c/common/surf_core.inc](../../c/common/surf_core.inc).
  The design is at the top of the file. About 7.9 KB of code at -Os.
- `src/digi_tx.c`: the first transmit milestone, FT8 and WSPR by stepping chip 0's PLL (no
  envelope, no audio path), with c/common/tone_fsk.c dithering each 10 Hz write so every symbol's
  mean frequency is right to about 0.05 Hz. Chip 1 off first, AG7EW in CW after the frame, a key
  keys down, 70 cm only unless the call says Jeff OK'd the run. In the sim the frame decodes with
  the K6's FT8 receiver. The polar SSB numbers behind it (what PLL rate, PA resolution and delay
  match voice SSB needs) are in [docs/rt950-polar-sim.md](../../docs/rt950-polar-sim.md).
- `port/jm_glue.c`: `rt_api_t` on jaymart1983's drivers (`bk4829_*`, `rx_path_receive`, the
  keypad, the ST7789 screen), and `rt950_surf2()`, the entry a menu item calls. CI compiles it
  against the pinned checkout; it is not yet wired into that tree's menus, and its transmit
  calls stay empty until that firmware has a TX path.
- `tools/rt950_backup.py`, `backup/`: the backup before anything else runs on a radio
  ([docs/rt950-backup.md](../../docs/rt950-backup.md)). A read-only CPS reader (the stock
  firmware's 64 KB, or all 4 MB and the bootloader through a dump-only image), the patch that
  makes that image from the base firmware (no SPI erase or program, no TX, BACKUP ONLY on the
  screen), the check CI runs on the built image, and a fake radio on a pty for dry runs.
- `sim/rt950_sim.c`: the two chips on a PC (register writes, the tune sequence, RSSI settling
  after a tune, signals that pulse, one chip's LO desensing the other, bus time), running the
  real app code. `tests/test_rt950_surf.py` drives it.

```sh
make -C radios/rt950                      # build/rt950_sim (and the no-guard build for a test)
make -C radios/rt950 target               # build/surf2.o for the Cortex-M4F, with its size
make -C radios/rt950 glue JM=../jm        # the glue against a checkout of the base firmware
make -C radios/rt950 backup-image JM=../jm   # the dump-only image, built and checked
make -C c/common                          # the polar SSB and tone_fsk host programs
python -m pytest -q tests/test_rt950_surf.py tests/test_rt950_digi.py tests/test_rt950_backup.py tests/test_ssb_polar.py tests/test_tone_fsk.py
```
- `port/surf2-image.patch` and `make surf-image JM=DIR`: the pinned base firmware with SURF linked
  in, booting straight into it (from VFO A's frequency; Back returns to the base firmware). The
  base's own Makefile builds it, so SURF and the glue compile under its `-Werror` rules; the
  flash tool's `verify` checks the .BTF. CI uploads it as `rt950-surf2`. It has not been on a
  radio.
- `sim/rt950/` (MIT): that image on an emulated board (the AT32F403A in unicorn, the keypad
  matrix, the LCD, both BK4829s), and with SoapyFang's chip model (`sim/rt950/soapy.py`) both
  chips hear real I/Q: a SigMF recording, the synthetic band or a live SDR.
  `tests/test_rt950_board.py` boots it into SURF and checks the sweep, the bars, the keys, that
  nothing is a transmit setting, and that the fast register path matches the bit-banged bus.
- `python3 -m sim.rt950`: that board in a browser workbench (http://127.0.0.1:5059/): the screen,
  a faceplate without PTT, a made-up 2 m band to edit, the SoapyFang panel and the listener's
  audio. `--source pluto` etc. as in `sim/soapyfang/README.md`; on hamlet the Pluto session is
  the bench runner's `sim.soapyfang` step with `--radio rt950` and the `rt950-surf2` artifact.

## What needs Andrew's radio

Each is a constant or an assumption in the code, listed with where it lives:

1. **Backup first**: the settings, the 4 MB SPI flash and the bootloader, and Radtel's .BTF for
   the stock version, before anything is written ([docs/rt950-backup.md](../../docs/rt950-backup.md)).
   Jeff has an RT-950 Pro too now (2026-11-16), so these can run on his bench as well.
2. **Both chips hearing at once.** The band relay and LNA (PE12 VHF, PE13 UHF, PB0) seem to be
   shared, so chip 1 is assumed to hear through chip 0's front end, on chip 0's side of 300 MHz.
   Check: chip 1's RSSI tracks a carrier while chip 0 is tuned elsewhere on the same band
   (`jm_glue.c`, `rf_tune`).
3. **Desense and the guard** (`GUARD_HZ` in `surf2.c`, ±25 kHz to start): a weak carrier on chip 0
   while chip 1 sweeps across and near it. The smallest guard where chip 0's RSSI and audio don't
   move is the one to use. If it can shrink below ±20 kHz, the lens comes back while listening.
4. **RSSI settle time** after a tune on the BK4829 (`SETTLE_US` 3 ms, `CHECK_US` 12 ms,
   `QUICK_PCT` 59, all the K6's FD6818 figures): read REG_67 every 0.5 ms after a tune onto a
   steady carrier.
5. **REG_67 to dBm** (`RT_RSSI_OFS` 160 in `jm_glue.c`): against the IC-7100 or a signal generator.
   The base firmware measured its floor at 76-82 and a strong signal at 196-226 raw.
6. **The chips' RSSI offset** (measured by the app at the start: its "loff" line) and whether it
   holds across the band.
7. **Bus timing**: the base firmware copies the OEM's ~5 µs half-bit delay, about 250 µs per
   register access, so a tune costs about 1 ms. If the BK4829 takes a faster clock, the sweep
   speeds up.
8. **The speaker path** under SURF (`audio_speaker_mute` / `unmute`, and whether chip 0's REG_47
   alone mutes cleanly on a retune).
9. **Keys**: the mapping in `jm_glue.c` (arrows, OK, Back, V/M, ABC, side keys), and the screen
   layout on the real ST7789.
10. **For transmit** (the polar plan, docs/rt950-polar-sim.md): PLL retune rate and glitches,
    whether REG_30 must relock for a 10 Hz step, how PA power is modulated and how fast, and the
    delay between the two paths. Every TX test carries AG7EW ident, low power first, and anything
    outside 70 cm needs Jeff's OK for that run.
