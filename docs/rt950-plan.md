# Blowfang on the Radtel RT-950 Pro: plan

*2026-11-16. Paper only: nothing here has run on an RT-950 Pro yet.* Andrew owns the radio.
Sources: Jeff's design note "RT-950 Dual BK4829: SSB Generation with Blowfang DSP" (project
upload, 2026-11-15), the BK48xx survey in [ht-survey.md](ht-survey.md), and what SURF and
EXPLORE taught us on the K6 ([k6-surf.md](k6-surf.md), [k6-explore-app.md](k6-explore-app.md)).

## The radio, as far as we know

- MCU: Artery AT32F403A, Cortex-M4F with FPU, 240 MHz max (the Hertzz58 firmware runs it at
  120), 1 MB flash, 96 KB SRAM (224 KB if the flash zero-wait area is traded for RAM).
- RF: two BK4829 (the BK4819 successor, with the same register numbering as the K6's FD6818,
  so our K6 register knowledge mostly carries over). One bit-banged SPI bus shared by
  both: clock PE10, data PE11, chip selects PE8 (chip 0, main VFO) and PE15 (chip 1, sub
  receiver / APRS). Band relay on PC4 routes RF paths; it does not chain the chips.
- SI4732 broadcast receiver (LW/MW/SW to 30 MHz, FM 64-108 MHz). It cannot hear 2 m or 70 cm,
  so it is no use as an SSB reference there; the IC-7100 at hamlet is our reference receiver.
- Audio out: 12-bit DAC + TIM6 + DMA. The stock radio does true AM by modulating PA power.
- Open-source base: jaymart1983/Radtel-RT950-Pro-Firmware (GPL-3, a fork of
  Hertzz58/Radtel-RT950-Pro-Firmware that receives on the radio; chosen 2026-11-16, see
  Decisions). Flashing and backup: its own cable and Bluetooth tools, or nicsure's RT-950 Pro
  web flasher.
- Front end (from jaymart1983's measurements): the band relay and LNA (PE12 VHF, PE13 UHF, PB0)
  are set on every tune and look shared by both chips, so the second chip probably only hears
  on the first chip's side of 300 MHz.

Compared with the K6 (Cortex-M0, 120 KB flash wall, 16 KB RAM) this is a big machine: room for
float DSP, sotto encode as well as decode, and SURF with history.

## Dual-chip SURF (Jeff, 2026-11-16)

On the K6, SURF and EXPLORE share one receiver between finding signals and listening to them.
That is the root of most of their compromises: listen-first sweeping (sweep only while the
listened channel is quiet), hush on every retune, carets that freeze while you listen, and
short pulsed signals missed because the confirm read lands in a gap (PR #83). With two chips the
roles split:

- **Sweep chip (chip 1, PE15):** never makes audio. Wide filter, fixed gain (SURF's 7/9 keys),
  steps the lens and outer bins continuously and feeds RSSI into the bars and the caret
  logic. It is the only thing that retunes during normal use.
- **Listen chip (chip 0, PE8):** parks on the selected frequency with audio on. Side keys and
  UP/DOWN retune only this chip; the sweep never stops. MON opens its squelch as on the K6.
- **Centre bin from the listener.** The sweep chip skips a guard band around the listen
  frequency (start with +-25 kHz, tune by measurement) and the listener's own RSSI fills the
  centre bins. That keeps the sweep chip's LO away from the listener and gives a free per-chip
  RSSI offset check whenever both read the same signal.
- **Shared SPI.** Each chip's register sequence (tune, settle, RSSI read) must be atomic on the
  bus. The listener touches the bus rarely (key presses, RSSI poll for its bin), so a simple
  "sweep step is one critical section" scheme should do.
- **TX.** Before chip 0 keys up, the sweep chip stops and its front end goes off; it resumes
  after TX.
- **Same idea for EXPLORE.** The sweep chip keeps finding new stations while the listener sits
  on one, which fixes EXPLORE's biggest K6 limitation (deaf while listening).

Port path: put SURF's radio calls behind a two-role interface (`sweep_*`, `listen_*`). The K6
build maps both roles to its one chip with today's behaviour (listen-first, hush), so the K6
stays the test bed and the peak logic Jeff signed off on 2026-11-12 is shared, not forked.

## SSB transmit: correcting the architecture

Jeff's note proposes: audio, Hilbert, phasing/Weaver in the AT32 to make an SSB "baseband"
waveform, then the BK4829 as an AM (power-modulated) PA. **That last step gives DSB, not SSB.**
Any real audio waveform has a mirror-symmetric spectrum, so `[1 + k·s(t)]·cos(wc·t)` puts a copy
on both sides of the carrier, whatever was done to `s(t)` first. Phasing only cancels a
sideband when I and Q drive a quadrature modulator at RF, which the BK4829 doesn't expose.

What does fit this hardware is **polar modulation** (envelope elimination and restoration, the
Kahn method, which the uSDX uses to make SSB from a Si5351 and a switching PA):

```
audio -> band-pass 300-2700 -> Hilbert -> analytic signal m + j·ĥm
      -> envelope  A(t) = |m + j·ĥm|     -> PA power (the 950 Pro's AM path)
      -> phase     f(t) = d/dt arg(...)  -> BK4829 frequency (PLL offset or FM input)
```

`A(t)·cos(wc·t + phi(t))` is exactly the SSB signal, and both halves map onto things the
radio already does: power modulation for amplitude, frequency for phase. The AT32's FPU makes
the Hilbert (63-127 tap FIR) and the arctangent cheap at 8 kHz.

Why it is promising: a single audio tone in USB is just a constant carrier `f_m` above `wc`, so
**single-tone digital modes (FT8, WSPR, FST4, JS8) need no envelope at all**, only frequency
steps, which a BK4829 does today. That is the first TX milestone and needs nothing but PLL
writes. Voice adds the envelope path.

What can break it (each is a measurement on Andrew's radio):
1. **Frequency path bandwidth.** The instantaneous frequency of voice SSB swings across 0-3 kHz
   with spikes near envelope nulls (clamp them, as the uSDX does). It needs a DC-coupled path:
   either PLL frequency register writes at audio rate (10 Hz steps; does a retune glitch or need
   a REG_30 relock toggle?) or the chip's FM input fed from the DAC with pre-emphasis and the
   audio high-pass turned off.
2. **Envelope path bandwidth and range.** How the 950 Pro modulates power (PA bias DAC? APC
   line? BK4829 PA gain register?), how fast it follows (needs several kHz), how linear it is,
   and how low it goes.
3. **Delay match** between the two paths. A mismatch of a few samples brings the opposite
   sideband back; it needs a trim, measured by nulling the unwanted sideband of a two-tone on
   the IC-7100 waterfall.

## Audio in, and what it means for each plan item (2026-11-16)

The FerriteFW author measured the RT-950 Pro's audio paths (facts and credit in
[rt950-characteristics.md](rt950-characteristics.md)). The MCU's DAC reaches the BK4829's audio
input through an RC network: a first-order high-pass near 170 Hz with about -90 deg of phase, and a
fixed 3.1 kHz chip filter after it unless REG_47 bit 0 bypasses it. With the bypass and a
level-and-phase equaliser it is flat 50 Hz-5 kHz, and G3RUH 9600 goes out at 0.3 % bit errors. No
solder anywhere.

| Plan item | Affected? | Why, and the path we take |
| --- | --- | --- |
| Dual-chip SURF | no | receive RSSI only, no audio in |
| FT8 / WSPR TX | no | PLL frequency steps (`digi_tx.c`) never touch the audio path |
| sotto TX (PHY R, 600/1200 Hz FFSK) | no | inside even the stock 400 Hz-2.5 kHz flat band; DAC first, bypass and equaliser if needed; the chip's tone generator is a second route |
| Faster modem TX (2400-9600) | no, with work | bypass plus a phase-correcting equaliser of our own, designed from our own channel measurement |
| Polar SSB TX | partly | the FM-input option for the frequency path is out (AC-coupled at ~170 Hz with -90 deg phase); PLL writes at ~8000 a second remain the path and are still unmeasured. The envelope path is unaffected |
| Modem RX | partly | 1200 and 2400 baud work (APRS 1200 clean as set; the chip's own FSK modem 1200/2400 clean both ways); 4800 marginal; 9600 receive not possible |
| sotto RX (PHY R) | likely yes | 600 Hz sits near the board's receive coupling corner with the voice filters off: keep the 300 Hz high-pass on, or correct the coupling in the demodulator |
| Mic audio (voice SSB, sotto encode) | unknown | still open question 4 |

## SSB receive: easier than the note says

Jeff's note calls RX the hard part because the BK4829 is "FM only". It isn't quite: the BK4819
family has a zero-IF baseband audio mode that UV-K5 custom firmware uses as SSB receive, and
our own SURF already has it as its DSB mode (key 4: AF 5 + REG_3D 0). It hears both sidebands
(no opposite-sideband rejection, so about 3 dB more noise), but a tuned SSB signal is
intelligible with 10 Hz tuning steps. We can try this on the K6 at hamlet now, against the
IC-7100 in USB, without Andrew's radio.

## Open questions that need Andrew's radio

1. Board revision, MCU clock, SRAM size, screen type and resolution (jaymart1983's firmware
   runs at 120 MHz and confirms an ST7789V, 240 x 320, on its radio). The full list of what
   two-chip SURF assumes is in [radios/rt950/README.md](../radios/rt950/README.md).
2. Bootloader, flash procedure, and a full backup (stock firmware and the external flash) before
   anything is written.
3. RF topology: can both chips receive at the same time on the same band? On different bands?
   How much does a sweeping chip desense the listener (listen to a weak carrier while the other
   chip sweeps across it)?
4. Where the mic goes: PA1 "audio level" on an ADC is unverified (raw audio or an envelope?).
   Voice SSB and sotto encode both need raw mic audio.
5. AM power-mod mechanism and its bandwidth (question 2 under SSB TX).
6. PLL retune rate and glitches. (There is no DC-coupled FM input: the DAC reaches the chip
   through an RC high-pass near 170 Hz, per Ferrite's measurements.)
7. BK4829 RSSI read time per bin and per-chip RSSI offset.

## Decisions (Jeff, 2026-11-16)

- **Licence: GPL-3 for the RT-950 port, for now.** The RT-950 build may use Hertzz58's GPL-3
  firmware as its base, and the RT-950 portion is GPL-3. The rest of blowfang stays MIT.
- **Base firmware: jaymart1983's** (Jeff, 2026-11-16: "a lot further along"). Both it and
  Hertzz58's are GPL-3; jaymart1983's forked Hertzz58's and has since verified reception, the
  screen, keys, SPI flash and Bluetooth flashing on the radio, fixed the BK4829 frequency units
  and RSSI register by measurement, keeps chip 1 off the audio bus, and doesn't carry Radtel's
  firmware images (Hertzz58's commits them and the bootloader, which we must not redistribute).
  The comparison is in [radios/rt950/README.md](../radios/rt950/README.md).
- **SSB transmission: allowed.** The usual bench rules still apply: every test TX carries
  AG7EW ident, first tests at low power, and anything outside 70 cm (430-450 MHz), such as
  2 m 144.174 or 144.200, still needs Jeff's OK for that run.

## First steps

1. **Host simulation (done 2026-11-16, [rt950-polar-sim.md](rt950-polar-sim.md)):** 30 dB of
   opposite-sideband suppression needs about 8000 PLL writes and PA updates a second, 64 PA
   levels and the two paths matched to about 15-30 us; 40 dB needs 16000 a second, 256 levels
   and about 6 us. The 10 Hz step hardly matters. FT8 and WSPR by dithered 10 Hz steps are right
   to 0.05 Hz a symbol at 1000 writes a second (radios/rt950/src/digi_tx.c). Originally: `c/common/ssb_polar.c` with a host test: Hilbert,
   envelope and frequency split, then quantise as the radio would (PLL 10 Hz steps at N writes
   per second, P power levels, D samples of path mismatch) and report opposite-sideband
   suppression. This tells us what register rate and resolution the radio must deliver before
   anyone touches it.
2. **K6 SSB receive trial (now, RX only):** SURF's DSB mode on 2 m / 70 cm SSB and FT8
   activity, IC-7100 in USB alongside.
3. **SURF two-role refactor (done 2026-11-16):** SURF's radio-independent half moved to
   `c/common/surf_core.inc` (the K6 modules build byte for byte the same) and the two-chip SURF
   is `radios/rt950/src/surf2.c`, tested on a PC against two modelled BK4829s
   (`tests/test_rt950_surf.py`). Originally: the `sweep_*` / `listen_*` interface above, K6 behaviour
   unchanged, emulator tests still green.
4. **With Andrew's radio:** backup, Hertzz58 bring-up (chip IDs on both CS lines, DAC tone),
   then the measurements in the open questions, starting with dual-chip desense and the AM
   path bandwidth.
5. **First RT-950 builds:** dual-chip SURF (RX only), then constant-envelope FT8/WSPR TX, then
   voice SSB TX by polar modulation.
