# One UV-380 image for M17, DMR and C4FM, all carrying sotto

*Design, 2026-11-18. Thread "Build a multi-mode OpenRTX for the UV-380". Jeff, 11-18 18:39Z: "Yes,
plan and build. There's a LOT of flash space on the UV-380 ... a C6000 abstraction that applies to
both DMR and YSF/C4FM, but initializer routines that live on flash."*

## Goal

One OpenRTX-based image for Jeff's MD-UV390 PLUS that sends and receives sotto in three on-air
formats, picked from the radio:

| Mode | On air | sotto payload | Proven so far (RAM modules, 0 W bench) |
|---|---|---|---|
| M17 | 4FSK 4800 sym/s, C6000 raw PHY (0x10 = 0x66) | stream frames, 16 bytes / 40 ms | TX: c6m17tx, BERT 0 errors, SMS 20/20, voice 73/74 (11-17). RX: c6m17rx on the FM tap, 0 errors to -65 dB, Codec2 to speaker (11-18) |
| DMR | the C6000's own DMR modem, voice call | voice bursts, 216 bits / 60 ms, our FEC (dmr_sotto.h, dmr_voice.h) | TX: c6dvtx, 60/60 blocks, bit exact (11-17). RX: data bursts only (c6dmrrx, PR #117 #120) |
| YSF (C4FM) | 4FSK 4800 sym/s, the same raw PHY at 140 % deviation | sotto VW (sotto docs/softmodem.md) or data FR | TX: c6ysftx, 78/78 frames, 0 raw bit errors (11-18). RX: never tried |

Everything that works today is a RAM module that carries its own copy of the C6000 driver
(c6spi.h, c6slot.h, c6key.h, w25sec.h) and borrows the transmitter through `api->tx_tone`. This
design moves those drivers and the chip set-ups into the image, where flash is plentiful, and keeps
RAM modules for experiments.

## Budget (measured, not guessed)

**Flash.** OpenRTX's linker script (`linker_script_MDx.ld`) gives the application
`0x0800C000`, length `1M - 48K - 128K` = **868,352 bytes**: the first 48 KB are TYT's bootloader
and the last 128 KB sector holds OpenRTX's settings. CI's `arm-miosix-eabi-size` on main
(run 44328590817, 11-18 14:52Z):

| Image | text + data | Free of 868,352 |
|---|---|---|
| `openrtx_mduv3x0p` (plain PAGER) | 324,120 | 544,232 (63 %) |
| `openrtx_mduv3x0p_i2sprobe_range` (on the radio: loader, encoder, Codec2, probe) | 395,808 | **472,544 (54 %)** |

So Jeff is right: about 460 KB of internal flash is free. The whole plan below adds an estimated
40-50 KB. Flash is not the constraint; RAM is.

**RAM.** 128 KB SRAM (`largeram`, DMA capable, code can run there) plus 64 KB CCM (`smallram`, no
DMA, no code). The i2sprobe_range image has 110,736 bytes of `.bss`: about 53 KB in CCM (`.bss`
and `.data`, including the sotto encoder's 25 KB arena that Codec2 now borrows) and about 58 KB in
SRAM (`.bss2`: the 40 KB framebuffer and the DMA buffers). The heap is the rest of SRAM, about
72 KB, of which ~16 KB goes at boot, 16 KB to the module arena once a module loads, and 16 KB +
~6 KB to the Codec2 speaker thread while M17 voice plays (uv380-memory-budget, c2_alloc.h). That
leaves under 20 KB of heap during an M17 receive with the speaker on.

These CCM/SRAM splits come from the 11-14 map and later notes, not from today's ELF (the cloud
can't fetch CI artifacts). PR 1 makes CI print `size -A` per section and the 30 largest symbols in
each, so every later PR shows its RAM cost in the log.

Rules that follow:

1. **Built-in modes take no module arena.** Their state is static, in one CCM union (below), not
   heap. The 16 KB module arena is taken only when a module is actually loaded, as today.
2. **No DMA in the modem path.** The C6000's control and data ports are bit-banged GPIO (OpenRTX
   does the same), so modem state can live in CCM. Only the speaker and I2S mic buffers need SRAM,
   and those already exist.
3. **Stream, don't prebuild.** The modules embed a whole prebuilt transmission (`m17tx_*.h`,
   `ysftx_*.h`, 3.7-4.8 KB, which caps a send at the 5 s key-up). Built-in modes frame one 40 ms (M17), 60 ms (DMR) or 100 ms (YSF)
   frame at a time from the sotto superframes, so a TX needs a few hundred bytes.
4. **One modem at a time.** TX, the FM-tap receiver and the speaker preview never run together,
   so they share one static `c6_arena` union in CCM (the M17 receiver needs 6.6 KB; YSF receive
   will be similar; TX under 1 KB).

Estimated flash and RAM per part:

| Part | Flash | Static RAM |
|---|---|---|
| C6000 core (bus, slot IRQ, profiles, keying, deviation from cal) | 4 KB | 64 B |
| raw 4FSK TX engine (M17, YSF, flat FSK 2400/1200) | 2 KB | 120 B (two slot buffers + state) |
| DMR voice TX engine (bursts, LC, V_SPI) | 3 KB | 100 B |
| M17 framer TX (C port of modem/m17.py: LSF, LICH, conv, puncture, interleave, decorrelate) | 4 KB | 200 B |
| YSF framer TX (FICH Golay, CSD, VW sotto payload, PN9) | 5 KB | 300 B |
| 4FSK tap receiver (split from m17_rx.c) + M17 framer RX | 10 KB | 6.6 KB in `c6_arena` |
| YSF framer RX | 6 KB | in `c6_arena` |
| DMR voice RX | 4 KB | 1 KB in `c6_arena` |
| sotto superframe queue (TX from the recording, RX into the speaker) | 1 KB | 1 KB |
| mode, menu, macro key, USB commands | 5 KB | 32 B |
| **Total** | **~44 KB** | **~8 KB CCM, 0 heap** |

## Architecture

Four layers. The bottom two are what Jeff described: one C6000 abstraction for every mode, with
the chip set-ups as tables in flash.

```
  OpMode_Pager (patch hooks)   PTT / FUNC record / # play / idle receive / USB "mode m17"
        |
  framers  (c/common, MIT, host-tested bit exact against modem/*.py)
        m17_tx  m17_rx   ysf_tx  ysf_rx   dmr_sotto + dmr_voice (exist)
        |                    |                     |
  engines  (firmware/uv380-openrtx/c6/)
        fsk4_tx: 36 bytes/slot -> idle TX RAM half      dmr_tx: burst type + V_SPI payload
        fsk4_rx: FM tap halves -> 4FSK symbols           dmr_rx: RX RAM bursts
        |
  C6000 core  (firmware/uv380-openrtx/c6/)
        c6_bus  c6_slot  c6_profile  c6_key  c6_cal
```

### C6000 core (in flash)

- **c6_bus**: every chip space (configuration, auxiliary, TX RAM, RX RAM, prompt FIFO, V_SPI)
  through OpenRTX's own `HR_C6000` object and its chip-select lock, instead of the modules'
  "interrupts off, wait for CS high, retry" dance (c6spi.h). One owner, no races with OpenRTX's
  own register writes.
- **c6_slot**: the PC0/PC1/PC2 interrupt lines as real EXTI interrupts. The modules can't take an
  interrupt (c6slot.h latches pending bits and polls every 1 ms; c6m17rx idled at 25-66 % CPU).
  In the image the slot ISR posts to the modem thread, which writes the next 36 bytes within
  microseconds of the edge. Counters (edges, late, lost) stay for the bench lines.
- **c6_profile**: Jeff's "initializer routines that live on flash". Each chip set-up the modules
  found is a `const` table of (register, value) writes plus the list of registers to save first,
  with named slots for the values computed at run time (deviation from calibration, colour code,
  the tap filter):

  | Profile | From | Key writes |
  |---|---|---|
  | `C6P_FSK4_TX` | c6m17tx, c6ysftx, c6phytx (on air 11-16..18) | 60=00, 01=F0, 45/46 = cal x dev %, 10=66, 11=80, 20=00, 21=02, 5F=F0, 81=1F, 83=FF, 41=00, then 40=E3 |
  | `C6P_DMR_VOICE_TX` | c6dvtx (11-17) | as above with 10=6E, 1F=CC<<4, 06=21, LC in page 2, 22=enc, 40=E3 |
  | `C6P_FMTAP_RX` | c6m17rx (11-18) | AT1846S DMR RX set, PD9 high, 10=80, 60=00, 26=FD, 81\|=02, 34=1C |
  | `C6P_DMR_RX` | c6dmrrx (PR #117) | AT1846S DMR set, 10=6E, ... |
  | `C6P_FM_IDLE` | OpenRTX `fmMode()` | what every profile restores to |

  `c6_apply(profile, args, saved)` saves, writes and later restores in one place, so no mode can
  leave the chip half set (the 11-16 stuck carrier was a module that lost track of state). The
  same tables are exported to modules (level 6), so a module that needs the M17 TX set-up names
  `C6P_FSK4_TX` instead of carrying 30 lines of writes.
- **c6_key**: the transmit sequence the modules proved, without borrowing `tx_tone`: Morse ident
  AG7EW first, the IWDG transmit guard armed for this key-up's length, 80 ms PA settle, the
  profile, the data, registers restored 350 ms before key-down. Frequency and power go through
  OpenRTX's own `radio_*` calls; transmit stays inside 430-450 MHz (the image refuses anything
  else) and, until Jeff says otherwise, on the two bench channels. Lines are held while keyed (1 W
  drops USB) and said after.
- **c6_cal**: the DMR modulation pair comes from OpenRTX's calibration struct (`sendIrange`,
  `sendQrange`, read from security register 2 at boot by nvmem_MDx.c), interpolated to the
  channel, times the mode's deviation percent: M17 123 % (2400 Hz outer), YSF 140 % (2712 Hz
  measured), flat FSK 100 %. No more SPI1 security register reads from modules (w25sec.h).
  The raw-PHY carrier offset (about +6.7 kHz, today handled by `trim N - 6680` from the bench)
  becomes a per-profile frequency correction the image applies itself.

### Engines

- **fsk4_tx**: one engine for every 4800 sym/s mode (the chip maps dibits 01/00/10/11 to
  +3/+1/-1/-3, which is both M17's and YSF's map). A framer hands it bytes; the engine fills the
  idle TX RAM half at each slot edge (TX_WRITE 1, TX_HALF 0, the fix from run 3), 0x77 when dry.
  The flat FSK 2400/1200 for the K6 (c6phytx) is the same engine with a different framer, so
  "K6 FSK" can be a fourth mode for free.
- **dmr_tx**: header, voice bursts A-F, terminator through 0x50 and V_SPI (c6vspi.h), the order
  c6dvtx proved.
- **fsk4_rx**: the FM tap reader (0x82 bit 1 flags, halves 0x030/0x230, inverted) and the 4FSK
  demodulator now inside `c/common/m17_rx.c`: symbol timing, level fit, soft dibits. Split out,
  it serves M17 and YSF; each framer brings its own sync words (M17 LSF/stream/packet/BERT, YSF
  D471C9634D). YSF receive is the one piece with no bench result yet: YSF's 2.7 kHz outer
  deviation is 12 % more than M17's, which the tap (flat to 3.4 kHz, ~3 units/Hz) should take.
- **dmr_rx**: the chip's own DMR receive into RX RAM (c6dmrrx), extended from data bursts to
  voice bursts.

### Framers and the sotto payload

The radio's sotto encoder makes sotto 1200 (6-byte superframes, 40 ms), which is what FUNC
records and PTT sends today. That is the common payload; each mode wraps it its own way:

- **M17**: an M17 stream (LSF with src = the radio's callsign, dst ALL, then stream frames).
  One superframe per 40 ms frame leaves 10 of 16 bytes spare. The LSF TYPE marks the data type
  as "data", not Codec2, so a stock M17 radio shows the call and stays quiet instead of playing
  noise; the META field carries a small sotto tag (codec, rate). The spare room fits sotto 2400
  (12 bytes / 40 ms) later.
- **DMR**: a group voice call whose bursts carry dmr_sotto blocks under our K=7 rate 1/2 code,
  exactly as c6dvtx sends now (9 payload bytes / 60 ms = 1200 bit/s in real time).
- **YSF**: the sotto softmodem's own frame (sotto docs/softmodem.md): FICH DT = voice FR with
  the provisional sotto flag (byte 2 bit 7), header and terminator CSD with the callsigns, and
  the "sotto VW" payload (5 x 48 bits + CRC-8, tail-biting K=7 at rate 7/18, PN9). That payload
  is 2400 bit/s of sotto frames; for sotto 1200 it carries two superframes per 100 ms frame and
  the rest as repetition, until the radio encodes 2400. The data FR plan c6ysftx already proved
  on air is the fallback if Jeff wants Yaesu radios to show "data" rather than garble.

The M17 and YSF transmit framers exist only in Python (modem/m17.py, modem/ysf.py, which built
the modules' tables). They get C ports in `c/common/` with host tests that must match the Python
byte for byte, the same pattern as m17_rx.c and dmr_voice.h.

### The mode on the radio

PAGER mode already owns everything around a send: FUNC records sotto from the mic with no
hardware mod, PTT sends, # plays, idle receive plays what it hears, the IWDG guard, the bench USB
commands. So the new modes are **sub-modes of PAGER**, not new OpenRTX opmodes:

- Macro key 4 (hold MONI, 4), which toggles RNG / FSK today, cycles **FSK, RNG, M17, DMR, YSF**.
  The main screen shows which. USB `phy m17|dmr|ysf|fsk|rng` does the same for the bench runner.
- PTT sends the message in that sub-mode; idle receive listens in that sub-mode (FSK and RNG on
  I2S3 as now; M17 and YSF on the FM tap; DMR on the chip's DMR receiver) and plays received
  sotto on the speaker through the existing preview decoder.
- A menu entry under Settings can come later; the macro key needs no new screens.

### Code layout (fixing the patch)

`uv380-blowfang.patch` is 2,386 lines, 1,700 of them `OpMode_Pager.cpp`. New code goes in real
files under `firmware/uv380-openrtx/c6/` (copied by build.sh like i2sprobe.c), and the patch only
gets the hooks: the sub-mode switch, the EXTI handler, the build lines. Reviews and merges get
easier, and the C6000 core can be tested on QEMU like the loader (`make -C radios/uv380 omqemu`).

### Modules, improved

On the K6 a module must carry its own drivers, because the 120 KB flash is full. Here the image
has room, so **module API level 6** exports the core: `c6_apply`/`c6_restore` with a profile ID,
`c6_slot_wait`, `c6_tx_ram_put`, `c6_rx_ram_get`, `c6_key(ms, cb)`, `fsk4_rx_open/read`, and the
framers. A module that is a 7-9 KB copy of c6m17tx today becomes 1-2 KB of experiment. Modules
still can't touch anything the image refuses (frequencies, power above the bench limit, the
guard). The K6 can take back one idea: profiles as named tables a module applies by ID, which
also saves flash there.

## Milestones (each a PR)

1. **This doc, and CI section sizes.** `size -A` and the biggest symbols per section in the
   uv380-openrtx job, so CCM and heap use are measured on every PR.
2. **M17 and YSF transmit framers in C** (`c/common/m17_tx.c`, `ysf_tx.c`), streaming one frame
   at a time from sotto superframes, host tests byte exact against modem/m17.py and modem/ysf.py.
   Cloud only, no bench.
3. **C6000 core in the image** (c6_bus, c6_slot IRQ, c6_profile tables, c6_key, c6_cal) and the
   fsk4_tx / dmr_tx engines, with module API level 6. First bench check, at 0 W on 441.850 via
   the runner: thin level 6 modules that send the same M17 BERT, DMR voice and YSF DN the old
   modules sent, decoded on the Pluto. **Needs one flash** (Jeff's typed line in a hamlet
   session; the runner inbox refuses flashes by design).
4. **Sub-modes on the radio**: macro key 4 cycles FSK/RNG/M17/DMR/YSF, PTT sends the recorded
   sotto message in that mode. Bench: each mode at 0 W to the Pluto, decoded by modem/m17rx.py,
   modem/dmrvoice.py and modem/ysf.py; the K6 FSK mode still to the K6. **Second flash.**
5. **Receive**: fsk4_rx split from m17_rx.c, M17 sotto receive to the speaker, YSF receive (new:
   Pluto sends modem/ysf.py frames, the radio decodes), DMR voice receive. Jeff's ear for the
   speaker. **Third flash.**

Later, not in this plan: live mic streaming (talk while keyed instead of record then send),
sotto 2400 on the radio, standard Codec2 3200 M17 voice from the unmodded mic path (the I2S3 mic
works with no mod, which OpenRTX's own M17 TX lacks on this radio), and a dual payload (Codec2
1600 plus sotto in the same M17 frame, so stock M17 radios hear Codec2 and ours hear sotto).

## Rules this follows

0 W sends whenever Jeff is away (above 0 W only with him present); Morse ident AG7EW before
every key-up and the callsign in every frame header; 430-450 MHz only, the bench channels for
now; all bench work as runner jobs; images keyed, modules signed with `~/.config/sotto/k6-mod.key`;
each flash needs Jeff's own typed line in a hamlet session. Nothing goes upstream to OpenRTX
without Jeff's OK. Clean room as before: RadioFoundry (MIT) is a source; OpenGD77 (GPL-2) and
RadioFoundry's GPL parts are facts only.
