# The K6's chip FSK modem at 2400 baud

*2026-11-16. Code: `c/k6/mod/fsk24rx.c`, `modem/fsk24.py`, `tests/test_k6_fsk24.py`.*

## Why look again

The FD6818 (BK4819 numbering) has a 1200/2400 baud FSK modem. REG_72 is the bit rate (baud x
10.32444, up to about 6,347), and the register list names "FSK 2.4K" as RX bandwidth bits 4 of
REG_58. What we had found so far:

- **Transmit works at 2400.** The K6's CAL burst in plain FSK at 2400 baud decoded on the
  IC-7100's recording (2026-11-04, `tests/data/k6/air/k6cal-2400-20261104T0928.flac`).
- **Receive never did.** The pager's D/2400 and F/2400 (FFSK 1200/2400) and POCSAG at 2400 baud
  never synced (docs/k6-pager.md, docs/k6-receiver.md, docs/k6-pocsag.md).

Every one of those receive tries came from the IC-7100's USB audio in FM-D. The UV-380 work
fitted that transmit path as pre-emphasis with a low cut near 300 Hz and a top near 3 kHz
(docs/uv380-c6000-modem.md, C2), and pre-equalising against it took the C6000's DMR from no sync
to 23 of 23 blocks. Plain FSK carries its data down to DC, so the low cut hurts it most; D at
1200 got through only after REG_2B's DC cancellation went off (7/28 to 31/31, 2026-11-05).

The FerriteFW author's RT-950 Pro audio note (2026-10-27; FerriteFW is PolyForm Noncommercial, so
facts only, no code) measured the BK4829, the same family, receiving its own modem's 2400 baud
FSK 50 of 50 from a flat transmitter (a bladeRF), with the receive 300 Hz high-pass (REG_2B<10>)
off for the NRZ modes. The same note found the 4800 and 9600 rates give no sync, and the FFSK
modes (1200/1800, 1200/2400) not decoded yet. So the open question is ours: the far end, and the
K6's receive filters.

## The test

`fsk24rx` (level 2, receive only, 2.1 KB) listens on 440.850 in packet mode and reads each
64-byte test packet by time. Five settings of 30 s, REG_2B's DC cancellation off in all:

| setting | REG_58 | baud | REG_2B bits | what |
| --- | --- | --- | --- | --- |
| 0 | 00e1 | 1200 | | the pager's D/1200 recipe: the control |
| 1 | 00e9 | 2400 | | FSK 2.4K bandwidth, filters as found |
| 2 | 00e9 | 2400 | 0400 | RX 300 Hz high-pass off (the RT-950 note's data set-up) |
| 3 | 00e9 | 2400 | 0700 | high-pass, 3 kHz low-pass and de-emphasis off |
| 4 | 00e1 | 2400 | 0400 | FSK 1.2K bandwidth, high-pass off |

The bench signal (`python3 -m modem.fsk24 wav`) sends rounds of eight blocks of four packets,
each block one variant: the baud, a pre-equaliser against a model of the IC-7100's transmit path
(`em`: pre-emphasis at 300 Hz undone; `hp`: a second-order 300 Hz high-pass undone, gain capped
20 to 30 dB above 1 kHz; `lp`: the 3 kHz top undone) and the level:

| variant | baud | pre-equaliser | level |
| --- | --- | --- | --- |
| 0 | 1200 | none | 0.25 |
| 1 | 1200 | em hp (30 dB) | 0.3 |
| 2 | 2400 | none | 0.25 |
| 3 | 2400 | em hp (20 dB) | 0.15 |
| 4 | 2400 | em hp (20 dB) | 0.3 |
| 5 | 2400 | em hp (30 dB) | 0.2 |
| 6 | 2400 | em hp lp (24 dB) | 0.25 |
| 7 | 2400 | em | 0.25 |

A packet is the pager's framing (16 bytes of 0x55, sync 07 6B 3E 36) around 0xF2, the variant, a
sequence number, 59 bytes of PN9 and a CRC-16/CCITT-FALSE. The module says each packet's variant,
whether its CRC is right, which way up it came and how many of the 472 PN9 bits are wrong, so a
setting that nearly reads shows as a few bits wrong, not just a fail.

Through the model channel our own demodulator reads every pre-equalised variant but 7 and neither
plain one, and through a flat channel only the plain ones (`python3 -m modem.fsk24 sim`). The model
is harsher than the real path (the chip read plain D/1200 31 of 31) and our demodulator isn't the
chip: the bench says.

## Bench

Keys: `/mnt/project-files/k6-fsk24/bench-keys.md`. Report: `python3 -m modem.fsk24 report LOG`.

## Run 1 (2026-11-16 19:02-19:06Z): no 2400 baud sync at all

K6 on main 1287138, `fsk24rx` at cb7bce2, IC-7100 in FM-D at RF power 0 and USB MOD 30 %, RSSI
-22 dBm. The first upload lost chunks 1, 2 and 6; the resend completed the transfer (the K6 keeps
a partial one), the module ran from 19:02:48Z and the signal went out 19:03:09-19:06:10Z, so all
five settings fell inside it. Files: `/mnt/project-files/k6-fsk24/run1/`.

| setting | REG_58 | baud | REG_2B bits | packets | CRC right |
| --- | --- | --- | --- | --- | --- |
| 0 | 00e1 | 1200 | | 6 | 2 |
| 1 | 00e9 | 2400 | | 0 | 0 |
| 2 | 00e9 | 2400 | 0400 | 0 | 0 |
| 3 | 00e9 | 2400 | 0700 | 0 | 0 |
| 4 | 00e1 | 2400 | 0400 | 0 | 0 |

- **2400 baud: not one sync** in four settings over two minutes, across all six 2400 variants
  (plain, four pre-equalisers, levels 0.15 to 0.4: about 200 packets on air in those two
  minutes). The chip's 32-bit sync never matched, so the high-pass, low-pass and de-emphasis
  bits (the RT-950 note's data set-up) are not what stands in the way.
- **1200 baud, the control: it still reads, as before.** The plain variant read 2 of 3 with 0 bits
  wrong; the third had its first 178 bits right but one, then lost the bit clock (85 wrong), the
  "errors grow along each packet" of 2026-11-05. The pre-equalised 1200 variant read nothing: three
  syncs came out upside down with about half the bits wrong. So the IC-7100's real low cut is far
  gentler than the model's second-order 300 Hz one, and undoing the model's breaks it.
- **What that leaves.** The FD6818's plain FSK receiver doesn't find 2400 baud in what the
  IC-7100's USB audio makes of it, with or without pre-equalising against the model we have. The
  RT-950 note's clean 2400 receive had a flat transmitter (a bladeRF). The open test is the same
  here: a flat FM transmitter as the far end. The RT-950 Pro running FerriteFW's modem test (a
  BK4829 sending its own chip's 2400 baud FSK) would be one; that needs the RT-950's programming
  cable (due about 11-30) and Jeff's choice to load FerriteFW. Until then, 2400 baud on the K6 is
  transmit only: the K6 to the IC-7100 (and the PC behind it).

## Run 2 (2026-11-16 19:24-19:29Z): 2400 baud syncs, bit-perfect in places

The same module (serial 1794856342), the K6 on main 1287138 and the IC-7100 in FM-D at RF power 0,
now sending run 2's signal: each bit as RRC +-3 symbol pairs through the equaliser the C6000
measured. The upload lost chunks 1-2 and the resend completed it. `fsk24 start` came at
19:25:32Z and the signal went out 19:26:10-19:28:57Z, so setting 0 missed its first 8 s.
Files: `/mnt/project-files/k6-fsk24/run2/summary.md`; log and report on hamlet in
`~/blowfang-work/runs/fsk24-2-20261116/`.

| setting | REG_58 | baud | REG_2B bits | packets | read as v8 | PN9 bits wrong (median of 472) |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 00e1 | 1200 | | 3 | 0 | (3 upside down junk) |
| 1 | 00e9 | 2400 | | 8 | 6 | 235 |
| 2 | 00e9 | 2400 | 0400 | 11 | 8 | 104 |
| 3 | 00e9 | 2400 | 0700 | 12 | 6 | 103 |
| 4 | 00e1 | 2400 | 0400 | 11 | 7 (and 3 of v9) | 98 |

- **The chip's 2400 baud receiver works through the IC-7100 once the audio is right:** 42
  packets, against none in run 1. v8 (the equaliser at level 0.08, the lowest) read in every
  2400 setting. v9 (0.12) read only in setting 4. v10 (0.16) and v12 (the same symbols with no
  equaliser) never read, so the equaliser is what did it and a lower level is better.
- **Bit-perfect data, CRC still wrong.** Three packets had all 472 PN9 bits right (setting 2
  q=21; setting 3 q=35, read twice) and one had a single bit wrong. FIFO word 30 (bytes 60-61) is
  right in each, but word 31, the CRC, is a different wrong value every time. So the last word
  isn't reaching the module, which reads 32 words by time. At 1200 baud it did (run 1's two good
  packets). Open: the chip at 2400, or the module's timing for the last word.
- **The rest slip a bit.** Five packets are right to bit 299-305, then the same data comes out
  one or two bits late (about 100 wrong). In setting 4 the slips come later, at bits 401-406
  (about 50 wrong). They come at the same places every time, just before PN9's long runs of
  equal bits, so the bit clock slips on long runs. The likely cause is baseline wander from what
  is left of the IC-7100's low cut, not noise. Setting 2 (RX high-pass off) did better than
  setting 1 (median 104 wrong against 235).
- **The REG_2B filters matter only a little;** the transmit audio path decided it. Run 3, with a
  flat FM transmitter, should read with no slips if that's right.

## Run 3 (2026-11-16 19:54-19:58Z): 234 of 234 from a flat transmitter

The ADALM-Pluto sending plain FSK straight from IQ (`tools/pluto_bench.py fsk24`, docs/pluto-bench.md):
stock firmware v0.39, calibrated against the IC-7100 to 0.3 ppm, rubber duck on its TX port,
gain -60 dB. The K6 was on the same bench running the same module (a fresh upload, serial
1794858813, over the IC-7100, all 18 chunks first time). `fsk24 start` came at 19:54:36Z. The
Pluto sent 19:55:13-19:58:14Z: 20 rounds, 400 packets, AG7EW in MCW first and last. Setting 0
missed its first 7 s. Files on hamlet: `~/blowfang-work/runs/fsk24-3-20261116/`; summary in
`/mnt/project-files/k6-fsk24/run3/summary.md`.

| setting | REG_58 | baud | REG_2B bits | packets | CRC right | PN9 bits wrong |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 00e1 | 1200 | | 10 | 10 | 0 |
| 1 | 00e9 | 2400 | | 56 | 56 | 0 |
| 2 | 00e9 | 2400 | 0400 | 56 | 56 | 0 |
| 3 | 00e9 | 2400 | 0700 | 54 | 54 | 0 |
| 4 | 00e1 | 2400 | 0400 | 58 | 58 | 0 |

- **The FD6818 receives plain FSK at 2400 baud perfectly.** Every packet the K6 synced on was
  right, at every deviation sent (1.5, 2.2, 3.0 and 4.5 kHz), in every setting, RSSI -79 dBm.
  That includes REG_58 00e1, the 1.2K bandwidth, and REG_2B with the receive filters as found.
  The settings and the variants were not the limit.
- **Runs 1 and 2 failed because of the IC-7100's audio path.** Its pre-emphasis and low cut were
  what stood in the way, not the K6. Run 2's wrong last FIFO word and its bit slips went away
  with a flat transmitter, so they came from the signal too.
- **Packets read:** 234 of the 400 sent, which is every one the module could read. Each
  setting listens at one baud, and the rest add up. 2400 baud packets during setting 0: about
  45. 1200 baud packets during settings 1-4: about 52. The 38 s the Pluto went on sending after
  the module's 150 s ended: about 66. A 30 s setting at 2400 has room for about 52 packets, and
  settings 1-4 read 54-58.
- **So:** a K6 can take 2400 baud from any flat FM transmitter: the Pluto, an RT-950, a UV-380
  in C6000 physical layer mode. That would make uploads and messages a quarter shorter (the
  table below). It can't take 2400 from the IC-7100's USB audio.

## What 2400 baud would buy

Per XL packet the pager puts 144 bytes on air (12 of preamble, 4 of sync, 128 of packet: 120
bytes of codec frames). Against F/1800, today's default:

| | F/1800 | 2400 |
| --- | ---: | ---: |
| air time a packet | 0.64 s | 0.48 s |
| codec payload | 1,500 bit/s | 2,000 bit/s |
| 60 s of sotto 1200 (75 packets) | 48 s | 36 s |
| sotto 1200 speech per second of air | 1.25 | 1.67 |
| sotto 2400 speech per second of air | 0.63 | 0.83 (still slower than real time) |

So a third more throughput: module uploads and messages a quarter shorter, and live sotto 1200
with more room, but not live sotto 2400. Range mode gains nothing: it trades speed for
sensitivity (about 550 bit/s), and plain FSK at 2400 needs about 3 dB more signal than at 1200
(docs/k6-pager.md's link model). The coded 4FSK IF modem (PHY G) already makes 1,200 to 1,730
bit/s at 45-49 dB-Hz, where uncoded 2400 needs about 52.

## The far ends tried

- **Run 2 (done, above): the IC-7100 with the equaliser the C6000 measured.** Run 1's pre-equalisers
  came from a model, and the 1200 control showed the model is wrong. The UV-380's C6000 measured
  the real IC-7100 transmit chain from its own soft values (docs/uv380-c6000-modem.md R1-R4), and
  the equaliser it fitted took DMR to 23 of 23. That equaliser works on 4800-a-second symbols, so
  run 2 sends each bit as two +-3 symbols (four at 1200 baud), RRC shaped, through it:
  `python3 -m modem.fsk24 wav OUT.wav --run 2 --eq EQ.json` (variants 0 and 8-12: the control,
  three levels at 2400, one at 1200, and the same symbols with no equaliser). Same module.
- **Run 3 (done, above): the ADALM-Pluto as the flat far end** (Jeff, 11-16). The Pluto puts plain
  FSK straight onto the carrier from IQ: no audio path at all, the same kind of transmitter the
  RT-950 note used. `tools/pluto_bench.py fsk24` (docs/pluto-bench.md) streams it through libiio's command-line tools
  (hamlet has libiio 0.26), under the bench's PTT lock and owner check, AG7EW in MCW before and
  after, the TX LO powered down outside the stream, and only on 440.850 MHz at 2.4 MS/s with gain
  -60 dB by default (about -55 dBm into the antenna; it refuses above -30). Variants 13-17, a
  round of 9.3 s, 20 rounds (186 s) covering the module's five settings:

  | variant | baud | peak deviation |
  | --- | --- | --- |
  | 13 | 1200 | 2.2 kHz (the control) |
  | 14 | 2400 | 2.2 kHz |
  | 15 | 2400 | 1.5 kHz |
  | 16 | 2400 | 3.0 kHz |
  | 17 | 2400 | 4.5 kHz (POCSAG's) |

  Gaussian NRZ (BT 0.5), the same module, uploaded over the IC-7100 as before. The Pluto's crystal
  is +-25 ppm (up to 11 kHz off at 440 MHz), so first `pluto_bench.py rx` while `k6_bench.py id` keys
  the IC-7100 (its TCXO the reference) and `pluto_bench.py cal --save` on that capture; then a rehearsal with `--rf-off` to check USB keeps up at 2.4 MS/s. If 2400
  reads here, the K6's receiver is fine and the IC-7100's audio path is what stood in the way.
- **The UV-380 as the flat far end** (next, the UV-380 thread's experiment 3 in
  docs/uv380-c4fm-foundry.md). RadioFoundryC4FM now sends whole C4FM calls through an HR_C7000's
  physical layer mode, decoded by Yaesu radios. That proves the chain we'd need: enter the mode,
  refill both TX RAM halves every 30 ms slot, fill with 0x77 when the queue runs dry, key up once,
  scale the deviation with 0x45/0x46. On the C6000, 0x10 = 0x66 (slotted, proven by RadioFoundry)
  or 0x02 (continuous) sends 288 raw bits a slot at 4800 symbols a second. Each of our bits as
  two equal outer symbols is 2400 baud plain FSK at +-1944 Hz, nine bytes a slot; four symbols
  a bit gives 1200. Run 3 says the K6 reads that: +-1944 Hz is inside the 1.5-4.5 kHz it read
  perfectly. This module is the K6 end, unchanged. It reports packets that come in upside down
  (`i=1`), so the symbol polarity, still to be checked, shows up either way. Still open on the
  UV-380 side: the 3.58 kHz untrimmed transmit offset (the Pluto can measure it), the 5 s keying
  cap, and the watchdog resets after 1 W key-ups (PR #127). The MCU's PWM into the C6000's line
  input isn't flat (pre-emphasis, a cut below 300 Hz), and the K6 read none of the UV-380's FFSK
  that way (11-14). The physical layer route skips that path.

## The sensitivity sweep (staged 11-16)

How weak can each rate be? `c/k6/mod/fsk24rx_sweep.c` runs fsk24rx in 36 slots of 12 s on a
fixed grid (`F24_ABS`): D/2400 (run 3's setting 2: REG_58 00e9, REG_2B +0400), F/1800 and F/1200
(the pager's recipes) in turn, three slots a level step. `tools/pluto_bench.py fsk24 --sweep
--follow LOG --gain -70` waits for the K6 logger's `fsk24 start` line and sends each slot its
rate's test packets (`modem.fsk24.sweep_track`), one step lower every three slots: 0 to -44 dB
in 4 dB steps, set digitally in the IQ. D/2400 goes flat at 2.2 kHz peak deviation; the FFSK
tones go 750 us pre-emphasised at 3.0 kHz peak, as an ordinary HT sends them (the K6 receives
with its de-emphasis on). The signal rides 250 kHz above the Pluto's TX LO, so the LO's leak
(440.600) and the IQ image (440.350) stay off the channel as the signal drops. Carrier only for
1.5 s at a slot's start and 1.0 s at its end, so a few hundred ms of slip between the K6's clock
and hamlet's don't matter. `python3 -m modem.fsk24 report --sweep LOG` gives packets right of
sent per step and rate, with the K6's RSSI.

## Plans revised after run 3 and RadioFoundryC4FM (11-16)

- **No more 2400 baud tries through the IC-7100's audio.** Run 2's equaliser got syncs but not
  clean data, and run 3 shows the chip was never the problem. 1200 and 1800 baud uploads over the
  IC-7100 go on as they are.
- **The K6's receive settings stay as found.** Run 3 read 2400 with REG_58 00e1 or 00e9 and REG_2B
  as found or with the filters off. RadioFoundry's FD6818 tables (MIT) show the chip's DMR set
  differs from its FM set in three registers: REG_2B = 0x0700, REG_7E = 0xB03F (fixed AGC) and
  REG_43 = 0x4048 (a wider RF filter). Those become variables only in a sensitivity run.
- **Next K6 test: sensitivity, with the Pluto.** Step the Pluto's gain down, from -60 to -90 dB
  and then with a pad, sending 1200 and 2400 alternately. That finds where each stops reading,
  and so checks the link model's "2400 needs about 3 dB more" (docs/k6-pager.md). Then repeat at
  the weakest level that still reads, with REG_43 = 0x4048 and REG_7E = 0xB03F, to see whether the
  DMR set's RF filter and fixed AGC buy margin. It needs `fsk24rx` with a REG_43/REG_7E column in
  its settings table, and pluto_bench's fsk24 with a gain per round.
- **What 2400 would take in use:** a 2400 baud PHY in the K6 image for uploads and messages, and a
  flat sender: the Pluto on the bench, or the UV-380 or RT-950 in the field. That's an image
  change, so it waits for Jeff's call on the next flash batch. The throughput is in the table above.
