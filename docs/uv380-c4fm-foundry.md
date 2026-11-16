# What RadioFoundryC4FM teaches us about the UV-380's HR_C6000

Jeff's 11-16 find: RadioFoundryC4FM (github.com/lycanananas/RadioFoundryC4FM, "C4FMFoundry"), a
fork of RadioFoundry that puts Yaesu System Fusion (C4FM DN voice) on a Baofeng DM-32UV beside FM
and DMR. It now works on the air with Yaesu radios: callsign, DG-ID, voice both ways, through
repeaters and Wires-X. We first read the fork on 11-14 (before its on-air results); this page
covers what is new since then and what it means for sotto on the UV-380. Read at commit 2012648
(2026-11-16). Research only: no bench time was used.

## Licence

| Part | Licence | What we do with it |
|---|---|---|
| Everything RadioFoundry wrote, including `drivers/modem/hrc7000/hrc7000_c4fm.c`, `hal/hal_c4fm.h`, `platform/hrc7000/fm_rx_audio.c`, `drivers/rf/fd6818/`, and every page under `docs/` (`docs/spec/c4fm.md` and `modem-hrc7000.md` carry SPDX MIT) | MIT | facts and values cited by file and section; code could come in with the copyright line, though none is copied here |
| `core/c4fm/` (YSF layer 2 `ysf.c`, the software receiver `c4fm_dsp.c`, the call engine) and `tests/host/c4fm*` | GPL-2.0-or-later (the YSF layout follows MMDVMHost and MMDVM_CM) | **facts only, no code copied**, as for OpenGD77. ("Or later" would be compatible with our GPL-3 UV-380 image, but that is Jeff's call, not this page's) |
| AMBE+2 | not included: taken from each radio's stock firmware | not used |

RadioFoundry is clean room and never reads OpenRTX, so nothing from our OpenRTX-derived UV-380
tree goes back to them.

## The headline: an HR_Cx000's physical layer is a general 4800-baud 4FSK transmitter

C4FM is DMR's 4FSK at the same 4800 symbols/s, the same root raised cosine (alpha 0.2) and the
same dibit mapping (01 +3, 00 +1, 10 -1, 11 -3), only at 1.39 times the deviation (+-2700 Hz outer
against DMR's +-1944) and with 100 ms continuous frames instead of 30 ms TDMA slots. So the fork
transmits C4FM with **no DMR coding at all**: the modem chip's physical layer sends the CPU's bytes
as symbols, back to back, and Yaesu receivers (FT5D, FT-70D, a repeater, a Wires-X node) decode
it. That is the first on-air proof that this chip family will carry a foreign 4FSK protocol for
whole calls, not just raw DMR frames (RadioFoundry's split duplex work, which we already cited).

The recipe (HR_C7000, `hrc7000_c4fm.c` and `docs/spec/c4fm.md` 7, MIT):

| Step | HR_C7000 (the fork) | HR_C6000 equivalent (ours) |
|---|---|---|
| Enter | layer 2 TX/RX control 0, slot enable 0; work mode through FM (resets the state machine), then **physical layer continuous** (0x02); layer 2 control 0x82 (TX on, own slot timing); clear the codec buffers | 0x10 through 0x80, then **0x66** (physical layer with slot timing; proven on air by RadioFoundry `modem-hrc6000.md` 9.1-9.2) or 0x02 (continuous, from the manual's BER test, untested on a C6000); 0x40 = 0xE3 for own timing |
| Each slot | at the first PHY_RDY or timeslot interrupt in a 30 ms slot (a second one within 15 ms is the same slot), write the next 36 bytes to **both** TX RAM halves (0x00-0x23 and 0x24-0x47), then "send next slot" (0x80) | the same: timeslot interrupt on PC0, both halves on page 2, 0x41 = 0x80. Both halves gives every slot; one half gives one slot in two (9.2) |
| Byte order | 288 bits a slot, MSB of the first byte first, two bits a symbol | same |
| Empty queue | fill with **0x77** (+3 -3 +3 -3, the preamble pattern) so the carrier never runs out of symbols | same idea |
| Keying | RF chip and PA keyed **once**, at the first RF TX interrupt, and held to the end (the chip stays in TX from slot to slot) | matches RadioFoundry 9.1: one RF TX interrupt per key-up in this mode |
| Deviation | scale both two-point amplitudes (RF_MOD_BIAS_CTRL Mod1 b7:0, Mod2 b15:8) by the wanted ratio while keyed, restore after | 0x46 (MOD1 amplitude) and 0x45 (MOD2 amplitude), 8.75 mV a step (`hrc6000-registers.md`). Linearity is an open question in the fork too |
| Safety | no slot interrupt for 200 ms: PA off | our image's IWDG transmit guard does the same job |
| Leave | TX/RX 0, PA off, amplitudes restored, work mode through FM back to DMR, layer 2 passive, both slot syncs released, receiver restart | 0x10 / 0x11 back to 0x6E / 0x80, MS sync, 0x40 = 0xC3, receiver restart (9.1) |

Rate: 36 bytes every 30 ms is **9600 bit/s raw, continuous**. Our DMR route today (c6dmrtx,
rate 1/2 data in one slot of two) carries 1600 bit/s.

Our own doc already lists the mode (`uv380-c6000-modem.md`, "Physical layer mode") and
`k6-fsk24.md` already proposed paired +-3 symbols as 2400-baud FSK. What the fork adds is that
the whole chain (enter, per-slot refill, idle fill, single key-up, deviation scaling, leave)
works on air with real receivers at the other end (and RadioFoundry's split duplex half kept the
same physical layer mode keyed for 5 minutes with no underrun, `modem-hrc7000.md` 2.10).

## Receive: a software 4FSK demodulator on 8 kHz discriminator audio

The fork does **not** use the chip's own 4FSK demodulator for C4FM. It puts the modem in FM, takes
the 8 kHz copy of the AF input the chip writes to its RX RAM (two 256-sample halves, 32 ms each,
system interrupt bit 14) and demodulates in software (`docs/spec/c4fm.md` 4; the code is GPL, the
facts are these):

- Interpolate x3 through a 41-tap RRC (24 kHz, 5 samples a symbol). 8 kHz is enough: the
  signal ends at 2.88 kHz.
- Sync search by correlation, DC removed, normalised by mean absolute deviation, above 0.75 of
  ideal; least squares fit of the 20 sync samples gives gain and centre (a negative gain just
  means an inverted discriminator).
- First lock tentative until the next sync turns up where due (receiver noise passes a 4-of-40
  sync match every 3-5 minutes).
- A 7-tap decision-directed NLMS equaliser (step 2^-6, centre tap held 0.7-1.2, reset if a side
  tap passes 0.5): repeater audio loses its upper band and the matched filter alone leaves the
  inter-symbol interference in. Voice BER 5.6 % to 3.5 % on repeater captures.
- A CTCSS notch: a repeater's 127.3 Hz tone under the 4FSK looked like level errors. Found by
  Goertzel on frame-to-frame differenced slicing errors (so a repeating frame's 10 Hz comb is not
  mistaken for a tone), two blocks to confirm, fresh at each lock. BER to about 1.6 %.
- Soft decisions (distance from threshold) into soft Viterbi and weighted majority: FICH decode
  50-60 % to 90-100 % on noisy captures.
- Direct from an FT-70D at 2-3 m: raw BER 0.32 %. Integer arithmetic, about 14 multiply-adds a
  24 kHz sample.

**The front end matters most.** On the DM-32 the RF chip is an FD6818, **the K6's own RF chip**, which
RadioFoundry also drives by BK4819 register numbers. In its FM set-up the audio is
voice filtered and de-emphasised: no sync at all, nothing under 300 Hz. Only the **DMR set-up
(a flat discriminator)** gives an eye. The DMR table differs from the FM one in REG_2B = 0x0700
(the RX 300 Hz high-pass, 3 kHz low-pass and de-emphasis all bypassed, by the BK4819 bit names;
FM has 0xC000, bits 15-14, which include the DC cancellation `k6-fsk24.md` turned off), REG_7E = 0xB03F (fixed AGC; FM 0x302E) and REG_43 = 0x4048 (wider RF filter;
FM 0x3028) (`drivers/rf/fd6818/fd6818.c`, MIT). On the HR_C7000 the RX RAM copy is taken
*before* the chip's FM filters, so the FM bandwidth register makes no difference.

For the UV-380 that lines up with what we found the hard way: the C6000 locks only with the
AT1846S in its DMR set (C2). The difference is that on the **C6000** the RX RAM copy is taken
*after* page-4 0x34's de-emphasis and filters (RadioFoundry `modem-hrc6000.md` 8.1, measured on an
AT1846S board), so we also need 0x34 bit 5 (de-emphasis) and bit 7 (300-3400 Hz band-pass) off.
Our 11-14 notes still see a fixed ~300 Hz high-pass with those bits off; whether it comes from the
AT1846S's FM set or from the C6000 itself is untested (bench item 5).

## What this means for sotto

1. **A continuous raw 4FSK channel from the UV-380, our framing.** 9600 bit/s raw is room for
   sotto 2400 under rate 1/2 FEC, or sotto 1200 under rate 1/4 or repetition for range, with
   no DMR slot structure in the way. That is the route for **live** sotto from the UV-380 mic
   (DMR data bursts top out at 1600 bit/s in direct mode, 3200 with both slots at rate 1).
2. **Flat 2400-baud FSK from the UV-380 to the K6.** Each bit as two equal outer symbols
   (bit 1 = +3 +3 = nibble 0x5, bit 0 = -3 -3 = nibble 0xF; polarity to be checked) is
   2400-baud 2FSK at +-1944 Hz, nine data bytes a slot. Pluto run 3 showed the K6's chip FSK reads
   2400 at 1.5-4.5 kHz from any flat FM transmitter, so this should close the gap left by the
   MCU-PWM path (pre-emphasis and the 300 Hz cut, 11-14). Four symbols a bit gives 1200 baud as
   a fallback.
3. **A software receiver on the UV-380 is feasible.** 8 kHz flat discriminator audio carries
   4800-baud 4FSK well enough to beat 1 % raw BER when strong; our RX RAM tap (0x82 bit 1) is the
   same kind of source. That would let the UV-380 receive our own continuous 4FSK, and any
   foreign FSK, without the chip's DMR framing (our `c6dmrrx_raw` showed the chip's own
   physical-layer receive returns 288-bit windows that don't line up with our bursts).
4. **The equaliser and tone notch carry over** to any of our software demodulators that run
   behind a repeater or another radio's audio path.
5. **FTM-300DR interop (idea, not proposed work).** The FTM-300DR is the radio this project
   started with. A UV-380 could send YSF frames (header with AG7EW in CSD1) that it would show,
   and read its FICH and callsign back with the software receiver. DN voice needs AMBE, which we
   don't have; and the YSF layout would have to be written from the open MMDVM facts, not from
   the fork's GPL code. Worth only a bench curiosity unless Jeff wants it.

## Bench experiments this suggests

All on 440.850 MHz (70 cm, inside the bench authorization), AG7EW ident first, the Pluto as the
instrument (stub on its RX port while the UV-380 keys), RAM modules on the current image. The
UV-380 transmits only while Jeff is around, and its keying call caps a transmission at about 5 s
until an image change lifts it.

| # | Experiment | Module / tool | Pass |
|---|---|---|---|
| 1 | **Raw PHY transmit.** 0x10 = 0x66, both TX RAM halves refilled each slot: 0x77 preamble, a 20-symbol sync, PN9, 0x77 when dry. Pluto records IQ | new `c6phytx.c`; host demod in `modem/` | gapless carrier across slots, one RF TX interrupt per key-up, PN9 bit exact, four clean levels. Then 0x10 = 0x02 to compare |
| 2 | **Deviation scale.** 0x45 / 0x46 at 0.7, 1.0, 1.39 of the calibration pair | same module, a setting | Pluto measures the outer deviation; linear or not (the fork's open question 1). Check the 3.58 kHz untrimmed TX offset too (`k6-fsk24.md`) |
| 3 | **UV-380 to K6 at 2400 baud** (paired symbols), then 1200 (four symbols) | `c6phytx.c` sending the fsk24 XL packets; the K6's `fsk24rx.c` | CRC-good packets on the K6, PN9 zero errors, as Pluto run 3 |
| 4 | **UV-380 DMR receive from a flat transmitter.** Pluto sends `uv380_dmrburst.py` bursts with no pre-equaliser | `tools/k6_pluto.py` (a DMR waveform mode), `c6dmrrx` | 23/23 blocks with no equaliser (our open question: a flat far end should need none) |
| 5 | **Flat FM receive tap.** AT1846S in its DMR set, C6000 in FM, 0x34 bits 5 and 7 off; Pluto sends 4800-baud 4FSK PN9 and a 100-1000 Hz tone sweep | new `c6fmtap.c` dumping RX RAM halves | where the ~300 Hz high-pass comes from; a four-level eye in the dump |
| 6 | **Sotto over raw PHY.** sotto 1200 / 2400 frames with our sync and a convolutional code, decoded on the host from the Pluto capture | after 1; `modem/` | the 528-byte test message exact, then live mic audio |

Experiments 1, 2 and 5 need nothing new on the image. 3 and 6 grow out of 1. 6 at length needs the
5 s keying cap lifted (an image change, so a reflash batch).

## See also

- `docs/uv380-c6000-modem.md`: what we have proven on the C6000 (DMR receive and transmit).
- `docs/k6-fsk24.md`: the K6 side of 2400-baud FSK (Pluto run 3).
- RadioFoundry `docs/spec/modem-hrc6000.md` 8.1, 9.1-9.2 and `hrc6000-registers.md` (MIT): the
  C6000 physical layer and audio taps.
- RadioFoundryC4FM `docs/spec/c4fm.md` 2, 4, 7, 9 and `docs/spec/modem-hrc7000.md` 2.10, 8.5
  (MIT): the C4FM transmit and receive paths summarised above.
