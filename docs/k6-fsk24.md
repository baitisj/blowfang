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

## Sensitivity sweeps (11-16)

How weak can each rate be? `c/k6/mod/fsk24rx_sweep.c` runs fsk24rx in slots of 12 s on a fixed
grid (`F24_ABS`), one receive recipe a slot, the recipes in turn at each level step.
`tools/pluto_bench.py fsk24 --sweep --follow LOG --gain -70` waits for the K6 logger's
`fsk24 start` line and sends each slot its rate's test packets (`modem.fsk24.sweep_track`), the
level set digitally in the IQ. D/2400 goes flat at 2.2 kHz peak deviation; the FFSK tones go
750 us pre-emphasised at 3.0 kHz peak, as an ordinary HT sends them (the K6 receives with its
de-emphasis on). The signal rides 250 kHz above the Pluto's TX LO, so the LO's leak (440.600)
and the IQ image (440.350) stay off the channel as the signal drops. Carrier only for 1.5 s at
a slot's start and 1.0 s at its end, so a few hundred ms of slip between the K6's clock and
hamlet's don't matter. `python3 -m modem.fsk24 report --sweep LOG` gives packets right of sent
per step and recipe, with the K6's RSSI.

**Sweep 1 (20:48-20:56Z, the Pluto at gain -70, steps of 4 dB, every recipe read by time):**

| step dB | RSSI dBm | D/2400 | F/1800 | F/1200 |
| --- | --- | --- | --- | --- |
| 0 | -88 | 16/16 | 0/16 | 3/12 |
| -4 | -92 | 13/16 | 0/16 | 3/12 |
| -8 | -96 | 6/16 | 0/16 | 0/12 |
| -12 | -100 | 0/16 (9 junk) | 0/16 | 0/12 |
| -16 to -44 | | 0 | 0 | 0 |

- D/2400 read everything at -88 dBm and nothing at -100 (the K6's RSSI, not a calibrated
  level). The packets that failed had 134-161 of 472 bits wrong, a slip partway through rather
  than scattered errors, so this may be the timed reads' limit more than the chip's.
- F/1800 and F/1200 failed even at full level: reading FFSK by time doesn't work in this probe
  (the pager reads F/1800 on the chip's interrupts, and F/1800 uploads work every day). So
  sweep 1 has no margin between the rates.
- **Sweep 2 (21:08-21:16Z) tried reading on the chip's interrupts** (polled REG_0C, 3 words a
  FIFO-almost-full, the rest at RX-done). Every packet it read synced with the right header and
  then went to junk (22 of 32 words padded or repeated), and a read that never saw RX-done held
  its slot for up to 7 minutes, so the grid fell apart. It repeated only D/2400 16/16 at -88 dBm.
  The interrupt code was dropped; making a probe read FFSK like the pager isn't worth it when the
  pager itself can be measured.
- **The F/1800 ladder:** the pager's own receive path, the one uploads use every day.
  `python3 -m modem.k6 test T.wav --phy F --baud 1800 --repeat 16` (the spec's test packet,
  which the pager checks bit by bit), then `tools/pluto_bench.py steps T.wav --preemph --dev
  3700 --gains ... --out steps.json` sends it once at each TX gain (the Pluto's range ends at
  -89.75 dB), each send its own transmission with its own ID, and `python3 -m modem.fsk24
  report-steps LOG steps.json` counts the K6's `K6 PKT` lines a send. Sweep 1's D/2400 was at
  TX gain -70 with the level cut digitally, so its steps 0, -4, -8, -12 dB line up with gains
  -70, -74, -78, -82.
- **Ladder 1 (21:27-21:30Z, gains -70 to -88 in 3 dB steps, 16 packets each):** none decoded.
  Four syncs, all at -76 and all junk (about 473 bits wrong); none at -70 or -73. The same
  waveform through a model of the Pluto (IQ, FM discriminator, with or without de-emphasis)
  decodes 4/4 on the host's demodulator, so the signal is right. The likely reading: the K6's
  FFSK receiver needs far more signal than its plain FSK (D/2400 read 16/16 at -70). IC-7100
  uploads come in much stronger. Ladder 2 starts 30 dB higher (-40 to -88 in 6 dB steps).
- **Ladder 2 (21:34-21:38Z, gains -40 to -88 in 6 dB steps): F/1800 needs far more signal.**

  | TX gain dB | about dBm at the K6 | F/1800 (pager) | D/2400 (sweep 1, probe) |
  | --- | --- | --- | --- |
  | -40 | -58 | 12/16 | |
  | -46 | -64 | 11/16 | |
  | -52 | -70 | 12/16 | |
  | -58 | -76 | 11/16 | |
  | -64 | -82 | 1/16 | |
  | -70 | -88 | 2/16 | 16/16 |
  | -74 | -92 | | 13/16 |
  | -76 | -94 | 0/16 (7 junk) | |
  | -78 | -96 | | 6/16 |
  | -82 and below | -100 | 0 | 0 |

  (dBm: the K6's RSSI at gain -70 was -88, and the rest are scaled from it, so they're not
  calibrated levels.) F/1800 falls off between -58 and -64. D/2400 is still perfect at -70 and
  half there at -78. **So from a flat transmitter, D/2400 holds on 12-15 dB lower than F/1800.**
  Even strong, about a quarter of F/1800 packets failed with block 0 perfect and the rest about
  half wrong: a slip after the first 16 bytes, at every level. The faster rate costs no range
  here; it gains it. Caveats: the FFSK went pre-emphasised at about 3 kHz peak, and the IC-7100
  (whose uploads also lose chunks) wasn't compared at a known level.


- **Sweep 3 (11-17 02:47-02:54Z; Jeff 11-16 21:54Z: "Should we test D/1800 and D/1200 then?"):**
  plain FSK at each rate, so a flat sender's fallback can step down within D. Slots in turn:
  D/2400 (00e9), D/1800 with the 2.4K and with the 1.2K RX bandwidth (00e9, 00e1; REG_72
  0x4898), and D/1200 (00e1), all REG_2B +0400, read by time; 2.2 kHz peak deviation; level cut
  0 to -21 dB in 3 dB steps, 32 slots, 6.4 minutes. The module went up over the IC-7100 (RF power
  0); the Pluto swept at TX gain -60, 10 dB above the plan (see "What went wrong first" below).
  The module read 419 of 463 and its report came back over ARQ.

  | step dB | K6 RSSI dBm | D/2400 | D/1800 2.4K | D/1800 1.2K | D/1200 |
  | --- | --- | --- | --- | --- | --- |
  | 0 | -79 | 16/16 | 16/16 | 16/16 | 12/12 |
  | -3 | -82 | 16/16 | 16/16 | 16/16 | 12/12 |
  | -6 | -85 | 16/16 | 16/16 | 16/16 | 12/12 |
  | -9 | -88 | 16/16 | 16/16 | 16/16 | 12/12 |
  | -12 | -90 | 16/16 | 16/16 | 16/16 | 12/12 |
  | -15 | -93 | 16/16 | 16/16 | 16/16 | 12/12 |
  | -18 | -96 | 14/16 | 14/16 | 16/16 | 11/12 |
  | -21 | -99 | 1/16 | 1/16 | 2/16 | 0/12 |

  **Every D rate is perfect to -93 dBm and all four fall off together between -96 and -99.**
  D/1800 on the 1.2K filter is a hair better at -96 (16/16); D/1200 is no better than D/2400.
  So on this chip a slower plain-FSK rate buys about 1 dB at most: the rate ladder's fallback
  from D/2400 is for timing and slips, not range. D/2400 also did better than in sweep 1 (14/16
  at -96 against 6/16), most likely because the UV-380 was off this time.
  The Pluto's output matches the afternoon: -79 dBm at gain -60 against sweep 1's -88 at -70.
  Files: hamlet ~/blowfang-work/runs/fsk24-sweep3-20261116/ (report_s3ic.txt, k6log-s3ic.txt).

- **What went wrong first (11-16 22:45Z to 11-17 02:45Z), so it isn't repeated:**
  - From about 22:45Z, every packet to the K6 failed from both the Pluto and the IC-7100. The
    UV-380 (another thread's c6phy tests, keyed on 440.850 next to the K6) was the cause. Once
    Jeff switched it off at 01:25Z, IC-7100 test packets read with zero bit errors (raw view, key
    6: `BE 0 ... 00000000`). A power-cycle, crystal drift (the K6 transmits +170 Hz, 0.38 ppm,
    measured by the Pluto against TV pilots) and offsets of ±15 kHz were all ruled out on the way.
    Hence the bench channel plan (docs/hamlet-channels.md): the UV-380 moves to 441.850.
  - The Pluto's duck turned vertical at 01:11Z was cross-polarised with the K6: 6-10 dB lost
    until it went back. Keep the bench antennas fixed between runs.
  - `k6_upload --via pluto` sent about 1850 Hz peak deviation, 4 dB under the F/1800 ladders, and
    on the weaker path every chunk failed. Fixed: 3 kHz peak, `--pluto-peak` (blowfang PR #141).
  - The K6 and Pluto tests at 02:06-02:09Z were void: the K6 was in another room.
  - Killing k6_upload during its closing CW ID left the IC-7100 keyed for about 40 s at RF power
    0 (02:54:37-02:55:17Z). Bench scripts now wait for the tool to exit and force PTT off; a
    SIGTERM handler in k6_bench's keying unkeys too.

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

## 2400 baud in the image: the rate ladder (11-16, not flashed yet)

Jeff (11-16 20:31Z): "Please plan for 2400 baud mode in the K6 image. We can retain backwards
compatibility for lower rates based on link quality or error rates." The plan is the doc
[K6 2400 baud mode: plan](https://claude.ai/artifact/2KyfdA4YJn8hYfyZjobG6r); this is its image
step (step 5), built ahead of the bench steps 1-3 because the bench was busy. The policy is
`c/k6/k6_rate.c` (host tests `tests/test_k6_rate.py`); the radio side is in the Aura patch
(`src/app/pager.rs`).

| rung | modulation | REG_58 | REG_72 | REG_2B | XL payload | who can send it |
| --- | --- | --- | --- | --- | --- | --- |
| D/2400 | plain 2-FSK | 00e9 | 60cb | high-pass off (+0400) | 2,000 bit/s | flat FM transmitters: the Pluto now; the UV-380's raw mode and the RT-950 later |
| F/1800 (default) | FFSK 1200/1800 | 3f01 | 4898 | as found | 1,500 bit/s | anyone, the IC-7100 included |
| F/1200 | FFSK 1200/1800 | 3fe3 | 3065 (a `--reg 72=3065` write) | as found | 1,000 bit/s | anyone |

D/2400 is run 3's setting 2, the recipe the sweep uses for 2400. The TX gain at D/2400 is the
old PHY D's 0x00E0 (the CAL burst on PTT goes out on the rung in force, which is how step 3 checks
the K6's own D/2400 on the IC-7100).

**What the K6 does.**

- It boots, idles and comes back to VOICE MSG on F/1800, so every old sender works as before.
- It advertises `rates=F12,F18,D24` on every `K6 CFG` line (the REPORT action prints one), and
  `phy=D baud=2400` there while it is on D/2400. The screen's second line starts with D or F.
- A control record with **phy=D and baud=2400 together** (`python -m modem.k6 ctl OUT.wav --phy F
  --set phy=D baud=2400`, sent at F/1800) moves it to D/2400. `phy=F` or `baud=1200` brings it back.
  A lone `baud=2400`, the old ask for the FFSK 1200/2400 variants (none ever synced; they and
  `var=` are gone), changes nothing, so an old sender can't leave the K6 deaf.
- It drops back to F/1800 by itself (`K6 RATE D24>F18 why=... n= bad=` and a CFG line):
  - `why=revert`: no good packet within 10 s of the switch (the old revert's 60 s is too long a
    deafness for a rate change);
  - `why=crc`: 3 of the last 8 synced packets at D/2400 failed their CRC;
  - `why=quiet`: 30 s with no good packet after the last one (a session's end, so the IC-7100
    can always reach it again).
  It never steps up by itself: only the sender knows its transmit path is flat.
- ARQ reports go at F/1800 on every rung (the plan's open question 2, its recommendation: they
  are short and every sender hears F/1800). Their status byte 9 carries the ladder above the
  state (k6_mod.h `KM_RATE_*`): this image takes D/2400, the K6 is on it now, and why it last fell
  back. `modem.k6mod.parse_report` gives `rates`, `rung` and `fell`; old images read as
  `rates=F12,F18`, so a new sender never asks them.
- D/2400 learns its own polarity from the session's first packet that checks only upside down
  (a voice packet's CRC, or a module chunk's magic and CRC; `K6 RATE D24 inv=1`), before the module
  loader looks, so an upload needs no voice packet first. F/1800's learned polarity isn't used
  there, nor changed. The FIFO byte order is the chip's and stays as learned.
- The `K6 PLAY` line gains `rung=`, `syncs=`, `crcbad=`, `rssi_min=` and `rssi_med=` (the link
  since the last PLAY line; RSSI at each packet's end, the median in 3 dB bins).

**Open, for the bench (after the flash, Jeff's batch).** The receive path at D/2400 is the image's
timed FIFO reads with 3 words of lead (`TIMED_LEAD_WORDS_2400`, set on 11-04 against the
IC-7100's audio). fsk24rx, which read 234 of 234, polls the FIFO and reads 4 words behind
instead. If the image's reads slip at 2400, `--reg 5e=0203` switches it to interrupt reads (as
F/1800 uses) for 60 s without a flash; the plan's step 2 module would settle it first. The
thresholds (3 of 8, 10 s, 30 s) are the plan's defaults until the sensitivity sweep (step 1)
measures the real margin. The image went on the radio with the backlight batch (11-17 03:27Z,
main 40a2ec9); its CFG line's `rates=` is still to be read on the bench.

**The sender's side (plan step 4; Jeff 11-17 04:27Z agreed the plan's answers).** The Pluto sends
D/2400 now; the UV-380 and RT-950 as their transmit work lands; K6 to K6 only if that becomes a
feature. K6 replies stay on F/1800. `tools/k6_upload.py --via pluto --rate auto` (not the
default for now, see the second bench run below):

- Each D/2400 send is one transmission: the F/1800 control record (`phy=D baud=2400`, three
  copies at the upload's packet size, pre-emphasised at 3 kHz peak as any F/1800 Pluto send),
  0.6 s of carrier (the K6 leaves its burst 250 ms after the last packet, then retunes), then the
  chunks at D/2400, flat, 2.2 kHz peak. The K6 falls back by itself, so every send asks again.
- It asks only once a report has shown `rates=..D24`, and only for an image of 16 XL chunks or
  more (about 1.9 KB): asking costs about 3 s a send and saves 0.3 s a chunk. First bench run
  (11-17 04:51Z, hello.c, 4 chunks, Pluto -52): D/2400 took twice first try, 0 lost, but 28 s
  wall against 24 s at F/1800. It keeps per-transmitter state in
  `$BLOWFANG_WORK/state/k6-rate.json` (`rung`, `clean`, `k6_d24`).
- Step down (the rest of that upload at F/1800, the path marked F18): a D/2400 send that brings
  no report, loses over 15 % of its chunks, or a report saying the K6 fell back on `crc` or
  `revert`. A silence after a D/2400 send steps the rate down before the packet size is switched.
  The next send after a step down opens with a D/2400 control record asking for F/1800 (unless
  the last report said the K6 is on F/1800 already): a K6 still on D/2400 hears only that.
- Step up: two clean uploads (under 5 % lost) at F/1800 mark the path D24 again. Sweep 3 showed
  a slower rate buys at most 1 dB, so the ladder is about timing faults, not range.
- `--rate F18` never asks, `--rate D24` always does (Pluto only); `--via ic7100` is F/1800.

**Second bench run (11-17 05:20-05:29Z, stockfont.c, 2,961 B, 25 XL chunks, K6 woken from
EXPLORE by the Pluto).** Long uploads at D/2400 lose most of their chunks, and the losses shrink
as the signal gets weaker:

| step | Pluto gain | sends | K6 | wall |
|---|---|---|---|---|
| 1 | -52 | F/1800 | 25/25, ran | 71 s |
| 2 | -52 | D/2400, then F/1800 | 3/25 at D/2400, fell back (crc); 25/25 after the F/1800 send | 116 s |
| 3 | -66 | D/2400, then F/1800 | 11/25 at D/2400, fell back (crc); 25/25 after | 115 s |
| 4 | -66 | F/1800 | 25/25, ran | 71 s |
| 5 | -78 | D/2400, D/2400 (no report), F/1800 | 23/25 at D/2400; 25/25 after the F/1800 send | 203 s |

The four-chunk hello.c went clean at D/2400 at -52 twice (04:51Z), so the losses come with long
bursts. Sweep 3's fsk24rx module (polled FIFO reads) was perfect from -60 to -81, so the
suspects are the image's own D/2400 receive: the timed reads (3 words of lead, set against the
IC-7100 on 11-04) slipping over a long burst, or gain/AGC on a strong signal. Next: the same
upload with `--reg 5e=0203` (interrupt reads, 60 s) and a D/2400 gain ladder. The ladder itself
worked as designed (CRC fallback, step down, the D/2400 record asking for F/1800). Until this is
fixed, `--rate` defaults to F18.

**Found: the timed reads (11-17 08:47-08:50Z, serial log on).** The K6 woken from EXPLORE by the
Pluto (PSK31 at -30, then `K6 EX beacon ... VOICE MSG`), each step within 30 s so it stayed in
ARENA (two earlier tries, 06:21Z and 06:30Z, were lost to the K6 dropping back to EXPLORE after a
quiet minute). stockfont.c, 25 XL chunks, Pluto -66, K6 RSSI -88 both times:

- Timed reads (the image's D/2400 default, CFG `timed=1`): chunks 0-4, 7, 8, 11, 12 (9/25),
  then `K6 RATE D24>F18 why=crc n=8 bad=3`. The failed packets are junk syncs (`res=-2`,
  `be` about 495 of 1024 bits), the mark of a read that slipped, not of noise.
- After `--reg 5e=0203` (`K6 REGS ... 5e=0203 poke=1`, CFG `timed=0` at D/2400): chunks 0-24 in a
  row, 25/25 in one send, the module ran, report `on D24`.

The image now reads by interrupt (REG_5E 0x0203) on every rung, D/2400 included (the
aura-pager.patch change after this run). Until that image is on the radio, a sender can get the
same with a `--reg 5e=0203` control record before the D/2400 session; `--rate` stays F18 by
default until the flash and a level ladder at D/2400 confirm it.
