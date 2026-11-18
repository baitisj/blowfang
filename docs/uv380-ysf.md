# YSF from the UV-380: C4FM with Yaesu framing out of the HR_C6000

Jeff, 2026-11-18: "Work on C4FM-style 4FSK next with Yaesu framing." This builds on M17 from the
same chip mode (docs/uv380-m17-scope.md, on air 11-17 with 0 bits wrong) and points at the
project's first goal: open data and voice that reach the FTM-300D.

## What it is

The HR_C6000's physical layer mode (`0x10 = 0x66`, docs/uv380-c6phy.md) sends TX RAM as 4FSK at
4800 symbols a second, one dibit a symbol, `01 +3, 00 +1, 10 -1, 11 -3`, with +3 above the
carrier. YSF uses that rate, that map and the same RRC roll-off (0.2), so a YSF transmission's
bytes go to the chip unchanged, as M17's did. Only two things differ from M17:

| | M17 | YSF |
|---|---|---|
| outer deviation | 2.4 kHz | 2.7 kHz (inner 0.9 kHz) |
| TX_DEV (percent of the DMR pair, 0x45/0x46) | 123 (2300 Hz measured) | 144 (about 2700 Hz expected) |
| frame | 40 ms, 48 bytes | 100 ms, 120 bytes |
| tail | EOT `55 5D` | `77` idle after the terminator |

The 2.7 kHz figure agrees with RadioFoundry's C4FM port, which scales the same two-point
registers by 1.39 from the DMR setting (docs/uv380-c4fm-foundry.md). The decoder prints the outer
level it measured, so the first on-air run tells us whether 144 is right.

## Frames (modem/ysf.py)

Written from the public YSF frame layout; sotto's earlier modem/ysf.py (MIT, the FTM-300D work)
was the start. No Yaesu code or firmware is used. GPL and ISC open-source decoders were read for
facts only (field order, table values, whitening, the AMBE+2 silence code), nothing copied.

    sync D4 71 C9 63 4D (40 bits) | FICH (200 bits) | 5 sections x 144 bits

- **FICH**: FI, CS, CM, BN, BT, FN, FT, Dev, MR, VoIP, DT, SQL, SQ in 4 bytes, CRC-16 (CCITT,
  init 0, inverted), 4 x Golay(24,12), K=5 rate 1/2 code, 5 x 20 dibit interleaver.
- **Header / terminator**: CSD1 (dest + src, 10 bytes each) and CSD2 (downlink + uplink),
  whitened (PN9, seed 0x1C9), CRC, convolutional code, 9 x 20 dibit interleaver.
- **DN (V/D mode 2, DT 2)**: per section 40 DCH bits then 104 VCH bits. The DCH carries 10 bytes a
  frame: FN 0 dest, 1 src, 2 downlink, 3 uplink, 4 and 5 remarks, 6 and 7 data. Each VCH is one
  49-bit AMBE+2 frame (27 bits sent three times, 22 once, a pad bit), whitened, 26 x 4 interleaver.
- **Data FR (DT 1)**: two CSD-coded 20-byte blocks a frame (FN 0 CSD1 and CSD2, FN 1 CSD3 and the
  first data block, then two data blocks a frame): 400 bytes a second of payload.

Plans (each fits one 5 s key-up: a 24-byte preamble and 39 frames, 3.92 s):

- `dn`: header, 37 DN frames of AMBE+2 silence (b0 = 124) with AG7EW in the data channel,
  terminator. FT 6, dest ALL, DG-ID 0. What a Fusion radio should show as a call from AG7EW.
- `data`: header, 37 data FR frames carrying 59 numbered blocks `NN AG7EW YSF DATA`, terminator.

`python3 -m modem.ysf plan dn` decodes a plan from its bytes; `sim` runs it through the chip's
shape with noise; `decode CAP.cf32` reads a Pluto capture (frames, FICH, callsigns, DN silence
frames, data blocks, outer level and the four level means).

## Module and bench step

`firmware/uv380-openrtx/mod/c6ysftx.c` is c6m17tx.c with YSF's bytes (`ysftx_PLAN.h`, written by
`python3 -m modem.ysf header PLAN OUT.h`), TX_DEV 144 and `77` idle. Everything that made M17 work
is kept: tx_tone's Morse ident AG7EW first, the image's transmit guard, 441.850 or 440.850 only,
idle-half writes at each 30 ms slot edge, nothing said over USB while keyed, registers restored
350 ms before key-down. `c6ysftx_dn.c` and `c6ysftx_data.c` transmit; `c6ysftx.c` is dry.

The bench runner's `uv380.ysf` step (`dn` or `data`) sends one, under the same power-0 read-back
as `uv380.m17`. A job, as the M17 runs did: Pluto rx in the background at 441.900 MHz, UV-380
`listen 0`, `power 0`, `zero 2`, `trim -580` (the FM trim 6100 less the raw mode's 6680 offset),
the send, then `trim 6100` back.

## Next

1. On air at 0 W into the Pluto: every FICH and data block good, outer level near 2700 Hz.
2. FTM-300D (needs Jeff): DN at 0 W, the radio on 441.850 in DN or AMS. Pass = it shows AG7EW
   and opens with silence. If it ignores the call, try FT 7 and the other DCH fill choices
   (`tx_dn(ft=...)`), then data FR.
3. A second opinion before the FTM-300D: an independent YSF decoder (dsd-fme reads discriminator
   audio) on the Pluto capture. Building it on hamlet needs Jeff's OK.
4. AMBE+2 tone frames (b0 126/127) would make the FTM-300D beep, for example an AG7EW Morse ident
   in tones that you can hear from its speaker. The tone frame layout in the open decoders is still
   tentative, so it waits until a radio has taken the silence frames.
5. Data FR as a sotto carrier: 400 bytes a second fits sotto 1200 three times over, with the
   framing's own FEC. The FTM-300D won't play it, but any C4FM receiver with a discriminator can.
