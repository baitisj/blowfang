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
REG_72 C195), four samples a bit.

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
| gap | 128 | lead, before each later packet's sync: the module finds a packet's end 32 bits late, then re-arms the chip on the next F0F0 |
| tail | 32 | lead |

Air time for n packets: (600 + 32 + 2096 n + 128 (n - 1)) / 1200 s. Six packets: 11.5 s.

## The receiver (c/k6/mod/rangerx.c)

Level 2, receive only, on the radio's own frequency. 4.1 KB at -Oz, stack 276 B. It follows the
chip's FIFO word by word (pocsag.c's follower), slices the samples into soft values with an
early-late bit clock in integers (edge and length in Q8 samples, length held to 4 ± 0.62 samples),
hunts the sync (|correlation| of 32 soft values ≥ 56; a better one in the first 160 bits starts the
packet again there), keeps the packet's soft values as 4-bit nibbles, and decodes it with PHY G's
Viterbi (ifpager.c's butterfly) in slices between FIFO reads. modem.k6r's `Slicer` and `Receiver`
do the same thing step for step.

It says one line a packet: `r N t= c= y= e= ok= s= r=- k=`, where c is the sync's correlation,
y the sum of |soft|, e the channel errors found by coding the decoded payload again (of 2064),
ok the CRC, s the packet's seq, r the RSSI during the packet, k the bit length (Q8 samples). The
end line: `range end n= ok= c= z= f= l= x= m=-` (packets, right, chip captures, captures with no
sync, clock flips, late decodes, resyncs, lowest RSSI).

rangerx_b is the bench build: 32 s wait (the module starts about 29 s before its upload ends),
then nine settings of 45 s. Each setting picks the front-end path (the one tune picked, the
other, neither: about 48 and 52 dB of loss, docs/k6-fesw.md) and REG_13 (AGC, or gain fixed at
five steps down to about -60 dB). The first setting comes again last. `s` and `v` lines mark
each setting. A board whose path pins aren't one of the two paths is left alone (returns -5).

## The model (sim/k6if/range.py)

Sender audio, then FM, white noise at a C/N0, the chip's channel filter, a discriminator,
de-emphasis and a 3 kHz low-pass, then the sign at 4800 a second. Two senders: the IC-7100 model
(study.ic7100: 300 Hz high-pass, limiter, 3.1 kHz low-pass) and a flat one. The POCSAG bench sent
NRZ runs of 10 bits whole, so the real IC-7100 is gentler than its model; the truth is between.

- The ideal receiver reads every packet down to about 42 dB-Hz. With a 5 dB noise figure that is
  roughly -127 dBm. **That is a model figure, not a measurement.**
- The module reads within 1-2 dB of that, and at sampler rates 1.75% off either way (the POCSAG
  bench found the sampler at 3.93 a bit).
- PHY G on the IF tap stops near -79 dBm, so PHY R should gain about 40 dB, if the chip's floor
  holds up on the bench.

## The sender

c/common/range_tx.h: portable C, no malloc, no floating point. It hands ffsk_tx tone bits:

```c
rg_air a;  ffsk_tx m;
rg_air_defaults(&a, pt_message_source, &msg);     /* lead 600, gap 128, tail 32 */
ffsk_init_src(&m, rg_air_next, &a, fs, RG_BAUD, RG_MARK_HZ, RG_SPACE_HZ, level);
```

UV-380 settings: 1200 baud, mark 600 Hz, space 1200 Hz, the same level and path as its pager mode
(MCU PWM into the C6000's line input, then FM). `range_tx_host tones|audio` writes what it sends
for the tests. For the IC-7100 bench, `python3 -m modem.k6r wav OUT.wav` writes bursts of six
packets for 440 s.

## The other way: the UV-380 receiving

The UV-380's receive audio doesn't reach its MCU without a hardware mod (OpenRTX). That is the
block for K6 to UV-380, and the I2S probe looks for a path without one.

## Bench

Keys in /mnt/project-files/k6-range/bench-keys.md. `python3 -m modem.k6r report k6.log` gives, per
setting, packets, those right, channel errors and RSSI.

Files: modem/k6r.py, sim/k6if/range.py, c/k6/mod/rangerx.c, c/k6/mod/rangerx_b.c,
c/common/range_tx.c, tests/test_k6_range.py. MIT licence.
