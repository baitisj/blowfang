# Do the 2400 baud FIFO findings help the other receivers? (audit, 2026-11-17)

Jeff asked (11-17 09:11Z) whether the K6's 2400 baud receive results improve any of the other
digital receivers. The 2400 finding (docs/k6-fsk24.md, PR #153): the image read the chip's FIFO
**by time** at D/2400, and over a long upload those reads slipped (9/25 chunks); read **by the
FIFO-almost-full interrupt** (REG_5E 0x0203) the same upload took 25/25. The lesson is general: a
read schedule kept by the MCU's clock drifts against the chip's bit clock (which follows the
sender) and stalls when the CPU is busy, so follow the chip (its interrupt, or the FIFO's content),
never the time.

## Every receiver, by how it reads

| Receiver | Reads | Same risk? |
| --- | --- | --- |
| Pager F/1800, F/1200 (image) | interrupt (0x0203) long before PR #153 | no; PR #153 only moved D/2400 onto it |
| Pager D/2400 (image) | interrupt since PR #153 (flashed 11-17 09:16Z) | fixed |
| GROUNDWAVE radio play (`gw.c`) | interrupt, by time only if the edge stalls | no |
| POCSAG (`pocsag.c`), range mode (`rangerx.c`), APRS (`aprsdfsk`, `aprsclk.c` DF reader) | plain FSK receiver as a sampler, where the almost-full flag never comes: the FIFO followed by content (an empty read gives the word of eight before), PR #74 | no; already the fix's plain-mode form (POCSAG run 5: added bits 16-36 a burst down to 0-3) |
| SAME (`samerx.c`) | sampler, by time with an empty-FIFO double read | partly; parked (no header ever decoded) |
| Probes: `fsk24rx`, `fsk24rx_uv`, `fsk24rx_sweep`, `acarsfsk`, `fskaprs`, `aprsclk` FSK_STREAM, `samecap`, `pocdump`, `dfskcap` | by time, LAG words behind | yes in principle, but each packet is short and starts its own schedule (fsk24 run 3 234/234); ACARS and chip-modem APRS are dead ends. Not changed |
| AIS, DMR (`dmrrx`), FT8/WSPR, APRS on the IF tap | GP0 tap by DMA, no chip FIFO | no (FT8's DMA-rate note in docs/k6-ft8-wspr.md is a separate, known 1.5 % item) |
| Morse, CW ID | the chip's tone detector, polled each ms | no |
| UV-380 PAGER listener (`pager_listen.c`) | DMA ring of codec audio; on overrun it skips to fresh audio | no FIFO; the skip isn't counted (small follow-up) |
| UV-380 C6000 DMR receive (`c6dmrrx`) | the chip's slot interrupt | no |
| RT-950 | no receiver in the repo yet | n/a |

So the FIFO fix itself carries to nothing else: every receiver in use already follows the chip.

## What the 2400 work did turn up: F/1800 test sends were under-deviated

Last night's logs (hamlet runs/fifo-audit-20261117) showed about a quarter of the F/1800 test
packets in the 11-16 Pluto ladder failing "block 0 good, the rest half wrong". Their FIFO interrupt
times were regular, and the bits after the failure were the sent bits one place early: the chip's
bit clock dropped a bit, not the reads. The bench (11-17 09:28-10:05Z, docs/k6-fsk24.md "F/1800 at
the upload's own deviation") found why: the test audio is half scale, so `--dev 3700` sent about
1850 Hz, under the 3000 Hz an upload uses. At 3000 Hz the same packets go 23/24 and F/1800 is about
as sensitive as D/2400 (all good to -88 dBm, two thirds at -94), not 12-15 dB worse as ladder 2
said. `pluto_bench.py --peak HZ` now scales a test wav by its own peak.

The UV-380 to K6 misses (1 in 7, 11-16) are different again: scattered bit errors the FEC mostly
corrects (res=1), no slips.

## Ideas not built

- **Slip repair.** Near the edge F/1800 (and D/2400, by the 2400 thread's logs) fails by a dropped
  bit more than by bit errors. On a CRC failure, try putting a bit back at each place and check the
  CRC again. About 500 distinct candidates a packet against a 16-bit CRC is a 1 % false accept, so it
  needs the packet's header (magic, transfer id, chunk number) as a second check. Maybe 1-2 dB.
- **Count the UV-380 listener's ring overruns** on its `heard` lines.
