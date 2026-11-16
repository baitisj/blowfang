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
