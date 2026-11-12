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
