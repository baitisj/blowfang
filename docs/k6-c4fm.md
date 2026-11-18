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

## Bench

The inbox job runs four steps. It wakes the K6, sends `k6.upload` of the built `c4probe.bin` over the Pluto
(level 3), and sends `trigger.wav`: about 4 s of PSK31 "AG7EW TEST", with the Pluto's own MCW ID first.
Then it starts a background `pluto rx run:c4.wav --hz 440849000 --seconds 60`. The Pluto is tuned 1 kHz
low because the K6 sits about -1.9 ppm and the Pluto about +1 ppm.

The Pluto can't capture while it sends, and the uploader holds it through its 27 s report wait. So
the module waits for the upload's carrier to go, then for a carrier of the job's own (the trigger, or
the uploader's closing ID), then for C4_PRE_MS (10 s) of quiet before it keys (trigger(); -7 if none
of that happens within 180 s). Then `python3 -m modem.k6c4fm analyze c4.wav`.

SPDX-License-Identifier: MIT
