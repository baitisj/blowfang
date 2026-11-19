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
| M17 framer TX (C port of modem/m17.py: LSF, LICH, conv, puncture, interleave, decorrelate) | 2.5 KB (measured) | 140 B |
| YSF framer TX (FICH Golay, CSD, VW sotto payload, PN9) | 2.5 KB (measured) | 160 B |
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
  D471C9634D). YSF receive has no bench result yet (c/common/ysf_rx.c, milestone 5): YSF's
  2.7 kHz outer deviation is 12 % more than M17's, which the tap (flat to 3.4 kHz, ~3 units/Hz)
  should take.
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

- Macro key 4 (hold MONI, 4), which toggled RNG / FSK, cycles **RNG, FSK, M17, YSF, DMR** in
  the bench image (built in milestone 4). The main screen shows which ("M17 PTT #"), and so does
  the macro menu. USB `phy m17|ysf|dmr|fsk|rng` does the same for the bench runner.
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
3. **The C6000 core as portable C, proven as RAM modules first** (done in PR #187, the RAM
   module rule: no flash until a module can't do it). `c/common/c6_core.[ch]`: the profile
   tables (`C6C_FSK4_TX`, `C6C_DMR_VOICE_TX`, `C6C_FMTAP_RX`, flat byte arrays so -fropi modules
   can hold them), `c6c_apply`/`c6c_restore`, and the engines `c6c_fsk4_tx` and `c6c_dmr_tx`,
   the proven module loops moved over unchanged, behind a `c6c_bus` of function pointers. Tested
   on a fake chip (`c6core_host`, tests/test_uv380_c6core.py: whole M17 and YSF sotto streams on
   the air byte for byte, DMR bursts in order, every register restored). `mod/c6bus_mod.h` fills
   the bus from the module helpers, and `mod/c6sotto.c` sends **the radio's own recorded sotto
   message** as M17, YSF or DMR (`c6sotto_m17|ysf|dmr[_dry].c`, 5-7 KB each), so all three
   modes go on air at 0 W through the runner (new step `uv380.sotto AIR [dry]`) on the image
   already on the radio. **No flash.**
4. **The core in the image** (flash 1). Built in two steps, the first one now:
   - **Step 1, built (this PR): the proven module path, from flash.** `mod/c6air.h` is the
     c6sotto modules' body, with the mode picked at run time (`c6air_run(api, air, src, dry)`);
     `mod/c6sotto.c` is now a thin wrapper around it, and `c6air_img.c` builds the same body into
     the bench image (BLOWFANG_RANGE + BLOWFANG_I2SPROBE, the image on the radio). The image
     calls it with its own module API table (`om_api()` in ortx_mod.c), so it keys exactly as the
     modules did on air: tx_tone, the Morse ident first, the IWDG transmit guard, 441.850 or
     440.850 only, the 5 s key-up cap (about 8 s of sotto as M17 or YSF, 4 s as DMR).
     - Macro key 4 cycles RNG, FSK, M17, YSF, DMR; the screen shows "M17 PTT #" and so on.
     - In M17, YSF or DMR, a PTT press queues the send and it goes out when PTT is let go
       (tx_tone keys only with PTT up). The message is the same one PTT sends in RNG and FSK
       (the last recording or message heard, else the stored one).
     - The raw modes sit 6.68 kHz above the FM carrier, so the image moves the keyed tone down by
       6680 Hz itself and puts the trim back after; the Morse ident stays on the channel. The bench
       keeps the FM trim (6100) for these sends; the runner refuses `uv380.air` with a negative
       trim, which would put it 6.7 kHz off.
     - USB `air m17|ysf|dmr [test] [dry]` runs the same with no PTT (test: the test message; dry:
       never keys), its lines as a module's, then `ok air ret=80` when the whole message went out.
       Runner step `uv380.air AIR [test] [dry]`, at power 0 like every send.
     - Cost: about 13 KB of flash (core, both framers, the DMR voice call), no new RAM beyond the
       stack: the message is read in place.
   - **Flashed and on air (11-18).** Flash 1 went on at 23:37Z (PR #193's CI image, cd3cbd0,
     keyed; 23 s hands-free). Job air1 at 00:09-00:10Z 11-19 (main 0e8b912, 0 W on 441.850, the
     Pluto listening) sent the radio's message with `uv380.air` in each mode, from flash:

     | mode | Pluto decode | on channel |
     |---|---|---|
     | M17 | 102 of 105 stream frames found, 102/102 CRC good, 204 superframes, LSF src AG7EW, first 9100475824d1 and last 56d3d545aca1 as sent | data +37 Hz, ident about +290 Hz |
     | YSF | 43/43 frames, FICH 43/43, 205/205 sotto VW CRC-8 good, AG7EW to ALL, first and last as sent; outer 2703 Hz | data +36 Hz, ident about +260 Hz |
     | DMR | 60/60 blocks CRC good, 85 superframes, text AG7EW, terminator seen | data +146 Hz, ident about +310 Hz |

     The data now lands on the channel, where the RAM-module runs had it 6.7 kHz high, and the
     DMR capture decodes with no mixing first. The dry run before it (air1-dry) streamed every
     mode with no late or lost slot. Logs /mnt/project-files/uv380-multimode/air1-1119/.
   - **Step 2, later:** the image's own bus (OpenRTX's HR_C6000 driver and chip-select lock, a
     real EXTI slot interrupt), cal from `calib->sendIrange/sendQrange`, module API level 6
     exporting the core, and no 5 s key-up cap (the guard deadline follows the stream).
   - Bench after the flash: each mode at 0 W to the Pluto (`uv380.air`), decoded by
     modem/m17.py, modem/dmrvoice.py and modem/ysf.py; the K6 FSK mode still to the K6. The flash
     needs Jeff's typed line in a hamlet session (the runner inbox refuses flashes by design).
5. **Receive**: YSF receive, M17 sotto receive to the speaker, DMR voice receive. As modules
   first, then in the image with Jeff's ear for the speaker (flash 2).
   - **YSF receive, step 1 (works on the radio, 11-18 21:49Z)**: `c/common/ysf_rx.[ch]`,
     the C version of modem/ysf.py's demodulator and decoder on m17_rx.c's front end (DC follower,
     x3 RRC interpolator at 24 kHz, here alpha 0.2 over +-6 symbols). It keeps the frame's 480
     symbols as 16-bit samples (not soft bits) and makes soft bits from them as each decoder asks,
     so the whole receiver is 4.7 KB of RAM: FICH (K=5 Viterbi, Golay, CRC-16), header and
     terminator CSD (callsigns), data FR, and sotto VW (tail-biting K=7 at rate 7/18, CRC-8 per
     superframe). Once locked, a frame whose FICH is lost is read on as the next sotto VW frame
     and its CRC-8s decide. On simulated tap audio (tests/test_uv380_ysfrx.py, 11 tests) every
     superframe decodes, 3000 units of noise on a 9000-unit outer level still decodes, and a
     minute of tap noise gives no frames. `mod/c6ysfrx.c` (8.8 KB, + 4.8 KB state in scratch)
     runs it on the radio's FM tap exactly as c6m17rx does; runner plans `uv380.rx ysf|ysftap`;
     `python3 -m modem.ysf wav` makes the Pluto's test transmission (c6sotto's test message as
     sotto VW, `--dev 5400` puts the outer symbol at 2700 Hz).
   - **On the bench (job ysfrx-1118a, main 9f63b68, 21:49-21:51Z):** c6ysfrx_tap on the radio's
     FM tap (no mod, nothing keyed on the UV-380), the Pluto sending `modem.ysf wav` (three
     sotto VW calls of 40 superframes, three data FR calls) on 441.850, at -50 dB, then at -60 dB
     with a 1 kHz offset (RSSI about -92 dBm). The radio decoded **108/108 frames, 240/240
     superframes CRC good, every callsign and data block**, no FICH lost, no lock lost, 0 tap
     half gaps; first 000102030405 and last eaebecedeeef as sent. The host receiver
     (`ysf_rx_host`) on the same tap samples agrees: 108 frames. Sync scores 0.90-0.997 but for
     two frames (0.60, 0.81), which still decoded.
   - The two receivers' front ends are the same code twice (about 40 lines); they become one
     `fsk4_rx` when both go into the image, not before (the M17 one is proven on air and stays as
     it is until then).
   - **Sotto messages back from the air, step 2 (RAM modules, 11-19)**: `c/common/sotto_rx.[ch]`
     puts a sotto message back together from M17 stream frames (CRC-16 per frame, two new
     superframes a frame or one plus a repeat) or YSF sotto VW frames (five superframes a frame,
     each with its CRC-8; a frame's place comes from its time, so a lost frame leaves a hole
     instead of shifting the rest). A hole is filled from the superframe before it. A message ends
     on its last frame, an EOT or terminator, or 1.5 s with nothing new; an EOT with nothing heard
     before it ends nothing (found on back-to-back sends: the last call's EOT cut the next one
     short after one frame). `mod/c6rx.h` is the radio side: the FM tap, one receiver,
     sotto_rx, and an `rx AIR msg sf= got= missing= bad= src= first= last=` line per message;
     it is what the image's PAGER M17 / YSF listen will run from flash. As RAM modules first:
     `c6sottorx_m17.c` / `c6sottorx_ysf.c` (300 superframes, 12 s of sotto; the compiler checks
     image + state fit the 16 KB arena), runner plans `uv380.rx m17sotto|ysfsotto`. The Pluto's
     test transmission: `python3 -m modem.m17rx wav OUT --tones-s 0 --bert-s 0 --sotto N
     --repeat R` (`--dev 4800`) or `python3 -m modem.ysf wav OUT --sotto N` (`--dev 5400`).
     Host tests: tests/test_uv380_sottorx.py.
   - **On the bench (job sottorx2, main e4ea6af, 01:07-01:13Z 11-19):** c6sottorx_m17 and then
     c6sottorx_ysf on the radio's FM tap (nothing keyed on the UV-380). The Pluto sent the test
     message three times at -50 dB, then three times at -60 dB with a 1 kHz offset, on 441.850:
     M17 75 superframes (`modem.m17rx wav --sotto 75`), YSF 60 (`modem.ysf wav --sotto 60`).
     **M17 6/6 messages and YSF 6/6 came back whole**: 76/76 and 60/60 superframes, none
     missing, no CRC failures, every one ended on its EOT or terminator, src AG7EW, and the first
     and last superframes as sent. Scratch was 8824 B against 8260 needed (M17) and 8104 against
     6712 (YSF).
   - **FTM echo (Jeff 11-19, job ftm-echo2, 01:01-01:06Z, 0 W on 441.850):** `c6ysftx_echo.c`
     (c/common/ysf_echo.c keeps each frame of a C4FM call as the bytes it came as, and sends the
     call back after the ident; runner `uv380.ysf echo`). Jeff keyed his FTM-300D in DN three
     times. Each call was heard whole: header, 24 voice frames and terminator, none lost, src
     AG7EW-JEFF. Calls 2 and 3 were longer than the 26-frame room, so 2 and 5 frames from
     their middles were dropped and their terminators kept. Each echo went out whole (3144
     bytes, no late slot). **The FTM showed "AG7EW - Jeff" and played his voice back.** There
     was no Pluto capture, because a recording of his voice would go to the public jobs branch.
   - **The same in the image (flash 2, FLASHED 11-19 01:36Z, main e4ea6af)**: `c6rx_img.c` builds
     c6rx.h into flash with both receivers (12.5 KB of code; c/common's receiver names renamed
     c6rx_* so none clash with OpenRTX's M17). Its state, about 11 KB for both receivers and a 30 s
     message, goes in the module arena's scratch, which `om_api` now gives whole when no module
     upload is waiting (`om_idle`). In PAGER's M17 and YSF sub-modes, the probe thread runs
     `airListen` in place of the PHY R receiver. A message it hears goes into the message
     (`c6rx_take`) and plays. A message stopped part way is dropped. The listen's host can't
     key, play or record. A USB line stops it, and the line goes into pbCmd for pbPoll to run,
     so no command is lost.
   - **Flash 2 hears (job hear3, 01:49-01:53Z 11-19):** the Pluto sent the sotto test message
     one copy at a time at -50 dB, with `listen` read after each: YSF 3/3 (60/60 superframes each)
     and M17 3/3 (76/76 each), each played to its end (pv=0/N). Copies sent back to back (hear2)
     were joined late while the first one played, so the last came out short (50/60, 40/76).
     A `uv380.cmd` step can't hold both `listen 0` and `listen 1`: the read-back checks the last.
   - **YSF voice demo for Jeff (ysfdemo3, 02:44-02:54Z 11-19, the radio on battery in his
     hand):** LibriSpeech 251_m and 3536_f, 12 s each, coded by sotto's fixed-point sotto 1200
     encoder and sent as YSF sotto VW by the Pluto at -40 dB. **Jeff: "Working"**, the radio
     played them. From power-on it needs MONI+4 three times (RNG, FSK, M17, YSF).
   - **The Pluto parrot (`pluto_bench.py parrot`, tools/pluto_parrot.py, Jeff 11-19):** listens
     on the channel, and each YSF or M17 sotto message it hears goes back 2 s later in the same
     mode (the bytes coded again, src AG7EW, MCW ident around it). The UV-380 sends with PTT in
     its M17 or YSF sub-mode. FUNC's record-and-send still sends RNG or FSK, so record with FUNC
     (that send gets no reply), then PTT to send it as YSF or M17.
   - **Parrot on air (11-19, Jeff on battery in the house, UV-380 YSF at 1 W, Pluto -40 dB):**
     the first tries (parrot1-3, 04:31-04:51Z) never answered. A plain capture of his PTT send
     (job pttcap1, the 251_m clip) decoded 43/43 frames and 205/205 superframes, so the radio and
     the stub were fine: the parrot measured burst power over its whole 240 kHz (15 dB more
     noise than one channel), and it stopped receiving while it decoded. PR #200 fixed both
     (8 kHz low-pass detector, receive thread). **parrot4/5 answered every YSF send: 205 sf at
     05:05Z, 105 at 05:07Z, 75 at 05:12Z**, and skipped his FUNC (FSK) sends in between. Jeff:
     "Yay!". RX gain 20 dB is right; 0 dB heard nothing.

**On air (11-18 20:21-20:22Z, job sotto3-air-1118, main 2bc44cf, 0 W on 441.850, the Pluto
listening):** the c6sotto modules sent the radio's own recorded message (250 superframes) in all
three modes from the image already on the radio, no flash. The dry run (sotto3-dry-1118) first:
every mode streamed with no late or lost slot.

| mode | sent | Pluto decode | check |
|---|---|---|---|
| M17 | 105 stream frames, 210 superframes | 101 frames found, 101/101 CRC good, LSF src AG7EW | 4 frames not found by the Python demod (3 gaps); first 9100475824d1 and last 56d3d545aca1 as sent |
| YSF | 43 frames, 205 superframes | 43/43 FICH good, 205/205 sotto VW CRC-8 good, AG7EW to ALL | first and last as sent; outer 2691 Hz (YSF wants 2700) |
| DMR | 3 headers, 60 voice bursts, terminator, 85 superframes | 60/60 blocks CRC good, text AG7EW, 510 bytes | first 9100475824d1, last 1f2604c8257a as sent |

The transmit side reported no late slots for M17, so the 4 missing M17 frames look like the
Pluto decoder missing a sync at 0 W rather than gaps on air; the M17 voice run of 11-17 also
found 73 of 74.

Measured so far (clang -Os, Thumb-2): m17_tx 2.5 KB, ysf_tx 2.5 KB, about half the estimate
above; a whole c6sotto module (core + framer + keying + cal) 5.8 KB (DMR) to 6.9 KB (YSF).

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
