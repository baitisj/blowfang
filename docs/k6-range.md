# PHY R: the K6's range mode

Jeff asked on 2026-11-11 for a mode that reaches further than PHY G. PHY G reads its soft values
from the GP0 IF tap, and that tap is deaf: about -79 dBm (docs/k6-if-fec.md,
k6-if-tap-sensitivity in project memory). The chip's plain FSK receiver hears down to about
-122 dBm (the DMR sync runs). PHY R puts PHY G's code on that receiver. The sender is the
UV-380 first: Jeff wants UV-380 to K6 to work, or the idea shelved.

What it carries: 128-byte K6 pager payloads, 1.75 s of air each plus a 0.11 s gap, so about
550 bit/s of payload bytes in a long burst. That is enough for sotto 400.

## The waveform

FFSK at 1200 baud: 600 Hz for tone bit 1 and 1200 Hz for tone bit 0, phase continuous. At 1200
baud 600 Hz is half a cycle a bit and 1200 Hz a whole one. So the audio changes sign at every bit
edge, and once more in the middle of a 0 (biphase):

- the chip's sampler gets a sign change at least once a bit (POCSAG: its clock slips in long runs);
- the line has no DC, so a sender's audio high-pass (the IC-7100's, about 300 Hz) can't bend it.
  NRZ direct FSK fails on the IC-7100 model for that reason (`python3 -m sim.k6if.range nrz`);
- the UV-380's pager sender, c/common/ffsk_tx.c, already makes it. Only the framing is new.

The chip samples the discriminator's sign 4800 times a second (plain receiver, REG_58 00E9,
REG_72 C195), four samples a bit. Its sampler is locked to the audio's edges rather than a steady
clock: bench run 2 saw runs of exactly 2 and 4 samples, and a run a sample short now and then (the
rate comes to about 3.93 samples a bit, as on the POCSAG bench).

Precoding: the tone bit sent is `b[k] = a[k] ^ a[k-1]` (the first is 1), where a is the line bit
stream. The sign of the audio across bit k's end edge then follows a[k] itself, so
`y[k] = (second half of bit k) - (first half of bit k+1)` is a coherent soft value (-4..4) of
a[k], up to one sign for the burst that the sync word settles. Reading the tone bits one by one
instead (did the sign flip mid-bit) costs about 1 dB.

## A burst

| part | line bits | what |
| --- | --- | --- |
| lead | 600 (0.5 s) | tone bits 1 1 1 1 1 0 0 0 over and over, ending on the pattern's end. The five 600 Hz bits make the chip's sync word F0F0, which starts the chip's packet. The 1200 Hz bits keep the module's clock from locking to 600 Hz edges only |
| sync | 32 | 1ACFFC1D, MSB first |
| packet | 2064 | PHY G's code (K=7, 133 and 171, 8 zero tail bits) of one 128-byte XL pager payload, interleaved 24 rows by 86: line bit i = coded bit 86 (i % 24) + i / 24 |
| gap | 128 | lead, before each later packet's sync: the module finds a packet's end about 50 bits late (32 behind the FIFO, the rest in its slicer), then re-arms the chip on the next F0F0 |
| tail | 32 | lead |

Air time for n packets: (600 + 32 + 2096 n + 128 (n - 1)) / 1200 s. Six packets: 11.5 s.

## The receiver (c/k6/mod/rangerx.c)

Level 2, receive only, on the radio's own frequency, with the AF path's de-emphasis off (REG_2B bit
8). 4.7 KB at -Oz in the emulator's build, stack under 600 B. It follows the chip's FIFO word by
word (pocsag.c's follower) and parses the samples into half bits with a segment decoder: a Viterbi
over 12 states (the half's sign, first or second half of its bit, samples so far), where a sample
against the sign costs 1 and a half of 1 or 3 samples costs 1. A bit's edge always changes the
sign; its middle may (1200 Hz) or may not (600 Hz). It traces back every 16 samples and says the
halves that end 64 samples back. A line bit's soft value is (its second half's samples) - (the
next bit's first half's), each ±1: ±4 from a clean bit.

Above that it hunts the sync (|correlation| of 32 soft values ≥ 96 of 128; a better one in the
first 160 bits starts the packet again there), keeps the packet's soft values as 4-bit nibbles,
and decodes it with PHY G's Viterbi (ifpager.c's butterfly) in slices between FIFO reads. It
re-arms the chip as soon as a packet is in. modem.k6r's `SegSlicer` and `Receiver` do the same
thing step for step (the tests check the module and Python agree on every packet's channel errors).

Runs 1 and 2 used a window slicer on an early-late bit clock instead and a sync threshold of 56.
The clock couldn't follow the sampler's slips. At 56, coded bits start a false packet about once
in 80 bits, which the module only survives because it re-arms the chip after each packet.

It says one line a packet: `r N t= c= y= e= ok= s= r=- k=`, where c is the sync's correlation,
y the mean |soft| × 10, e the channel errors found by coding the decoded payload again (of 2064),
ok the CRC, s the packet's seq, r the RSSI during the packet, k the halves of 1 or 3 samples in the
packet (the sampler's slips, and noise). The end line: `range end n= ok= c= z= f= l= x= m=-`
(packets, right, chip captures, captures with no sync, traces that disagreed with halves already
said, late decodes, resyncs, lowest RSSI).

rangerx_b is the bench build: 32 s wait (the module starts about 29 s before its upload ends),
then nine settings of 45 s. Each setting picks the front-end path (the one tune picked, the
other, neither: about 48 and 52 dB of loss, docs/k6-fesw.md) and REG_13 (AGC, or gain fixed at
five steps down to about -60 dB). The first setting comes again last. `s` and `v` lines mark
each setting, and each setting's first capture is said word by word (`c` and `d` lines, as
rangerx_d) so the chip's samples at each level can be studied; saying it costs the next packet. A
board whose path pins aren't one of the two paths is left alone (returns -5).

## The model (sim/k6if/range.py)

Sender audio, then FM, white noise at a C/N0, the chip's channel filter, a discriminator,
de-emphasis and a 3 kHz low-pass, then the sign at 4800 a second, on a steady clock or on one
locked to the edges (`sample(..., lock=g)`). Two senders: the IC-7100 model (study.ic7100: 300 Hz
high-pass, limiter, 3.1 kHz low-pass) and a flat one.

- The ideal receiver (steady clock, best phase, windows) reads every packet down to about
  42 dB-Hz. With a 5 dB noise figure that is roughly -127 dBm.
- The module's segment decoder, flat sender, steady or edge-locked sampler: every packet at
  47 dB-Hz, 30 of 36 at 46, 22 at 45, 13 at 44. So about -122 dBm, 4 to 5 dB short of the ideal
  receiver, which assumes a steady clock that the chip doesn't have. The model keeps the
  de-emphasis that the module now turns off, so the real figure is likely worse.
  **These are model figures, not measurements.**
- Through the IC-7100 model the segment decoder reads clean packets only with some samplers: the
  model's 300 Hz high-pass moves the edges where the tone changes by a sample. The real IC-7100
  was kinder (run 2 below). How the UV-380's audio path treats the edges is for the bench.
- PHY G on the IF tap stops near -79 dBm, so PHY R should still gain about 40 dB if the chip's
  floor holds up on the bench.

## The sender

c/common/range_tx.h: portable C, no malloc, no floating point. It hands ffsk_tx tone bits:

```c
rg_air a;  ffsk_tx m;
rg_air_defaults(&a, pt_message_source, &msg);     /* lead 600, gap 128, tail 32 */
ffsk_init_src(&m, rg_air_next, &a, fs, RG_BAUD, RG_MARK_HZ, RG_SPACE_HZ, level);
```

UV-380 settings: 1200 baud, mark 600 Hz, space 1200 Hz, the same level and path as its pager mode
(MCU PWM into the C6000's line input, then FM). The UV-380 range image
(`BLOWFANG_RANGE=1 firmware/uv380-openrtx/build.sh`, firmware/uv380-openrtx/README.md) sends
the stored message this way on PTT. `range_tx_host tones|audio` writes what it sends
for the tests. For the IC-7100 bench, `python3 -m modem.k6r wav OUT.wav` writes bursts of six
packets for 440 s, pre-emphasised as the UV-380 sends them (`--preemph off` or `alt` otherwise).

## The K6's sender (c/k6/mod/rangetx.c)

A level-3 RAM module that keys up on 440.850 (after the channel has been clear for 1 s), sends one
burst of six test packets (`rangetx_bits.h`, written by `python -m modem.k6r txhdr`, the same
packets as the bench wav) and identifies in MCW. The FFSK comes from the FD6818's tone oscillator
(REG_70 on with gain 60, REG_71 the frequency word: 6195 for 600.033 Hz, 12389 for 1199.968 Hz),
written only where the tone changes. The bits are timed on the MCU's TIM17 at 96 MHz (80000
cycles a bit), unwrapped by Aura's TIM14 as dmrdet does.

The oscillator is phase continuous, so what matters is its phase at each bit edge, not just when
the word changes. A change up that lands d late takes 600 Hz x d of phase off, a change down
puts it on, and the words' own errors (+54 and -27 ppm) add a little every bit. The module keeps
the sum as A and aims each change to cancel it, starting each register write its own measured
length early (the last write's; it refuses to send, -8, if a write takes over 3/4 of a bit). In
the emulator, with writes of 180-200 us, the phase stays within 6 degrees of the edges over the
11.5 s burst, and the UV-380's receiver reads 6 of 6 through a model of its receive path, with
or without de-emphasis on the K6's tone (tests/test_k6_rangetx.py).

For the bench, `rangetx_lo` is the same with the PA off from just after tx_on to the end (Jeff
asked for low-power K6 sends, 11-13): only the FD6818's leak goes out, about 40 dB under the
module's low power at bench range (docs/k6-own-fsk.md), still plenty for the UV-380 a few feet
away. Both say `pa=` on their first line.

What the emulator can't say:

- The FD6818's crystal against the MCU's. The tone follows the first, the bits the second, so a
  difference walks the phase: 30 ppm is a third of a cycle over a burst, and on the model the
  receiver starts losing packets near there (10 ppm is fine). `-DRT_PPM=` trims it once it's
  measured; a recording of the K6's burst on the UV-380 (rangetap) gives it.
- Whether the oscillator starts at phase 0 when REG_70 turns it on (the receiver reads any start
  phase, so this only matters for analysis), and whether the K6 pre-emphasises the tone.
- The deviation at gain 60 against the IC-7100's level; `-DRT_GAIN=` sets it.

### K6 to UV-380 on air: runs K7 and K8 (2026-11-13)

K7 (17:00Z, rangetx, full low power) and K8 (17:13Z, rangetx_lo, PA off, RT_PPM 248): the K6 sent
the burst both times (`rangetx w=6503 late=0 wr=11223-11232 d=99`: a register write takes
about 117 us), and the UV-380, unmodified, heard it at -17 and -48 dBm, but read 0 of 6, with
about 390-430 channel errors a packet from the first. The recording shows why: at every 600 to
1200 Hz change the tone has a runt half cycle of 0.06-0.2 ms, a phase jump; 1200 to 600 looks
clean, and the IC-7100's audio has no runts. So the oscillator isn't phase continuous across a
REG_71 write, or not the way rangetx assumed. (A first fit read the jumps as the tone running
+248 ppm against the bit clock; RT_PPM 248 changed nothing, so that was the fit.) The received
1200 Hz comes out about 2 dB over 600 Hz after the UV-380's de-emphasis: the K6's tone path is
flat or mildly pre-emphasised.

`tonewr` (c/k6/mod/tonewr.c, PA off) measures what a write does: 7 s of scheduled writes from
tone-on (same-word REG_71 rewrites, 600/1200 changes at every phase, tone 1 off and on, tone 2
(REG_72) against tone 1), timed on TIM17. `tools/k6_tonewr.py report REC` reads a recording
(IC-7100 48 kHz, or the UV-380's rxrec log) and says for each kind of write whether the phase runs
on through it or is set at it, and whether a tone runs on while it's off.

**K9 (2026-11-13 17:37Z, tonewr, PA off), the IC-7100's 48 kHz recording:**
- A REG_71 change is phase continuous, both ways (jump R 1.00, the phase after it spread out),
  and rewriting the same word does nothing (0.1 degree rms).
- Tone 1 runs on while REG_70 has it off: turned on again, it comes back on its own line.
- The FD6818's tone runs 271 ppm fast against the K6's MCU clock (600.195 Hz in K6 time for
  600.033 Hz written); the recorder agrees with the tone within 2 ppm, so it's the MCU's clock
  that's about 270 ppm slow.
- Tone 2 doesn't sound: REG_72 = 1200 Hz with REG_70 bit 7 gave nothing (level 2 against tone
  1's 7810). REG_72 read 0x4898 before (the pager's FSK set-up) and was put back.

So K7's failure was the clock: 271 ppm walks the tone about 87 degrees a second off the bit
edges, through the bad phases within each packet. `tools/k6_rangetx_check.py REC` measures that
walk on a recording of a burst (both tones' phase at every bit edge against ideal FFSK).

**K10 (2026-11-13, rangetx_lo, PA off, RT_PPM 365): 0 of 6 again** (UV-380 at -49 dBm, channel
errors 354-409 a packet). rangetx_check on the IC-7100's recording: the tone walks -23 deg/s, so
the offset was about 292 ppm that time. The offsets measured so far are 355 (K7), 377 (K8), 271
(K9) and 292 ppm (K10), runs minutes apart, and it wanders within a burst too: the MCU's clock
behaves like an RC oscillator, so no RT_PPM holds the phase through 11.5 s.

**So the receiver follows it (11-13, range_rx.c RR_TRACK).** The walk turns both tones' phase
alike, which is a slow rotation of the audio's analytic signal; range_rx now takes each y from the
audio turned back by an angle it follows (a 31-tap Hilbert filter gives the quadrature;
decision-directed, 1/32 of the error a bit; the bank's phases use the turned audio too). On the
emulated K6 burst with RT_PPM 0 and the tone -400 to +500 ppm off, through the UV-380 model
(de-emphasis or flat, 8 dB SNR): 6 of 6 every time, channel errors at most 8; without the follower
0 of 6 past 300 ppm and packets lost from 30 ppm. It costs nothing where nothing walks (2-6 dB SNR:
the same or slightly fewer errors), and the module about 200 B of code and 64 B of state (the y
arrays went: the step before's y comes from the audio again). Gain 1/8 to 1/128 tried: 1/32 and
1/16 hold everywhere, 1/128 loses packets from 300 ppm.

**The replay, and the second cause (hamlet, 11-13).** Through the follower, the IC-7100's
recordings of K7, K8 and K10 read 3, 5 and 6 of 6, but the UV-380's taps of the same bursts still
0 of 6. The UV-380's response to them, measured against the IC-7100's recording (cross-spectrum,
clock and delay fitted; tests/data/uv380/rx-response-20261113.txt): 6 dB less at 1200 Hz than at
600 (+4 dB at 600, -2 at 1200, -5 at 1450, from 1000 Hz), its de-emphasis, which the K6's flat
tone doesn't undo; the phase (a steep 300 Hz high-pass below 450 Hz, then a slow rise above 1
kHz) is the same as for the IC-7100's own pre-emphasised bursts, which arrive flat and read 42 of
42. So the tilt is what stops the K6. An equaliser fitted from the two recordings got the UV-380's
taps to 3-5 of 6 of the bursts.

**Fix: rangetx pre-emphasises.** The 1200 Hz bits go out at RT_GAIN12 (110) and the 600s at
RT_GAIN (44): 2.5 times, written to REG_70 right after each REG_71 change (the first write-length
of each bit is at the other tone's level). A model of the UV-380 as measured
(tests/test_k6_rangetx.py `uv380_measured`) reproduces the bench: sent flat, 14-16 of 18 with
hundreds of errors in the rest even at 20 dB and no walk; pre-emphasised, with the follower, 281 of
288 over 0, +-100 and 300 ppm at 8-20 dB (errors at most 13; the losses are early packets locking
half a bit off now and then). 2.0 times is nearly as good, 1.5 not. Whether REG_70's gain is
linear isn't measured: `tools/k6_rangetx_check.py` now prints the 1200 Hz bits' level against the
600s' on the IC-7100's recording (the model: +7 dB for 8 dB sent, through its band-pass).

## The other way: the UV-380 receiving

The UV-380's receive audio doesn't reach its MCU without a hardware mod (OpenRTX). That is the
block for K6 to UV-380, and the I2S probe looks for a path without one.

## Bench

**Run 1 (2026-11-12, 01:00-01:08Z, rangerx_b).** The K6 found a packet's sync in every setting
from -21 to -126 dBm (130 packets, c ±56..93 where the model gives ±110..128), but none decoded:
about 700 of 2064 coded bits wrong at every level, mean |soft| 2.2 where the model gives 3.5.
So it's systematic, not noise. The sampler ran at 3.9-3.96 samples a bit as on the POCSAG bench.
The model reads the same packets through a flat or an IC-7100 sender, with or without
pre-emphasis, low-passes down to 600 Hz and offsets to 300 Hz. Only a carrier offset of 600 Hz
or more, or pre-emphasis with the de-emphasis off, breaks it like this.

**Run 2 (2026-11-12, rangerx_d).** Each capture's sampled words went to the log, 60 s with the
AF path as found and 60 s with its de-emphasis off. The wav alternated plain and pre-emphasised
bursts. What the samples showed:

- The signs are clean. Runs are 2 and 4 samples, with a 3 now and then where a run of 600 Hz
  bits ends: the sampler slips. Its rate wanders between 3.88 and 4.0 samples a bit from one
  64-bit block to the next, so the window slicer's clock lost about a third of the bits.
- Pre-emphasised bursts with the de-emphasis off (captures 27 and 29, two whole packets) read
  with no line bit wrong once the samples are parsed into half bits. That is now the module's
  slicer, and the module turns the de-emphasis off. The UV-380's PAGER mode sends with
  pre-emphasis, so this matches.
- Plain bursts, with or without the de-emphasis, had many runs of 3 where the tone changes and
  read with 20-30% of the bits wrong. No whole pre-emphasised packet was caught with the
  de-emphasis on, so that pairing is still open.

Captures 27 and 29 are in tests/data/k6/ and the tests read them. `python3 -m modem.k6r dump
LOG` lays each capture against the bench's own packets.

**Run 3 (2026-11-12, 03:21-03:29Z, rangerx_b at 9de4228).** The new decoder with the
de-emphasis off, the IC-7100 sending the pre-emphasised wav. 189 of 189 packets right with no
channel errors, in all nine settings (13 to 23 packets each), RSSI readings from -20 down to
-126 dBm. Every dumped capture after the first (settling noise) laid on the bench's packets with
0 of 2064 bits wrong. The last settings read low because their gain is fixed low, not because the
signal is weak: the ladder still leaves about 40 dB of S/N, so with no errors at all this run
says nothing about the edge. That needs the IC-7100 on a dummy load or behind an attenuator.

Keys in /mnt/project-files/k6-range/bench-keys.md. `python3 -m modem.k6r report k6.log` gives, per
setting, packets, those right, channel errors and RSSI.

Files: modem/k6r.py, sim/k6if/range.py, c/k6/mod/rangerx.c, c/k6/mod/rangerx_b.c,
c/k6/mod/rangerx_d.c, c/common/range_tx.c, tests/test_k6_range.py, tests/data/k6/k6r_run2_c*.txt.
MIT licence.

## The UV-380's receiver (c/common/range_rx.c)

The UV-380 hears PHY R with no hardware mod (2026-11-13): with the HR_C6000's register 26 at fd
and RX_AUDIO_MUX (PD9) high, the FM receiver's audio comes off the codec's I2S port, SPI3 as a
slave in 32-bit channels at 8 kHz. It has the audio itself, not a chip's signs, so the receiver is
an integrate and dump on the bit edges: y = (the audio over the half bit before an edge) - (over
the half bit after it), the same soft value the K6 gets from its four signs. The clock is a bank
of 16 phases a bit apart, each with a running mean of |y|; the best phase is where the edges are
(half a bit off, 600 Hz bits give 0), and moving round the bank follows a drifting clock (a wrap
adds or drops a soft value). Soft values are scaled to the best phase's mean as 4, so the sync
(threshold 96), the de-interleaver and PHY G's Viterbi are the K6's. A sync of 112 or more that
beats the packet's own restarts the packet anywhere in it, so a false sync in the noise between
bursts doesn't swallow the next packet.

tests/test_range_rx.py: every packet at -2 dB audio SNR (the whole 4 kHz band), with the clock off
200 ppm, with de-emphasis, behind a 300 Hz high-pass, upside down, and with seconds of noise around
bursts. The module (firmware/uv380-openrtx/mod/rangerx.c) is 3.7 KB, its state 9.6 KB with the
Viterbi's survivors (soft values kept 4 bits each), and a 2 KB DMA ring: 0.8 KB under the 16 KB
arena.

On the radio (11-13): the first live runs read nothing although rxrec's recording of the same
audio decoded 42 of 42 on the host, and rrinject (the receiver on the MCU fed that recording over
USB) matched the host bit for bit. rangetap, which copies the receiver's input to USB, showed why:
the I2S slave had started out of step with the codec's frames, so every halfword was a sample's
last bit or two and fill. rxrec had started in step by chance. rxaudio.h now enables the slave on a
WS edge and checks the samples (each comes twice, a halfword apart), trying again until they do.

**UV-380 run 5 (2026-11-13, 15:48-15:50Z, rangetap at 0097a6d).** The IC-7100 sent 7 bursts of 6
packets on 440.850 at RF power 0; the UV-380, unmodified, read 42 of 42 with the CRC right
(align=1, c 125-137, no ring overruns, longest gap between reads 49 ms). range_rx_host read the
tapped input the same, line for line. Five more starts all aligned at the first try. Within a
burst the channel errors climb from 0-11 at its first packet to 50-150 (of 2064) at its sixth and
start again at the next burst's sync: the clock bank follows the drift between the IC-7100's wav
and the codec's 8 kHz, but late, so longer bursts or weaker signals would have less margin.

Why (hamlet's look at run 5's tapped audio): the audio clocks agree to about 4 ppm, with the
wander 0.04-0.09 samples, so the clock was never lost. The bank keeps one phase until another's
mean edge energy E beats it by 1/8, and E at the neighbour reaches 1.08 times E at the phase in
use without a move: the soft values drift up to 1/16 bit early before the phase moves, and the
errors climb. Host runs on that audio: a 1/32 threshold with E averaged over 64 steps kept 42 of 42
with the errors at most 68 (mean about 22, against 50 and 152 before); 1/32 with E over 32 lost
two packets.

**Fix (11-13): fractional timing.** The bank still picks the phase (RR_HYST, RR_EAVG keep 8 and
32), but each soft value is now taken at the top of a parabola through E at ph - 1, ph and ph + 1,
to 1/256 of a phase, so a drift between phase moves costs nothing. The step runs one phase later so
that audio is in; 32-bit only (a module has no 64-bit divide). Simulated 6-packet bursts, channel
errors per packet, old against new: at 4 dB SNR and 4 ppm 1-7 against 0-2, at 1000 ppm 6-12 against
0-4; at 2 dB SNR and 1000 ppm 20-32 against 9-16. rangerx grows 220 B to 3910 B (about 570 B under
the arena).

On run 5's tapped audio the new receiver reads 42 of 42 with the errors mean 5.4, max 58 (one
packet; the rest at most 13), against 50 and 152 before; on run 2's recording mean 4.4, max 10
(16.5 and 108 before). RR_HYST 32 with RR_EAVG 64 caps run 5 at 12 but adds a false sync line, so
the defaults stay.

**UV-380 run 7 (2026-11-13, 16:44-16:45Z, rangetap at 0e13789).** As run 5: the IC-7100 sent 7
bursts of 6 on 440.850 at RF power 0. 42 of 42 with the CRC right (align=1, no overruns); channel
errors a packet mean 5.1, max 16, with no climb within a burst. The old receiver on run 7's tapped
audio gives mean 51, max 136, so the change is all the receiver's.
