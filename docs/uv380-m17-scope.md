# M17 on the UV-380 through the HR_C6000 (scope)

*2026-11-17. A plan: nothing here has run on the radio yet. Numbers from `docs/uv380-m17-sim.py`
(numpy, noise free). Upstream read at OpenRTX `13dfe3e` (master, 2026-11-17) and the
openrtx.github.io site the same day.*

## In short

- **Transmit: yes, and it is almost free.** The C6000's physical layer mode (`docs/uv380-c6phy.md`)
  already sends gapless 4800 baud 4FSK with the same dibit map as M17 (01 +3, 00 +1, 10 -1, 11 -3),
  +3 above the carrier, flat to DC. M17 frame bytes go into the TX RAM unchanged. Two things
  differ from the M17 spec: the outer deviation (1944 Hz, M17 wants 2400: scale 0x45/0x46 by 123 %)
  and the shaping (the chip's RRC is alpha 0.2, M17's is 0.5). The shape costs an M17 receiver
  almost nothing (30.8 dB signal to ISI, eye 78 % open, below any real noise floor).
- **Receive: unknown, and that is where the prize is.** Upstream OpenRTX needs a solder mod on
  the MD-UV3x0 to receive M17. The C6000 copies its FM input to an RX RAM at 8 kHz, which could
  feed a software demodulator with no mod. It works only if that tap is DC coupled (a high pass
  under about 20 Hz) and reasonably flat to 3 kHz. The I2S3 codec path we already know (a steep
  high pass under about 450 Hz, de-emphasis) can't do it. Measuring the tap is milestone 2.
- **Upstream value:** OpenRTX's own site says the MD-UV380's "HR_C6000 DMR baseband ... cannot be
  repurposed to run M17". The GD-77 and DM-1801 (also HR_C6000) have no M17 in OpenRTX at all.
  A C6000 4FSK path could give them M17 TX, and maybe RX without the mod.
- **Recommended first milestone:** standard M17 (BERT, a packet, 4 s of Codec2 3200) from the
  unmodded UV-380 through a RAM module at 0 W on 441.850, decoded by OpenRTX's own
  `m17_replay_tool` from a Pluto capture. No reflash.

## What OpenRTX already does

| Item | Upstream today |
| --- | --- |
| MD-UV3x0 M17 | `CONFIG_M17` on. `radio_UV3x0.cpp` OPMODE_M17: AT1846S in its DMR set (RX filter off), 25 kHz bandwidth, C6000 `fmMode()`, input gain +6 dB "found experimentally" |
| TX path | `M17::Modulator` builds the 4FSK as 48 kHz PCM (RRC alpha 0.5, `RRC_GAIN` 23000), through `PwmCompensator` to TIM3 PWM, into the C6000's LINE_IN, through its FM chain (`startAnalogTx(LINE_IN, BW_25kHz)`). A FIXME notes a carrier-only gap at the start that clips the preamble (a 50 ms sleep works round it) |
| RX path | `AIN_RTX` = PC3 (ADC2 ch 13), 24 kHz into `M17::Demodulator`. PC3 is only connected after the RF to MCU mod (openrtx.org `M17/mduv380_mods.md`: "Medium", a 50 kOhm resistor, a capacitor removed). The mic to MCU path also needs a mod |
| Other C6000 targets | DM-1701: M17 by the same LINE_IN path. GDx (GD-77, DM-1801): HR_C6000, **no `CONFIG_M17`**. CS7000/CS7000-PLUS: C6000 plus their own M17 path |
| Live work | M17 demodulator reliability (office hours 2026-10-19: about 10 PRs, soft Viterbi merged, more in review). Coordinate with it rather than touch the demodulator ourselves |
| Design hint | AGENTS.md (#396): the demodulator only knows syncwords, the decoder does the rest, "what lets a target with a hardware demodulator reuse the upper half". A hardware 4FSK path is anticipated |

Our UV-380 image is OpenRTX plus `uv380-blowfang.patch` (GPL-3 like its base), so OpenRTX's own
M17 mode is already on the radio (MONI + 5 cycles FM, M17, PAGER), TX by the LINE_IN path.

## What the C6000 gives (measured 11-16, PR #134 and #139)

| Property | Value | Source |
| --- | --- | --- |
| Mode | 0x10 = 0x66 before 0x40 = 0xE3; 36 bytes per 30 ms slot, halves 0x00 and 0x24 alternate | `uv380-c6phy.md` |
| Feeding | write the idle half at each PC0 slot interrupt (writing both clobbers the half on air) | run 3 |
| Dibits | 01 +3, 00 +1, 10 -1, 11 -3, MSB first: **identical to M17** | RadioFoundry `modem-hrc6000.md` 9 (MIT) |
| Polarity | +3 above the carrier (M17: +3 = +2.4 kHz) | run 3 |
| Deviation | outer +-1944 Hz from the DMR cal pair; 0x45/0x46 scale it (C4FM used 139 % on the C7000) | `uv380-c4fm-foundry.md` |
| Shape | RRC alpha 0.2, two-point modulation, flat to DC | DMR spec, chip |
| Gaps | none: 26/26 at 2400, 12/12 at 1200, 0 bits wrong | run 3 |
| Carrier | the raw mode sits about +6.7 kHz high with the FM trim (5745); trim -935 puts it within 40 Hz | run 3 |
| Key-up | `tx_tone` + IWDG guard: ident first, at most 5 s (`TX_MS`), nothing said over USB while keyed | `uv380-c6000-modem.md` |
| Idle byte | 0x77 = +3 -3 +3 -3: **M17's preamble** before an LSF | RadioFoundry, M17 spec |

## Transmit

- **Bytes in, M17 out.** An M17 frame is 48 bytes (16 bit sync + 368 bits) every 40 ms; a slot
  takes 36 bytes every 30 ms. Both are 1200 bytes a second, so a byte FIFO feeds the slots and
  needs no alignment (3 frames = 4 slots = 120 ms). Preamble = 0x77 for 40 ms, then LSF sync
  0x55F7, stream frames 0xFF5D (packets 0x75FF, BERT 0xDF55), then EOT 0x555D repeated.
- **Deviation:** `TX_DEV` 123 % for 2400 Hz outer (800 Hz inner). Read on the Pluto per level;
  M17 receivers track deviation, so a few percent off is harmless.
- **Shape:** alpha 0.2 is narrower than the spec's 0.5 (baseband edge 2.88 kHz, not 3.6 kHz).

  | TX shape into RX filter (sim) | Signal to ISI | Worst eye |
  | --- | --- | --- |
  | RRC 0.5 into RRC 0.5 (spec) | 68.7 dB | 99.7 % |
  | **RRC 0.2 (C6000) into RRC 0.5 (OpenRTX, m17-cxx-demod)** | **30.8 dB** | **78.0 %** |
  | RRC 0.2 into RRC 0.2 | 40.0 dB | 92.9 % |

  30.8 dB is far above the SNR where M17 starts to lose bits (about 10-15 dB), so standard
  receivers should not notice. It is still a nonstandard transmit shape: say so upstream. Whether
  a C6000 register picks another alpha is unknown (RadioFoundry documents none).
- **Timing:** the symbol clock is the C6000's crystal (DMR grade); measure its ppm on the Pluto.
- **Against upstream's LINE_IN path:** no PWM, no PWM compensator, no FM chain (its pre-emphasis,
  high pass and limiter), deviation from the radio's own DMR calibration instead of an input gain
  found by ear, and no start gap (both halves are prefilled before the PA keys). Our FFSK through
  that same LINE_IN path came out about 10 dB low with a 300 Hz cut until PR #135. An A/B of
  the two M17 paths on the Pluto is useful data for upstream on its own.
- **CPU and memory:** the module only copies 36 bytes per 30 ms at the slot interrupt.
  Milestone 1 sends frames built on the host; on-radio encoding (Codec2 3200, which OpenRTX
  already runs on this radio) is milestone 3.
- **Key-up length:** 4.6 s fits preamble, LSF and about 110 stream frames (4.4 s of voice). Real
  calls need the image's 5 s cap and guard extended: an image change, milestone 3.

## Receive

| Path | Verdict |
| --- | --- |
| C6000 FM RX RAM tap: 8 kHz 16 bit, a half per 32 ms, 0x82 bit 1 (RadioFoundry `modem-hrc6000.md` 8.1, measured on an AT1846S board), AT1846S in its DMR set | **Candidate.** RadioFoundry decodes C4FM (same rate, alpha 0.2) from the C7000's tap at 0.3 % BER direct. On the C6000 the tap sits after 0x34's filters (C4FM notes: bits 5 and 7 off). High pass corner and top edge unknown |
| I2S3 codec input in RX | No: de-emphasis and a steep high pass under about 450 Hz (`uv380-characteristics.md`) |
| C6000 DMR receiver | No: it only syncs on DMR syncwords |
| Upstream's PC3 mod | No: solder (Jeff's rule) |

What the tap must look like (sim: standard M17, alpha 0.5, through a stand-in AF filter, read at
8 kHz, x3 to 24 kHz, RRC 0.5; "eq" = decision directed baseline restorer plus 7-tap equaliser,
the C4FM receiver's recipe):

| Tap | Signal to ISI | Worst eye |
| --- | --- | --- |
| DC coupled, flat to 3.8 kHz, + eq | 53.7 dB | 98.6 % |
| DC coupled, flat to 3.4 kHz, + eq | 49.4 dB | 97.1 % |
| DC coupled, flat to 3.0 kHz, + eq | 35.0 dB | 87.8 % |
| DC coupled, flat to 2.6 kHz, + eq | 27.7 dB | 70.7 % |
| 3.4 kHz, one-pole HP 20 Hz, + eq | 23.6 dB | 37.0 % |
| 3.4 kHz, one-pole HP 50 Hz, + eq | 18.2 dB | -6.1 % |
| 3.4 kHz, one-pole HP 100 Hz, + eq | 13.8 dB | closed |
| 3.4 kHz, one-pole HP 300 Hz, + eq | 8.4 dB | closed |

The 8 kHz rate itself is fine: without the equaliser the same DC coupled taps read 15-19 dB,
mostly from the sim's short interpolator, which a real one fixes. A high pass is not: 4FSK carries real energy near DC, and a corner much
above 20 Hz closes the eye. A known one-pole high pass could be undone by a decision feedback
filter modelled on it; that is research, not a plan. So RX is a go only if the tap measures
DC coupled (or under about 20 Hz) with some bit of 0x34 or a neighbour.

On the radio: 512 bytes per 32 ms over the bit-banged C6000 SPI (16 kB/s), a x3 interpolator
to feed OpenRTX's 24 kHz demodulator (or a demodulator option for 8 kHz input).

## Payload: Codec2 or sotto

- **Standard first:** Codec2 3200 voice (or 1600 + 1600 data), so stock M17 radios, OpenRTX
  radios and M17 reflectors hear it.
- **Sotto is a nonstandard extension.** Ways to carry it, best first:
  1. Voice + data stream: Codec2 1600 in the voice half for everyone, sotto 1200 in the data
     half for radios that know it. Stock radios still hear the call.
  2. Packet mode: sotto clips as packets. Ask the M17 project for a protocol ID rather than
     squatting on one.
  3. A data-type stream of sotto 2400. Stock radios show the call and play nothing.
- Sotto stays in blowfang and sotto. Nothing sotto-specific goes upstream unless OpenRTX asks.

## Bench plan (hamlet)

Rules for every run: UV-380 on **441.850** (its own channel; claim `--freq 441.850`), **0 W**
(PA on, APC 0) unless Jeff is present, CW ident AG7EW first (the image's `tx_tone` ident) and
AG7EW as the LSF source, the UV-380's own listener off, the Pluto RX LO parked 50 kHz up
(441.900) with the pad on its RX port, Pluto TX gain at most -30 dB with its self-ident. RAM
modules only for milestones 1 and 2 (the image already has the loader); no flash.

**Runner:** PR #158 (merged) gives `uv380.cmd` and `uv380.send`, but no step runs a module other
than `txreq.c`. Milestone 1 adds one fixed step, `uv380.m17` (the checkout's
`firmware/uv380-openrtx/mod/c6m17tx.c` plus a frame file whose sha256 is in the job, signed by
the runner, the same pwr=0 and freq-in-claim checks as `uv380.send`), in a small runner PR with
tests. Never submit `uv380.flash` for this work.

**Reference receivers:** Pluto capture, FM discriminator, 24 kHz s16 into OpenRTX's own
`tests/platform/m17_replay_tool` (built from upstream master on hamlet; it counts LSFs,
packets, stream frames and misses), with m17-cxx-demod or libm17 as a second opinion. These are
used on hamlet only, never copied into blowfang.

**Reference transmitter (RX tests):** the Pluto sending spec M17 (alpha 0.5, +-2.4 kHz) from a
host encoder, with its CW ident.

| Test | What | Pass |
| --- | --- | --- |
| T1 dry | `c6m17tx` with `TX_DRY`: slots clocked, no PA | `late=0`, both halves read back, all frames out |
| T2 BERT | 4.4 s of M17 BERT (PRBS9) at 0 W | 0 bit errors; levels +-800/+-2400 Hz within 5 %; symbol rate and carrier offset logged |
| T3 packet | M17 SMS "AG7EW M17 TEST" | `m17_replay_tool` packetsOk = 1, CRC good |
| T4 voice | 4 s Codec2 3200, TTS "AG7EW M17 test" (a clean demo voice), host encoded | all stream frames, decoded audio posted for Jeff's ear |
| T5 A/B (Jeff present) | the same LSF from OpenRTX's own M17 mode (LINE_IN path) | eye, deviation and BER side by side |
| R1 tap | module captures the 8 kHz tap while the Pluto sends a 50 Hz-4 kHz FM tone sweep, then M17 BERT; 0x34 variants | the response curve; go/no-go for RX |
| R2 offline | host demod of the R1 BERT capture (x3, RRC 0.5, eq) | BER at -80 dBm and down to the edge |

## Milestones

1. **M17 TX, no mod, no flash (recommended first).** `c6m17tx` RAM module plus host frame
   builder (Python, against the spec; tests check the frames against OpenRTX's replay tool and
   the byte packing against the slot model in `modem/c6phy.py`), the runner step, then T1-T4.
   About a day of code, one bench session at 0 W.
2. **RX tap measurement (R1, R2).** One module, Pluto TX only, no UV-380 TX. Decides whether
   no-mod RX exists.
3. **M17 from the mic, on the radio.** Image change: OpenRTX's FrameEncoder and Codec2 feeding
   the C6000 path, key-up guard for long calls, PTT in M17 mode. Hands-free flash.
4. **M17 RX** (only if milestone 2 says go): tap driver, x3 interpolation, OpenRTX's demodulator.
5. **Upstream** (below), starting after milestone 1 with evidence in hand.

## Milestone 1: built (2026-11-17), works on air (22:00-22:27Z)

| Piece | What |
| --- | --- |
| `modem/m17.py` | M17 frames from the spec: LSF, stream (LICH, Golay), packet, BERT, EOT; Codec2 3200 through libcodec2; a demodulator (RRC 0.5, syncword lock, levels from the sync) and decoder (Viterbi) for Pluto captures, with `--baseband` writing 24 kHz s16 for OpenRTX's `m17_replay_tool` |
| Checks | the packet frame equals OpenRTX's RF-captured reference (`tests/unit/M17_packet.cpp`) byte for byte; OpenRTX's own test baseband (`M17_test_baseband.raw`) decodes to 1 LSF and 663 stream frames (their test wants 660 or more), and re-encoding every one of them gives the received bytes exactly |
| `c6m17tx.c` + `_bert`, `_sms`, `_voice` | c6phytx's keying and slot feeding with the bytes of `m17tx_PLAN.h`; `TX_DEV` 123; EOT fill after the data; 7.7-8.8 KB |
| Plans | bert: 98 PRBS9 frames (4.0 s); sms: 20 transmissions of "AG7EW M17 TEST nn" (3.2 s); voice: 2.96 s of Codec2 3200, Kokoro af_heart "A G 7 E W. M 17 test." (`tests/data/uv380/m17-voice.c2`, 3.1 s on air) |
| Sim | all three plans decode through the chip's alpha 0.2 at 2400 Hz and at 1944 Hz (TX_DEV 100), with noise |
| Runner | `uv380.m17` step (one plan, the `uv380.send` read-back), and a Pluto `rx` may run in the background so it hears the send |

Bench job (per plan, at 0 W on 441.850; job files in `/mnt/project-files/uv380-m17/bench-keys.md`):
Pluto `rx` in the background at 441.900 MHz, `uv380.cmd` listen 0, power 0, zero 2, trim
(the FM trim less 6680), `uv380.m17 PLAN`, wait, `uv380.cmd` trim back. Then
`python3 -m modem.m17 decode CAP.cf32 --baseband CAP.raw` and `m17_replay_tool CAP.raw`.

### On air (11-17, the UV-380 at 0 W on 441.850, the Pluto receive only at 441.900)

Every key-up: the Morse ident (3.9 s), then the burst, `chk 00=36/36 24=36/36`, every slot on
time (`late=0 lost=0`), all bytes sent, the radio's registers and trim back as found. Trim for the
raw mode: 6100 - 6680 = -580.

| Job (UTC) | Sent | Decoded from the Pluto capture |
| --- | --- | --- |
| m17-bert-1117d (22:00:26.5-31.3Z) | 4800 B, 100 frames, 136 slots | 92 BERT frames found, 0 of 18124 bits wrong; probe: every one of the 132 data slots in order, symbol errors 0.87 % before FEC |
| m17-sms-1117e (22:25:32-35.9Z) | 3840 B, 80 frames | LSF 20/20 good, source AG7EW; packets 20/20 CRC good, "AG7EW M17 TEST 01" .. "20" |
| m17-voice-1117e (22:27:06.3-10.1Z) | 3696 B, 77 frames | LSF 1/1, source AG7EW; 73 of 74 stream frames, LICH 0 bad; Codec2 decoded to 2.92 s of speech |

Measured: symbol rate 4800.0 baud (+0 ppm against the Pluto's clock, from the 40.000 ms frame
spacing); levels about -2300 / -770 / +750 / +2310 Hz, so `TX_DEV 123` gives 96 % of M17's
2400 Hz with the inner levels at a third (a later wrapper can try 128); the burst +200 to +370 Hz
from 441.850 by the uncorrected Pluto, the ident before it about 7 kHz lower (the raw mode's own
offset, `docs/uv380-c6phy.md`). The chip's byte-to-symbol rule is the standard one: MSB dibit
first, 01 +3, 00 +1, 10 -1, 11 -3 (`python3 -m modem.m17 probe CAP plan` tests every order and
map against the sent bytes).

Two decoder lessons (host side only, the module was right from the first key-up):
- The capture's one carrier estimate spans the ident and the burst, kilohertz apart, and left the
  burst off zero: no sync matched (BER 0.38 on lone false syncs). The demodulator now blocks DC
  over a frame, finds the frames, takes each run's own DC from its frames and decodes again; the
  capture filter is 12 kHz wide for M17.
- Lone sync matches in noise made frames of every kind; frames now count only in runs 40 ms apart.

A few frames go unfound (8 of 100 BERT, 1 of 74 stream) at the 0.93 sync threshold: the next
decoder step is to decode every 40 ms slot of a run, found or not. OpenRTX's `m17_replay_tool` on
the `--baseband` files is still to run as a second opinion.

## Upstream plan (OpenRTX)

**What we offer, in order:** M17 RX on MD-UV3x0 and DM-1701 with no mod (if milestone 2 goes);
M17 TX for GD-77 and DM-1801, which have none (needs a GDx owner to test: "help wanted"); a
cleaner M17 TX on MD-UV3x0, with the A/B data to show it. Open questions to put to them: the
alpha 0.2 shape, and whether they want it as default or as an option.

**Licence.** OpenRTX is GPL-3.0-or-later. blowfang's new code is MIT, which can go into a GPL-3
tree (MIT SPDX kept, or relicensed by Jeff); `uv380-blowfang.patch` is already GPL-3. C6000 facts
come from RadioFoundry's docs (MIT): credit them. Never copy RadioFoundry's `core/c4fm` or tests
(GPL-2.0-or-later: facts only), OpenGD77 code, or the vendor's C6000 manual. Upstream code is
written fresh against OpenRTX's own `HR_Cx000` driver.

**Their process** (CONTRIBUTING.md, AGENTS.md, PR template):
- Open an issue first for anything large and agree the design before code.
- Linux kernel style via `scripts/clang_format.sh`, C++14 without exceptions, enums not defines,
  no `std::string`, buffers allocated when a mode starts, SPDX header on every new file.
- One logical change per PR; commits prefixed by module (`drivers:`, `m17:`, `rtx:`), each
  building on its own; review fixes as `fixup!` commits, squashed before merge.
- Catch2 unit tests under `tests/unit`; "Testing performed" filled in with hardware ticked.
- **AI rules:** each AI-assisted commit carries a Co-authored-by line; Jeff signs off every
  commit himself (`git commit -s`, DCO), never an agent for him; PRs an agent opens stay
  **draft** until Jeff has reviewed them and marks them ready.

**Patch series (proposal for the issue):**
1. `drivers: HR_Cx000: add physical layer 4FSK transmit` (enter, leave, feed the next slot,
   deviation). A generic 4FSK primitive, not M17-specific.
2. `m17: Modulator: add a symbol-domain backend` for targets with a hardware 4FSK modulator,
   gated by a `CONFIG_` for that capability; the PCM path stays the default.
3. `drivers: radio_UV3x0: use the C6000 4FSK path for M17 TX` (and DM-1701 if someone tests it).
4. `test: m17: ...` frame-to-slot packing, preamble, EOT.
5. Website: `M17/mduv380_mods.md` says TX needs no mod.
RX (tap driver, 8 kHz input) and GDx enablement are separate PR series later.

**Talking to the maintainers** (openrtx.org "Get in touch"): GitHub issues, the developers
mailing list (new in October), monthly office hours (notes published), and their chat. Start
with one issue or mailing list post: "M17 TX through the HR_C6000 physical layer mode, no mod",
with the milestone 1 captures, BER and an eye plot, the website line it updates, and the
question of how they'd like it structured. Mention the demodulator reliability epic so RX work
lines up with it. **We draft, Jeff posts:** no upstream issue, PR, post or message goes out
without his OK.

## Risks and unknowns

- The tap's high pass (decides RX).
- Whether 0x45/0x46 reach 2400 Hz cleanly without clipping the chip's shaping (C4FM's 139 %
  says yes on the C7000).
- The C6000's symbol clock ppm and whether `TX_MODE` 0x02 (continuous, untested on C6000) beats
  0x66 for long calls.
- Upstream appetite for a nonstandard alpha; the M17 demod rework may change interfaces under us.
- 1 W and up only with Jeff present (the 1 W USB drop is handled by the existing guard).

Related: [C4FM Foundry notes](uv380-c4fm-foundry.md) (same TX path aimed at Yaesu framing,
toward the FTM-300D), [flat FSK](uv380-c6phy.md), [DMR modem](uv380-c6000-modem.md),
[characteristics](uv380-characteristics.md), [bench runner](bench-runner.md).
