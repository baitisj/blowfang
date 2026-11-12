# DMR on the K6: detect it, mute it, maybe decode it later

*2026-11-08.* Jeff's idea: the Baofeng UV-K6 should at least recognise DMR and mute its audio,
and perhaps decode it later. The local DMR repeater is on 440.925 MHz. Mixed-mode, it also sends
an analog CW ID, which EXPLORE keeps finding at -90 to -100 dBm.

**Answer: yes, with no hardware change, as a RAM module on today's image. It works on the air
(2026-11-08 07:02Z): the K6 muted a real DMR key-up on 440.925 20 ms after the carrier came up,
then played the repeater's analog CW ID** (see "Run 3" below; the first design needed the gain
frozen and a band-pass added before it saw real DMR). The K6 can't hear
its own receive audio: the FD6818's AF output goes straight to the speaker amplifier. But it
can see the receive IF on PB1 (GP0 code 13, the sigma-delta stream that `gp0dma` and the FT8
and softmodem modules read by DMA). In that IF's FM-demodulated frequency, DMR's 48-bit sync
words show up plainly. A correlator for them, about 3 KB of Thumb code using about a quarter of
the K6's CPU, finds every kind of DMR transmission in the model within 0.1 to
0.7 s. It found nothing in 30 s each of FM voice, FFSK, CW tones and noise. The `dmrdet` module
mutes the speaker (REG_47) while DMR lasts and plays FM between. A later decode can reach the
metadata (colour code, talkgroup, source ID), but not the AMBE+2 voice.

```
FD6818 low IF (16.93 kHz), gain frozen at REG_13 0x03FF -> GP0 code 13, 1-bit sigma-delta on PB1
  -> DMA ch1, 5.5 MS/s, 512 B ring -> ones per 64 samples (85.8 kHz counts)
  -> band-pass biquad around the IF -> rising zero crossings, interpolated
  -> the IF's phase at 9600 points a second (2 a DMR symbol), the grid set from the DMA's measured rate
  -> frequency per grid step -> spread gate -> 4 sync correlators (BS, MS, direct TS1, TS2)
  -> hit (rho >= 0.8) -> confirmed (2 hits a whole number of 30 ms slots apart) -> mute REG_47
```

Code: `sim/k6if/dmr.py` (the float model and the study), `c/k6/dmrsync.h` (the integer detector,
header-only), `c/k6/dmrsync_host.c` (it on the host), `c/k6/mod/dmrdet.c` (the module), and
`tests/test_k6_dmr.py`.

## What DMR looks like to the K6

DMR (ETSI TS 102 361-1) is 4FSK at 4800 symbols/s. Dibits 01, 00, 10 and 11 map to +3, +1, -1
and -3 units of 648 Hz deviation, with root-raised-cosine pulses (alpha 0.2), in two 30 ms TDMA
slots. Each burst is 54 payload symbols, a 24-symbol mid field (sync, or embedded signalling),
then 54 payload symbols.

- **A repeater (BS) transmits continuously while it's up.** Both slots go out, each with a
  12-symbol CACH in front, and an idle slot carries idle bursts with a data sync every 60 ms. So
  at a repeater's output the carrier does **not** pulse. The 16.7 Hz RSSI pattern is real only
  for a mobile or a direct-mode radio (one 27.5 ms burst every 60 ms), so RSSI alone would miss
  the repeater, which is the case that matters here.
- **The sync words use only the outer symbols (+-1944 Hz) and are balanced.** A data sync is
  the voice sync negated, so one correlator per source family finds both, and the sign tells
  which. A voice call carries its sync in one burst of six (every 360 ms a slot). Headers, data,
  CSBK and idle bursts carry it in every burst.

| Family | Voice sync | Data sync |
|---|---|---|
| BS (repeater output) | 755FD7DF75F7 | DFF57D75DF5D |
| MS (mobile to repeater) | 7F7D5DD57DFD | D5D7F77FD757 |
| Direct mode, TS1 | 5D577F7757FF | F7FDD5DDFD55 |
| Direct mode, TS2 | 7DFFD5F55D5F | D7557F5FF7F5 |

**What the K6 can't use.** The MCU gets no audio samples (`docs/ht-survey.md`). The chip's FSK
modem tops out at 2400 baud, binary only. The coordinator's spectral-flatness idea needs audio
samples too; the discriminator frequency from GP0 is that audio, and the sync words in it
are a sharper test than flatness.

## The detector

1. **Discriminator.** Rising zero crossings of the 85.8 kHz counts, interpolated (as `gp0dma`),
   give the IF's phase at each crossing. Linear interpolation gives the phase at a uniform grid
   of 9600 points a second. Each grid step's phase change, less the nominal IF's, is the
   frequency over that 104 us (Q11, 4.69 Hz an LSB). That is an integrate-and-dump over half a
   symbol; the model found no gain from 4 points a symbol or full-symbol windows.
2. **Grid timing.** The grid's step in counts comes from the DMA's rate, which moves about 3%
   with code placement and bus load. The module measures it against TIM17 every ~90 ms
   (`ks=` on the summary line). A grid 1% off still gives hits, but their spacing drifts off
   the 30 ms slot raster; 0.3% off is fine (the confirmation allows 1/256 of the gap).
3. **Spread gate.** Over the sync's 24 symbols (every other grid value), D = 24 sum(y^2) -
   (sum y)^2 is 576 times the variance. A window whose spread is outside 1300-2600 Hz rms is
   skipped: a bare carrier, squelch noise (far wider), most voice. The outer-symbol sync is
   1944 Hz rms.
4. **Correlators.** For each family, c = sum p_i y_i, computed as 2 x (the sum over the
   pattern's 12 plus positions) - sum y, and rho^2 = c^2 / D: a normalised correlation, so it
   ignores the carrier's frequency offset and the deviation's scale. A hit is rho >= 0.8,
   the best within +-2 grid points.
5. **Confirmation.** A hit is confirmed when its family had another hit a whole number of
   slots (1 to 24, up to 720 ms) earlier, within +-2 grid points plus 1/256 of the gap. All DMR
   bursts sit on the 30 ms raster, so this holds for repeater, mobile and direct mode alike.

## Results in the model

The model is the GP0 path from the softmodem study (`sim/k6if/frontend.py`: the FD6818's filter
and AGC, the 1-bit sigma-delta, the DMA at 5.49 MS/s), with DMR from a TS 102 361-1 framer
(`sim/k6if/dmr.py`: CACH, bursts, superframes, RRC alpha 0.2), 3 s per case.

| Case (C/N0 54-70 dB-Hz) | Syncs found | Confirmed | First confirm after the carrier |
|---|---:|---:|---:|
| Repeater: a voice call joined midway + idle slot | 58 | 57 | 0.11 s |
| Repeater: voice calls on both slots | 18 | 16 | 0.14 s |
| Mobile: voice joined midway (pulsed carrier) | 9 | 7 | 0.50 s |
| Direct mode TS1: voice joined midway | 8 | 7 | 0.62 s |
| FM voice (3 speakers, CTCSS), FFSK 1200, a keyed 1 kHz tone, a bare carrier, noise | 0 | 0 | |

- **Sensitivity:** unchanged down to 54 dB-Hz. At 50 dB-Hz about a third of the syncs still
  come through, and they die out at 47, where the zero-crossing discriminator passes the FM
  threshold. 600 Hz of carrier offset changes nothing. (This section first said 54 dB-Hz was
  about -116 dBm with an 8 dB noise figure. On the K6 it isn't: with the pager's AGC, the real
  repeater at -85 to -90 dBm reached GP0 at what the model calls about 40 dB-Hz. See "Captures
  of real DMR" below.)
- **False hits:** over 30 s each at 55 dB-Hz, the largest |rho| was 0.63-0.75 on FM voice,
  0.76 on FFSK, 0.69 on the keyed tone and 0.53 on noise (the threshold is 0.8). Random 4FSK
  at 4800 baud (what YSF or P25 payload looks like) gave 4 single hits in 30 s and no
  confirmation. A false confirmation would need a second such hit within a few grid points
  of a slot multiple: about one every few hours of continuous 4FSK. Muting YSF or P25 would be
  welcome anyway.
- **The C core matches the float model** on DMR, FM voice and FFSK: the same confirmations
  within 10%, the same families, nothing on non-DMR (`test_c_matches_python`).

## The `dmrdet` module

`c/k6/mod/dmrdet.c`, API level 2 (it uses `key_poll` on level 4 images), receive only. It tunes
to 440.925 MHz (wide), puts GP0 on code 13, runs DMA channel 1 into a 512-byte ring (TIM17 as
the clock), and plays the receive audio on the speaker (REG_47 AF 1, PB2 on) only while
there is a carrier (RSSI 10 dB over the floor measured at the start) and no DMR. The audio
opens 150 ms after the carrier comes up, so a transmission's first syncs (its headers, or
the repeater's idle slot) keep it shut. Once DMR is confirmed, any sync of that family keeps it
muted for 720 ms more, enough to bridge a voice superframe's 360 ms. It runs 600 s; any key
stops it. Everything it changes is put back.

```
dmr start f=440925000 floor=-125
D on bs d r2=234 +13          DMR: family, voice/data sync, rho^2 x 256, ms after the carrier
s 60 h=15 lost=0 idle=58 ks=5490
c -60 2578 h=55               a carrier came and went: dBm, ms, syncs during it
D off 2611
```

On the emulated Cortex-M0 (`tests/k6_live_emu.py`'s CPU and DMA model, calibrated against
`gp0dma` on Jeff's K6) against the modelled stream:

- **A repeater keying up with a voice call:** DMR confirmed 13 ms after the module saw the
  carrier, inside the 150 ms hold, so the speaker never opened. No samples lost, CPU idle
  54-58% (busy 42-46%), the DMA rate tracked to 5490 kS/s.
- **FM voice:** no DMR, the audio opened after 150 ms, one `c` line with `h=0`.
- **Size:** 3104 bytes of code (CI's clang makes it ~90 bytes bigger). The 512-byte ring and
  the state are on the stack (see "Captures of real DMR"). The ring is 512 bytes (93 us) rather
  than gp0dma's 1 KB because the module needs the room. That's why it checks keys with
  `key_poll` (5 us) rather than `key_down` (60 us), and makes only one FD6818 call at a time
  between drains. Getting it to fit cost the screen (the WSPR modules' level 6 calls) and
  `gp0dma`'s 20 ms rate measurement at the start: the grid starts at 5.49 MS/s and is
  corrected within ~90 ms.

## How it fits EXPLORE and the listener

- **EXPLORE: built (2026-11-08, `c/k6/xdmr.c`, docs/k6-explore-app.md "DMR mute and the CTCSS
  read").** Every listen runs the detector alongside the CW ID listener, mutes DMR on the
  speaker, marks the row DMR and sets the saved entry's `RT_F_DMR`. The plan was:
  when it listens to a row, run the detector
  alongside the CW ID listener. The CW ID decoder uses the FD6818's tone detector, not the
  audio, so muting doesn't affect it. A row that confirms DMR gets a DMR mark, and its
  table record a mode flag and later the colour code (byte 53 is spare,
  `docs/k6-repeater-table.md`). The 440.925 row that EXPLORE keeps listening to without decoding anything would
  then say "DMR" at once, and listen only for the analog ID.
- **Aura's own receiver:** the same detector could gate the speaker in normal VFO and memory
  listening. That needs firmware: the DMA running whenever the squelch is open, and ~45% of the
  CPU while a DMR carrier lasts (less on FM: the correlators run only on windows that pass the
  spread gate). It would go into the pager patch, like EXPLORE, after the module has proved
  it on air.

## Decoding DMR metadata (2026-11-08)

Metadata only: colour code, slot, talkgroup or unit, source ID, talker alias, and which CSBKs and
data packets go by. No voice: DMR voice is AMBE+2, which is patent-encumbered. The open decoders
(mbelib, and OpenDMR's voice path built on it) are PC code of uncertain legal standing that would
not fit the M0 anyway.

**Python reference (`sim/k6if/dmrdec.py`).**
- It reads a receiver's FM audio (`audio`, `calls`, `summary`, `check`) or the K6's GP0 counts
  (`counts_symbols`), resampled to 8 points a symbol through an RRC matched filter.
- The input's polarity comes from the slot types (data syncs must give clean Golay words). The
  IC-7100's audio is inverted.
- Each burst decodes as follows:
  - TACT: Hamming (7,4).
  - Slot type: Golay (20,8), the (23,12) generator 0xC75 shortened, plus overall parity.
  - Voice LC headers and terminators: BPTC (196,96) and RS (12,9), parity masks 0x96 and 0x99.
  - CSBKs and data headers: CRC-CCITT, init 0, inverted, masks 0xA5A5 and 0xCCCC.
  - Voice bursts B-E: the EMB, QR (16,7,6) from the (17,9) QR code's generator 0x139, and the
    embedded LC, BPTC (128,72) with a 5-bit checksum.
  - Talker alias: FLCO 4-7.
- A call's headers and terminators are also soft-combined. A terminator's masks are turned
  into a header's, which works because the codes are linear.
- `voice_call_symbols` builds whole calls for the tests.
- `tools/k6_dmrwatch.py` turns recordings into a JSONL answer key, one line per call or data
  packet.

**Checked on 440.925 (11 IC-7100 recordings).**
- Every layout came out at zero syndromes. Everything is colour code 1.
- Voice is on TG 3027 (FLCO 0, FID 0x10) from three radio IDs.
- Every ~7 s burst is a DMR GPS report: 5 preamble CSBKs, an unconfirmed data header (SAP 3)
  and 8 rate-1/2 blocks carrying `$GPRMC`, relayed over the network from distant stations.
- No talker alias was seen.

**On the K6 (`c/k6/mod/dmrrx.c`).** It is dmrdet with the decoder (`c/k6/mod/dmrdec.inc`,
`c/k6/dmrburst.h`). It keeps the mute.
- dmrsync.h's 2-point-a-symbol grid feeds a 13-tap RRC whose output is kept in an 8-bit ring of
  512 in the module's scratch.
- At each sync hit, the burst is read back:
  - The timing is found again to a quarter grid point on the filtered sync: the correlation at
    the 5 whole grid points around the hit, then a parabola through the peak, rounded to a
    quarter by comparisons. Reading between grid points uses Catmull-Rom. At 2 points a
    symbol, the nearest point alone left 1-2 bit errors a burst at any level.
  - The burst is scaled by its own sync.
  - The slot type and the CACH's TACT are read first. The rest of a header, terminator, CSBK
    or data header is sliced on the next poll, so the DMA ring drains between the two.
- Slot: every burst's TACT counts, voice included. Hamming (7,4) fixes one bit, and a fixed
  TACT is kept only if it agrees with the slots' alternation (every 288 grid points). A burst
  whose own TACT failed takes its slot from the last good one.
- Voice bursts B-E are read at A + 576 k grid points. A flywheel reads the next superframe
  360 ms on when its sync is missed (two in a row at most). Once a chain has passed an EMB
  check, voice hits off its beat are dropped: false hits had started chains of their own.
- Embedded LC: the four fragments are kept as soft bits, and taken when at least 2 of the 4
  EMBs check out. One row with two errors is fixed from the columns whose parity fails. A
  fragment set that fails alone is added to the last superframe's, then the one before (talker
  alias LCs come between the call's LC), then both: a call's LC is the same each superframe.
- The grid's step locks to DMR's 30 ms slots. The DMA's measured rate is only good to ~2%,
  which over a superframe is 10 symbols. The first measurement (~50 ms in) is taken whole,
  later ones averaged: the old 1/4 steps from the 5.49 MS/s guess cost most embedded LCs when
  the DMA ran 1% off it.
- Housekeeping (keys, RSSI, the screen, a line) waits, up to twice its 48 ms, for the other
  slot's time while a voice chain runs. Its API calls stop the counting and the filter fills
  again after them, ~5 ms lost, and on the radio they outlast the DMA ring. Emulated at 52
  dB-Hz with 300 us register reads: embedded LCs 16 to 24 of 32, full aliases 3 to 12 of 16.
- The grid index counts across pauses (`ds_skip`, laps from the clock), so bursts can still be
  predicted by it.
- Every line ends in ` @` and the module's time in tenths of a second, for
  `tools/k6_dmrscore.py`.

On the K6 model (`tests/test_k6_dmrdec.py`), 10 calls of 6 superframes for each level. The
"before" column is the decoder as first pushed:

| | before | now |
|---|---|---|
| 52 dB-Hz, embedded LC / full alias | 49 of 60 / 5 of 10 | 56 / 9 |
| 50 dB-Hz, embedded LC / full alias | 22 / 0 | 44 / 4 |
| 48 dB-Hz, voice mid bursts read / embedded LC | 146 of 240 / 1 | 188 / 11 |
| 48 dB-Hz, no alias (as on 440.925), embedded LC | 1 | 31 |

No run gave a wrong talkgroup, source or alias. The LC header itself (soft-combined) decodes in
every call down to 48 dB-Hz.

- **The DMA ring:** the emulated call lost the ring 34 times in 2 s. A gap trace (the DMA
  counter's reads, and what ran between them) put 45 of the 62 long gaps in the old quarter-step
  sync search. With the parabola, one alignment a poll, Hamming fixes found from the masks'
  columns, and BPTC stopping after a clean pass, it loses 3, all in housekeeping (the status
  line and an LC decode). dmrdet alone loses none. Since the first bench run the ring is 512
  bytes, in scratch: it laps ~20 times a second while a burst is read, which costs nothing
  (the burst is in the filter's ring by then; the same lines as with 1 KB at 50-52 dB-Hz), and
  housekeeping's laps aren't counted as lost.

| | |
|---|---|
| Code | 9.4 KB, so only the SPI voice images' 11,776-byte shared module block (PR #146); not EXPLORE or the lean images |
| Scratch | the filter's 512-byte ring, the decoder's 1,032-byte state and the 512-byte DMA ring (2,056 B), or it returns -6 and says `dmr scratch N need M`: never on the stack |
| Stack | 1,288 B emulated, of the 3,112 B the module call gets on the vlrh image |

Serial lines, besides dmrdet's (every line ends in ` @` and tenths of a second since the start;
`D off` gains the decoder's counts so far, `D off 6906 r=51 g=50 t=6 d=0`: data and LC bursts
read, slot types that passed, BPTC decodes tried, and decodable bursts dropped because one was
still waiting):

    L TS1 CC1 TG 3027 src 3025107 F0 @52     voice LC header or terminator (comb N: only the soft sum decoded)
    E TS1 TG 3027 src 3025107 @131           embedded LC, when it says something new (late entry)
    A AG7EW TEST @120                        talker alias so far
    K TS1 bd00800104c2bf301cd5 @803          a CSBK that passed its CRC (here a preamble)
    H TS1 ... @804                           a data header

The screen (level 6) shows "DMR CC1 TS1", "TG 3027", "ID 3025107" and the alias.

**First bench run (16:43-16:53Z 2026-11-08, main bf7990a vlrh image, quiet repeater).** The
module ran its 10 minutes and returned 1, then the K6 reported `stack_free=0`, keyed for its
upload report and hung with the carrier up on 440.850 (low power) until Jeff cut the power at
~17:09Z. The module's stack had overrun the core's RAM: 2,580 B emulated (the 1 KB DMA ring and
two 288-byte slicing buffers on it) of the 3,112 B, plus the firmware's own frames inside the
API calls, which the emulator doesn't count. Also: `lost` climbed ~21 a second with no DMR on
the air (every housekeeping: the radio's API calls outlast the 1 KB ring, which the emulator's
20 us register reads never did; reproduced at 300 us) and `ks` never left its 5490 start (each
of those laps restarted the ~90 ms rate measurement). Fixed after: everything in scratch, the
stack at 1,288 B, housekeeping's laps not counted and the rate measured over each ~45 ms
stretch between them. The repeater was quiet (at most 11 syncs a summary), so nothing decoded yet.

**Second run (22:31-22:41Z 2026-11-08, image 006ee1b, no telemetry).** Clean: the full 600 s,
`stack_free=1828`, the upload report sent. With telemetry 12 it refuses (`dmr scratch 1468 need
2056`). One 501 ms data burst came up (`D on` / `D off`), nothing decoded; the key had nothing
decodable either.

**Third run (17:44-17:54Z 2026-11-09, image cf9516e).** One real GPS packet at -84 dBm: `D on
bs d` 120 ms after the carrier, 78 syncs in its 7 s (a third of the bursts), `lost=82` over it,
no K or H line; the IC-7100's key has the data header at 17:45:36.66Z. Two causes, from the
model of such a packet (`dmrdec.data_packet_symbols`: 5 CSBK preambles, the header, 8 blocks):
- A third of the syncs is ~46 dB-Hz on the K6 model, where the decoder reads few bursts and
  decodes none (52 dB-Hz: 5/5 preambles and the header in every packet; 50: 28/30 and 4/6; 48:
  9/30 and 2/6; 46: 0). The K6's own antenna is the lever there.
- One burst waits for housekeeping at a time, and housekeeping skipped the decode whenever it
  had another line to say. The header came 70.66 s in; `D on` was said at 70.4 s, so a preamble
  was still waiting and the header (if its sync was heard) was dropped. Emulated at 52 dB-Hz, 2
  of the 6 were dropped that way. Now a decode goes before the other lines (`D on` waits a
  housekeeping): 6 of 6, `d=0`.

**Scoring a bench run.** On hamlet, `tools/k6_dmrwatch.py` turns the IC-7100's 440.925
recordings into the answer key. `tools/k6_dmrscore.py --key KEY.jsonl --log K6.log` then lines
the K6's serial lines up with it: it finds the module's start from the talkgroup lines
themselves, or takes `--start`. It counts calls named and named right, wrong and stray lines,
data headers, and full aliases. It prints no payloads, which may hold people's positions.

## Real DMR from 440.925 (IC-7100 audio, 2026-11-08)

Five carriers recorded on the IC-7100 (FM-D, 12 kHz, 48 kHz USB audio, flat to a sharp edge near
2.5 kHz) between 04:24 and 04:37 UTC, run through `python3 -m sim.k6if.dmr audio` (the audio
stands in for the discriminator):

| Clip (UTC) | Length | Syncs (BS family) | Confirmed | First confirm | median rho at hits |
|---|---:|---:|---:|---:|---:|
| 04:24:56 | 8.9 s | 232 + 1 other | 231 | 1.04 s | 0.99 |
| 04:32:15 | 12.0 s | 304 | 303 | 1.05 s | 0.95 |
| 04:34:34 | 11.9 s | 284 | 283 | 1.05 s | 0.99 |
| 04:35:03 | 10.7 s | 261 | 260 | 1.05 s | 0.97 |
| 04:36:54 | 9.0 s | 232 + 1 other | 231 | 1.05 s | 0.97 |

- Every clip starts with about 1 s of margin, so DMR was confirmed within ~50 ms of the
  carrier, on its second burst.
- Syncs came every 30 ms (one slot apart) with a few two-slot gaps: both slots carried
  data, idle or header bursts most of the time. The rarer opposite-sign syncs are the voice
  bursts. The IC-7100's audio is inverted relative to the model (its data syncs read as
  voice), which the correlator doesn't mind.
- The family is BS throughout (a repeater), as expected; the 2 other-family hits were
  unconfirmed.
- The rest of the time |rho| stayed at p99 0.62-0.65, well under the 0.8 threshold.

So the sync shape, timing and framing hold on the real repeater, through a 2.5 kHz audio path
that's worse than the K6's IF. What's still untested is the K6's own GP0 discriminator on a real
DMR carrier: that's the bench run.

## On the K6 (2026-11-08)

Two receive-only runs of CI's `mod-dmrdet.bin` (3116 B with header, from main f56f0a0) on
440.925, the IC-7100 sending the upload on 440.850:

- **Run 1 (05:08:55Z, no cable, speaker on):** Jeff heard a DMR transmission start as a short
  buzz, then a prompt mute, at about 05:17:40, and the analog side otherwise behaved. The report
  went out on 440.850 (Jeff saw the TX LED on time) but the IC-7100 was back on 440.850 about
  5 s too late: the K6 starts a module as soon as it has the last new chunk, ~17 s before the
  upload audio ends, so the hamlet side now returns 35 s earlier. No log from this run.
- **Run 2 (05:39:12Z, cable in, serial log):** a miss. Floor -108 dBm, one 0.5 s carrier at
  -91 dBm (05:47:15, `c -91 488 h=0`): not one sync hit, yet the IC-7100's recording of it holds
  16 BS syncs one slot apart at rho 0.97 (`audio` mode, 15 confirmations from 1.24 s). One stray
  hit early on never confirmed, `lost=0` every minute, `ks=5374` (the real DMA rate, 2% under
  the model's 5490; a 2% grid error would cost the confirmations, not the hits, so it isn't the
  rate). 17 dB over the floor is about 58 dB-Hz, where the model finds every sync. So the K6's
  own IF path doesn't look like the model on real DMR, and the mute Jeff heard in run 1 is
  unconfirmed (the IC-7100's copy of 05:17:40 is bare hiss).
- **CPU idle:** the run printed `idle=286` and then 1-2%, which was a reporting bug: the idle
  and window sums were in 96 MHz cycles and wrap a u32 every 44.7 s, so a 60 s window can't be
  measured. Unwrapping the numbers gives about 73% idle in the first minute and 75% after that,
  better than the emulator's 54-58% (its CPI 1.5 is pessimistic). The summary line now comes
  every 40 s, inside the wrap (summing in coarser units cost 32 bytes the store doesn't have
  with CI's compiler).
- **Run time:** the firmware's `K6 MOD ret=0 in 3766 ms` is also wrong (600 s ran). Its
  module clock only advances when the module calls into the firmware's tick (delays, TX
  guard), and the 100 us counter wraps every 6.5 s between calls; dmrdet only polls keys and
  RSSI. Cosmetic, firmware side.
- **The short buzz:** the speaker opens 150 ms after the carrier (`HOLD_MS`), and a confirm
  needs two syncs a whole number of slots apart. On a repeater that is keying up from idle the
  second sync can come later than 150 ms. If that buzz is common, raise `HOLD_MS` (FM pays it
  as opening delay) or open on a single strong sync's absence for 2 slots.

- **Run 3 (06:54:46Z, cable in, PR #140's module: gain frozen, band-pass):** it works. The
  only carrier in ten minutes was the repeater keying up at 07:02:35Z with ~0.5 s of DMR (the
  IC-7100's audio: 17 BS syncs at rho 0.94), then dropping to analog for WA7DMR's CW ID
  (975 Hz, 20 wpm, ~3.7 s). The K6, verbatim:

  ```
  07:02:35Z D on bs d r2=198 +20      DMR (BS, data sync, rho 0.88) 20 ms after the carrier
  07:02:36Z c -80 483 h=14            the DMR carrier: 14 syncs in 483 ms
  07:02:36Z D off 559
  07:02:40Z c -80 3740 h=0            the analog ID: no syncs, speaker open
  s 40..560 h=0..15 lost=0 idle=73-74 ks=5390-5393
  ```

  Muted inside the 150 ms hold, so the DMR was never heard. The analog ID played. No false
  `D on` in the other 9.5 minutes, no counts lost, 74% idle (the band-pass costs ~1%). The
  floor reads -97 dBm at the frozen gain (-108 under the AGC).

### Captures of real DMR (2026-11-08 06:06Z)

`c/k6/mod/dmrcap.c` (level 2, receive only) listens on 440.925 and, each time the RSSI rises
8 dB over the floor, records 38 ms stretches of the raw IF counts (on the stack, the first
within ~5 ms of the carrier) and dumps them over the cable in gp0cap's format, plus one
no-carrier reference. A repeater with both slots going has a sync every 30 ms, so each capture
holds a whole one. `python3 -m sim.k6if.dmr cap LOG` runs the detector on every capture (IF
offset, best sync with and without the spread gate, measured +-3 deviation), and `--diag`
compares four discriminators and measures how far the IF band stands above the 1-bit path's
noise.

The first run (pager's AGC only) caught two real transmissions, three captures each, which the
IC-7100's audio decodes at rho 0.98-1.00:

| | counts rms | IF band over the noise above | best \|rho\|: MCU crossings / band-pass / complex | IF mean, spread (MCU crossings) |
|---|---|---|---|---|
| no carrier (-108 dBm) | 12.8 | -16 dB | 0.77 / 0.67 / 0.68 | noise |
| DMR, -85 to -90 dBm | 4.1-4.4 | +3.4 to +5.3 dB | 0.64-0.76 / 0.59-0.89 / 0.67-0.86 | +2.5 to +3.5 kHz, ~5 kHz |
| model at 60 dB-Hz | 21 | +20 dB | 0.99 / 0.98 / 0.98 | +0.1 kHz, 1.4 kHz |
| model at 42 dB-Hz | 5.7 | +7 dB | 0.66 / 0.72 / 0.60 | +1.8 kHz, 4.8 kHz |

The real DMR looks like the model at about 40 dB-Hz. The detector isn't wrong: the IF reaches
GP0 too weak. With no carrier the counts swing at rms 12.8 (the receiver's noise at full
gain). With the -85 dBm carrier they drop to 4.2, about a fifth of the model's working level,
so the AGC turns the IF down to where the sigma-delta's own noise is only 4-5 dB below it in
band. Even a band-pass ahead of the crossings, or a complex discriminator, only reaches
0.8-0.9 on half the captures. (The pager's 1200-baud modes have far more margin per symbol,
which is why they never showed this.)

The second run (06:31Z) cycled each carrier's captures through four gain steps: the pager's
AGC, then the AGC frozen (REG_7E bit 15, index 3) with REG_13 = 0x03FF, 0x03BE, 0x037E. Two
7 s DMR transmissions, eight captures each:

| gain | RSSI reads | counts rms | IF band over the noise | best \|rho\|: bare crossings / band-pass / complex |
|---|---|---|---|---|
| pager's AGC | -86 | 4.3 | +4 to +5 dB | 0.60-0.69 / 0.65-0.94 / 0.67-0.95 |
| REG_13 0x03FF | -79 | 6.8 | +10 dB | 0.71-0.82 / **0.95-0.99** / 0.90-0.97 |
| REG_13 0x03BE | -86 | 4.3 | +4 to +5 dB | 0.59-0.70 / 0.75-0.88 / 0.68-0.90 |
| REG_13 0x037E | -94 | 3.5 | +1 dB | 0.60-0.71 / 0.60-0.76 / 0.65-0.72 |

At full gain the real DMR matches the model at about 48 dB-Hz (+10 dB, bare crossings 0.80,
band-passed 0.95). Bare crossings still miss most syncs there; band-passed ones find them.
So dmrdet now does both:

- **Gain:** it freezes REG_13 at `GAIN13` (0x03FF) for the run and puts the AGC back after.
  The RSSI reads ~7 dB higher at that gain, and the floor is measured after the freeze.
- **Band-pass:** `dmrsync.h` runs each count through a 2nd-order Butterworth biquad around the
  IF (10.4-23.4 kHz at 85.8 kHz, Q14 coefficients, Q8 out) before the crossings. That's 3
  multiplies a count. On the model it moves the threshold ~3 dB: at 48 dB-Hz, 30 of 39 syncs
  and 29 confirmations instead of 3 and 1. At 46 dB-Hz it's 11/10 instead of 0. A 4th-order
  filter adds ~1 dB more. False hits are still nil (10 s each at 46 and 55 dB-Hz of FM voice,
  FFSK, a keyed tone, a bare carrier and noise; the largest non-DMR |rho| is 0.77, on FFSK).
- **Room:** the 512-byte ring and the 452-byte state moved to the module call's stack (the
  emulator measures under 2 KB in all, against 3.5-6 KB), so the store only has to hold the
  code: 3104 bytes.

To rerun a capture: cable in, `tools/k6_upload.py mod-dmrcap.bin --api-level 2 --run-s 620`
while 440.925 is busy, with the IC-7100 recording alongside for ground truth.

## Sync by the chip's own FSK receiver (`dfskcap_dmr`, 2026-11-11)

The detector above reads the IF tap, whose own noise sets its limit at about -79 dBm
(`docs/k6-ais.md`, `docs/k6-aprs-clock.md`); the repeater on 440.925 MHz comes in at -85 to
-90 dBm. The chip's own FM receiver has its floor at -117 to -123 dBm on 70 cm. What was said
above of the chip's modem ("2400 baud, binary only") is half out of date: its plain FSK
receiver runs to 6348 baud, and what it gives is the sign of the discriminator's output on a
clock that follows the zero crossings (the APRS and ACARS work, `docs/k6-acars.md`). It is
still binary. But:

- At 4800 baud that sign is the first bit of each dibit (01 and 00 are +3 and +1, 10 and 11
  are -1 and -3).
- A sync word has outer symbols only, so its signs are a fixed 24 bits: the BS data sync
  DFF57D75DF5D is **439B4D** with + as 1, and the voice sync is its inverse. The receiver
  takes a sync word of two or four bytes and needs no preamble before it (it starts on 3333
  in APRS flags).

So the chip may find DMR's sync by itself, at its FM receiver's sensitivity: a detector with
no DMA, no correlator and no CPU load, 30 dB better than the tap's. `dfskcap_dmr` asks
whether it does (receive only; `python3 -m modem.dmrsign LOG` reads its log). The sync word is
the last sixteen signs, 9B4D, or 64B2 for the other sense of the discriminator (which is not
known; a data sync one way up is a voice sync the other). A capture is 128 bytes: the signs
of the 1024 symbols after the sync, so the sync fields of the next seven bursts would be its
bits 120 to 143, 264 to 287 and so on if the clock kept time. A capture that shows them is
DMR beyond doubt, and where they stand says how well the slicer and the clock hold on 4FSK.

| Setting | Sync | Filter | AFC | REG_2B |
| --- | --- | --- | --- | --- |
| 0, 1 | 9B4D, 64B2 | narrow | off | + 0700 (de-emphasis and both AF filters off) |
| 2, 3 | 9B4D, 64B2 | narrow | off | as found |
| 4, 5 | 9B4D, 64B2 | wide | off | + 0700 |
| 6, 7 | 9B4D, 64B2 | narrow | on | + 0700 |

Eight settings of 0.8 s, 110 times round (12 minutes: a key-up of the repeater's is some
7 s, so each one meets every setting). The emulated test (`tests/test_k6_dmrsign.py`) checks
the module and the report on a stream of signs; whether the real slicer follows 4FSK is the
bench's question.

What it cannot give: the second bit of each dibit. A decoder would still need the tap, or a
slicer leaned to one side (REG_58's bits 7:6), which is not tried here. A detector, the
EXPLORE mark and the mute need only the sync.

### Run J (2026-11-11 07:30:49 to 07:43:13Z, build 13c7783): it finds the sync

The K6 listened on 440.925 MHz for 110 rounds (880 slots), receive only. The repeater keyed
once, at 07:39:48Z, for about half a second at -81 dBm; every other slot peaked at -124 to
-132 dBm, but three at -93 to -95 that were the bench's ID keeper on 440.850 MHz. The key-up
fell into setting 4 (9B4D, the wide filter, the AFC off, REG_2B + 0700), which started on it
(the capture is stamped 55 ms into the slot) and gave 139 words, 2224 signs, to the carrier's
end.

- **The capture holds 15 sync fields**: 5 with all 24 signs, 8 with one sign dropped (always
  sign 16, the second of a pair of +3), 2 with three and four signs off. All the data sync's
  way up, so 9B4D is the right sense on this channel.
- **Noise gives none.** The other 48 captures (51,536 signs) hold no sync field, whole or
  short. They are what chance owes a 16-bit sync word: one in 65,536 bits, 13.7 s of open
  receiver at 4800 baud.
- **The clock does not keep time.** The sync fields are 130 to 152 bits apart, 140 on
  average, not 144: the plain receiver's clock follows each turn of the slicer
  (`docs/k6-acars.md`), and on 4FSK that drops and adds signs. The bursts were idle ones on
  both slots (the idle message's 96 bits through `sim/k6if/dmrdec.py`'s `bptc196_encode` and
  `burst_bits` give the signs that follow each sync), and against that the capture has signs
  missing where inner symbols stand: a run with a -1 or +1 in it comes one short, a lone +1
  between negative symbols is not seen.

So the chip finds DMR and cannot read it, not even the first bit of each dibit. For a
detector that is enough: the chip's start, then one more sync field (whole or short) anywhere
in what follows, which chance does not give. With 9B4D a third of the sync fields can start
it, so a key-up is found within some 90 ms; CD4D, the last sixteen signs of the short form,
would match half of them. `python3 -m modem.dmrsign LOG` finds the sync fields wherever they
are (`s` marks the short ones), and the capture is a test (`tests/test_k6_dmrsign.py`).

Not answered: the level. -81 dBm is where the tap's detector gives up, and the chip's floor
read -131 dBm in this run, but no weaker DMR came by. And one key-up met one setting, so the
other seven are untried.

### Run K (2026-11-11 12:55:48 to 13:42:31Z, `dfskcap_dmr2`, build fad6693): every key-up, every setting

The same listen with the sense known, 47 of its 60 minutes (a key ended it for the next
bench user), 3,401 slots:

| Setting | REG_58 | Sync | Filter | AFC | REG_2B | Starts on DMR | Sync fields, whole + short |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 00E9 | 9B4D | wide | off | + 0700 (run J's setting 4) | 1 | 4 + 7 |
| 1 | 00E9 | CD4D | wide | off | + 0700 | 2 | 47 + 52 |
| 2 | 00E9 | 9B4D | narrow | off | + 0700 | 1 | 37 + 34 |
| 3 | 00E9 | CD4D | narrow | off | + 0700 | 1 | 48 + 43 |
| 4 | 00A9 (bits 7:6 = 10) | 9B4D | wide | off | + 0700 | 1 | 14 + 20 |
| 5 | 00E9 | 9B4D | wide | off | as found | 4 | 17 + 42 |
| 6 | 00E9 | 9B4D | wide | on | + 0700 | 2 | 13 + 17 |
| 7 | 00E9 | CD4D | wide | on | + 0700 | 1 | 39 + 45 |

- **The repeater keyed six times in DMR, at -83 to -87 dBm, and every key-up was caught**:
  three of 7 s (at 182, 1085 and 1988 s into the run) and three of half a second (at 788,
  1691 and 2594 s). It keeps a clock: a long one every 903 s, a short one 606 s after each.
- **Every setting starts.** Inside the long key-ups each new slot started again, mostly 22
  to 85 ms after the slot's own start (one took 338 ms: setting 5), whatever the sync word,
  the filter, the AFC, bits 7:6 or the AF filters. No slot with DMR in it went without a
  start. (After the short key-up at 1691 s the carrier stayed for 4 s at -86 to -89 dBm and
  five settings found nothing in it: the repeater's analog ID, most likely; it was not
  recorded.)
- **72% of the sync fields read**: 479 in 93,584 signs (some 668 bursts), 219 whole and 260
  short. A capture runs on to the carrier's end or to 1056 words, so the counts follow the
  key-up a setting happened to meet, not the setting.
- **Noise gives none**: 260 captures of noise (285,664 signs), one in about 11 s, and no sync
  field in any. One was a start on the bench's ID keeper next door (-99 dBm in the wide
  filter), with nothing in it either.

So at this level nothing is left to choose: 9B4D or CD4D, either filter. The narrow filter
keeps the neighbours out. What is still not known is the level it works down to.

### Run L (2026-11-11 14:32:13 to 15:03:01Z, `dfskcap_dmr3`, the module built from 9b7dd51): how weak?

The repeater has one level here, so the receiver is made deaf by steps instead: one setting
(9B4D, the narrow filter, the AFC off, REG_2B + 0700) with the gain fixed (REG_7E bit 15 with
index 3, REG_13 as the gain, as `surf.c` does) at eight values, taken off the front first so
that the signal sinks towards the noise of the stages behind. Each slot's `p=` and `f=` give
the signal and the floor at that gain, so how far over the floor the signal stood is
measured, not reckoned. A capture is cut at 96 words (a third of a second) so that a key-up
of 7 s meets every gain.

The K6 only listened (the IC-7100 sent the one upload on 440.850 at RF power 0): 2,240 slots
of 0.8 s, 166 captures, "dfskcap end n=166", ret=166, stack_free=408 B. The repeater keyed
four times: 0.5 s at t=489 s and t=1397 s, 7 s at t=791 s and t=1693 s (903 s apart again,
the short one 606 s after the long one). All four were caught: the short ones fell on
settings 5 and 4 and both gave sync fields.

| Setting | REG_13 | Gain by the tables | DMR read | Floor read | Over the floor | Slots with DMR | Sync fields (whole + short) | Of the bursts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 03FF | 0 dB | -77 dBm | -125 | 48 dB | 2 | 16 + 14 | 76% |
| 1 | 035F | -14 dB | -96 | -144 | 48 dB | 2 | 17 + 18 | 82% |
| 2 | 031F | -24 dB | -108 | -153 | 45 dB or more | 3 | 24 + 17 | 77% |
| 3 | 021F | -35 dB | -121 | -156 | 35 dB or more | 2 | 14 + 18 | 75% |
| 4 | 011F | -40 dB | -127 | -156 | 28 dB or more | 3 | 27 + 14 | 60% |
| 5 | 001F | -43 dB | -131 | -156 | 24 dB or more | 3 | 14 + 17 | 66% |
| 6 | 0007 | -51 dB | -136 | -146 | 10 dB | 2 | 1 + 0 | 7% |
| 7 | 0004 | -60 dB | -145 | -155 | 10 dB | 2 | 5 + 2 | 21% |

("Of the bursts": sync fields found over the signs captured with DMR in them, at 144 signs a
burst. Setting 4's figure includes the tail of a short key-up.)

- **Down to 24 dB over the floor nothing is lost**: two thirds to four fifths of the sync
  fields, as run K's 72% at full strength.
- **At 10 dB over the floor it is at its edge**: the chip still started in all four slots
  that had the carrier (six captures; a start is a 16-sign match), and four of the six held
  at least one more sync field, but only 8 fields in some 48 bursts. That is where an FM discriminator
  falls into its threshold, so the chip's clock and slicer cost little on top of it.
- **In this station's terms**: the repeater reads -83 to -87 dBm with the AGC on and stood
  48 dB over the floor, so the floor is near -133 dBm, the edge near **-122 dBm** and full
  yield holds to about **-108 dBm**. (This line first said -115 and -100 dBm: it added the
  10 and 24 dB to the floor as it read at the fixed gain, -125 dBm, where the signal read
  8 dB high as well. The front-end probe of the same day read the floor directly at
  -132 dBm with the AGC on, docs/k6-fesw.md.) All by the K6's own meter, which is not
  calibrated. The GP0 IF tap's detector needs about -79 dBm (48 dB-Hz).
- **What the figures are not**: a weak signal made by taking gain off is not a weak station.
  The floor reads at the bottom of the RSSI's scale (-153 to -156) on settings 2 to 5, so
  their "over the floor" is a lower bound and there is no point between 24 and 10 dB. The
  gain that the RSSI lost (19, 31, 44, 50, 54, 59, 68 dB) is more than the tables say. With
  the mixer's gain at its least (settings 6 and 7) the floor comes up by 9 dB against setting
  5 though the gain is lower: the noise there is the mixer's own. A truly weak station (an
  attenuator, or the antenna off) would settle it.
- **Noise**: about 130 noise captures gave one lone match of the short form, upside down
  (setting 5, r=-155, near the end of a capture; left out of the table). Over runs J, K and L (some
  480,000 noise signs) about 0.1 such match is expected by chance; the rule of a second sync
  field in the capture throws it out.
- Six slots of settings 1 to 5 held a carrier with no sync in it, 10 to 30 dB under the DMR
  key-ups' level at those gains (about -87 to -106 dBm by the gain lost): not the key-ups
  missed, but something else on the channel, as the plain carrier of run K. They were not in
  the seconds after the long key-ups; their times were not looked up.

The detector that follows from J, K and L: the plain receiver at 4800 baud with the sync
word 9B4D (or CD4D), the narrow filter, the AFC off, and a capture counted as DMR when a
second sync field (whole or short, either way up) stands in it. `python3 -m modem.dmrsign
LOG` gives a setting's floor, its slots with a carrier and in how many of them a sync was
found.

### Run M (2026-11-11 22:30:46 to 2026-11-12 00:51:26Z, `dfskcap_dmr4`, the module built from 4f588ca): did the tuning cost run L its weak steps?

Run L tuned at every one of its 0.8 s settings, and `api->tune` restarts the receiver. The
POCSAG sampler, which reads the same plain receiver, gave noisy bits for a minute or two after
a restart and far fewer when the module left the tuning out (docs/k6-pocsag.md, runs 6 to 8).
That was put down to the AFC pulling in again, and run L had the AFC off, so it need not carry
over; but run L never had a setting that was not half a second from a restart. It matters for
the explorer, whose detector comes to a channel by tuning to it.

`dfskcap_dmr4` is run L's setting in blocks of 144 rounds (922 s or a little more, so every
block meets one of the repeater's key-ups, which come 903 s apart), three kinds by turns,
three blocks each:

| `bk=` | The tuning | The AFC |
| --- | --- | --- |
| 0 | at every setting, as run L | off |
| 1 | none: the receiver runs on from kind 0's last restart | off |
| 2 | once at the block's start (for the AFC bit to take hold), then none | on |

The gains are 03FF, 021F, 011F, 001F, 0017, 000F, 0007 and 0004: run L's without 035F and
031F, which told nothing new, and with the mixer's two steps between 001F and 0007, where run
L jumped from 24 dB or more over the floor to 10. The s line carries `bk=`, and `python3 -m
modem.dmrsign LOG` gives a line a gain and kind.

The K6 only listened (the IC-7100 sent a CW ID to wake it at 22:30:07Z and the one upload at
22:30:24Z, both on 440.850 at RF power 0, serial 1794436223): 10,368 slots of 0.8 s, 1,025
captures, "dfskcap end n=1025", ret=1025, stack_free=408 B. The repeater keyed seventeen times:
6 to 10 s every 903 s or so, a single slot's worth five minutes before most of those, a 37 s
one, and one call of 129 s (00:37:09 to 00:39:17Z, in a kind 2 block). The logger was cut at
00:27:50Z (the hamlet session's two-hour limit on a background command took the go script and
its logger with it) and a new one ran from 00:28:35Z: 46 s of a kind 1 block are missing, seven
rounds, none with a key-up. The module ran on untouched.

A slot has a carrier when its peak stands 10 dB over the floor of its gain and kind. "Of the
bursts" is the sync fields found (whole and short) over the signs captured in those slots, at
144 signs a burst. Kind 2 is given without the long call, which is on its own line.

| REG_13 | DMR read | Floor read | Over the floor | Kind 0: slots with a sync, of the bursts | Kind 1 | Kind 2 |
| --- | --- | --- | --- | --- | --- | --- |
| 03FF | -74 to -84 dBm | -122 to -125 | 46 dB | 4 of 4, 83% | 5 of 6, 79% | 7 of 7, 84% |
| 021F | -120 to -128 | -156 | 33 dB or more | 5 of 6, 58% | 3 of 4, 77% | 7 of 7, 69% |
| 011F | -124 to -133 | -156 | 28 dB or more | 5 of 7, 62% | 3 of 4, 67% | 5 of 6, 77% |
| 001F | -127 to -136 | -156 | 26 dB or more | 6 of 8, 62% | 4 of 4, 67% | 5 of 6, 81% |
| 0017 | -129 to -139 | -150 | 17 dB | 4 of 5, 54% | 3 of 3, 51% | 4 of 5, 77% |
| 000F | -132 to -140 | -147 | 13 dB | 4 of 6, 46% | 3 of 3, 44% | 5 of 6, 35% |
| 0007 | -133 to -139 | -146 | 11 dB | 5 of 5, 20% | 2 of 3, 39% | 3 of 3, 28% |
| 0004 | -142 to -147 | -155 | 11 dB | 3 of 4, 17% | 1 of 4, 14% | 3 of 3, 33% |
| all | | | | 36 of 45, 52.5% | 24 of 31, 61.2% | 39 of 43, 64.3% |
| the long call | | | | | | 100 of 103, 48% |

- **The tuning cost run L nothing.** With no tuning at all (kind 1) the chip read no further
  down than with one at every setting (kind 0): 61% against 52% of the bursts over all gains,
  and at the two weakest steps 39 and 14% against 20 and 17%, on three or four slots each.
  POCSAG's settling after a restart is the AFC's, or the sampler's alone; the plain receiver
  with the AFC off is as good half a second after a restart as a quarter of an hour after.
  The explorer may come to a channel as often as it likes.
- **The edge stands, and it is a slope, not a step.** At 26 dB or more over the floor two
  thirds of the sync fields are read; at 17 dB half or more; at 13 dB under half; at 11 dB a
  quarter over all the kinds (run L: 7 and 21% on two slots each). In this station's terms (the floor
  near -133 dBm with the AGC on): full yield to about -107 dBm, half at -116 to -120, the
  edge near -122 dBm, as run L had it.
- **The AFC on is no worse and probably better.** Kind 2 had a sync in 39 of its 43 slots
  with a carrier (kinds 0 and 1 together: 60 of 76), and read more of the bursts on the
  middle steps. What differs most is the form: with the AFC on 72% of the sync fields came
  whole (83% in the long call, where the AFC had two minutes to pull in), with it off 54 to
  59%. So the sign that the chip's clock drops (run J) has to do with where the signal sits
  against the slicer, and the AFC moves it towards the middle even in a burst of seven
  seconds, or keeps its pull from one key-up to the next. The chip starts on the whole word
  only, which is why more slots started. A detector may leave the AFC as the firmware has it.
- **The gain takes hold without a tuning**: every gain's floor is the same in the three
  kinds (03FF: -125, -123 and -122).
- The long call reads less of its bursts at the strong steps (59% at 03FF against 84%) and
  as much at the weak ones: a call's traffic is not the idle key-up's. Not looked into.
- No key-up fell in the first two minutes of a kind 1 or kind 2 block but the long call, so
  nothing had to be counted apart.
- The numbers are small: three or four key-ups a kind, one repeater, one evening.
- Three of the key-ups, one of each kind, with the quiet rounds before them, are in
  `tests/data/k6/dfskcap-dmr4-runM-20261111.log.gz` (the whole log, 2.5 MB of lines, is on
  hamlet under `~/blowfang-work/runs/dmr-M-20261111/`).

### Run N (2026-11-12 01:41:16 to 03:13:00Z, `dfskcap_dmr5`, the module built from 3ce8f73): where does the slicer sit?

Run M's lead was that more sync fields come whole with the AFC on, so where the signal sits
against the chip's slicer matters. `dfskcap_dmr5` asks where that slicer is. One setting
(9B4D, the narrow filter, the AFC off, the gain the radio's own), tuned off the repeater by
steps, 0.8 s each, 840 times round (an hour and a half). The plan, with what each answer
would give:

| o= (100 Hz) | tuned | ones, slicer at the tuned centre | ones, slicer at the signal's mean |
|---|---|---|---|
| -13 | 1.3 kHz below | 75% | 50% |
| -9 | 0.9 kHz below | 75% | 50% |
| -5 | 0.5 kHz below | 50% | 50% |
| -2 | 0.2 kHz below | 50% | 50% |
| 2 | 0.2 kHz above | 50% | 50% |
| 5 | 0.5 kHz above | 50% | 50% |
| 9 | 0.9 kHz above | 25% | 50% |
| 13 | 1.3 kHz above | 25% | 50% |

DMR's inner symbols are 648 Hz off the middle and its outer ones 1944 Hz. A slicer at the
tuned centre puts the inner symbols of one side on the wrong side of it from 0.65 kHz off,
and the share of ones in a capture goes from a half to a quarter or three quarters
(`dmrsign.ones_at`); the sync fields, outer symbols only, should still be found. The report
ends each offset's line with the share of ones in its captures that hold a sync.

What each answer buys:

- **Follows the tuning**: a listen 0.9 kHz above tells the upper outer symbol from the
  other three, and one 0.9 kHz below the lower outer one. With the sign from a listen on
  tune that is the whole symbol, from three listens: of use only for what a repeater says
  in every burst, which the colour code is (the slot type either side of the sync). A lead
  towards the colour code for the explorer through the sensitive receiver, not yet a way.
  And a two-level mode through this receiver must be tuned within a few hundred Hz, or
  carry its own balance.
- **Stays at a half**: the chip takes the mean out itself, and POCSAG's need for the AFC
  (insight 48) is not a matter of where the slicer sits.
- **The whole fields' share by offset** says how far the K6 sits from the repeater: it
  should peak where the two agree.

Receive only on 440.925 MHz; the one transmission is the upload on 440.850 MHz at RF power 0.
In the model the signs are the symbols' own whatever the tuning
(`test_dfskcap_dmr5_on_the_model` checks the tunings, the `o=` on the lines and the report).

**What happened.** The K6 only listened, on 440.925 MHz. The IC-7100 sent a CW ID
(01:40:38Z) and the upload (01:40:55 to 01:41:18Z, serial 1794447651), both on 440.850 MHz at
RF power 0, and nothing after. "dfskcap end n=1317", ret=1317, stack_free=408 B, 6,720 slots.
The repeater was far busier than in run M and off its quarter-hour pattern: 23 key-ups, 1,200
slots with a carrier (10 dB or more over the floor, which read -130 dBm at every offset),
peaks -80 to -86 dBm. One call ran from 03:00:01Z to the module's end and holds 70% of the
carrier slots. A carrier fell on every offset alike (146 to 153 slots each).

| o= | Tuned | Carrier slots with a sync | Sync fields whole / short | Ones |
| --- | --- | --- | --- | --- |
| -13 | 1.3 kHz below | 0 of 148 | 0 / 0 | |
| -9 | 0.9 kHz below | 10 of 149 | 10 / 1 | 68% |
| -5 | 0.5 kHz below | 150 of 152 | 1947 / 15 | 59% |
| -2 | 0.2 kHz below | 149 of 150 | 1846 / 22 | 52% |
| 2 | 0.2 kHz above | 24 of 152 | 0 / 27 | 40% |
| 5 | 0.5 kHz above | 0 of 153 | 0 / 0 | |
| 9 | 0.9 kHz above | 0 of 150 | 0 / 0 | |
| 13 | 1.3 kHz above | 0 of 146 | 0 / 0 | |

What it says:

- **The slicer sits at the tuned centre.** With the AFC off the share of ones moves with the
  tuning, from 68% at 0.9 kHz below to 40% at 0.2 kHz above, the way `dmrsign.ones_at`
  says (tuned below, the signal sits above the centre and more of it reads as ones). The
  chip does not take the signal's mean out.
- **The sync is found only within some 0.4 kHz of the best tuning**, not out to 1.3 kHz as the
  plan expected from the outer symbols alone. Why the window is so narrow is not settled: the
  narrow filter's edge, or the chip's clock, which follows the slicer's turns and has fewer of
  them on the right side when one side's inner symbols all read the same.
- **The best tuning is 0.2 to 0.5 kHz below 440.925 MHz**, and the share of ones crosses a
  half about 0.1 to 0.2 kHz below. That is the K6's and the repeater's offset together (FT8
  run 5 put the K6 145 Hz from the IC-7100). Tuned to the nominal frequency, as every run
  before this one was, the K6 sat near the window's upper edge.
- **That explains run M's short sync fields and its AFC.** Inside the window nearly every
  sync field comes whole (99% at -5 and -2); at 0.2 kHz above every one is short. Run M,
  tuned on the nominal frequency, found 54 to 59% whole with the AFC off and 72% with it on
  (83% in a call long enough for the AFC to settle): the dropped sign 16 is the mark of a
  signal off the slicer's centre, and the AFC pulls it back. So run L's edge (-122 dBm) was
  measured off centre too, and may be a few dB better centred: not measured.
- For the detector (`dmrdet`, the explorer's DMR mute): with the AFC off a radio a few hundred
  Hz off a repeater misses it. Latch the AFC on with one tuning (it takes hold at the next
  tune, insight 48), or learn the radio's own offset once and tune by it.
- For a two-level mode through this receiver (idea D): the sender and the K6 must agree within
  a few hundred Hz, or the K6 must run the AFC; and the mode's own bits should be balanced, so
  that a slicer at the tuned centre sees as many of each.
- The colour code through three listens off tune is out: off by 0.9 kHz the sync is lost, so
  there is nothing to hang the off-tune signs on.

The whole log is on hamlet under `~/blowfang-work/runs/dmr-N-20261112/` (`listen-N.log`,
`dmrsign-N.txt`).

## Bench plan (receive only; done, kept for reruns)

1. Real DMR first: the explorer thread's hamlet recorder rotates through 440.925 and is cutting
   DMR and analog clips into `~/blowfang-work/clips/dmr-real/`. The IC-7100's FM audio is
   de-emphasised and band-limited (300-3000 Hz), so the clips check timing and framing, not
   the K6's discriminator. A flat (9600 bps DATA) output would be better if the rig offers one
   over USB.
2. On the K6: from EXPLORE press EXIT twice to VOICE MSG, then upload CI's `mod-dmrdet.bin`
   (`tools/k6_upload.py mod-dmrdet.bin --api-level 2 --telemetry 12 --run-s 620`). Telemetry
   brings the first ~20 lines back over the air without the serial cable, which mutes the
   speaker. The K6's report goes out on 440.850 with its MCW ID, inside the bench rules. The
   module listens on 440.925 for 10 minutes with the speaker on, and the IC-7100 records
   440.925 alongside as ground truth. Jeff should hear the repeater's analog ID and FM, and no
   DMR buzz. A second run with the cable in gives the full serial log.
3. What would change the plan: syncs found but no confirmations (the grid rate, then the
   tolerance); no syncs at all on a strong DMR carrier (the narrow/wide filter, the deviation,
   or the GP0 path under the FD6818's AGC); `lost=` above 0 (the 512-byte ring); idle under
   20% (the correlators' cost).
