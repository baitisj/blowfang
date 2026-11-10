# K6 APRS clock: UTC from packets heard on 144.390

*2026-11-08.* Jeff's idea: the K6 has no clock (the WSPR beacon uses the bench's CW ID as its sync
mark, `docs/k6-ft8-wspr.md`), but APRS traffic carries the time. `c/k6/mod/aprsclk.c` is a
receive-only RAM module that listens on 144.390 MHz, decodes APRS packets on the MCU, reads
their timestamps and votes them into a UTC clock. Nothing is transmitted (an upload sent with
`--arq` still gets its usual report on 440.850 after the module returns).

**Why not Aura's APRS code.** Aura (commit `edf3b98`, the one the pager patch is based on) can
only *send* APRS: `app/ax25/` builds a position beacon (address field, CRC, NRZI) and
`drivers/fd6818.rs` keys it out as Bell 202 by switching the chip's tone oscillator between
1200 and 2200 Hz on a TIM6 bit clock. There is no demodulator, no HDLC deframer and no parser,
and the chip's own FSK modem can't receive AX.25 (it wants its sync words and fixed lengths).
The receiver had to be new either way, so it is MIT C on the IF tap the FT8 and softmodem
modules already use. If the K6 ever sends APRS, Aura's tone switching is the reference for that
module.

## What it does

```
GP0 code 13 on PB1 -> DMA ch1 ring (1 KB, ~5.5 MS/s)                       (gp0rx.h's front end)
 -> counts of 64 samples (~86 kHz) -> mixed down by the nominal IF (64-entry sine)
 -> 2nd-order CIC, decimate by 6 (~14.3 kHz complex)
 -> CORDIC phase (11 steps); the phase step is the FM discriminator (no division)
 -> DC removed; sliding correlators over one bit at 1200 and 2200 Hz; mark > space
    (each tone's deviation, tracked where it wins; the carrier offset from the DC)
 -> DPLL bit clock on those transitions (nudged towards each, firmer inside a frame)
 -> each bit: a matched filter on the IF, mark against space (see "The matched filter"), NRZI
 -> HDLC: flags, bit stuffing, CRC-16/X.25 (good residue 0xF0B8)
 -> the APRS information field's timestamp -> one interval per station -> Marzullo's vote
```

**Timestamps it reads.** `/` and `@` positions, `;` objects and `>` status reports with
`DDHHMMz` (zulu, one-minute resolution) or `HHMMSSh` (zulu, one second); raw NMEA
`$GPRMC`/`$GNRMC` with status `A` (`hhmmss[.ss]` and the date `ddmmyy`); positionless weather
reports `_MMDDHHMM` (zulu by the spec, one minute, with the month). It ignores `DDHHMM/`
(local time, zone unknown), Mic-E (no time), `}` third-party traffic (internet delays) and
everything else.

Positionless weather was added after the 2026-11-09 survey of the worldwide feed: 0.6% of the
packets heard on the air are `_MMDDHHMM`, but only 18 of 102 were within 10 minutes of UTC; most
sent local time or a stale stamp. Stations in one time zone sending local time would agree with
each other, so weather stamps count in the vote only beside another kind: agreement among weather
stamps alone is one vote, never a clock (`test_weather_stamps_alone_set_nothing`).

**The vote.** A stamp says the true UTC at the frame's first bit (the frame's end, less its bits
at 1200 baud) was between the stamp and the stamp plus its resolution plus a delay: 3 s heard
direct, 8 s through a digipeater (any H bit in the path). In offsets (UTC ms of day less the
module's ms, taken round the day so midnight is no edge) each station keeps one interval, the
newest; a digipeated copy of the same stamp is not a second vote. Marzullo's algorithm (NTP's)
finds the offset the most stations agree on, the narrowest region among ties, so a station with
a wrong clock is outvoted. With two or more agreeing the clock is set: the estimate is the
region's low edge plus half its width, at most 0.5 s, because a stamp can't be later than the
send and GPS trackers key within a second of their fix. With minute stamps only (a region wider
than 10 s) it is the region's middle instead, within about 30 s. A seconds stamp in the vote gives the
second ("(sec)" on the screen); `DDHHMMz` alone gives the minute. The date comes from the newest
agreeing stamp that has one (`$GPRMC` gives day/month/year, `DDHHMMz` the day).

**On the radio.** Level 2 to run (receive, GP0, TIM17, DMA channel 1, all put back as gp0rx.h
does); at level 6 it draws its screen (UTC and the date, stations voting, packets heard (preambles)
/ decoded / stamped as `heard 9 ok 7 ts 0`,
and the station heard last, `last AG7EW-7 h0` with its digipeater hops) once a second, never while a frame or its flags are coming (a flush takes 20-40 ms and
the ring holds 0.2 ms); at level 7 it blinks the green LED on each good frame. Any key ends it,
else 15 minutes (`RUN_MS`). It tunes 144.390 with the wide filter and leaves the chip's AFC on,
which pulls a sender's offset in (unlike FT8's narrow tones, AFSK on FM doesn't mind). Lines:

```
aprs start hz=144390000 ks=5490 n=12
f VE7ABC-9 n=47 h=0 ts=17:52:00h               (a good frame: source, bytes, hops, stamp and kind)
clk --:--:-- w=4000 v=1                        (the clock after each stamp: UTC, region ms, votes)
f VE7WX n=67 h=1 ts=17:51:00z
clk 17:52:03.060 w=4000 v=2 d=08
f N0CALL-7 n=81 h=0 ts=17:52:04.220r
clk 17:52:05.274 w=3010 v=3 d=08/11
aprs end ok=3 bad=0 ts=3 v=3 utc=17:52:06.562 lost=0
```

It returns the votes behind the clock (0: not set), or -2 (a key before any frame), -3 (old
image), -4 (PB1 not an input), -5 (REG_33 refused), -7 (TIM17 or DMA channel 1 in use), -8 (the
scratch too small).

## The matched filter (2026-11-10)

Jeff's question after the live run of 2026-11-10 (one packet heard in 8 minutes, his car): is the
decoder just insensitive? It was: deciding each bit on the discriminator falls apart at the FM
threshold, around 50-52 dB-Hz. The softmodem study (`docs/k6-if-modem.md`) had found a
noncoherent matched filter on the IF 5-6 dB better than the best discriminator for FFSK on FM, so
the module now decides each bit that way. The discriminator stays for three jobs it does well
enough below its threshold, because each averages over many samples: the bit clock (its mark /
space transitions), the carrier offset (its DC), and each tone's deviation.

- **The template.** A tone f at deviation D puts the phase exp(j beta sin(2 pi f t + theta)) on
  the carrier, beta = D / f. Each tone keeps a 64-step table of it (int8 cos, sin, its phase
  rounded to 1/64 turn), indexed by the tone's own phase accumulator, the one the correlators use.
  The audio phase theta at the bit's start is unknown, so 16 are tried: 8 sets of sums (x gc, y
  gs, y gc, x gs) give theta from z conj(g) and theta + pi from z g.
- **Per sample**, the IF sample turned back by the offset goes into both tones' sums (64
  multiplies). **Per bit**, at the clock's half turn (the discriminator's sliding window puts its
  transitions half a window late, so that is the bit's end), each tone's metric is its largest
  |sum| over the 16 phases, the bit is mark > space, and the sums are cleared. A first version
  that did the whole window once a bit decoded on the host and nothing on the emulated M0: its
  burst of about 6,000 instructions outran the 0.2 ms DMA ring, which only reports a loss when a
  poll sees it, so the samples after each burst were silently overwritten.
- **The deviation tracker.** Senders differ (2-4.5 kHz, pre-emphasis twist of either sign), and
  a template at the wrong beta loses a lot: J0(beta error), 3.7 dB for the mark tone 1.5 kHz out.
  Each tone's discriminator level, where that tone wins, sets its beta (a one-pole over 64
  samples, so it settles inside a preamble); the table is rebuilt when beta moves half a step.
  With a fixed 3 kHz template, +6 dB twist and 4.5 kHz deviation decoded nothing at all.
- **The bit clock** pulls a quarter of the way towards each transition inside a frame before;
  now a sixteenth (`PLL_IN` 4): the transitions are noisier than the decisions below the
  discriminator's threshold. That alone brought the once-a-bit decision level with deciding every
  sample.

Packets decoded of 48 (`sim/k6if/aprs_mf.py`'s scene: 24 packets of assorted lengths, two seeds,
through the K6 receive model; the host build of the module, matched filter / `-DAPRS_DISC`):

| C/N0 (dB-Hz) | 46 | 48 | 50 | 52 |
| --- | --- | --- | --- | --- |
| 3 kHz deviation | 2 / 0 | 39 / 0 | 48 / 11 | 48 / 46 |
| 2 kHz | | 47 / 0 | 48 / 43 | 48 / 48 |
| 4.5 kHz (the chip's filter clips it) | | 0 / 0 | 14 / 0 | 48 / 3 |
| space +6 dB (pre-emphasis) | | 32 / 0 | 48 / 19 | 48 / 47 |
| space -4 dB | | 47 / 0 | 48 / 11 | 48 / 44 |
| carrier +800 Hz (no AFC in the model) | | 18 / 0 | 48 / 0 | 48 / 24 |
| carrier -800 Hz | | 42 / 0 | 47 / 0 | 48 / 37 |

The half-way point moves from about 50.5 to 47.5 dB-Hz at 3 kHz, and from about 53.5 to 50.5 at
4.5 kHz: 3 to 4 dB, about 1.5 times the range. The float study (`python3 -m sim.k6if.aprs_mf`)
got a little more (45-46 at the half-way point) with a window of n + 1 samples slid every sample,
which costs too much CPU on the M0. `test_matched_filter_beats_the_discriminator` keeps the gap.

**CPU.** On the emulated M0 it decodes all three test stations with no counts lost up to CPI 2.5
(the calibrated cost is 1.5), loses a few counts at 3 and still decodes them; the discriminator
alone went to 5. So the margin went from 2.7x to 1.7x (the emulator errs slow against the radio).
That is with the module at -Os: at -Oz the Arm Toolchain for Embedded's build (CI's, the one sent
to the radio) lost counts at 2.5, a margin of about 1.4x, so the module asks for -Os (1 KB more
code) and the emulator's builds follow the source's choice. The sums are about 16% of the instructions.

## What the model left out: the tap's own noise (2026-11-10)

The first live run of the matched filter (05:34-05:49Z, K6 on the big antenna, image 686e2cf,
module 8a2057d) ran its 15 minutes with `lost=0`, heard two preambles and decoded one frame
(Jeff's car), while Jeff's other handheld, indoors on a short antenna, heard much more on
144.390. The RF path is not the cause: `tune` is Aura's `retune_rx`, which switches the VHF
front end below 300 MHz, and the module uses none of the chip's tone detectors.

The cause is the one the AIS and DMR modules had already met (docs/k6-ais.md run 1,
docs/k6-dmr.md "the second run"). The C/N0 figures above are against the noise *in the counts*,
and the model took that to be the receiver's noise. On the radio it is the 1-bit tap's own:
flat from 0 to 43 kHz with no carrier, above the receiver's noise even at maximum gain. On the
frozen-gain RSSI, 48 dB-Hz is a carrier of about -79 dBm, so this receiver needs packets some
tens of dB stronger than the chip's own FM receiver does. Two things make it worse here, and
both have fixes the AIS module uses:

- **The AGC.** With the pager's AGC running, a DMR carrier stood +4 to +5 dB over the tap's
  noise; with the gain frozen at REG_13 = 0x03FF, +10 dB. The module now freezes it (`GAIN13`,
  REG_7E bit 15 and index 3, both put back at the end; `-DGAIN13=0` leaves the AGC on).
- **The DMA's rate.** Reading the 6.5 MHz stream at 5.5 MS/s folds its shaped noise into the IF
  band: 166 Hz rms of FM noise on a carrier against 60 polled at 22.9 MS/s, about 9 dB. A
  polled APRS receiver has to demodulate between polls (a packet is up to a second, too long to
  record and decode after as AIS does); not built yet.

Neither closes the gap to the chip's own receiver. That needs the chip's demodulated audio or
bits in place of the IF.

**The gain freeze on the air (06:20-06:35Z, K6 on the big antenna, image 43a548d, module
0305b92, 144.390):** 15 minutes, `lost=0`, 11 preambles (2 with the AGC running, an hour before)
and no frame: `ok=0 bad=225`. The minute lines put the floor at -93 to -95 dBm and the
strongest signal of each minute at -86 to -91 dBm on the frozen-gain RSSI. So the packets on
144.390 here peak 7 to 12 dB under the -79 dBm this receiver needs, and polling the tap (9 dB)
would only bring the strongest of them to the edge. The tap is the wrong place to listen.

## The chip's own FSK modem (`fskaprs`, a probe)

Baofeng's own firmware reads nothing from PB1 and gives the MCU no demodulated audio, so the only
other way to the chip's sensitivity is its FSK modem, which the pager's uploads use at levels the
tap can't read. It was written off for AX.25 (line 13 above) for its sync word and fixed length.
`c/k6/mod/fskaprs.c` (level 2, receive only) asks the chip on the bench channel.

**Probe 1 (06:15-06:19Z, 0305b92): plain AX.25, nothing.** 159 s of AG7EW packets (`python3 -m
modem.aprs wav aprs.wav --now --repeat 40`), six variants 8 s each in turn: four with REG_5D =
0xFF00 read as a stream (REG_58 0x3FE3, 0x3F03, 0x33E3, 0x33E9), two with the sync word set to a
run of flags as it is on the line (FE FE FE FE, 01 01 01 01). Every variant: `w=0 i=0000`, no
word and not one interrupt (sync, almost-full or RX-done) in 260 s, at -19 dBm. So the chip does
not stream by itself (the 2026-11-04 note in docs/k6-receiver.md was one packet; the pager's
later stream mode got 0 of 16 too), and a run of Bell 202 flags did not match as a sync word.
The run had no control: nothing in it shows the module's set-up receiving anything.

**Probe 2 (06:57-07:00Z, cf6caff): the chip's own sync word, then the APRS.** The test signal
was units round and round, each a preamble and a sync word of the chip's own kind and then about
a second of Bell 202 AX.25 on the same bit clock: F+ and F- (the pager's FFSK 1200/1800 at 1200
baud, 24 bytes of 55, 07 6B 3E 36; F- with the APRS tones swapped), D2400 and D4800 (direct FSK
as audio, sync CC CC AA CC and CC CC F0 CC). Six variants, 12 s each, the IC-7100 at -19 dBm:

| | REG_58 | Baud | Sync | Result |
| --- | --- | --- | --- | --- |
| 0 | 3FE3 | 1200 | 076B 3E36 | 6 syncs, 384 words, 75 HDLC flags, no frame |
| 1 | 3F03 | 1200 | 076B 3E36 | the same, the words one bit later |
| 2 | 00E9 | 2400 | CCCC AACC | 7 syncs (direct FSK takes a sync word sent as audio) |
| 3 | 00E9 | 4800 (REG_72 = C195) | CCCC F0CC | 5 syncs: REG_72 goes to 4800 |
| 4 | 3FE3 | 1200 | 076B 3E36, REG_5D = FF00 | 180 words: no stream, one 256-byte packet that ran into the next unit |
| 5 | 3FE3 | 1200 | FEFE FEFE | 1 sync, and that one on the variant before's sync word |

What it settled:

- **The control works**, so probe 1's nothing was real: the modem never starts on plain APRS.
- **The FFSK receiver follows Bell 202 bit for bit, one bit short on every mark run.** Against
  the line sent, the words after the sync are exactly `out[i] = sent[i] AND sent[i+1]` (1 = the
  1200 Hz tone), 0 exceptions in 1,000 bits. A lone mark bit vanishes and a lone space becomes
  two, so no frame can pass its CRC. A run of flags whose long tone is the mark reads as
  rotations of 3F (six ones, two zeros); one whose long tone is the space reads as all zeros.
  The first word is in the FIFO when the sync interrupt comes (16 bits after the sync word).
- **Direct FSK is not a 1-bit sampler.** It syncs at 2400 and 4800 baud, but its bit clock
  follows the tone's zero crossings, and the words agree with the sent audio's sign only 55 to
  57% of the time. (On the host model a true sampler would need 4800 a second and about 30 dB.)
- **A new sync word alone doesn't take hold** when REG_58 and REG_72 stay the same (variant 5).

**Probe 3 (07:34-07:40Z, 53268de): what gives the bit back, and what starts the receiver.**
18 settings, 12 s each, against a round of units: P0 to P3 (the pager's preamble and sync at 1200
baud, a word naming the unit, then Bell 202 AX.25 0 to 3 quarters of a bit late), C (the same
line at the chip's own 1200/1800 Hz, a control that must decode), F24 (preamble and sync at 2400
baud), A+ and A- (a plain APRS packet after 40 flags, the flags' long tone the mark or the
space). 107 packets captured, 25 control frames decoded:

| | Setting | Result |
| --- | --- | --- |
| 0 | 3FE3, 1200 baud, sync 076B 3E36 | P0, P1, P2, P3 all one bit short, the rule fits 100%: the loss is not where the bit clock sits (the chip follows the data's own transitions). C: 3 frames |
| 1, 2, 3 | REG_72 to 2400, 3600, 4800 at the sync interrupt | the write takes hold (256 bytes come in half, a third, a quarter of the time), but the 1200/1800 receiver at those rates is no sampler: marks come out two bits and more short, even C fails |
| 4, 5 | 3FE1, to 2400 at the sync; 3FE1 at 2400 throughout | C decodes at two samples a bit (99.4%); Bell 202 doesn't (70%) |
| 6, 7 | sync 3F3F 3F3F; 3F3F in two bytes (REG_59 = 00F0) | **the receiver starts on plain packets**: 10 syncs each, on A+ and on every stretch of flags whose long tone is the mark. The data after it is the one-bit-short kind |
| 8 | sync 0000, two bytes | 12 syncs: on A-, and on anything else |
| 9 | as 7, to 2400 at the sync | syncs; data as 1 |
| 10 | 3FE1 | two bits short, worse |
| 11, 12 | 3FE7, 3CE3 | as 0 |
| 13 | REG_2B + 0100 (de-emphasis off) | as 0 |
| 14 | REG_2B + 0700 | two bits short, worse: REG_2B's filters are in the modem's path |
| 15 | REG_58 to 33E3 at the sync (the 1200/2400 Hz receiver) | **no bit lost on runs**: flags of both senses and long runs read as sent. But where the tones alternate every bit it drops the mark and its place slips, so 80 to 84% right and no frame |
| 16, 17 | to 33E9, to 3FE9 | all zeros after the write: the 2.4K bandwidth is deaf at 1200 baud |

The RX-done interrupt came for none of the packets read on the pager's sync within 300 ms of
their last word (probe 2's `i=3002` was the packet before's, late).

**Probe 4: round the 1200/2400 receiver.** `c/k6/mod/fskaprs.c` (2.6 KB) now tries 27
settings, 8.5 s each from the first sync it hears. The round is P0, P2, C, A+, A- (6.8 s;
`python3 -m modem.aprs fskprobe probe.wav`, 262 s).

| | Setting | Asks |
| --- | --- | --- |
| 0 | REG_58 to 33E3 at the pager's sync | probe 3's 15 again |
| 1, 2, 3 | to 33E3, 33E9, 33E1 and REG_72 to 2400 baud | 1200/2400 Hz at 2400 baud is that receiver's own use, and samples each APRS bit twice |
| 4 to 9 | to 33E1, 33E5, 33E7, 33EB, 33ED, 33EF | its RX bandwidth bits |
| 10, 11 | to 30E3, 31E3 | RX gain bits |
| 12, 13 | to 3303, 33A3 | bits 7:4 |
| 14, 15 | to 33E3 with REG_2B + 0100, + 0200 | de-emphasis off (more 2200 Hz), the 3 kHz low-pass off |
| 16 to 20 | to 23E3, 27E3, 2BE3, 2FE3, 37E3 | RX modes 0, 1, 2, 3, 5 |
| 21, 22, 23 | 33E3 from the start, sync FEFE FEFE, FEFE, 0101 | does that receiver start on a plain packet's flags by itself? |
| 24, 25, 26 | 33E9 and 33E3 at 2400 baud, sync FFFC FFFC, FFFC | the same at two samples a bit |

To make a new sync word take hold the module turns REG_58 off and on around it and arms twice.
It reads a packet's words by time, two word times behind the chip, and says them as the packet
ends: a `c` line (variant, capture, ms at the sync interrupt, ms from there to RX-done, words,
interrupt flags, samples a bit) and `d` lines (the words, ten a line), then a `v` line at each
slot's end. The telemetry is full after the first few lines, so the run is read from the
serial log: `python3 -m modem.aprs fskreport serial.log` names each capture's unit, fits
"every mark run e bits short" to it, says how much agrees with the line when the place may
slip, and counts the frames that pass their CRC. The emulated test
(`test_fskaprs_probe_4_on_a_model_of_the_chips_modem`) runs the module against a model of
packet mode, and the report on the module's lines.

**Probe 4's results (2026-11-10 08:18 to 08:24Z, module 9e860a1, 120 captures, IC-7100 at about
-21 dBm).** One setting reads Bell 202:

| | Setting | What the chip gave |
| --- | --- | --- |
| 13 | **REG_58 to 33A3 at the sync** (the 1200/2400 Hz receiver, bits 7:6 = 10) | **Bell 202 as sent**: three captures, each no mark run short, 99.4% right with one place lost at the write, **all 9 frames pass their CRC**. (Its own 1200/1800 control does not decode there, as it should not) |
| 8 | to 33ED | close: 98%, 1 frame |
| 0, 4 to 7, 9 | to 33E3, 33E1, 33E5, 33E7, 33EB, 33EF | probe 3's: runs whole, alternating tones dropped, no frame |
| 10, 11, 12 | to 30E3, 31E3, 3303 | no frame |
| 14, 15 | 33E3 with REG_2B + 0100, + 0200 | no frame |
| 16 to 19 | to 23E3, 27E3, 2BE3, 2FE3 (RX modes 0 to 3) | no frame |
| 20 | to 37E3 (RX mode 5) | the 1200/1800 receiver again: every mark run a bit short |
| 1, 2, 3, 26 | 2400 baud | bad: two samples a bit is no help in this receiver either |
| 21, 22, 23 | 33E3 from the start, sync FEFE FEFE, FEFE, 0101 | **it starts on a plain packet's flags by itself**: 6, 8 and 8 syncs, 194 to 250 flags read. The same A- packet was caught with FEFE and with 0101, so the chip seems to take its sync word either way up |
| 24, 25 | 33E9 at 2400 baud, sync FFFC FFFC, FFFC | 0 and 7 syncs, data bad |

So the chip's own modem can be an APRS receiver: listen with the sync word FEFE (two flags as
that receiver reads them), slice with REG_58 = 33A3 at 1200 baud, and do the NRZI, HDLC and CRC
on the MCU.

### The receiver on the chip's modem (`aprsfsk`)

`c/k6/mod/aprsfsk.c` is `aprsclk.c` built with `-DAPRS_FSK`: the same HDLC, timestamp parser,
vote, clock and screen, with the IF tap's front end (DMA, mixer, CORDIC, correlators, matched
filter) left out and the chip's packet modem in its place. 5.1 KB against the tap build's 7.6 KB,
336 B of stack on the emulator.

- Set-up: REG_58 = 33A3, REG_72 = 1200 baud, sync word FEFE FEFE (REG_59 = 00F8, four flags),
  REG_5D = FF00 (256 bytes, 1.7 s, the chip's longest packet), no CRC of the chip's, the modem
  turned off and on and armed twice.
- At the sync flag in REG_02, polled every 2 ms (16 bits after the sync word, the first FIFO word already in), it reads a
  word every 13.3 ms by its own clock, one word behind the chip. Each of the word's 16 tone bits
  goes through NRZI into `hdlc()` with its own time (ms at the interrupt + 5/6 ms a bit), so a
  frame's first bit is timed as in the tap build.
- A packet's read ends when the line has been outside a frame for 40 bits (after the closing
  flags the next abort), or after the 128 words. Then the modem is turned off and on (REG_59 and
  REG_58 to 0, REG_58 back, armed twice) for the next sync word: arming alone does not end a
  packet the chip is in.
- An RX-done flag with no packet being read means the chip read a whole packet nobody took: the
  search starts again and the end line's `lost=` counts it.
- The minute's line and the screen are done only between packets.
- Lines besides the tap build's: `y n t= w= ok= st=` for each packet (its number, ms at the
  sync, words read, good frames in it, REG_02's flags and REG_0B in hex) and `d n 000 fefe ...`
  its words, ten a line (every packet in the 440 builds; on 144.390 only those of six words or
  more without a good frame, and no line at all for a few words of noise).
  `python3 -m modem.aprs fsklog LOG` reads them back: flags, good frames, and bad ones a single
  bit away.

Variants:

- `aprsfsk`: 144.390 MHz for 15 minutes.
- `aprsfsk_440`: 440.850 MHz for 110 s, for the bench signal
  (`python3 -m modem.aprs wav OUT.wav --now --repeat N --swap`: `--swap` sends every second
  round with the tones swapped, so the flags' long tone is the mark in one and the space in
  the next).
- `aprsfskb`, `aprsfskb_440`: the receiver reading the FIFO all the time (FSK_STREAM, below).
- `aprsfskd_440`: `aprsfskb_440` with the receiver's de-emphasis off.

A sender that keys up with zeros (alternating tones) and fewer than four flags gives this
receiver nothing to start on.

**First bench run (2026-11-10 08:56 to 09:30Z, a3d8d1c, K6 image 35e982f).** That build had a
two-flag sync word, re-armed with REG_59 alone and waited for REG_0C's "interrupt pending".
IC-7100 at about -20 dBm on 440.850, 42 timestamped packets a run, every second round with its
tones swapped; then 15 minutes on 144.390.

| Run | Result |
| --- | --- |
| 440.850, three runs | 6 of 126 packets decoded, the clock set each time (v=2) |
| 440.850, the 33E3-then-33A3 variant | 0 of 42 |
| 144.390, 15 min | 0 frames from 259 starts; the channel's floor -108 dBm, packets' peaks -93 to -97 dBm |

What the captures showed:

- **33A3 starts on flags by itself, and in both tone senses.** The packets it took at their
  start read 19 words of FEFE (or 0101, the other sense) and then the frame, and all of those
  passed their CRC (REG_0B's low byte A2 for one sense, 62 for the other: bits 7 and 6 are the
  sync's polarity). So the slicer and the sync word are right.
- **Arming does not end a packet the chip is in.** After the module gave up a read early and
  re-armed, the chip went on to its 256 bytes (1.707 s), deaf to the next packets, and the
  module's next "sync" came with the chip's RX-done: exactly 1.707 s after the flags of a packet
  it never saw start. It then read a FIFO the chip had finished with: eight stale words, then
  the first of them again and again (`fefe` x27, or `bf93` x120).
- **A two-flag sync word starts on a packet's two or three closing flags** (then reads silence),
  and **on noise**: the receiver's bits in noise are mostly ones, close enough to FE FE several
  times a second.
- Silence on a carrier reads as zeros.

The fix (3f093fa) is the list above: the modem off and on for each search, the flags polled
directly, RX-done handled, four flags of sync word.

**Second bench run (2026-11-10 09:46 to 10:05Z, 3f093fa, K6 image 35e982f, the K6 silent: uploads
with no reply asked).**

| Run | Result |
| --- | --- |
| 440.850, `aprsfsk_440`, 42 packets, every second round with its tones swapped | **42 of 42 decoded**, the clock set (v=3), `lost=135` |
| 144.390, `aprsfsk`, 15 min | 0 frames. Four real packets heard (10 captures in four bursts), then a lock-up for the last 53 s |

What the captures showed:

- **Turning the modem on raises REG_02's sync flag by itself**, within a few ms, with REG_0B's low
  byte 02 (no sync bit). The module took each for a sync word, read three stale words (the empty
  FIFO gives its last word again: `2862` x3, later `0000` x3), found the line idle and turned the
  modem off and on again: a round every 59 ms, 1,363 of them in 110 s.
- **The packets arrived inside those reads**: 40 of the 42 good frames came from a read that began
  on a flag of the chip's own and ran into the real words (`2862 0101 0101 ...`). So the timed
  reads need no sync flag: reading the FIFO word by word is enough. REG_0B's sync bits were still
  set in the first read after a packet (they outlive one turning off and on), so they cannot tell
  a real sync word from the chip's own flag either.
- A read was always under way, so **the screen and the minute's line never had their turn**.
- **On 144.390 the bits of real packets come with errors.** The ten captures of real traffic
  open with flags that have slipped or lost their short tone (`0808 0008 0808`, `0101 8100 0101
  0102 0202 0208 0404`: the one-bit tone of each flag missing or a bit late) and go on mostly as
  zeros. Not one has three clean flag words in a row. On the bench at -20 dBm, flat audio, the
  same receiver read every bit. What differs on the air: the signal is 75 dB weaker (peaks of
  -93 to -97 dBm), senders pre-emphasise, and openings vary.
- **A real packet that began cleanly was thrown away.** Its first FIFO word was `012b`, the end
  of a flag and a frame's first byte: the chip had taken the opening's last four flags as its
  sync word, so no flag was left in the FIFO for `hdlc()` to open the frame with, the line
  looked idle and the read was given up. Worse, `012b` again and again (the empty FIFO) reads as
  flags, so every later read ran its 128 words: the receiver was deaf from then to the end.
- The end line had grown past the 60 characters of a say line and lost its `pre=`.

**What came of it (`aprsfskb`, FSK_STREAM).** The FIFO is read all the time, a word every
13.3 ms; a word that is not the last one again means the chip has found its sync word, and
`hdlc()` starts as it is after a flag (the bit after a sync word of flags is a 0, a change of
tone, which gives the tone before it). The modem is turned off and on when the line has been
idle for 40 bits, at RX-done, and every 2 s with nothing heard. Stale words are never decoded,
and the screen and the minute's line run whenever no packet is being read. `aprsfsk` itself is
left as it ran on the bench until `aprsfskb` has had its own run; both say `aprs fsk ms= key=
pre= fake=` before the end line. `aprsfskd_440` is `aprsfskb_440` with the receiver's
de-emphasis off (REG_2B + 0100).

**A rehearsal of 144.390 for the bench** (`python3 -m modem.aprs wav OUT.wav --now --kinds
--repeat 7`): packets of eight kinds in turn, each from the station number of its kind and
stamped with the UTC at its first bit.

| Station | Kind |
| --- | --- |
| -1 | flat: the bench signal as it was |
| -2 | pre: a sender's pre-emphasis (6 dB an octave, 5 dB between the tones) |
| -3 | de: a flat sender through a receiver's de-emphasis |
| -4 | low: a third of the deviation |
| -5 | n12: noise as an FM receiver gives it on a weak signal, 12 dB under the packet |
| -6 | n6: the same, 6 dB under |
| -7 | short: eight opening flags |
| -8 | pre n9: pre-emphasis and noise 9 dB under |

The float decoder (`modem.aprs decode`) takes all of them but most of n6.

**Third bench run (2026-11-10 10:27 to 10:35Z, K6 image 6f30645, the K6 silent).** The rehearsal
signal, seven rounds, on 440.850 MHz at -21 dBm. Decoded packets of 7:

| Build | flat | pre | de | low | n12 | n6 | short | pre n9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `aprsfsk_440` (as in the second run) | 7 | **0** | 7 | 7 | 0 | 0 | 6 | 0 |
| `aprsfskb_440`, first version (4e20c67) | 7 | **0** | 7 | 1 | 0 | 0 | 7 | 0 |
| `aprsfskb_440` (5602977) | 7 | **0** | 6 | 1 | 0 | 0 | 6 | 0 |

(The last run's signal began 46 s into the module and its last 3 s fell after the module's
end.)

- **The modem reads no pre-emphasised packet.** With the space tone 5 dB over the mark there is
  either no capture at all or ten words that are no flags (`0040 4919 0000 8190 1c1c ...`). Flat
  and de-emphasised packets (the mark level with the space, or 5 dB over it) read every time.
  REG_2B reads C000 on this K6 and the pager clears bit 14, so by the BK4819 notes the
  receiver's own de-emphasis is on (bit 8 = 0). Senders on 144.390 pre-emphasise (the bench's
  recording of 2026-11-08 decoded only after de-emphasis), so this alone explains the two
  listens with no frame.
- **Noise 12 dB under the packet is too much for it**: the flags come with their one-bit tone
  missing or slipped (`0800 0808 0800 0808 0008 0801`), as in the captures from 144.390. The
  float decoder reads these packets 7 of 7.
- **One turning off and on does not end a packet the chip is in either.** The stream build did
  it once after each packet; the next packet (low, 0.6 s after de) then arrived with all of its
  40 flags at an odd bit position (`0040 4040 ...`: no sync word was taken from it) and was cut
  short by RX-done 1.707 s after de's sync word: the chip had stayed in de's packet. `aprsfsk`,
  which starts its search again every 59 ms, read the same packet behind a fresh sync word
  (`0101` x18, four flags fewer). `aprsfskc_440` is the stream build doing that on purpose.
- The stream build's screen and minute line run (`m 1 rssi=-131 pk=-21 pre=15 ok=9`).

Next: `aprsfsks_440` goes round eight settings of the modem against the same signal (REG_58's
bits 7:6 at 00, 01 and 11, the two other bandwidths, less RX gain, the de-emphasis off), a
setting a round, to find one that reads the pre-emphasised kind.

The emulated test (`test_aprsfsk_on_a_model_of_the_chips_modem`) runs both builds against the
model of packet mode: three stations (one with its tones swapped) between random bits, every
packet decoded, the clock set within its bound, the modem off and on for each packet, the
registers put back. The model can raise the sync flag at every arming as the chip does: then
`aprsfsk` goes round its stale reads as on the bench (no screen), and `aprsfskb` takes its
third packet with an opening of four flags only. The model ends a packet when it is armed,
which the real chip does not: the first bench run's fault could not show there.

## Size: which images

5,046 bytes of code (clang -Oz) and about 2.3 KB of state (the DMA ring 1 KB, a frame 332 B, the
correlators 256 B, twelve stations 336 B), 288 B of stack. With the matched filter (2026-11-10):
about 7.4 KB of code (-Os) and 2.9 KB of state (two 92-entry templates 368 B, the sums 256 B). That needs the shared arena of the SPI
images (`aura-pager-only`, `-lr`, `-vlr`, `-vlrh`: 11,776 B since PR #146). It does **not** fit
the EXPLORE image or the lean images (5,376 B block): the radio needs a voice image, and the
module can move into the SPI module library unchanged once that exists.

## Tested (emulator and host, `tests/test_k6_aprsclk.py`)

Three stations through the study's model of the K6's receive path (sim/k6if: FM, the FD6818's
filter and AGC, the GP0 sigma-delta at 5.49 MS/s): a tracker's `HHMMSSh` stamped 0.4 s before
it keys, a weather station's `DDHHMMz` through a digipeater, and a `$GPRMC` 0.7 s old.

- **Emulated M0** (`tests/k6_live_emu.py`, CPI 1.5 calibrated on gp0dma): all three decoded, no
  bad frames, no counts lost, clock 0.19 s behind the truth (the stamps' own lag), v=3, screen
  and LED as above. It still decodes everything at CPI 4 and 5 (the CPU cost x 3.3), fails at 6:
  the receiver takes roughly a quarter to a third of the CPU.
- **Host build of the same C** (`c/k6/aprsclk_host.c`), 3 seeds each: clean with the space tone
  +6 dB over the mark (a sender's pre-emphasis) or -4 dB, deviation 2 to 4.5 kHz, carrier ±800 Hz
  off. At ±1500 Hz with 3 kHz deviation it loses some frames (peaks past 4.5 kHz); on the radio
  the AFC pulls the carrier in. Noise: 17 of 18 frames at C/N0 52 dB-Hz, 4 at 50, none at 48, about
  where a float discriminator on the same model breaks (13, 9, 0): it's the FM threshold. 52 dB-Hz is
  roughly -116 dBm at the antenna, so local digipeaters and igates, not distant mobiles.
- A peak tracker per tone (to undo pre-emphasis twist) made it worse (lost frames at +6 dB): left
  out. Parser cases: one station isn't enough; a digipeated copy is one vote; three agreeing GPS
  stations outvote one 7 minutes slow; local-time, impossible, no-fix, untimed and third-party
  packets give nothing; objects and status reports count; stamps either side of midnight agree.

## Making it the radio's clock

A module's clock dies with the module: `ms()` counts from its own start. Module API level 8
(Jeff's OK 2026-11-09 04:49Z) gives the radio one UTC clock that lives in the core from one
module run to the next, until a reboot, fed by whichever module hears the time first (AIS
first, APRS if no AIS: Jeff 03:43Z):

    int clock_set(uint32_t utc_ms, uint32_t date, uint32_t w_ms);   /* 1 taken, 0 kept, -1 refused */
    int clock_get(uint32_t *utc_ms, uint32_t *date, uint32_t *w_ms); /* 0, or -1: no clock */

- `utc_ms`: UTC milliseconds of day at the moment of the call (0 to 86,399,999).
- `date`: `day | month << 8 | year << 16`, the year in full (2026), 0 for any part not known
  (`KM_DATE(d, m, y)` in `k6_mod.h`). APRS gives a day from `DDHHMMz`, the whole date from
  `$GPRMC`; AIS message 4 gives the whole date.
- `w_ms`: a bound. UTC is within `w_ms` of `utc_ms`. aisclk's slot fixes are about 2 ms, a
  coarse AIS fix 1,000; aprsclk passes the far end of its Marzullo interval from its estimate
  (about 1.5 s with second stamps heard direct, about 30 s with minute stamps only).

The core runs the fix on from uptime that never wraps (`hal::txdog::ticks`: TIM14's count and
its wrap interrupt, in every image that has the `txdog` table) and widens the bound by 1 ms
every 4 s (250 ppm). The MCU runs on its own RC oscillator, not the radio's TCXO: AIS run 3
measured it 134 ppm fast, wandering ±100, so the calibration record's -53 ppm (the RF crystal)
doesn't apply and the bound is set wide enough for both. A new fix replaces the clock only if
its bound is no wider than the clock's bound at that moment, so a 2 ms AIS fix outranks APRS for
the next 6 hours or so (until 2 ms + 250 ppm passes APRS's 1.5 s), and date parts a fix lacks come
from the fix before. Past midnight the date steps on (month lengths, leap years by 4). After
59.6 hours without a fix the clock is dropped, and `clock_get` says -1. The lean images
(codebook install, table images) have no clock: both calls return -1.

`aprsclk` calls `clock_set` with each `clk` line that has two votes or more (on level 8 images;
on older images it runs as before). `clkget` (level 8) says the core's clock, `core clk
04:52:10.123 w=14 d=09/11/2026` or `core clk none`, and shows it on screen: run it minutes after
aisclk or aprsclk to see the clock was kept. `test_module_sets_the_radios_clock` and
`test_clkget_says_the_core_clock` check both against the emulator's model of the core
(`tests/k6_live_emu.py`).

Cost: about 570 B of flash and 20 B of RAM in each image with the clock (local build of the
pager-only and hiss images). CI had the hiss image (vlrh) at 1,068 B free before it.

**On the radio** (2026-11-09, image cf9516e vlrh). Right after the flash, `clkget` said `core clk
none`, as it should. The 70 cm `aprsclk_440` loopback decoded nothing that time (rssi -130, the
IC-7100's stub at power 0 into the K6's big 2 m antenna), so the first fix came from AIS: aisclk
(714a454) handed the core 25 fine fixes between 05:56:21 and 06:02:02Z. Then clkget read:

| clkget line | hamlet's NTP stamp on the line | bound |
|---|---|---|
| 06:11:55.074, d=09/11/2026 | 06:11:55.043 (31 ms) | w=150 |
| 06:17:32.238 | 06:17:32.387 (stamp late by up to one serial read) | w=234 |

Both are inside their bounds. The bound grew 84 ms in 337 s, the 250 ppm it is set to, and the
clock carried across module runs ten and fifteen minutes after the last fix. Log:
`tests/data/k6/air/clkget-20261109T0543Z.log.gz` (on the AIS thread's branch).

Uses: the VOICE MSG screen shows UTC (`hh:mm:ssZ` on its fourth line, in place of the build)
once a fix is in, and the alarm module (`mod-alarm.bin`, docs/k6-midi.md) plays a tune at a set
time. Next: the WSPR and FT8 modules can start on their slots from `clock_get` instead of the
bench's CW ID.

## Bench

Receive only on 144.390: upload `mod-aprsclk.bin` to a voice image (any SPI image with the
11,776 B arena), leave the K6 on 144.390 with traffic, and compare the `clk` lines (or the
screen) with NTP on hamlet.

**Run 1** (2026-11-08 21:45-22:00Z, image 006ee1b, 15 min on 144.390): `ok=0 bad=589 ts=0 v=0
lost=1`, stack free 2,276 B, the report went out on 440.850 as planned. No frame passed its CRC.
The 589 bad frames are flag-to-flag runs, which noise makes too, so they do not show whether any
APRS reached the radio: the IF tap had never been tried at 144 MHz, and nobody had checked that
144.390 is busy at hamlet. The module now prints a line every minute, `m <min> rssi=-<dBm> pre=
ok= bad=` (since 2026-11-10 `m <min> rssi=-<floor> pk=-<peak> pre= ok=`: the least and the most
of a look at REG_67 each second), where `pre` counts preambles (three flags in a row inside a run), which noise rarely
makes, so a quiet channel and a deaf demodulator look different.

**Loopback on 70 cm** (`mod-aprsclk_440.bin`, `c/k6/mod/aprsclk_440.c`: the same module on
440.850 for 5 minutes). The IC-7100 sends three AG7EW stations whose stamps are hamlet's UTC at
each frame's first bit:

    python3 -m modem.aprs wav ~/blowfang-work/runs/aprs-loop/loop.wav --now
    sh tools/k6_send.sh ~/blowfang-work/runs/aprs-loop/loop.wav

The first prints `start` (the time the stamps assume the audio starts) and each frame's end
offset; the second prints `t0`, and the audio starts about 0.2 s after it (PTT settling). The
upload is the usual `python3 tools/k6_upload.py mod-aprsclk_440.bin --api-level 2 --telemetry 12
--run-s 360 --key-file ~/.config/sotto/k6-mod.key --payload XL`, and a CW ID sent just before
keeps the send from putting one in. With exact stamps the interval's low edge is the truth, and
the estimate adds the half second a real station's stamp leads its keying, so the `clk` line
after AG7EW-3 should read (start + its end offset) + 0.5 s. The stamps are early by
D = t0 + 0.2 s minus start, so against hamlet's clock it reads 0.5 s minus D ahead
(`test_bench_loopback_packets` checks 0.45 to 0.55 s through the modelled radio).

**Run 2, the loopback** (2026-11-08 22:49-22:54Z, image 006ee1b, `mod-aprsclk_440.bin` from CI
run 42530008074, 5,480 B as CI's clang builds it): three bursts 100 s apart, every packet
decoded: `ok=9 bad=154 ts=9 v=3 lost=0 pre=9`, ret 3, stack free 1,828 B, RSSI -112 to -115 dBm
(the IC-7100 at RF power 0, a few metres away). Each `clk` line against the frame's true end on
air (audio start = t0 + 0.2 s): -135 to -251 ms, mean about -0.2 s. The bursts' stamps were early
by D = 0.67-0.73 s, so the prediction was 0.5 s - D = -0.17 to -0.23 s: the receive chain, the
parser and the vote work on the radio as modelled, within a few tens of ms. The `bad` count is
noise between frames (about 40 a minute on a quiet channel), so `pre` is the line to read. The
serial logger sees each line about 3.3 s after its frame, which is why a `clk` line looks 3.4 s
behind the logger's own stamp.

**Run 3** (2026-11-08 23:03-23:18Z, image 61cc53a, `mod-aprsclk.bin` 5,480 B, 15 min on
144.390): `ok=0 bad=592 ts=0 v=0 lost=0 pre=0`, `pre=0` in every minute line, so not one packet
preamble. The minute RSSI sat at -85 to -87 dBm, flat for 15 minutes, against -115 dBm on an idle
440.850 in run 2. Either a steady carrier or a K6 birdie (its own synth or an MCU harmonic) sits
on 144.390 (a carrier would also quiet the discriminator, which fits `pre=0`), or the chip's RSSI
reads about 29 dB higher on 2 m. Since the loopback decodes, the open question is the 2 m channel.

**Run 4** (2026-11-08 23:31-23:47Z, image f70e79e, 15 min on 144.390, with the IC-7100
beside it receive only, Jeff's OK): the K6 decoded Jeff's car tracker at 23:39:13Z, `f AG7EW-7
n=62` (Mic-E, so no stamp: `ok=1 ts=0 v=0 pre=1`). The IC-7100 saw the same packet at S9+45 dB
and nothing else in 12 minutes. Its S-meter sat near S7 on 144.390 with the K6 on 2 m and only
3 dB lower with the K6 back on 440.850, and its audio was flat open-squelch noise, so run 3's
-86 dBm is the band's real noise floor at the bench, not a K6 birdie, and 144.390 is simply quiet
there. The recording decoded only through a 1-pole de-emphasis (the mobile's pre-emphasis off
the IC-7100's discriminator), so `modem.aprs decode` now tries flat, 0.95 and 0.9. The K6 decoded
it without one. To test the clock off-bench, a tracker has to send a stamped format: a position
with HHMMSSh or DDHHMMz, or raw $GPRMC.

**Run 5** (2026-11-09 00:09-00:24Z, image 0fb4d6c, 15 min on 144.390): Jeff forced beacons and
sent a message from his car. `ok=7 bad=670 ts=0 v=0 lost=0 pre=9`: three 62-byte Mic-E
positions and four 49-byte frames (the message and its retries), all AG7EW-7. Two preambles did
not end in a good frame. None carried a time, so there is no `clk`. The minute RSSI (-90 to -92
dBm) is the minute's floor, so packets do not show in it. The receiver is proven on 2 m. What is
left is a station that sends a stamped format.

**Who sends stamps near the bench** (APRS-IS feed, 40 km around 48.95 N 122.48 W, 00:36-00:57Z,
2026-11-09): 13 stations heard on RF (digis, igates, trackers, Jeff's car), none with a usable
stamp (one repeater object carries a fixed `111111z`). The stamped weather stations nearby (CWOP)
reach APRS-IS over the internet only. Jeff's FTM-300D sends Mic-E positions only and has no
timestamp option.

**Run 6, big antenna and a stamped burst on 2 m** (2026-11-09 01:09-01:41Z, image af064d6, the
K6 on Jeff's big antenna, two 15 min listens; the IC-7100 on a stub at RF power 0). The minute
RSSI fell to -97 to -101 dBm (from -86 on the K6's own antenna: local pickup, not a carrier).
With Jeff's OK the IC-7100 sent one 4 s burst on 144.390 at 01:17:19Z, no digi path (the
loopback's three AG7EW packets). The K6 decoded all three, voted 3 stations, took the date
(9/11), and read about +0.12 s against each frame's true end, as predicted (stamps 0.37 s early
against the real audio start). Seven minutes later its end line was within 0.14 s of the logger.
Listen 1 also decoded Jeff's car again. Listen 2 heard nothing in 15 min (`pre=0`). So the clock
works end to end on 2 m. At Jeff's location it needs a stamped source of its own, such as a
fixed station beaconing like the burst, since no local station sends one.

**How common stamps are** (worldwide APRS-IS feed, 15 min, 2026-11-09 01:50-02:05Z, packets an
igate heard on RF only): 17,755 RF packets (about 1,180 a minute); 8% carry a usable stamp
(within 10 min of arrival), 95 a minute from 588 stations. About as many again carry a wrong
one (repeater objects at `111111z`, trackers at `000000h`, digipeater clocks hours off, stale
objects), which is why the vote needs two stations. Usable by kind (packets / stations):
DDHHMMz positions from digis and WX3in1 boxes 515 / 295, HHMMSSh trackers 334 / 99, weather
297 / 162, objects 232 / 53, status 39 / 29, raw $GPRMC 5 / 1. Seconds stamps arrive 2-3 s
after their stamp, mostly internet delay; minute stamps about 50 s. Europe and North America
are alike (about 40 usable a minute each); within 300 km of the bench only 1.2 a minute from
12 stations, mostly minute stamps, two with seconds (41 and 98 km). Positionless weather
(`_MMDDHHMM`, 0.6% of RF packets) carries a date and time the module does not read yet.

This tests the IF tap, demodulator, HDLC, parser and clock on the real radio; if it passes and
144.390 still decodes nothing, the next step is an IC-7100 recording of 144.390 (receive only)
run through `python -m modem.aprs decode REC.wav` to count local stamped packets.
