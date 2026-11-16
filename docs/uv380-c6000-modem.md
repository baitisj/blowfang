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
| `c6dmrrx.c` | receive | AT1846S and C6000 into DMR layer 2 receive; slot check; every burst's type, CRC, colour code, sync errors, FSK error, payload; soft values of the first bursts. `c6dmrrx_flat.c`: AT1846S left in FM (comparison). `c6dmrrx_raw.c`: physical layer mode, raw 288-bit bursts |
| `c6dmrtx.c` | **none by default** (TX_DRY 1): the chip frames bursts that go nowhere | the full transmit sequence as a rehearsal: slot clock, RF TX interrupts, TX flags |
| `c6dmrtx_live.c` | 1 W, 440.850 MHz only, 4.6 s, then the Morse ident | the test pattern as DMR rate 1/2 bursts, keyed through `api->tx_tone`. Only with Jeff at the radio |
| `c6dmrtx_msg.c` | as live | the radio's own message (last recording or message heard) |

The DMR transmit borrows the image's FM keying: `tx_tone` keys at 1 W on 440.850 (refusing
anything else), and once the C6000's analog TX is up the module switches the chip to DMR with
the radio's own DMR modulation pair, sends its bursts at the slot edges, and puts every register
back 350 ms before the keying ends so the ident goes out in FM as always.

## Host tools

- `tools/uv380_dmrburst.py wav`: the IC-7100 test signal, repeater-style continuous DMR (lead-in
  idle, 3 CSBK preambles, a dmrsotto message in slot 1, slot 2 idle) as 48 kHz FM audio;
  `--deemph` pre-compensates a pre-emphasising transmitter.
- `tools/uv380_dmrburst.py decode`: an IC-7100 recording of the UV-380's bursts back to the
  sotto message (`--preemph` for a de-emphasised recording).
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

## Open questions

- Is the IC-7100's FM audio path (300 Hz high pass, pre-emphasis) flat enough for the C6000 to
  sync on synthesized 4FSK? If not, step 2 waits for step 4 (the UV-380 can't hear itself).
- The interrupt pins on the MD-UV380 (PC0-2) are inferred from the DM-1701 family; c6pages and
  the slot check confirm them.
- The DMR modulation pair: OpenRTX writes 0x45 = 0 (MOD1 only) for FM and M17; DMR wants both
  (the calibration's sendIrange and sendQrange). Deviation to be checked on the IC-7100 or K6.
- Longer messages than one 4.6 s keying (about 3.5 s of sotto) need a keying call without the 5 s
  cap, for the next reflash batch, or a transmit path in the image.
- Physical layer mode receive: what the chip needs to frame a burst (a DMR sync, presumably), and
  whether its soft values make a better outer code worth it over the chip's BPTC.
