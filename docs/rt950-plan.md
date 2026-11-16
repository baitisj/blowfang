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
- Open-source base: Hertzz58/Radtel-RT950-Pro-Firmware (GPL-3, heavily WIP). Flashing and
  backup: nicsure's RT-950 Pro web flasher.

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

## SSB receive: easier than the note says

Jeff's note calls RX the hard part because the BK4829 is "FM only". It isn't quite: the BK4819
family has a zero-IF baseband audio mode that UV-K5 custom firmware uses as SSB receive, and
our own SURF already has it as its DSB mode (key 4: AF 5 + REG_3D 0). It hears both sidebands
(no opposite-sideband rejection, so about 3 dB more noise), but a tuned SSB signal is
intelligible with 10 Hz tuning steps. We can try this on the K6 at hamlet now, against the
IC-7100 in USB, without Andrew's radio.

## Open questions that need Andrew's radio

1. Board revision, MCU clock, SRAM size, screen type and resolution.
2. Bootloader, flash procedure, and a full backup (stock firmware and the external flash) before
   anything is written.
3. RF topology: can both chips receive at the same time on the same band? On different bands?
   How much does a sweeping chip desense the listener (listen to a weak carrier while the other
   chip sweeps across it)?
4. Where the mic goes: PA1 "audio level" on an ADC is unverified (raw audio or an envelope?).
   Voice SSB and sotto encode both need raw mic audio.
5. AM power-mod mechanism and its bandwidth (question 2 under SSB TX).
6. PLL retune rate and glitches; whether a DC-coupled FM input exists.
7. BK4829 RSSI read time per bin and per-chip RSSI offset.

## Decisions (Jeff, 2026-11-16)

- **Licence: GPL-3 for the RT-950 port, for now.** The RT-950 build may use Hertzz58's GPL-3
  firmware as its base, and the RT-950 portion is GPL-3. The rest of blowfang stays MIT.
- **SSB transmission: allowed.** The usual bench rules still apply: every test TX carries
  AG7EW ident, first tests at low power, and anything outside 70 cm (430-450 MHz), such as
  2 m 144.174 or 144.200, still needs Jeff's OK for that run.

## First steps

1. **Host simulation (now, no radio):** `c/common/ssb_polar.c` with a host test: Hilbert,
   envelope and frequency split, then quantise as the radio would (PLL 10 Hz steps at N writes
   per second, P power levels, D samples of path mismatch) and report opposite-sideband
   suppression. This tells us what register rate and resolution the radio must deliver before
   anyone touches it.
2. **K6 SSB receive trial (now, RX only):** SURF's DSB mode on 2 m / 70 cm SSB and FT8
   activity, IC-7100 in USB alongside.
3. **SURF two-role refactor (now):** the `sweep_*` / `listen_*` interface above, K6 behaviour
   unchanged, emulator tests still green.
4. **With Andrew's radio:** backup, Hertzz58 bring-up (chip IDs on both CS lines, DAC tone),
   then the measurements in the open questions, starting with dual-chip desense and the AM
   path bandwidth.
5. **First RT-950 builds:** dual-chip SURF (RX only), then constant-envelope FT8/WSPR TX, then
   voice SSB TX by polar modulation.
