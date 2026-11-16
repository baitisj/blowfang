# RT-950 Pro: characteristics and limitations

What the Radtel RT-950 Pro can and can't do for this project, with no hardware mod. **M** = measured
by us (date, run), **F** = measured by the FerriteFW author (see Sources), **I** = inferred,
**D** = from documentation. Keep this current: a new finding goes here as well as in its own doc.
Plans live in [rt950-plan.md](rt950-plan.md), the backup in [rt950-backup.md](rt950-backup.md).

## Sources

- **F:** "RT-950 Pro audio limits: what data the radio can carry", Ferrite bench, 2026-10-27, by
  Alex Harvey (FerriteFW, https://ferritefw.dev), shared with Jeff 2026-11-16. One radio, its UHF
  chip, 446.475 MHz, wide FM, a bladeRF as the far end, transmit mostly with the PA off. Ferrite is
  PolyForm Noncommercial: we take the measured facts and register findings below, never its code,
  filters or equaliser coefficients.
- **M:** our own runs on Jeff's radio (software Ver0.29, hardware RT950Pro-Ver1.00).

## The radio

| | | |
| --- | --- | --- |
| Firmware, hardware | About menu: Software Ver0.29, Hardware RT950Pro-Ver1.00 | M, 11-16 |
| Connectors | no K-plug or 3.5 mm jack: a 14-pad pogo side port with a screw lock; the USB-C port is on the battery pack and only charges | M, 11-16 |
| Programming | stock CPS over Bluetooth answers on module UART selector 2 (115200), not 0 or 5; ~73 B/s at 32-byte reads (64 KB in ~15 min); a second handshake within ~1 s of a session gets no ACK | M, 11-16 Stage A |
| CPS identity | 'F' reply = BCD band limits 136-174, 400-520, 200-260 MHz plus 01 03 30 04; 'M' = "RT-950"; no version query | M, 11-16 |
| Bluetooth | one session at a time: a second command drops the first | F |

## Transmit audio: getting audio in

The MCU's DAC feeds the BK4829's audio input through a small RC network on the board. Everything we
transmit as audio (sotto, modems, tones) takes this path; there is no line input. (F)

| | | |
| --- | --- | --- |
| As the stock data set-up leaves it | flat within 1 dB 400 Hz-2.5 kHz, -3 dB at 185 Hz and 2.8 kHz, then a wall: 3150 Hz -7 dB, 4 kHz -56 dB | F |
| What makes the wall | a fixed chip filter: raising the TX low-pass (REG_43<8:6>) or clearing every REG_2B TX filter bit doesn't move it | F |
| REG_47 bit 0 ("bypass all AF TX filters") | removes the wall; what remains is the RC network: first-order roll-off near 170 Hz, about 6 dB an octave across the band, and a near-constant -90 deg phase | F |
| Bypass level loss | about -6.8 dB at 1 kHz; won back with REG_7D (mic sensitivity) and REG_40 (deviation). REG_40 is far from linear: deviation ~ REG_40^2.76 | F |
| With bypass and an equaliser in the DSP | flat within about 1 dB 50 Hz-5 kHz at full deviation, and to 8 kHz at reduced level (a full-level 8 kHz sine clips the DAC) | F |
| Level-only equalising isn't enough | correcting magnitude alone left G3RUH 9600 at 23 % bit errors; correcting phase as well brought it to 0.3 % | F |
| Soft limiter | REG_2F<7:5>, off with the bypass | F |
| Narrow FM | worse both ways; the walls stay | F |
| DTMF trap | REG_70 holds both DTMF detection and the tone generator: detection left armed at key-up sends 1000 and 3300 Hz tones on air at 1.2-1.5 kHz deviation. Disarm before every key-up | F |

Transmit bit errors, plain slicer, radio to bladeRF (F):

| Mode | As set | Bypass + equaliser |
| --- | --- | --- |
| FSK 1200 | 13 % | 0 % |
| FSK 2400 | 3.8 % | 0.6 % (level-only design) |
| GMSK 4800 | 2.8 % | 0.45 % |
| FSK 4800 / 9600, raised cosine | - | 0.23 % / 0.34 % |
| G3RUH 9600 | 36 % | 0.30 % |

## Receive audio

| | | |
| --- | --- | --- |
| As set (REG_2B 0x0000) | steep 300 Hz high-pass (-50 dB at 100 Hz) and de-emphasis (-8.5 dB at 2.5 kHz); a wall above 3.1 kHz | F |
| REG_2B 0x0700 (HPF, 3 kHz LPF and de-emphasis off) | flat 800 Hz-2.5 kHz; below that the board coupling rolls off from about 600 Hz, which no register moves; the 3.1 kHz wall stays (4 kHz -33 dB) | F |
| Route 9 (REG_47<11:8> = 9, Beken's RF_EnterBypass) plus 0x0700 | lifts the top: within 2.5 dB 800 Hz-3.2 kHz, then a smooth slope (3150 Hz -2.4, 4 kHz -5.4, 5 kHz -10.9, 6.3 kHz -23.7 dB). Unfiltered on the speaker too, so for a muted data slot | F |
| Firmware fights | the stock-style FM tune rewrites REG_2B and REG_43 on every retune, and audio routing rewrites REG_47; data set-ups must be held by the firmware, not poked from a PC | F |
| 9600 receive | not possible today: about -9 dB at 4.8 kHz and -24 dB at 6.3 kHz even with route 9, plus the low-end coupling | F |

Receive bit errors, bladeRF to radio, raw 12 kHz capture, plain demodulator (F):

| Mode | As set | Filters off | Route 9 |
| --- | --- | --- | --- |
| AFSK 1200 (APRS) | 0.01 % | 0.66 % | 0.98 % |
| AFSK 2400 | 17 % | 1.8 % | 0.83 % |
| FSK 1200, +-4.5 kHz | 24 % | 7.4 % (3.5 % with the coupling undone) | 7.1 % |
| FSK 2400, +-4.5 kHz | 22 % | 3.6 % | 2.6 % |
| GMSK 2400 | 22 % | 1.9 % | 2.3 % |
| GMSK 4800 | 26 % | 8.3 % | 14 % |

## The BK4829's own FSK modem

FSK 1200 and 2400 work both ways (radio to bladeRF 12/12 and 20/20 clean; bladeRF to radio 50/50,
CRC good). No sync at 4800 or 9600. Frame format: MSB first, the FIFO's low byte first on air, CRC
is CRC-16/CCITT-FALSE sent big-endian. FFSK 1200/1800 and 1200/2400 not decoded yet. (F)

## For sotto and modems

- **Audio in is solved in firmware, no solder:** DAC, REG_47<0> bypass, and a level-and-phase
  equaliser measured from the channel. Our K6 and UV-380 modems never needed phase correction;
  here a modem above 1200 baud does. (I, from F)
- sotto's PHY R (600/1200 Hz FFSK) sits inside even the stock transmit passband (400 Hz-2.5 kHz
  flat). Receive is the weak side at 600 Hz: with the voice filters off the board coupling rolls
  off from about 600 Hz, so keep the 300 Hz HPF on, or correct the coupling in the demodulator. (I)
- PLL frequency writes (the FT8/WSPR path in `radios/rt950/src/digi_tx.c`) never touch the audio
  path, so they avoid the RC network and its phase entirely. (I)
- Only one radio measured; Ferrite plans the same on a second unit. Treat the corners as
  per-radio until we measure Jeff's. (F)
