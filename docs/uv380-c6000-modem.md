# The UV-380's HR_C6000 as a modem for sotto

Jeff's 11-14 to-do: get sotto onto the MD-UV380's DMR baseband chip, either inside DMR data
bursts or with the chip as a general 4FSK modem. This page collects what the chip offers, where
each fact comes from, and the bench plan. Started 2026-11-16.

## Sources and licences

| Source | Licence | Use here |
|---|---|---|
| RadioFoundry `docs/spec/hrc6000-registers.md`, `modem-hrc6000.md`, `hardware-dm1701.md`, `hardware-mduv380.md` (radiofoundry.org) | MIT, written clean room from the vendor manual and SDR bench work | the main reference: register meanings, init and DMR sequences, pins, calibration layout. Facts and values cited by section in the module sources |
| OpenRTX `platform/drivers/baseband/HR_C6000_UV3x0.cpp`, `AT1846S_UV3x0.cpp`, `nvmem_MDx.c` | GPL-3.0 (our image is OpenRTX plus `uv380-blowfang.patch`) | what the image leaves in the chip (FM and M17 register values), the SPI and I2C drivers the modules share with |
| OpenGD77 (github.com/open-ham/OpenGD77) | GPL-2.0 | read for facts only: no code copied into the MIT modules. Its `firmware/source/hardware/HR-C6000.c` runs the same layer 2 flow RadioFoundry documents (0x10 = 0x6E, 0x40 = 0xC3 / 0xE3, 0x41 per slot, 0x50 data type, payload in page 2). Its `docs/data_sheets/C6000/` holds the vendor HR_C6000 manual (Chinese original, English translations, G4EML and VK3KYY register notes): a fuller manual than the partial datasheet we knew of. Not redistributed here |

## What the chip can carry

The HR_C6000 does the DMR physical and data link layers in hardware: 4FSK at 4800 symbols/s
(+-648 and +-1944 Hz), sync detection and slot timing, slot type, BPTC(196,96) and rate 3/4
trellis FEC, CRCs. The AT1846S does the RF; on receive it hands the C6000 its raw FM
discriminator output (the C6000 runs in "AF receive" mode, 0xA1 bit 7).

| Path | Payload | Rate | FEC | Notes |
|---|---|---|---|---|
| Rate 1/2 data bursts (DT 7), one slot, direct mode | 12 bytes / 60 ms | 1600 bit/s | BPTC(196,96) by the chip | 9 bytes + our framing = **sotto 1200 exactly in real time** (below). What c6dmrtx sends |
| Rate 3/4 data bursts (DT 8) | 18 bytes / 60 ms | 2400 bit/s | rate 3/4 trellis by the chip | sotto 1200 with room for a stronger outer code, or sotto at a higher rate |
| Rate 1 data bursts (DT 10) | 24 bytes / 60 ms | 3200 bit/s | none (the chip still does sync and slot type) | |
| User data types 1011-1111 | per 0x4B / 0x4C coding: BPTC, 3/4 trellis, BPTC72 or **none** | up to 196 bits / burst | ours | a "raw payload" burst inside DMR framing |
| Both slots (continuous, our own timing) | twice the above | 2x | | direct mode normally uses one slot in two; nothing stops a sender from filling both |
| Physical layer mode (0x10 = 0x66 slotted / 0x02 continuous) | 288 raw bits per 30 ms slot from TX RAM 0x00-0x23 / 0x24-0x47 | 9600 bit/s raw | ours | RadioFoundry 9.2 sent its own CACH frames bit exact this way. On receive the chip stores each demodulated 36-byte burst at RX RAM 0x30 (0x82 bit 0) |
| Soft values | 264 per slot, 6 bits each, RX RAM 0xA8 / 0x1C8 | | | every burst's demodulator decisions with confidence: soft decision decoding of our own codes, and an eye-quality measure |
| MSK 1200 (MPT1327-style) | 64-bit blocks | 1200 bit/s | ours | chapter 7 of the manual; the TX buffer's location is undocumented |
| FM via MOD1 offset (0x04) | an 8-bit frequency DAC | ~32k writes/s | | RadioFoundry bench: 75 Hz a step, flat to 6 kHz: MCU-made FM with no audio chain in the way (relevant to the UV-380 FSK phase-glitch problem) |

### Sotto in DMR data bursts (modem/dmrsotto.py, c/common/dmr_sotto.h)

A rate 1/2 block is 12 bytes: a kind and 5-bit sequence number, 9 payload bytes, a CRC-16. Nine
bytes every 60 ms is 1200 bit/s, sotto 1200's 6 bytes per 40 ms superframe. A message is a head
block (msg id, codec, superframe count, text block count), the ident text (`AG7EW`), the
superframes back to back, and an end block. The receiver places blocks by sequence steps from
the head, so a lost burst costs only its own 1.5 superframes (concealed by the decoder), even
across the sequence wrap. DMR's rate 1/2 blocks carry no CRC of their own, hence ours.

Because the bursts are real DMR (BS / MS data sync, slot type, CSBK preambles), any DMR
receiver locks on them: the K6's dmrrx, `sim/k6if/dmrdec.py` on an IC-7100 recording, an SDR
running DSD. Only the payload is ours.

## The RAM modules (firmware/uv380-openrtx/mod/)

The image's module table reaches only the C6000's main register page, under OpenRTX's lock.
Everything else is in headers the modules include, each sharing a bus with OpenRTX safely:

| Header | Reaches | How it shares |
|---|---|---|
| `c6spi.h` | every C6000 space: page 1 (sync pattern, CRC masks, tone table), TX RAM, RX RAM (payloads, soft values), 11-bit addresses | bit-banged on PE2-5 as OpenRTX does; a transaction runs with interrupts off and only while CS is high, else it sleeps 1 ms |
| `c6slot.h` | the chip's interrupt lines: PC0 timeslot, PC1 system, PC2 RF TX | 0.3 us pulses latched in EXTI pending bits with the NVIC interrupt left off; masked read-modify-writes only |
| `at1846.h` | AT1846S registers (DMR receive set: filters off, low frequencies passed) | OpenRTX's I2C has no lock: a transfer starts only after both lines read idle with SDA an output, interrupts off. Never writes frequency, TX or power registers |
| `w25sec.h` | the factory calibration in the W25Q128 security registers (DMR I/Q gains at 0x2030 / 0x2040) | read only: the read-security-register command (0x48) is the only one it sends |

| Module | RF | Does |
|---|---|---|
| `c6pages.c` | none | checks c6spi.h against the image's own reads, dumps every space, the DMR calibration, counts interrupt edges |
| `c6dmrrx.c` | receive | AT1846S and C6000 into DMR layer 2 receive; slot check; every synced burst's type, CRC, colour code, sync errors, FSK error, payload; soft values of the first bursts; re-arms between transmissions. `c6dmrrx_flat.c`: AT1846S left in FM (comparison). `c6dmrrx_raw.c`: physical layer mode, raw 288-bit bursts. `c6dmrrx_long.c`: 60 s, for several sends in one run |
| `c6dmrtx.c` | **none by default** (TX_DRY 1): the chip frames bursts that go nowhere | the full transmit sequence as a rehearsal: slot clock, RF TX interrupts, TX flags |
| `c6dmrtx_live.c` | the Morse ident, then 1 W, 440.850 MHz only, 4.6 s; API level 4 images only (transmit guard) | the test pattern as DMR rate 1/2 bursts, keyed through `api->tx_tone`. Only with Jeff at the radio |
| `c6dmrtx_msg.c` | as live | the radio's own message (last recording or message heard) |

The DMR transmit borrows the image's FM keying: `tx_tone` sends the Morse ident, unkeys for
100 ms, then keys at 1 W on 440.850 (refusing anything else). Once the C6000's analog TX is up
again the module switches the chip to DMR with the radio's own DMR modulation pair, sends its
bursts at the slot edges, and puts every register back 350 ms before the keying ends. The image
(API level 4) keys under a transmit guard: the STM32's independent watchdog, armed at the first
key-up and kicked by a thread of its own unless a key-up outlives its length by 1.5 s. A hung
module, a stuck thread or a frozen MCU resets the radio about 2 s later (1.4-3.8 s), which lets go
of the PA.

## Host tools

- `tools/uv380_dmrburst.py wav`: the IC-7100 test signal, repeater-style continuous DMR (lead-in
  idle, 3 CSBK preambles, a dmrsotto message in slot 1, slot 2 idle) as 48 kHz FM audio;
  `--deemph` pre-compensates a pre-emphasising transmitter.
- `tools/uv380_dmrburst.py decode`: an IC-7100 recording of the UV-380's bursts back to the
  sotto message (`--preemph` for a de-emphasised recording).
- `tools/uv380_dmrburst.py fit`: a c6dmrrx log's soft values placed in the known stream, the
  whole chain fitted as a symbol-rate FIR, and a pre-equaliser for `wav --eq`.
- `tools/uv380_dmrburst.py rx`: a c6dmrrx log's bursts counted, matched to the sent blocks and
  unpacked into the message.
- `tests/test_uv380_c6000.py`: dmrsotto round trips and losses, the C header against the Python
  byte for byte, the test signal through the decoder, the modules building position-independent,
  and that only the two `_live`/`_msg` wrappers can key.

## Bench plan

1. **c6pages** (no RF): c6spi.h matches the image (`chk diff=0`), the calibration reads sane,
   the interrupt lines are where RadioFoundry says.
2. **c6dmrrx** with the IC-7100 sending `uv380_dmrburst.py` audio at RF power 0 on 440.850 (CW ID
   by k6_bench): does the C6000 lock and decode? Plain against `--deemph` audio, and against
   `c6dmrrx_flat` (AT1846S filters on). RadioFoundry saw best DMR decoding with the carrier
   about 0.9 kHz high on UHF; the IC-7100 can be offset within the channel if needed. Then
   `c6dmrrx_raw` for the physical layer mode. No UV-380 TX in this step.
3. **c6dmrtx** dry (no RF): the slot clock and TX flags.
4. **c6dmrtx_live**, Jeff at the radio: the IC-7100 records, `uv380_dmrburst.py decode` and the
   K6's dmrrx read it. Then `c6dmrtx_msg` with a real recording.

## Bench results (2026-11-16, receive only and dry; no UV-380 TX)

- **C1 c6pages.** c6spi.h reads page 4 like the image (228 same; 0x0B and 0xBB read 0x28 / 0x02
  through c6spi, the manual's PLL values, and 00 through the image's path; 0x83 is write to
  clear). Page 1 reads all zeros (write only, it seems). Calibration at 440.850: DMR I = 0x7F,
  Q = 0x36 (the dmr_i row is all 7F, dmr_q 3C 3D 39 3A 36 38 34 36 32); FM I = 0, Q = 0xB4, which
  is what 0x45 / 0x46 hold under the image. No slot interrupts in FM, as expected.
- **Interrupt lines.** PC0 is TIME_SLOT_INTER: 10 edges in 300 ms of active timing.
- **C2 receive from the IC-7100.** The C6000 locks on the IC-7100's DMR (BS data sync found, idle
  bursts decoded with colour code 1), but only with the AT1846S in its DMR set; with the FM
  filters on (`c6dmrrx_flat`) it sees no slots at all. Once locked it free-runs on its own slot
  clock and missed the next transmission: c6dmrrx now restarts the receiver after 13 slots with
  no synced burst. With no sync the chip still flags a burst every slot (sync class 0, stale RX
  RAM); those are now only counted. Only bytes 0-11 of RX RAM change (the 96-bit payload).
- **Soft values** (RX RAM 0x0A8 and 0x1C8, consecutive slots): one byte per bit, two per symbol,
  7-bit signed: the symbol's level L, then 16 - |L|, so 16 is the inner / outer threshold and a
  clean +-3 sits near 24. Each holds slot type, sync and the 98 info symbols (not the CACH), in
  the orders slot type | sync | info (0x1C8) and sync | info | slot type (0x0A8).
- **Why the payloads were wrong.** The levels are spread evenly from 0 to about 30 with no four
  level eye. In the sync, every sign is right but half the +-3 symbols read as +-1, at the same
  positions in the soft values and in the raw (physical layer) bursts. Fitting channel models to
  the sync's levels, the IC-7100's pre-emphasis with a low cut (about 300 Hz) and a 3 kHz top
  fits best (rms 3.3 against 7.9 for a flat channel); de-emphasis alone does not. Hence
  `uv380_dmrburst.py fit` (the chain measured from the chip's own soft values) and `wav --eq`
  (the symbols pre-equalised against it).
- **R1, the pre-equaliser works.** From 22 soft dumps of the plain stream, `fit` found a
  differentiator-like chain (main tap 4.6, neighbours -1.8 and -1.7, +-3 symbols at level 13.7)
  and a chip symbol error rate of 0.49. Pre-equalised against it, the send at `--amp 0.18`
  (5 % clipped) decoded on the C6000: 72 bursts, text AG7EW, all 30 superframes, 21 of 23 rate
  1/2 blocks (3 lost, 1 bad, concealed). At 0.25 (17 % clipped) only 4 blocks came through, and
  at 0.35 (33 % clipped) nothing synced. The re-arm works: each send locked afresh. The chip
  seems to normalise its input level, so clipping, not level, is what the louder sends lost.
- **R2, quieter is better.** Same equaliser, no clipping: 0.12 gave 20 of 23 blocks, 0.08 gave
  21 of 23 with none bad (twice: also with a sharper equaliser, regularisation 0.03), 0.06 began
  to fail (7 lost, 4 bad). Every equalised send decoded AG7EW and all 30 superframes; the plain
  stream at 0.12 never synced. With the equaliser the chip's symbol error rate fell from 0.49 to
  0.12 and a +-3 symbol sat at level 20.
- **R3-R4, error free.** Refined once (`fit --base`, from R2's 0.12 window), the equaliser
  leaves the whole chain as nearly a single tap (main 7.0, every other tap 0.1 or less), the
  chip's symbol error rate drops to 0.015, and +-3 symbols sit at level 21. At 0.10 and 0.12 it
  decoded 23 of 23 blocks with no errors in 5 of 5 sends; 0.14 (1.6 % clipped) lost one block.
  At 0.08 and 0.06 the refined equaliser never acquired sync, though the eye would have been
  clean: the chip's sync search seems to need an absolute level, and decoding after it does not.
  Working recipe for the IC-7100 harness: fit on a plain send, refine on an equalised one, send
  at 0.10-0.12 of full scale (USB MOD 30 %).
- **The chip's crc=0 means nothing for rate 1/2 data** (DMR gives those blocks no CRC of their
  own): five crc=0 blocks came back with wrong bytes. dmrsotto's CRC-16 caught every one.
- **Raw mode** (`c6dmrrx_raw`): 288-bit windows that do not line up with the bursts (the sync
  turns up 3 to 5 bytes in), and the same magnitude errors.
- **C3 dry transmit.** 65 of 65 bursts (3 CSBK, 62 data) queued on the slot clock, first edge
  after 2 ms, an RF TX interrupt for each burst, none late; every register restored.
- **C4, the first live run (16:12:47Z): stuck carrier.** `tx_tone` keyed (1 W, 440.850, the
  1 kHz tone) and the module's last line was its "cal" line (I=7f Q=36). No "key ok" and no
  "err the keying never started", though that wait has a 400 ms timeout, so the module stopped
  inside it (or its lines stopped reaching USB). The image's 4.6 s tone never ended either, so
  the Morse ident (then sent after the tone) never went out. USB stopped answering, and the
  IC-7100 heard a steady 998 Hz tone at S9+60 for 111 s until Jeff switched the radio off
  (16:14:38Z). No DMR was sent. The dry run never called `tx_tone`, so the cause is in the
  keyed path: not found yet. Since then the image keys only under the transmit guard, sends the
  ident first, and `c6dmrtx` says a line before each step, so a re-run ends within seconds
  whatever happens and shows where it stops.
- **C5 guarded image (PR #123, API level 4), 16:3xZ.** Flashed with no hands (radio_tool
  usb-leave restarts it). Dry run 65/65, none late, registers restored. Live 1 ran every step
  but sent no RF: the flash script had left the channel power at 0 (PA off). Live 2 at 1 W
  (16:37:03Z): ident 3.5 s, 50 ms unkeyed, then the DMR keying, 4.7 s. USB went silent at
  key-up and the module's lines stalled it: DMR began about 3 s late, so the IC-7100 decoded
  the 3 CSBK preambles and 24 of 62 blocks (text AG7EW, id 1, 38 never sent, 1 bad). The
  carrier was down on time, and the radio reset 1.6 s after key-down and came back by itself.
- **Why the radio hung: 1 W knocks the UV-380 off USB.** In every 1 W run the radio dropped
  off the USB bus at key-up (the kernel logs a disconnect, not just a quiet CDC link); at power
  0 every line got through. A module's say() then blocks, which delayed live 2's DMR and very
  likely froze the 16:12Z run. `c6dmrtx` now keeps its lines from tx_tone to the key-down.
- **C6, error free (16:43:04Z, 1 W).** Ident, 50 ms gap, DMR from 16:43:07.99 to 12.60Z: the
  IC-7100 decoded 62 of 62 rate-1/2 blocks and the 3 CSBK preambles, 0 lost, 0 bad, text AG7EW,
  id 1, the 528-byte test message byte for byte. Plain decode: the chip's DMR arrives flat
  (with --preemph nothing decodes). The radio still reset about 1.4 s after key-down and the
  held lines were lost with the USB; the reset cause (watchdog or not) is still to be read.

## Open questions

- The UV-380 drops off USB under its own 1 W and reset about 1.9 s after each keying: the
  transmit guard's watchdog (RCC_CSR 0x3E000000). PR #127 (no writes into a dead bus, kicker at
  top priority, `guard` breadcrumbs) didn't stop it, but C7 (17:16Z, a second perfect send, 62/62)
  showed why: state off, the last kick 355 ms after key-down, as the host re-enumerated the radio.
  The likely cause, found in code: ST's USB library leaves an abandoned write's FIFO-empty
  interrupt enabled, and once the endpoint reopens it is pending again each time its handler
  returns, so no thread runs. The next image clears it (usb_dcd_int.c, usb_vcom.c); C8 checks.
  Still open: a ferrite or a short shielded cable (no solder), so the lines survive the keying.

- Receive is proven through the IC-7100 harness (pre-equalised). A DMR transmitter with a flat
  audio path (another UV-380, a real DMR radio) should need none of that: C4 shows it.
- PC0 is confirmed as the slot interrupt; PC1 and PC2 are still inferred from the DM-1701
  family (C3 counted an RF TX edge per burst on PC2).
- The DMR modulation pair: OpenRTX writes 0x45 = 0 (MOD1 only) for FM and M17; DMR wants both
  (the calibration's sendIrange and sendQrange). Deviation to be checked on the IC-7100 or K6.
- Longer messages than one 4.6 s keying (about 3.5 s of sotto) need a keying call without the 5 s
  cap, for the next reflash batch, or a transmit path in the image.
- Physical layer mode receive: what the chip needs to frame a burst (a DMR sync, presumably), and
  whether its soft values make a better outer code worth it over the chip's BPTC.
