# DSotto raw: live sotto in its own frames on the UV-380's C6000 raw PHY

*2026-11-19. Thread "Simulate DSotto over C6000 raw PHY" (Jeff, 11-19 09:11Z: yes to "simulate
DSotto raw PHY ... ready for a bench test"). Code: `modem/dsr.py`, `c/common/dsr_tx.[ch]`,
`firmware/uv380-openrtx/mod/c6sotto_dsr[_dry].c`, `tests/test_uv380_dsr.py`.*

## What this is, and what it isn't

The UV-380's HR_C6000 in physical layer mode (0x10 = 0x66) is a gapless 4800 symbol/s 4FSK
transmitter: DMR's modulation (RRC alpha 0.2, dibit 01 +3, 00 +1, 10 -1, 11 -3), 9600 bit/s,
36 bytes a 30 ms slot (`docs/uv380-c6phy.md`). The multi-mode image already sends sotto through
it inside M17 and YSF frames (`docs/uv380-multimode.md`), and sends "DSotto" proper (sotto in a
real DMR voice call, `c6dvtx`) through the chip's own DMR modem.

**DSotto raw** is sotto 1200 in frames designed for it, on the same raw PHY. M17 and YSF frames
were designed for other payloads (Codec2 3200, AMBE), so with sotto's 1200 bit/s inside them most
of the 9600 bit/s goes to padding or plain repetition. DSotto raw spends it on a low-rate code
and a longer interleaver instead, keeps a CRC over the frame number, and is built for **live**
use: a frame whenever enough superframes are ready, with no whole message prepared in advance.
No stock radio decodes it. Its syncs are kept away from DMR's, M17's, YSF's and P25's, so a DMR
radio doesn't start a call on it and an M17 radio doesn't play noise.

## The formats

Every format sends the chip's 4FSK at 2400 Hz outer (TX_DEV 123 % of the DMR pair, the same as
M17). Each frame is a sync followed by coded bits. The coded bits are:

```
[type 4 | counter 8 | callsign chunk 12] [n sotto 1200 superframes, 6 bytes each] [CRC-16]
  -> K=7 tail-biting convolutional code -> bit i to position A*i mod N -> XOR PN9
```

| | DSR40 | DSR40D | **DSR120** |
|---|---|---|---|
| frame | 40 ms, 192 symbols | 40 ms | **120 ms, 576 symbols = 4 C6000 slots, 144 bytes** |
| sync (outer symbols only) | 16 symbols FF75F5D5 | 16, 5DD7D5FF | **24, 7FF57D775D5F** |
| superframes a frame | 1 | 1 | **3** |
| info bits / code | 88, rate 1/4 | 88, rate 1/4, each codeword over 3 frames (diagonal) | **184, rate 1/6** |
| overhead (header, CRC, sync) per 48 payload bits | 72 bits | 72 | **19** |
| mouth to ear (default codec / low-delay codec) | 457 / 337 ms | 537 / 417 ms | **617 / 497 ms** |

- **Header.** Type (1 sotto 1200, 2 sotto 2400 reserved, 15 end of transmission), an 8-bit
  frame counter, and 12 bits of the sender's callsign (M17's base-40, 48 bits, chunk = counter
  mod 4). The callsign is complete every 4 frames (480 ms for DSR120). The CRC (M17's CRC-16)
  covers the counter, so a superframe is put in the wrong place only if the counter wraps
  past the receiver (see the results). In M17 live the frame number isn't under the sotto CRC,
  and the simulation shows M17 misplacing superframes near its threshold.
- **Code.** Generators 0105 0127 0157 0167 0113 0173 (octal, bit 0 = the newest input bit),
  found by search. They are nested: the first four make a rate 1/4 code with free distance 20,
  and all six make a rate 1/6 code with free distance 30 (K=7 rate 1/2, as DMR, has 10). Tail
  biting means no flush bits, which is why the sizes come out exact: 184 x 6 + 48 sync bits =
  1152 = 4 slots.
- **Interleaver and whitening.** Coded bit i goes to A*i mod N (A = 205 for 1104 bits, 117 for
  352). The R bits of one trellis step land about 13 ms apart, and each info bit gets half its
  coded bits on the reliable sign (MSB) of a symbol and half on the inner/outer (LSB) bit. PN9
  (x^9 + x^5 + 1, seed 0x1FF) keeps the symbols balanced for the receiver's DC follower.
- **Stream.** A 40 ms preamble of 0x77 (+3 -3 ...), the frames, an end frame, then 0x77 0x77.
  The C framer (`dsrt_frame` builds one frame; `dsrt_msg` pulls a stored message 36 bytes at a
  time for `c6c_fsk4_tx`) matches `modem/dsr.py` byte for byte.

## The simulation

`python3 -m modem.dsr curves OUT.json` (results: `docs/results/dsr_curves.json`). The model:

- **Transmitter**: the chip's RRC alpha 0.2 at 2400 Hz outer, +150 Hz carrier offset, the far
  end's clock 20 ppm fast.
- **Channel**: a carrier of unit mean power plus complex white noise at the given C/N0. The
  fading runs are Clarke flat Rayleigh at 5 Hz (walking, or a slow car, at 440 MHz) and at
  20 Hz (about 50 km/h).
- **Receiver**: as the UV-380 has it. The IF filter is +-6 kHz (the AT1846S in 12.5 kHz mode),
  then a limiter-discriminator (the FM threshold and its clicks come out of the model by
  themselves), then the C6000 FM tap: 3.4 kHz low-pass, 8 kHz samples.
- **Demodulator** (one for every format, and the same one decodes the bench tap logs): x3 to
  24 kHz, RRC matched filter, sync correlation, sync peaks chained into runs on the far end's
  frame clock (a frame with a weak sync is still read in place, and the CRC decides), a least
  squares level and DC fit from the sync, soft bits (MSB = level, LSB = 2 - |level|), and a
  wrap-around soft Viterbi.
- **M17 live**, the baseline: the multi-mode image's real-time M17 sotto (superframe k and k-1
  in each 40 ms frame), read by the same front end with M17's K=5 Viterbi. A superframe counts if
  either copy arrives.

Superframe error rate (SFER) is the share of 1500 superframes per point that didn't come back
exactly. The table gives the C/N0 for 1 % SFER (and dBm at a 6 dB noise figure, for scale only:
see the bench note below):

| C/N0 (dB-Hz) for 1 % SFER | static | Rayleigh 5 Hz | Rayleigh 20 Hz | superframes misplaced (all points) |
|---|---|---|---|---|
| M17 live (baseline) | 47.1 (-120.9 dBm) | 61.4 | 59.1 | 103 |
| DSR40 | 45.4 (-1.7 dB) | 63.0 (+1.6) | 60.7 (+1.6) | 0 |
| DSR40D | 45.6 (-1.5) | 62.1 (+0.7) | 59.6 (+0.5) | 10 (all at 41 dB-Hz) |
| **DSR120** | **43.9 (-3.2)** | **56.9 (-4.5)** | **55.0 (-4.1)** | **0** |

At 10 % SFER, where speech is still understandable with concealment, DSR120 is about 5 dB
ahead of M17 live in 20 Hz fading (about 49 against 54 dB-Hz). M17 live misplaced 103
superframes over the run, because its frame number isn't under the sotto CRC. DSR40D's 10 came
from its 8-bit counter, which wraps every 10 s at one superframe a frame, at a point where almost
nothing decoded and the frame clock had to be pieced together. DSR120's counter wraps every
30.7 s.

What it says:

- **On a steady signal every format hits the FM threshold together.** Below about 44 dB-Hz
  (a C/N0 of 3 dB in the 12 kHz IF) the discriminator's clicks take over, and no code fixes
  that for long. DSR120 moves the edge 3.2 dB, and DSR40 about half as far (1.7).
- **In fading, the long frame wins clearly.** A deep fade at 20 Hz lasts roughly 5-20 ms. DSR120's rate 1/6 code spread over 120 ms survives losing a third of a frame, and moves the
  1 % point about 4 dB below M17 live's (4.1 at 20 Hz, 4.5 at 5 Hz). The 40 ms formats can't: DSR40's 40 ms codeword can sit inside a
  single fade and comes out worse than M17 live there. DSR40D's diagonal spread gets back only
  about 1 dB at rate 1/4.
- **The price is latency.** DSR120 waits for 3 superframes and sends a 120 ms frame: 160 ms more
  mouth to ear than a 40 ms format. Nearly all of the rest is sotto's own delay (the encoder's
  first superframe after 200 ms, the decoder 2 superframes behind), and the low-delay builds
  (`-DFX_LAG=4 -DLR_AHEAD=0`, `-DLR_INTERP=0 -DSOTTO_LR_DEC_DELAY=1`, sotto docs/sotto-1200.md)
  take 120 ms off it.
- **No false frames.** 20 s of an empty channel gives no frame with a good CRC in any format
  (`tests/test_uv380_dsr.py`). The receiver reads every frame position its sync runs reach, and
  CRC-16 at about 1 in 65,000 per read is the only gate.

**Choice: DSR120.** It gains about 3 dB on a steady signal and about 4 dB in fading (5 dB at 10 %
SFER), protects its own frame numbering, and fills exactly 4 C6000 slots. DSR40 and DSR40D are
kept in the code and the ladder as measured alternatives, since that is the evidence for the
choice. If 617 ms proves too slow in use, the low-delay codec builds bring it to 497 ms before
any change to the frame.

## The bench

**Receive ladder (job `dsraw-ladder1`, 11-19, receive only on the UV-380).** The Pluto
sends `dsr-ladder.wav` six times on 441.850 MHz, at -64, -66, -68, -70, -72 and -74 dB. Each send
is the same 48-superframe test message as DSR120, DSR40, DSR40D and M17 live, with the Pluto's
MCW ident AG7EW first and last, and the wav is made by `python3 -m modem.dsr wav`. The UV-380
transmits nothing: `uv380.rx tap1c` (c6fmtap_1c, 0x34 = 0x1C, 150 s) records its own FM tap.
Decode it with `python3 -m modem.dsr tap uv380.log --steps steps.json`, which prints a line per
send with the superframes back in each format. R4 put M17's edge on this radio between -65 dB
(every frame) and -70 dB (none), and the ladder brackets it in 2 dB steps. The Pluto's dB isn't
a calibrated dBm (R3/R4 read RSSI -81 dBm at -65 dB, while the tap was already at its FM
threshold), so the ladder measures the difference between formats, not absolute sensitivity.
Files: `/mnt/project-files/uv380-dsotto-raw/ladder1/`.

**Result (11-19 09:43-09:46Z): the radio agrees with the simulation.** Superframes back of 48
per format, from the UV-380's own tap (the C6000 FM tap, no mod, 0 half gaps in 150 s, RSSI from
the module's st lines):

| Pluto gain | RSSI | DSR120 | DSR40 | DSR40D | M17 live |
|---|---|---|---|---|---|
| -64 dB | -77 dBm | 48 | 48 | 48 | 48 |
| -66 | -80 | 48 | 48 | 48 | 48 |
| -68 | -82 | 48 | 48 | 48 | 48 |
| -70 | -84 | 48 | 48 | 48 | 46 |
| -72 | -86 | **48** | 44 | 41 | **6** |
| -74 | -86 | 9 | 5 | 4 | 0 |

At -72 dB DSR120 still got every superframe through while M17 live got 6 of 48. Its edge sits
between -72 and -74, about 2-3 dB below M17 live's (between -70 and -72), against 3.2 dB in the
model. DSR40 and DSR40D fall between them, also as modelled. No superframe came back wrong in
any format. The RSSI meter stops moving at -86 dBm, which is again the FM threshold rather than
the radio's noise floor (R3). Decode: `dsr-tap-report.json` beside the job; the log and steps.json
are in `result/files/`.

**UV-380 send at 0 W (job `dsraw-air1`, staged).** `c6sotto_dsr_dry.c`, then `c6sotto_dsr.c`,
send the radio's own message as DSR120 through `c6air.h` (mode 4), with the Pluto recording.
They run after this PR merges and the runner self-updates, because the runner's `uv380.sotto`
plan list gains `dsr`. Decode with `python3 -m modem.dsr decode dsr.cf32`. DSR is a RAM module
only (`C6AIR_WITH_DSR`, set by `c6sotto.c` for mode 4), so the image builds exactly as before.
The image's own `air` command keeps m17, ysf and dmr, and the runner has its own list for it
(`UV380_AIR_AIRS`). Files: `/mnt/project-files/uv380-dsotto-raw/air1/`.

## Into the multi-mode image

DSotto raw fits the multi-mode design (`docs/uv380-multimode.md`) as a sixth PAGER sub-mode. It
needs no new chip work: same profile (`C6C_FSK4_TX`), same engine (`c6c_fsk4_tx`), same receive
front end as M17 and YSF.

**Transmit, stored message (works now as a RAM module).** `c6air_run(api, C6AIR_DSR, ...)`. In
the image this is one more name for the macro cycle (RNG, FSK, M17, YSF, DMR, DSR) and for `air`.
It costs about 1 KB of flash (dsr_tx.c) and 160 bytes of state.

**Transmit, live (needs milestone 4 step 2, a flash).** Live means holding PTT and talking while
the radio sends:

1. **Keying with no 5 s cap.** `c6_key` keys for as long as PTT is held, up to a time-out (say
   120 s). The IWDG guard is re-armed at every slot while frames are flowing, so a hung
   pipeline still drops the carrier within about 1.5 s.
2. **The pipeline.** The I2S3 mic feeds the fixed-point sotto 1200 8 kHz encoder in its own task
   (70 MHz worst case at -O2, 24.4 KB arena in CCM; `uv380-sotto-codec`). Its superframes go
   into an 8-deep queue. Every 4 slot edges (120 ms) the framer takes 3 superframes and builds a
   144-byte frame (`dsrt_frame`) into a byte FIFO, and `c6c_fsk4_tx` pulls 36 bytes per slot.
   While the encoder warms up (its first 200 ms, plus 80 ms to fill the first frame), the engine
   sends preamble, which gives a receiver time to lock.
3. **Clocks.** The framer runs on the slot clock (C6000), and the encoder runs on I2S3, which the
   C6000's codec also clocks. If the two turn out not to be locked, the framer repeats or drops
   one superframe on a slip, and `sotto_rx` conceals it as it does a lost one.
4. **Underrun.** If the queue is short at a frame time, the framer repeats the last superframe,
   so the frame clock never stops (a receiver's flywheel depends on it).
5. **Release.** When PTT is released, the current frame finishes, then the end frame, two pad
   slots, registers restored and key down. The ident policy for live calls (the Morse ident
   before every key-up, as on the bench now, or once per call at the end, with AG7EW in every
   frame header meanwhile) is Jeff's decision, and only becomes a question once live works.
6. **Open question for the bench: does the mic stay alive in raw PHY?** I2S3 carries the mic
   while the C6000 keys in FM (11-14 `micsend`), and with the C6000 keyed and no PA
   (`micsilent`). The raw PHY profile turns the FM chain off (0x60 = 0), which may take the
   codec ADC down with it. Testing this needs Jeff, because the side-jack cable switches the
   internal mic out. Next step: a dry module (no PA) that records I2S3 rms in raw PHY against FM.
   If raw PHY kills the mic, the fallback is to record on PTT and send on release, as FUNC does
   now.

**Receive.** `c6rx.h` and `airListen` gain a DSR deframer behind the shared 4FSK front end:
24-symbol sync, a frame clock, 1104 soft bits as int8 (1.1 KB), and a K=7 wrap-around Viterbi
(64 states over 280 steps; 2.2 KB of decisions, about 110k multiply-adds per 120 ms, under 1 % of
the CPU). It places superframes by counter into `sotto_rx` and plays them through the existing
speaker path, about 6 KB in `c6_arena` in all. The order is the same as for M17 and YSF: a C port
`c/common/dsr_rx.[ch]` checked against `modem/dsr.py`, then a receive-only RAM module
(`c6dsrrx_tap`, runner `uv380.rx dsr`) fed by the Pluto's ladder wav, then flash.

## Next

1. Done: `dsraw-ladder1` puts DSR120 2-3 dB ahead of M17 live on the radio's own tap.
2. Merge this PR, then run `dsraw-air1` (0 W, the Pluto listening).
3. `dsr_rx` in C, and the receive module.
4. The mic in raw PHY with Jeff, then live keying in the image (milestone 4 step 2), and the DSR
   sub-mode.
5. Later: sotto 2400 in DSR120 (288 payload bits at about rate 1/3.4: better voice, about the
   same robustness as M17 live), and soft decisions weighted by a per-frame noise estimate (the
   simulation uses none).
