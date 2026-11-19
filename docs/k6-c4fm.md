# C4FM (YSF) and DMR-shaped 4FSK from the K6

Jeff, 2026-11-18: "Any chance we can get the K6 FSK modem to speak either C4FM or DMR?"

Both are 4800-baud 4FSK. A dibit picks one of four deviations: 01 is +3, 00 is +1, 10 is -1, 11 is -3.
YSF's outer level is ±2.7 kHz and DMR's is ±1.944 kHz, with inner levels a third of that. The UV-380 already sends both
from its HR_C6000 (docs/uv380-ysf.md, docs/uv380-m17.md, docs/uv380-c6000-modem.md). This page covers the K6,
whose FD6818 has no 4FSK modem.

## Receive: the easier half

The K6 already demodulates 4800-baud 4FSK on the MCU. The GP0 IF tap feeds DMA into a discriminator
(docs/k6-if-modem.md), and `dmrrx` (c/k6/mod/dmrrx.c, docs/k6-dmr.md) uses it to read DMR CACH, slot type and
LC from real repeater bursts. A YSF receiver would use the same front end with the YSF sync
(D4 71 C9 63 4D) and the FICH decoder (Golay, K=5 code, CRC) in place of the DMR fields. It fits a module in
the 11,776-byte arena. The chip's own FSK receiver at 4800 baud only gives each symbol's sign,
and it slips (docs/k6-dmr.md, runs J-L), so it isn't the path.

## Transmit: the open question

- **Crystal (REG_3B) and frequency word:** ruled out on 2026-11-06. Both only take effect at the next RX/TX
  turn-on (docs/k6-own-fsk.md).
- **The chip's FSK modem at 4800 baud** (REG_72 = 0xC195, direct FSK, REG_58 = 0x00E1): each FIFO bit is one
  symbol's sign, so the chip alone sends ±3. That is enough for a DMR sync, which uses only ±3, but not for a
  YSF sync or any payload.
- **The size from the MCU:** REG_70's tone-2 gain (bits 6:0, bit 7 on) sets the FSK deviation. If the chip
  applies a write mid-packet, the MCU can make each symbol outer or inner by rewriting the gain on the
  symbol edges. Those writes are timed on TIM17, as rangetx times its tone changes. One write through
  `api->fd_write` takes about 95 µs (9,000-9,100 cycles at 96 MHz, measured by rangetx) against a 208 µs
  symbol, so even a change on every symbol keeps up. The module refuses to send if a write takes more than
  3/4 of a symbol (-8).
- **The tone oscillator** as a DC source (REG_71 frozen at a phase): the fallback. It needs two writes a symbol
  and a DC-coupled tone path, and neither is known.
- **Hardware** (the TCXO's modulation input, injecting into the mic line): out under the no-solder rule.

## The probe (c/k6/mod/c4probe.c)

This is a level-3 RAM module, so no reflash is needed. The FSK registers it uses (0x58-0x5F, 0x70, 0x72, 0x40,
0x2B, 0x3F) are not on the refused list, and GROUNDWAVE (gw.c) already sends chip FSK from a module. It
sends one key-up on 440.850 MHz with the PA off (the chip's leak only), about 6 s of packets, then AG7EW in
MCW. Every packet is 480 symbols at 4800 baud after the chip's 16 preamble bytes and 4-byte sync word (160
alternating outer symbols), with 20 ms of carrier between packets:

| packet | what it answers |
|---|---|
| P0 | PN9 signs at gain 96: the deviation and eye at 4800 baud |
| P1 | REG_70 gain stepped 96/72/48/32/16 every 96 symbols: whether the write takes effect mid-packet, whether the gain is linear, and where the change lands against the module's aim (the chip's latency) |
| P2 | REG_40's deviation level at the same shares (restored after) |
| P3 | 240 zeros then 240 ones: droop from a TX high-pass |
| P4 | P3 with REG_2B bits 2:0 set (the BK4819 list's TX filter bypasses; restored after) |
| P5 | the DMR BS data sync every 144 symbols, outer only |
| Y00-Y31 | YSF frames in turn (header, data FN 0, FN 1 "AG7EW K6 C4FM", terminator): signs in the FIFO, sizes by REG_70 writes. Packet i's writes are shifted (i - 8)/4 symbols from the aim, so the sweep covers -2 to +5.75 symbols of unknown chip latency |

A packet's first payload symbol is aimed at the TX_EN write's end plus 160 symbols. A YSF frame is exactly
480 symbols, so each Y packet carries one whole frame, sync first, after the chip's lead.

`python3 -m modem.k6c4fm analyze CAP.wav` reads a Pluto discriminator capture (`pluto_bench.py rx OUT.wav`).
It finds the packets by their lead, samples each with a symbol-wide boxcar filter, and aligns it by its
known signs, in either polarity. For each packet it reports deviation, gain shares, where each step landed,
droop, DMR sync hits, and, for each Y packet, symbol errors against the frame sent and the YSF decode
(FICH, CSD, DCH). `sim` runs the same analysis on a model of the air. tests/test_k6_c4fm.py runs the module
on the emulated M0 against a model of the FSK transmitter, turns its writes back into air, and decodes the
offset-0 frame with 0 symbol errors.

**Reading the result.** If P1's levels step, the gain is live, and the best Y packet gives the timing for a
real sender: a continuous YSF transmission with FIFO refills in symbols that need no size write. If P1
reads LATCHED, the gain only takes effect per packet. Then the tone oscillator is the next try, and DMR
syncs (P5) are the most this path gives.

## Run 1 (2026-11-18 01:32Z, job c4fm-1118a): the K6 sends C4FM

The module ran on the radio (main 8a78fb5, keyed). It returned 38 packets with 7,856 size writes, none
started late, each write 11,223 cycles (117 µs). The Pluto at 440.849 MHz heard the K6's carrier
+1.55 kHz off its centre, about 180 Hz rms in the gaps (good SNR with the PA off). Files:
/mnt/project-files/k6-c4fm/run1/.

- **The gain is live mid-packet.** P1's levels were 3425, 2437, 1434, 973 and 731 Hz at gains 96, 72, 48, 32
  and 16, close to linear with a small offset. REG_40 (P2) moves the level too, but only to 0.70 at a sixth.
- **4800-baud FSK:** P0 outer about 3.5 kHz at gain 96 (YSF wants 2.7 kHz: gain about 74), 0 sign errors.
  No droop: P3 and P4 both kept 100 %, so the REG_2B bits don't matter.
- **DMR:** P5's three BS data syncs were found at exactly 54, 198 and 342.
- **YSF:** 8 of 32 frames decoded with a good FICH, every frame type among them. That covers the header and
  terminator (dest ALL, src AG7EW), data FN 0 (callsigns) and FN 1 ("00 AG7EW K6 C4FM"). Y13-Y17
  (+1.25 to +2.25 symbols) had 2-13 of 480 symbols wrong. The best was Y15, writes +1.75 symbols late:
  the chip's FSK path runs about 365 µs behind the register write. The leftover errors look like the
  wav's clipping: full scale is 5 kHz, and +1.55 kHz of offset plus 3.5 kHz of deviation passes it.

Next: centre the capture on the K6 (440.8505), set gain about 74 and inner about 25, and make the
offset +7/4 the default. Then a continuous YSF transmission (header, comms frames, terminator back to
back), with FIFO refills in symbols that need no size write, so a real C4FM radio can decode it.

## Run 2 (c/k6/mod/c4ysf.c): whole transmissions

This run uses run 1's numbers: gain 78 for ±2.7 kHz, inner 27 for ±0.9 kHz, and size writes +7/4
symbols late. It sends four single-frame packets (A0-A3). Then it sends the whole transmission back
to back in one 240-byte packet (B1-B3): header, data FN 0, FN 1, terminator. B1 preloads all 120 FIFO
words. B2 and B3 preload 64 and 32 words, then write the rest as the chip takes them. Each refill goes
in a symbol whose edge, and the next one's, needs no size write. A 117 µs write then ends before the
next size write must start. These packets answer the FIFO's size, and whether timed refills keep a
long packet whole. A long packet is what a C4FM radio needs, with frames every 100 ms and no gaps.
`analyze --plan ysf` also runs the plain YSF receiver (modem.ysf.demod: RRC, sync search, 100 ms runs)
over the capture. In the emulator, with the chip lagging 1.75 symbols, every frame decodes and the plain
receiver finds three 4-frame runs.

Run 2 on the bench (2026-11-18 01:45Z, job c4fm-1118b, files /mnt/project-files/k6-c4fm/run2/). The
plain YSF receiver found 9 K6 frames in 3 continuous runs, every one with a good FICH. Levels were
±2.7 kHz outer and ±1.0 kHz inner. The FIFO holds all 120 words, and timed refills work. Symbol errors
grow along each long packet: the size pattern slides about 0.25 symbol later per 480-symbol frame. The
Pluto puts the chip's baud at 4800.0, so the MCU's TIM17 clock is about 520 ppm slow against the chip.
Each packet also starts at a different phase, about 0.9 symbol of jitter, because the chip starts on
its own bit clock. Both need the MCU locked to the chip's symbol clock, not open-loop timing.

## Run 3 (c/k6/mod/c4lock.c): the MCU locked to the chip's symbol clock

Run 2's captures hold one more fact. Measured against a single 4800.0-baud grid from the first
packet, every packet of a key-up starts within 0.08 symbol of a grid tick: the chip's bit clock
runs on unbroken from packet to packet. The 0.9 symbol start jitter is the enable landing at a
random point in the chip's symbol, and the drift is the MCU's clock. So the MCU can learn the
grid once and time everything on it.

- **The grid in MCU cycles.** After every packet the module polls the TX-done flag (REG_0C bit 0)
  and takes the rise as the midpoint between the last read that saw it low and the first that saw
  it high. The rises fall on the chip's grid. A least-squares line through the last 16 gives the
  chip's symbol in MCU cycles (Q12) and its phase. The rate is fitted only once the points span
  2,048 symbols. Until then the module uses the prior C4_PPM (-520, from run 2) and fits the
  phase only.
- **Where a packet starts.** C00 finds the grid. C01..C16 (64 PN9 symbols each) put the enable
  at each eighth of a symbol in turn, twice. The enable-to-done time, plus the enable's phase, is
  flat except for one step of a whole symbol where the enable crosses the chip's threshold. Every
  data packet puts its enable half a symbol away from that step, so it starts on a known tick.
- **The size writes** go on that tick's grid plus a lag. The lag is C4_LAG4 quarter symbols
  (8: run 1's +7, plus run 2's drift at mid-frame), less the mean enable-to-done time over the
  eight phases. That mean is what runs 1 and 2 saw on average with random enables.
- **The packets.** A00..A12 are single frames with the size writes at -6..+6 quarter symbols from
  that lag. B0..B4 are the whole transmission (four frames in one 242-byte packet) at -4, -2, 0,
  +2 and +4. Every data packet ends with 16 outer symbols, so the done poll starts after the last
  size write.

Nothing is said while keyed. After the key-down the module reports the calibrated rate, the
enable phase, each phase's enable-to-done time (one step of about 100 % shows the threshold),
misses (a packet that ended off its predicted tick), lost polls, and the final fit.

In the emulator (tests/test_k6_c4fm.py, FskGrid) the chip runs on its own grid at -380, -520 or
-650 ppm against the MCU, at any phase and enable threshold. The module fits the rate to within
3 ppm, every data packet ends on its predicted tick, the size writes sit within 0.005 symbol of
each other against the chip's symbols across a 2,100-symbol packet, and the frames decode with 0
symbol errors. `analyze --plan lock` adds where each packet starts against a 4800.0 grid, and
where the size changes land (fraction of a symbol, frame by frame).

Run 3 on the bench (2026-11-18 02:21Z, job c4fm-1118c, files /mnt/project-files/k6-c4fm/run3/).
The module returned 35 packets, none late. It fitted the chip at -617 ppm against the MCU after
the calibration and -647 ppm at the end (run 2's -520 was a rough figure). The worst fit residual
was 0.42 symbol, so a TX-done poll on the radio is coarser than in the emulator, and three data
packets' polls rounded to the wrong tick. On the Pluto every packet started within 0.2 symbol of
one 4800.0-baud grid. So the enables now follow the chip: run 2 spread over 0.9 symbol.

- **Whole transmissions:** all of B1-B4 decoded 4 of 4 frames with a good FICH. B3 (+2/4) had
  35 of 1,936 symbols wrong and B2 (0) 48, against hundreds along run 2's long packets. Levels
  were ±2.7 / ±1.0 kHz. The plain YSF receiver found 15 frames in 4 runs, all with a good FICH.
- **Drift:** mostly gone. A small slope is left: errors grow along B1 (early) and shrink along B4
  (late), so the fitted symbol is a little short. The three mis-rounded polls likely pulled the
  fit.
- **Timing:** best between 0 and +2/4, so C4_LAG4 9. The single frames swept from A05 to A07
  (57-167 errors), a narrower window than the long packets show. Each A packet carries a
  different frame, so they don't compare one to one.

Next (run 4): fit data packets against the tick they were placed on, not a rounded one, and drop
polls more than 3/4 symbol off. Bin the calibration phases by where the final fit puts them, set
the prior to -630 ppm, and set C4_LAG4 to 9. Then a C4FM radio test (FTM-300DR) with B packets
only.

Run 4 on the bench (2026-11-18 02:45Z, job c4fm-1118e, files /mnt/project-files/k6-c4fm/run4/).
The first try (c4fm-1118d) never reached the module: EXPLORE was in a 2-minute listen and missed
the wake. The job now wakes it, waits for the listen to end, and wakes it again.

- **The fit:** no misses, no lost polls, and the worst residual was 5 % of a symbol (run 3: 42 %).
  A poll takes 11,171 cycles (116 µs). The calibration's step was clean: 0 49 99 99 99 99 99 99.
  The rate read -604 ppm after the calibration and -524 ppm at the end.
- **Whole transmissions:** all five B packets decoded 4 of 4 frames with a good FICH. B2 (lag 9/4)
  had 14 of 1,936 symbols wrong (3, 2, 5 and 4 a frame, so no drift is left), and B3 (10/4) had
  22. A quarter symbol either side costs 61, and half a symbol costs 336-372. The plain YSF
  receiver found 20 frames in 5 runs, all with a good FICH.
- **Single frames:** A05 and A06 (-1/4 and 0) had 20 and 17 of 480 wrong.
- On the Pluto every packet started within 0.2 symbol of one 4800.0-baud grid. That includes the
  slow slide of the Pluto's clock against the chip.

The analysis needed three fixes for this capture. A packet it can't place no longer swallows the
rest. The burst finder now accepts the chip's ragged first 12 ms of preamble and noise spikes in
the gaps. And it now uses each packet's sampling phase for its start time.

## The clock moved (2026-11-18, after the K6 was re-plugged at 04:24Z)

From 08:00Z every run drifted: size writes slid 0.1-0.5 symbol a frame, differently in each call,
and the FTM-300DR showed no callsign from the K6. Run 4's own binary, run again (c4fm-ab1), fitted
-851 ppm with 38 % residuals and then failed its calibration. The refcal module was ruled out (it
puts REG_3B/73 back, and the crystal held at +0.05 ppm).

**The probe (c/k6/mod/c4fifo.c, 10:05Z).** The FSK FIFO's almost-empty flag gives chip-timed
marks in the middle of a packet. REG_3F bit 14 enables it. It shows in REG_02 bit 14 (write 0 to
REG_02, then read it). Its threshold is REG_5E<9:3> in words; <2:0> is the receive almost-full.
The flag comes up as a word leaves the FIFO with the level at the threshold or below, and stays up
until a refill. Refilling a word each time brings it back every 16 chip symbols. A poll takes
117 µs (0.56 symbol), so each mark is only bracketed to that. The polls start at a dithered time,
because a fixed start brackets every mark at the same phase and biases the line. Inside a packet
the MCU's clock read **-1,500 ppm** against the chip (run 4 read -520). That is too far for the
fit to pull in from its -630 ppm prior, so it settled on a wrong line. Later runs read -1,240,
-970 and -1,150 ppm, and the rate stepped 100-150 ppm between packets a second apart. The MCU
runs on an RC oscillator, and it wanders.

**The fix, in three runs:**

- **lock5:** two rate packets (R0, R1) come before the calibration. Each is 128 words, with 108
  marks. Their line gives the rate to about 25 ppm (emulated at 117 µs reads), and the lock
  starts there. The single frames were clean again (A06: 0 wrong), but whole calls still slid up
  to 0.9 symbol.
- **lock6:** a rate packet now comes right before each B packet and sets its rate. Nothing slid
  inside a call any more. B0 and B3 landed exactly, but B2 and B4 started a whole symbol off the
  calibration's prediction. Predicting the start from the rate packets' own starts (lock7) did
  worse.
- **lock8:** each B packet now times itself. It starts with 7 words of alternation (more
  preamble; 128 words in all, REG_5D's most), and the FIFO is kept at the threshold through them.
  Six flags place the payload's first symbol. The done grid then follows from dd, the rate
  packets' done time after their payload on the same lattice; the fit's phase was 0.4 symbol off
  when emulated. Result: **B0-B4 all 4 of 4 frames with a good FICH**, and B2 (lag 0) had 5 of
  2,048 symbols wrong. The plain YSF receiver found 20 frames in 5 runs.

**c4sotto (sotto3, 11:44Z):** the Pluto decoded D0, D1, D2 and D4 with 5-14 symbols wrong each.
D3's first two frames were damaged. The UV-380's FM tap got 18 frames, all with a good FICH, and
200 of the clip's 240 bytes. The earlier try (sotto2, before the fix) got 5 of 12 blocks.

## Bench

The inbox job runs four steps. It wakes the K6, sends `k6.upload` of the built `c4probe.bin` over the Pluto
(level 3), and sends `trigger.wav`: about 4 s of PSK31 "AG7EW TEST", with the Pluto's own MCW ID first.
Then it starts a background `pluto rx run:c4.wav --hz 440849000 --seconds 60`. The Pluto is tuned 1 kHz
low because the K6 sits about -1.9 ppm and the Pluto about +1 ppm.

The Pluto can't capture while it sends, and the uploader holds it through its 27 s report wait. So
the module waits for the upload's carrier to go, then for a carrier of the job's own (the trigger, or
the uploader's closing ID), then for C4_PRE_MS (10 s) of quiet before it keys (trigger(); -7 if none
of that happens within 180 s). Then `python3 -m modem.k6c4fm analyze c4.wav`.

**Pre-armed go (c4ftm, `C4_KEYGO`; Jeff 11-18: a go should start the send within seconds).** A typed
go through the bench inbox takes up to a minute plus the job's wake and upload steps. So the demo job
uploads first and leaves the module armed. Once the upload's carrier is gone (C4_CLEAR_MS), it says
`c4 armed: a key sends` and shows ARMED / ANY KEY: SEND. A K6 key pressed and let go is the go: it
says `c4 go` and keys C4_GO_MS (5 s) later, with the channel clear for C4_CLEAR_MS. The job waits for
`K6 MOD say c4 go` and then starts its background Pluto capture, which needs about 2 s to start. A key
before the arming, or a second key during the count-down, stops it (-4). No key within C4_WAIT_MS
(40 min), or a channel still busy 30 s after the go, gives -7. On air 11-18: armed at 15:16Z. The
capture only starts while the K6 is on its cable, because the job reads the go line from the logger.

**DTMF go (`C4_DTMF`, Jeff 11-18: the K6 stays on the bench).** While armed, the module also runs
the FD6818's tone detector in DTMF mode. REG_09 is loaded with the 8 DTMF tones and their second
harmonics, as 128 cos(2 pi f / 8463). REG_24 is 0x8000 | 24 << 7 | 0x7E and REG_21 is 0x06D8. A digit
shows as REG_0B bit 12 with its code in bits 11-8. Three polls in a row of code C4_DTMF (1) are the
go: the module says `c4 go dtmf` and keys 5 s later, as for a key. Other digits and short blips do
nothing. The detector is turned off, and REG_21/REG_24 put back, before any key-up. On air 11-18 the
Pluto sent 697 + 1209 Hz FM for 2 x 0.6 s, and the K6 said `c4 go dtmf`.

**Long calls (`C4_CHAIN`, c4ftm).** The FTM-300DR blinks its blue bar for the 4-frame calls but
shows no callsign. It shows AG7EW for the UV-380's full calls: header, FN 0-7 and terminator (10
frames), with the radio-type and position block in DCH 6/7. One chip packet holds at most 128 FIFO
words, about 4 frames. So c4ftm sends a call as a run of packets with no gap between them. The first
packet is timed by its fill marks, as before. Each later one is written with a 24-symbol lead
(REG_59 0x0000) and enabled so that its first frame lands exactly 480 symbols after the last one.
Each frame ends in 52 symbols of AMBE silence, so a packet drops its last 3 FIFO words (48 symbols)
and the next one's lead overlaps them.

The chip's 4,800 Bd grid runs on through key-ups. A packet starts on the first tick at or after its
enable plus a fixed latency. Copying the first packet's enable phase was not enough: on air (chain2)
call 1 came out whole, but call 2's chained packets were each a symbol late. The module now keeps a
latency window. Every packet timed from its marks, and every chained packet's TX-done, narrows it, and
each chained packet is enabled half a symbol inside the window. The report line reads `c4lock chain
n= late= lat=lo/hi d=...`. If the window is empty, the call stops after its first packet (`late`).

**Telemetry writes over big modules.** With `--telemetry N` set, the K6 keeps the module's say
lines in the top 696 B of the 11,776 B module block, at offset 11,080 and up. A larger image has its
tail written over while it runs. That broke c4ftm's size table on chain1 and is the likely cause of
c4sotto's lost D3. `modem.k6 mod` now turns telemetry off, with a note, for an image over 11,080 B.
c4ftm is 11,751 B of code, 1 byte under the limit.

## The FTM-300DR shows AG7EW from the K6 (2026-11-18 20:32Z, job c4fm-ftm6)

c4ftm at main 1783e92, uploaded with telemetry off, PA off, 440.850. It armed at 20:30:06Z. Jeff sent
DTMF 1 from a radio beside the FTM at 20:32:34Z, and the K6 keyed at 20:32:39Z. Jeff: "Yes! Call sign
was received!" This was the first callsign the FTM-300DR showed from the K6. The 4-frame calls of
the morning had only blinked its blue bar.

The Pluto capture decoded 24 frames in 3 runs of [10, 10, 4]. Every FICH and DCH was good, both
long calls had FN 0-7 from AG7EW, and frame spacing was off by at most 0.2 symbol. Chain line:
`n=4 late=0 lat=-25/17 d=9999,8,9999,-47`. The MCU clock sat about -11,000 ppm from the chip, and
the rate packets followed it. The capture and K6 log are in the project files under
`k6-c4fm/ftm6/`.

## The whole sotto clip over C4FM (2026-11-18 20:52Z, job c4fm-sotto4)

c4sotto from main, unchanged (11,277 B), uploaded with `--telemetry 0`, to the Pluto only. The
Pluto's plain YSF receiver (`modem.ysf.demod`) decoded all 20 frames with a good FICH, and all 12
data blocks of the clip, every one matching `c4sotto.bit`: 240 of 240 bytes. That makes the
received sotto 400 audio identical to the sent clip. Earlier runs had lost D3 (sotto2: 5 of 12
blocks; sotto3: 200 of 240 bytes through the UV-380 tap). With telemetry off nothing writes over
the module's tail, which supports the telemetry-overwrite explanation. Symbol errors per data call:
D1 260, D2 241, D3 167 of 2048 (the outer FEC carries them); grid starts within 0.2 symbol. The
capture, K6 log and analysis are in the project files under `k6-c4fm/sotto4/`.

## DMR from the K6 (c4dmr, 2026-11-18)

`c/k6/mod/c4dmr.c` runs c4lock's chained calls with `C4_DMRS`. The table
(`k6c4fm.dmr_frames`) is the repeater-style DMR stream from `tools/uv380_dmrburst.py`: BS data
sync, CACH, slot 1 rate 1/2 data, slot 2 idle. It carries a dmrsotto test message (head, the
`AG7EW` text, 36 bytes counting up, end). That is 20 frames of 60 ms, cut into 12 table frames
of 480 symbols. A packet holds 3 table frames, which is 5 DMR frames (1440 symbols), so each
splice's 48 erased symbols fall at the end of a slot 2 idle burst. Each call is one 1.2 s run
of 4 packets. The module sends 5 calls, each with its own inner gain (`BIN`) and size timing
(`BQ`). The outer gain is 56, about +-1944 Hz.

Pluto runs, PA off, 440.850 (analysis: `tools/uv380_dmrburst.py decode`, and per call by
correlation against `k6c4fm.dmr_symbols()`):

| run | calls | result |
|---|---|---|
| dmr1 21:10Z | inner 19, plain and inverted | syncs, slot types, CSBKs; 5 of 7 data blocks; 11% of symbols wrong, the inner level at half the outer |
| dmr2 21:24Z | inner 8, 11, 14; q -1, +1 | inner 8 and 11 at q 0: the whole message (nothing lost, no bad block, end seen); 5.6% wrong at 8 |
| dmr3 21:33Z | inner 4-8 | below 8 the inner level stays near 1.25 of 3; inner 8 whole again; 5-16% wrong call to call |

Most wrong symbols are ones whose size changes at their start, and moving the size writes a
quarter symbol either way doesn't remove them. The last table frame of each packet is worse. A
half-symbol boxcar in place of the receiver's RRC halves the errors, so the K6's pulses are not
RRC-shaped and a real receiver's filter matters.

The UV-380's C6000 (`uv380.rx dmr`, c6dmrrx_long) did not sync on the K6 in dmr4 or dmr5, with
plain or inverted calls, although it heard them at -39 dBm. It did not sync on the Pluto's own
FM copy of the bench stream either (dmr-uvref2, 22:39Z: plain and inverted, -54 dBm). So today
the UV-380's DMR receiver is the open question, not the K6. With the UV-380 tuned to 440.850,
the Pluto's decode of the K6 got worse (dmr4), as in the 11-16 sweep.

## A voice call from the K6 (c4voice, 2026-11-18)

`c/k6/mod/c4voice.c` runs c4lock's chained calls with `C4_VOICE`. The table
(`k6c4fm.voice_frames`) is one DN call in the FTM-300DR's layout: header, FN 0-7, FN 0-3,
terminator, with the radio-type and GPS block (the made-up position) in FN 6 and 7, and 60
AMBE+2 frames (1.2 s) in the twelve comms frames. It goes out three times, each in four packets
back to back. The splices drop the 5th VCH of frames 4, 8 and 12, so 3 of the 60 voice frames
come out garbled.

There is no AMBE+2 encoder here, so the voice has to be a real radio's frames. The repo's table
holds AMBE silence. For a real voice, write a local header with
`python3 -m modem.k6c4fm header c/k6/mod/c4probe_frames.h --voice FRAMES.npy` (60 x 49 bits)
and never commit it. A built module still reaches hamlet through the public job branch. The
11-18 06:10Z FTM capture (ftmref) looks like background rather than speech: low gain, unvoiced,
repeating frames.

Pluto checks with random test frames, PA off, sizes timed at the lag (voice1-3, 11-18):

| run | inner gain (outer 78) | frames heard | AMBE bits wrong a frame |
| --- | --- | --- | --- |
| voice1 | 27 x3 | 10, 14, 3 | 5 (whole call); calls cut short |
| voice2 | 27, 22, 18 | 14, 12, 4 | 5, 2.3, 2.5 |
| voice3 | 22, 20, 18 | 14, 12, 14 | 4.4, 1.5, 2.8 |

At 27 the inner symbols came out at 0.45 of the outer (YSF wants a third), and lowering it
helped. In voice3 the errors bunch by packet, so a packet's size-write timing matters more than
the level. Every FICH and DCH decoded. In voice1 and voice2 the later calls ended early because
c4lock's latency window had emptied. Under `C4_VOICE` a sample that misses the window now
starts the window again (voice3: all 9 chained packets sent). The module now uses inner 20 for
all three calls.

`c/k6/mod/rxtrig.c` is a receive-only carrier trigger. It says `rxtrig on DBM` when a carrier
comes up and `rxtrig off MS DBM` when it goes. A job can then start a Pluto capture on another
radio's key-up, such as the FTM-300DR's own voice call.

## Receiving C4FM sotto (ysfrx, 2026-11-19)

Jeff, 2026-11-19: "is the K6 able to receive C4FM and decode/synthesize Sotto?" `c/k6/mod/ysfrx.c` is a RAM
module that receives the sotto VW frames the UV-380 and the K6 send (modem/ysf.py, docs/uv380-ysf.md),
puts the message back together and prints its sotto 1200 superframes. It doesn't play them yet, because
modules have no call for sotto playback (the design: an API level 11 `sotto_play`, with each receive mode a
SPI library module).

- **Front end.** dmrdet's GP0 IF grid (c/k6/dmrsync.h) at 2 points a symbol, with its 13-tap matched
  filter. `DS_NOSYNC` drops the DMR sync search, which saves 700 B and the CPU time it took. `DS_ERASE` makes
  the points in a pause 0 instead of stale ring values.
- **Frames** (c/k6/mod/ysfdec.inc).
  - Search for the 20-symbol sync only while there is a carrier. Before this gate, noise's false syncs held
    the receiver when the call began.
  - Take the scale and DC from the sync, then slice each symbol as its points come in.
  - Look for the next sync where it is due, ±3 quarter points, keeping it even when weak (the flywheel).
    Two missing syncs or a terminator end the call.
- **Decoders** (c/k6/ysfdec.h, integer and divide-free).
  - FICH: dibit deinterleave, K=5 Viterbi, Golay data halves, CRC-16.
  - Sotto VW: PN9, K=7 rate 7/18 tail-biting Viterbi with a 24-step wrap, CRC-8 per superframe.
  - Both are butterfly Viterbis with 4-bit soft values. They run a step at a time between DMA samples.
  - The K=7 decoder keeps only the last 64 steps' decisions (512 B, where the whole frame's took 2,432 B)
    and traces back every 32 steps, so 32 steps lead in. On the host model this costs 0 to 4 of 200
    superframes near the threshold (48 dB-Hz: 181 against 185), about 0.2 dB.
- **The message** (c/common/sotto_rx.c, the UV-380's, built with `SRX_NO_RADIO`). A VW frame's good
  superframes go to place frame × 5 + k. The frame count includes frames the flywheel kept, so a frame
  lost whole or a superframe failing its CRC-8 leaves a hole in the right place. After the call the holes
  get the superframe before them (`srx_conceal`), so sotto holds the sound over a gap.
- **Room.** 7.3 KB of code. The scratch holds the filter ring (256 B), the receiver (2.3 KB: two payload
  buffers, the decoders), the DMA ring (512 B) and then the message buffer, which takes whatever the block
  has left: 285 superframes (11.4 s of sotto 1200) in the 11,776-byte block. Under 100 superframes, the
  module returns -6.

Lines:

    ysf start f=440850000 floor=-125 msg=285   msg: the message buffer, superframes
    Y call fr=41 fich=40 vw=190/200 sf=200 miss=10 crc=9d79
        frames, FICHs good, superframes good / read, the message's superframes, those filled in
        (over=: superframes past the buffer), the message's CRC-16
    Y d 0 000102030405...                      the message, 24 bytes a line, after the call

A new call while the last one's lines are still going out drops the rest of them.

Model results (tests/test_k6_ysfrx.py):

- **Host decoder** on the modelled K6 IF: all 40 superframes of `sotto_test(40)` are byte-exact down to
  50 dB-Hz, either FM polarity. At 48 dB-Hz it gets 37 of 40 and the FICHs start to fail.
- **Lost frames, host:** a 12 s message (300 superframes) with one frame lost whole and one frame's payload
  lost. All 300 places are right, and the 10 lost superframes repeat the one before them.
- **Module on the emulated K6** (300 µs register reads, 5 ms per say line): an 8 s message (200
  superframes) with one frame lost whole and one payload lost comes out byte-exact with its 10 holes
  filled, with no samples lost. The header frame is missed because the carrier is first seen half a frame
  in.

Four things the emulator taught:

1. **Double buffering.** The K=7 decoder could not finish in the 25 ms between one payload's end and the
   next one's start, so the payload is double buffered and a frame decodes while the next one arrives.
2. **Short steps.** Each step must stay well under the DMA ring's 93 µs. One correlation runs per call,
   tracebacks run 16 steps per call, and the CRCs use nibble tables.
3. **No pauses during a frame.** Register reads and say lines pause the sampling, and those pauses cost
   superframes even inside a payload. During a frame the module reads no registers and says no lines, and
   the superframes come out after the call.
4. **CPU.** About 55% idle during a call.

Bench, next: the Pluto sends `python3 -m modem.ysf wav` frames on 440.850 (the K6 link channel, with its MCW ID)
at low level, and the K6
runs ysfrx and reports the lines.

SPDX-License-Identifier: MIT
