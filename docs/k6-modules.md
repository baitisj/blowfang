# K6 RAM modules: native code over the air

The K6 pager firmware can take a small native program over the air, check that you signed it,
and run it once from RAM. Nothing is written to flash. The first module, `c/k6/mod/hello.c`,
prints where it is running and times a small loop, all on the serial report. The design memo
(prior art, memory map, later stages) is the Claude Doc "Modular K6 firmware with loadable
modules".

## What happens

1. The PC builds the module with clang for the Cortex-M0 using `-fropi`, so its code and
   constants are reached PC-relative and it runs at any address. `modem/k6mod.py` links it at
   two addresses and refuses it unless the bytes come out identical.
2. It adds a 16-byte header and an 8-byte SipHash-2-4 tag under your 16-byte key, cuts the
   image into packets with magic `0xAB`, and makes transmit audio like any other packet.
3. The K6 (`c/k6/k6_mod.c`) puts the chunks straight into the firmware's shared RAM block
   (below), in any order, repeats ignored. Once all are in, it checks the header, the tag and
   the serial, then calls the entry function with a table of services. Any message held
   before the module is gone. In the images with the SPI library the module's image stays in
   the block afterwards, so side key 1 can run it again (below), until a message needs the
   store.

**Run it again: side key 1** (Jeff asked for it, 2026-11-10: "instead of just dropping the last
program in the RAM arena, the module should be able to be re-run by pressing a key"). In VOICE
MSG, side key 1 (SK1) runs the last module again without a resend. The press counts when the key
is let go, and after a module returns the core waits (up to 3 s) for any key held to stop it to
be let go, so that release isn't a press (side key 1 would start the module again, `*` would
clear). Serial: `K6 LIB run slot=254 r=CODE_LEN`, then the usual `K6 MOD run` / `K6 MOD ret`
lines. Before it runs, `km_again` (c/k6/k6_mod.c) checks the image's SipHash tag under the
radio's key again (the tag covers the header too), so a module that wrote over its own code, or
anything else that used the block since (EXPLORE, an IF session, or audio or the screen buffer
in the PWM ring under a module longer than the 4 KB store), is refused and doesn't run. A module
has no statics, so each run starts clean: its state lives on the stack and in its scratch,
which the core hands out fresh every run. When the copy in the block is gone, a module that
last ran from an SPI library slot loads from that slot again (`K6 LIB run slot=N r=...`);
`slot=255 r=-6` means there is nothing to run again. The kept image goes when the store is
wanted: the first good voice packet, a coded 4FSK session, key `*`, control action clear,
EXPLORE, or a new module upload. The store stays empty meanwhile, as it is while a module
arrives. A rerun carries no upload reply or telemetry, and skips the serial check (the image
passed it when it came in). Refused while a message plays (`r=-9`, also for control action
lib). In the images with the SPI library only (`spilib`: pager-only, lr, vlr, vlrh, explore,
voice-explore); the lean images clear the store after a module as before. Cost: about
230 B of flash (local clang 18 build), 2 B of RAM.

**How much room.** Images built from 2026-11-08 on keep the message store (4 KB), the PWM audio
ring and the voice decoder's working memory side by side in one block (`hal::arena` in the Aura
patch), because a module never runs while a message plays. A module gets the whole block: in
every image that reads its codebooks from SPI flash (`aura-pager-only`, `-lr`, `-vlr`, `-vlrh`)
it is 11,776 bytes, so the header, code and tag can be up to 11,752 bytes, and whatever the code
leaves is the module's scratch. The serial line `K6 ARENA bytes=11776 ... dec=... need=...` at
power-on shows the layout (9,760 bytes in the first images of 2026-11-08, before the screen
buffer left main's stack frame: insight 37). The EXPLORE image and the lean images (codebook install, codebooks in
MCU flash) keep the store plus the ring: 5,376 bytes. Before
2026-11-08 every image gave modules the 4 KB store only.

The hello module is about 360 bytes of code (366 with clang 18 here, 358 in CI), 4 XL packets,
3.1 s on air at F/1800 per copy; `modem.k6 mod` sends every chunk twice by default.

**On air, 2026-11-05** (image f6b0d2a keyed with `keyimage`, F/1800 XL, level 0.3): the first
send lost chunk 0 (bad CRC, the burst's first packet) and chunk 1; `--only 0,1 --copies 2`
filled them in, and the K6 reported `run ... at 0x20001bfc`, `say hello world from RAM at
0x20001bfd` (the Thumb bit is set), `sum of squares 0x9f31fc58 in 0x000e ticks`, `ret=107
(0x6b) in 11.8 ms`. A sotto 1200 message afterwards played with a matching `pcm=` checksum.

## Packet and image format

De-whitened (PN9, as voice packets), an L or XL module packet is

| Bytes | Field |
| --- | --- |
| 0 | `0xAB`, or `0xAE` for the same chunk with a reply wanted (upload ARQ, below) |
| 1 | transfer id |
| 2 | chunk number, from 0 |
| 3 | chunk count (1-255) |
| 4 .. len-3 | data: 58 bytes in L, 122 in XL |
| len-2, len-1 | CRC-16/CCITT-FALSE of bytes 0 .. len-3, big-endian |

The chunks' data, in order, is the image (the last chunk padded with zeros):

| Offset | Field |
| --- | --- |
| 0 | `K` `6` `M` and ABI major `1` |
| 4 | code length, u16 little-endian |
| 6 | entry offset in the code, u16, even |
| 8 | serial, u32: must be newer than the last module this boot ran |
| 12 | API level the module needs (1 to 7; a K6 refuses a newer one), flags (0), telemetry parts (0-12, level 4 images; see below), 1 reserved byte |
| 16 | code: Thumb, position-independent, no writable statics |
| 16 + code length | SipHash-2-4 tag of bytes 0 .. 15 + code length, u64 little-endian |

The entry is `int k6_main(const k6_api_t *api)`. API level 1 (`c/k6/k6_mod.h`) has `say`, a
serial line `K6 MOD say <text>` of up to 60 characters, and `ticks`, the 100 us uptime
counter (16 bits). The table only grows: a later level appends services and keeps these.

API level 2 (images from 2026-11-06 on) adds the receiver:

| Call | What it does |
| --- | --- |
| `fd_read(reg)` | an FD6818 register |
| `fd_write(reg, v)` | writes one, 0; or -1 for the registers it refuses: 0x30 (RX/TX state), 0x33 (GPIO), 0x36 (PA), 0x37 (power). In images from 2026-11-06 06:30 UTC on, a 0x33 write that changes only bits 8 and 0 (GP0, the line to MCU PB1) is allowed, and the firmware puts 0x33 back after the module |
| `delay_us(us)` | busy wait, any length |
| `tune(hz, wide)` | receive on `hz`: band switch, PLL, wide or narrow filter, RX on |
| `rx_hz()` | the frequency the pager listens on |
| `ms()` | ms since the module started, 32 bits |
| `key_down()` | 1 while any key is held |
| `scratch(&bytes)` | RAM free while the module runs: the rest of the shared block past the code and tag (up to the telemetry text, if the module keeps one), word aligned, its size in `bytes`. About 11.7 KB less the code in the SPI images; 4 KB less the code before 2026-11-08 |

Level 2 is receive only: there is no way to key the transmitter. After the module returns the
firmware puts the radio back on the pager's frequency and sets the FSK receiver up again,
whatever the module changed. `KM_RSSI_DBM(fd_read(0x67))` is the RSSI in dBm. A module that
uses level 2 says so with `#define K6_MODULE_LEVEL 2` before including `k6_mod.h`;
`modem.k6 mod` reads it and puts it in the header (`--api-level` for a `.bin`).

## API level 4: upload ARQ, telemetry, calibration

Images from 2026-11-06 evening on (`KM_LEVEL` 4) add three calls and two over-air services. Level
3 (`tx_on`, `tx_pa`, `tx_off`: docs/k6-ft8-wspr.md, PR #71) is unchanged.

| Call | What it does |
| --- | --- |
| `key_poll()` | 1 while any key is held, from one GPIO read with all rows driven (a few us; `key_down` scans six rows, about 60 us). The lean images fall back to `key_down` |
| `cal_read(buf, n)` | the first `n` (up to 64) bytes of the calibration sector, SPI flash 0x10F000; n, or -1 (lean images) |
| `cal_write(buf, n)` | erases that sector and writes `n` bytes; once per run and never while keyed; n, or -1 |

**Upload ARQ.** A module sent with magic `0xAE` (`modem.k6 mod --arq`) asks for a reply. When
the transfer stops (2 s after the last packet heard, and the channel below -90 dBm), the K6
keys up on 440.850 MHz only, at low power, and sends a 64-byte report twice in the pager's PHY
and baud (F/1800 by default), then an MCW ID (AG7EW at 25 wpm, on the tone oscillator). It
sends at most 16 reports a transfer and waits up to 30 s for a busy channel, then gives up.
The serial log shows `K6 ARQ tx ...` and `K6 ARQ sent ...` around each.

| Report byte | Field (de-whitened, CRC-16 of bytes 0-61 at 62-63 as module packets) |
| --- | --- |
| 0-4 | `0xAF`, kind (1 status, 2 text), seq, part (0 for the status), parts (1 + text parts) |
| status 5-13 | transfer id, chunks held, chunk count, last `km_packet` result, state (0 partway, 1 ran, 2 refused), serial (once checked) |
| status 14-45 | while partway: a bit per chunk still missing, chunk 0 in bit 0 of byte 14 |
| status 46-61 | call sign (6 bytes), the module's return value (i32), its run time in ms (u32), free stack (u16) |
| text 5-61 | 57 characters of the module's `say` lines, newline-separated |

A partway report lists the missing chunks, so the sender resends just those with the same
serial and transfer id. A repeat of a chunk after the run asks for the report again.
`tools/k6_upload.py` does the whole loop on hamlet: it plays the upload on the IC-7100,
records the IC-7100's receive audio with `parec`, decodes the reports (`modem.k6 reports
REC.wav` does the same for any recording), resends what's missing, and stops on "ran" or
"refused".

**Telemetry.** `--telemetry N` (with `--arq`) puts N in header byte 14: while the module runs,
the K6 keeps its `say` lines (up to N x 57 bytes, at most 12 parts) in the top 696 bytes of the
shared block (the idle PWM audio ring before 2026-11-08), which the module's scratch then stops
short of, and sends them after the status report. No serial cable is needed, so the speaker stays on.

**Calibration.** `c/k6/mod/ifcal.c` (level 4) measures where a bare carrier on 440.850 lands on
the GP0 IF with the AFC off and writes a 32-byte record (`c/k6/mod/k6_cal.h`: magic `K6CL`, the
offset in 0.01 Hz from the nominal 16927.083 Hz, the same in ppb, the frequency, the block count,
spread, RSSI, and a check word). An IF-tap module can read it with `k6cal_get()` and scale it to its
own frequency with `k6cal_off_chz()`. The FT8 front end does (`gp0rx.h`: ft8wf, ft8wf_inv,
ft8rx, which print `cal=` in 0.01 Hz on their start line, 0 for none), so FT8 goes at its true
audio frequency instead of an `--f0` aimed by hand. iflive finds the offset itself with its
sweep and AFC; the softmodem thread will seed it from the record later. A module that reads the
record still declares the level it needs otherwise: `k6cal_get()` checks `api->level` first,
so on an older image it just mixes at the nominal IF. Where it lives: 0x10F000 is a 4 KB sector
of its own, after the v3.7 codebook (0x100000-0x107820) and sotto 600 / 400's (0x108000-0x10D320),
outside the repeater table (0x0F0000-0x0FFFFF) and above everything Aura uses (to ~0xED200).
`ifcal` writes it only if it is blank or already holds a `K6CL` record (else -11, nothing
measured or written). Run it once per radio: within 90 s of `ifcal start`, key
the IC-7100 on 440.850 in FM with no modulation for about 8 s. It refuses (-9) a carrier that
wanders more than 30 Hz or fewer than 16 good blocks, and the record stays as it was. Before
deciding, it lists every block's IF (`ifcal b0 16763 +1 -2 ...`: the first in Hz, then each
block's step from the one before), so a refusal shows whether the carrier drifted, jumped or
was noisy.

The lean images (`aura-pager-cbi`, `-only-table`, `-lr-table`, `-vlri`) have no room for any
of this: they take level 4 modules but send no reports, keep no telemetry and refuse
`cal_write`.

## API level 6: the module's screen

Added 2026-11-07 for the WSPR modules (Jeff: "Design a module with nice UI"), after level 5's
flash calls (`docs/k6-explore-app.md`). Until then a module could only talk over the serial
report; the screen was Aura's. The firmware keeps the state and owns the font and the display;
a module sends only text and pixel rows (`c/k6/k6_mod.h`):

- `lcd_text(line, s)`: six lines of 21 characters in FONT_6X10 at baselines y = 9, 19 .. 59
  (the explorer screen's layout), line 0 drawn as an inverted title bar;
- `lcd_bar(line, num, den)`: a progress bar after that line's text;
- `lcd_wf(ring, rows, head)`: up to 30 rows of 128 pixels (16 bytes each, MSB leftmost) from
  y = 21 down, in place of lines 2-4, `ring[head]` on top. The rows stay in the module's
  memory, so the firmware needs no buffer for them;
- `lcd_flush()`: draws the whole frame and sends it to the display (about 20-40 ms; 0, or -1
  with no display: the lean images leave the screen out, 1 KB they don't have, so a module
  there keeps to its serial lines).

The firmware's part is in the Aura patch: `ModScreen` and the four calls in `app/pager.rs`,
`draw_mod_screen` in `ui/pager.rs`, and a hook `main.rs` registers once with the display
(`ui::hook_mod_screen`), since modules run inside the main loop's calls and never while it
draws. When the module returns, the pager's own screen comes back. About 190 B of RAM.

**One build for old and new images.** A module reads `api->level` at run time and calls the
screen only when it is 6 or more; its `K6_MODULE_LEVEL` stays what its other calls need. So
`wsprtx` (level 3) and `wspriq` (level 2) run on today's images without a screen and draw one
on a level 6 image. The emulators (`tests/test_k6_i2s.py` `run(lcd=...)`, `tests/test_k6_ft8.py`
`emulate(lcd=...)`) record each flushed frame, and fail a screen call below level 6.

## API level 7: the green RX LED

Added 2026-11-08 (Jeff: "indicate modem receive activity by blinking the green LED in the front
of the radio by the volume knob"). The LED is MCU pin PA3 (Aura's `G_LED`, push-pull, active
high; stock Aura lights it while the squelch is open, which main skips in pager mode, so in
VOICE MSG the pin is free). One call, `led(on)`: 1 lit, 0 dark. The core puts it out when the
module returns.

The pager itself blinks it once a packet while its modem receives (no module needed):

- **Chip FSK packets** (PHY F and D: voice messages, control packets, module chunks): lit at a
  packet's sync (or its first FIFO-full), dark 120 ms later. A burst at PHY F (a packet every
  391 ms) blinks about 2.5 times a second. A false sync on noise blinks it once.
- **The coded 4FSK session** (PHY G, `c/k6/ifpager.c`): lit at each sync word, dark 144 symbols (120 ms)
  later, through the same `api->led` (the session gets the module table); a packet every
  0.92 s at rate 1/2. The check runs with the session's key poll (every 256 counts, about
  3 ms), away from the receiver's bursts, and keeps no state (it sets the LED from the last sync
  word each time: the session's stack is at its test limit). `test_session_blinks_the_led_once_a_packet` checks
  the pattern on the emulated M0 and that the session keeps its CPU margin.

A module reads `api->level` and calls `led` only on 7 or more, as with the screen; the
emulator (`tests/k6_live_emu.py`, `api_level=7`) records each call as `(ms, on)`.

The lean images (`aura-pager-cbi` and the table images) keep the LED dark and their `led` is a
no-op: cbi had 96 B of flash to spare and the LED took 184 (CI, 07bb380). Gated off it costs
28 B there. The other images pay about 300 B.

## API level 8: the radio's clock

Added 2026-11-09 (Jeff OK'd it for BLOWFANG's time sync). `clock_set(utc_ms, date, w_ms)` and
`clock_get(&utc_ms, &date, &w_ms)`: one UTC clock in the core, kept between module runs until a
reboot, fed by aisclk or aprsclk and read by any module. The fix with the smaller bound wins;
the core widens a fix's bound by 250 ppm as it ages. Units, rules and costs are in
`docs/k6-aprs-clock.md` ("Making it the radio's clock"). A module reads `api->level` and calls
them only on 8 or more; `clkget` (level 8) says the clock. The emulator
(`tests/k6_live_emu.py`) keeps the same clock: set `Emu.clock` to start with one, and
`Emu.clock_sets` records each call.

## The SPI module library

Added 2026-11-09 (Jeff approved it 2026-11-08: modules written to SPI once per install).
`c/k6/k6_lib.c` keeps up to 12 module images in the radio's SPI flash, so a module that runs
often (an EXPLORE page, a decoder) goes over the air once and then runs from the radio's own
flash on a control packet. The SPI flash isn't memory-mapped, so a run still copies the image
into the shared block first, as an upload does; reading 11.7 KB takes about 16 ms.

- **Where:** slot n at `0x110000 + n * 0x4000` (16 KB each, 0x110000-0x13FFFF), above sotto's
  codebooks (0x100000-0x10D320) and the calibration sector (0x10F000). A slot holds the image
  exactly as it arrived (header, code, tag) and 0xFF after it.
- **Install:** `python -m modem.k6 mod OUT.wav SRC.c --key-file KEY --install N` sets header
  flag `KM_F_LIB` (byte 13) and the slot in byte 15. The K6 checks it as any module (tag,
  serial), then, instead of running it, erases the sectors it needs, programs it a page at a
  time and reads it back: `K6 LIB put slot=N r=LEN` (or a negative `KL_*` from
  `c/k6/k6_lib.h`). With `--arq` the report says `ran` with that number as its return. A
  module image never runs straight from an install packet, even on an image without the
  library (km_packet leaves it unarmed).
- **Run:** `python -m modem.k6 ctl OUT.wav --action lib --slot N` (control action 11, the slot
  in byte 4) reads the slot into the shared block, checks the header and the tag again and runs
  it through the same path as an upload (`K6 LIB run slot=N r=CODE_LEN`, then the usual
  `K6 MOD run` / `K6 MOD ret` lines). Refused while a message plays (`r=-9`); the stored message is
  dropped, as by an upload. `--slot 255` only prints the slots, as the boot does:
  `K6 LIB slot=N r=0 code= serial= level=` for each used one, then `K6 LIB used=N`.
- **Rules:** the only SPI writes are an install's, inside 0x110000-0x13FFFF, page and sector
  aligned, never while the radio transmits (the core's flash calls refuse them). Nothing else of
  the radio's state goes there: never the message store. A worn or overwritten slot fails its
  tag (`r=-3`) and doesn't run.
- **Serials:** a run from a slot skips the serial check (the slot was checked when it went in;
  the serial guards the air), and never lowers the loader's last serial, so loading an old slot
  doesn't let an old recording replay.
- **Images:** the `spilib` feature: pager-only, lr, vlr, vlrh (since 2026-11-10: #31 left it
  5.9 KB free) and explore. Not the lean images (cbi, -table: they never take modules that need
  it). On those an install image checks but never runs (`K6 MOD ret=-100`), and nothing is
  written.
- **Tests:** `tests/test_k6_lib.py` runs the library and the loader on this machine against an
  emulated NOR flash (`c/k6/kl_host`), checking every write against the rules above.

## Security

- **Signed.** A module runs only if its SipHash tag checks under the key the image was built
  with. SipHash-2-4 is a keyed MAC; forging a tag means guessing 64 bits, one transmission
  per guess.
- **No key, no modules.** An image built without `K6_MOD_KEY_FILE` (CI's) holds a placeholder
  and refuses every module (`r=-5`) until `modem.k6 keyimage` writes your key over it. A keyed
  image holds the key: don't publish it, or the key file.
- **Replays.** The K6 refuses a serial that isn't newer than the last module it ran since
  power-on (`r=-6`). After a reboot a recorded transmission of an old module of yours would
  run again; that's the gap a boot nonce would close later.
- **The module is trusted code once it runs.** The M0 has no MPU, so a bad module can still
  hang the radio with interrupts off until a power cycle. A fault or a hung key-up reboots it
  (txdog, below). Recovery is the usual one: power off and on, or reflash.
- **A stack overrun can leave the transmitter keyed** (2026-11-08 16:53-17:09Z). dmrrx returned
  with `stack_free=0` on the vlrh image (3,112 B of stack): the overrun had written over RAM
  below the stack, the core's ARQ report then printed a garbage status (`xfer=243
  have=109/110`), keyed up on 440.850 at Low power and hung before its first packet. A bare
  carrier stayed up for about 16 minutes, with no K6 ID, until Jeff switched the radio off. A
  HardFault on this MCU landed in the bootloader's `b .` (its vector table at 0x08000000 is the
  one in use: no VTOR), so no handler of Aura's ran. Since then the core reboots
  instead when a module returns with under 64 B of stack never touched (`K6 RAM stack ran out`),
  checks the same before an ARQ key-up, and won't key up on a status with `have > count`. A
  module must show real headroom in the emulator (about 512 B or more at its deepest) with the
  stack the target image gives it before it goes on air.
- **txdog is OFF for now (2026-11-08 19:51Z).** The first voice image with it on (acd06ba)
  reset in a loop about 5 times a second, before its first serial line; `k6_update.py --manual`
  caught one of the bootloader's 200 ms windows and flashed 843b8e0 back with no hands on the
  radio. The table is now only in the `txdog` feature's diagnostic image (aura-pager-vlrh-txdog);
  the others say `K6 TXDOG OFF` on the serial report (the debug UART prints nothing in release
  builds). RAM modules on 843b8e0 since (20:02-20:13Z, receive only) ruled out the obvious:
  - `vectab`: the live table at address 0 equals flash 0x08000000 (48 of 48), all bootloader addresses.
  - `loram`: nothing writes 0x20000000-0x20000FFF in a second of Aura running. The bootloader's
    RAM is 0x20000000-0x2000046F (its 200 ms count, a 1 KB RX buffer at 0x40); its SysTick and
    USART1 trampolines only read the UserVectors at 0x20001000.
  - `remapsoak`: the same SRAM table held 10 s with interrupts on, HardFault and a TIM14 handler
    included, ran clean (`rs ... a=1 t14=01 back=1`).
  So the loop comes from how the image sets the table up at boot. The diagnostic image prints
  `K6 TXDOG remap`, `K6 TXDOG on` and `K6 FAULT pc lr` straight to USART1, so each pass of a
  loop says where it faulted. The last boot's note moved to `.uninit`: the bootloader clears its
  own RAM, where the vector slots are, at every reset.
- **The diagnostic image works (006ee1b vlrh-txdog, on the K6 2026-11-08 21:19-21:27Z).** It
  booted normally, three times, and:
  - `dogfault`: `K6 FAULT pc lr 20001d38 0800c479` (the module's UDF), a reboot in under a
    second, then `K6 FAULT last boot pc=20001d38 lr=0800c479`: the `.uninit` note survives the
    bootloader.
  - `dogtx`: the CW ID, then keyed with the PA off (S9+20 on the bench IC-7100 even so) and hung;
    `K6 TXDOG key-up over its limit` at 19.4 s against 18 s, the carrier gone the same second,
    then `K6 TXDOG last boot: key-up ran 19403 ms, limit 18000 ms: rebooted`.
  - `remapspeed`: code runs as fast with SRAM at 0 as with flash (`rsp m3=00ca m2=00ca`).

  Why acd06ba looped is still open. It loops at every flash (again at 22:14Z), while
  `aura-pager-vlrh-txdogq` (vlrh-txdog without its USART1 lines, 61cc53a) boots and passes
  `dogfault` too (22:57Z), so the lines are not it: the note's old place, vector slots 45-47 in
  the bootloader's RX buffer, is the one difference left.
- **txdog is on again in every image that transmits** (the `txdog` feature in build.sh: pager-only,
  lr, vlr, vlrh, explore; the lean images never transmit), with the tested code: the note in
  `.uninit`, the boot lines `K6 TXDOG remap`/`K6 TXDOG on` and `K6 FAULT pc lr` straight to
  USART1. A new image that loops at boot anyway: `k6_update.py IMAGE --manual`, Enter at once.
- **EXPLORE image on the K6 (f70e79e, 23:20-23:26Z):** boots with txdog on, `dogfault` reboots
  it in a second, it goes into EXPLORE after 120 quiet seconds, and the bench's CW ID on 440.850
  wakes it (`K6 EX beacon -25 dBm: VOICE MSG for 60 s`, 4.2 s into the ID), long enough to flash
  it with nobody at the radio. But its next boot had no `K6 FAULT last boot` line: `.uninit`
  lands lower in the smaller images, inside the bootloader's stack (it starts at 0x20004028), which
  overwrote the note. PR #162 moved it to 0x20000FF0, below the UserVectors.
- **EXPLORE image 0fb4d6c (2026-11-09 00:05Z):** `dogfault` again rebooted it in a second, and
  again no `K6 FAULT last boot`: 0x20000FF0 lost the note too. Everything below 0x20001028 is the
  bootloader's own RAM (Aura's starts right after its UserVectors), and the bootloader evidently
  clears it at reset (its startup's zero-fill, likely; not read in its code). The note now sits
  at 0x20005FF0, the top 16 bytes of SRAM: memory.x leaves them out of Aura's RAM, so the stack
  starts below them, and the bootloader's stack (from 0x20004028, growing down) never gets there.
- **Note at 0x20005FF0 on the K6 (af064d6, 2026-11-09 00:26-00:28Z):** `dogfault` on the EXPLORE
  image and then on vlrh: each rebooted in a second and its next boot printed `K6 FAULT last boot
  pc=20001eac lr=0800e289` (EXPLORE) and `pc=20001d38 lr=0800c481` (vlrh), the module's UDF.
- **txdog: faults and hung key-ups reboot the radio** (`hal::txdog` in the Aura patch). The
  `vecprobe` module showed on the K6 (2026-11-08 17:51Z, `alias=1 fault=1 back=1`) that SYSCFG
  CFGR1 MEM_MODE = 3 maps SRAM at address 0 and a fault then reaches a handler of ours. At boot
  the image copies the bootloader's 48 vectors to 0x2000_0000, points HardFault and TIM14 at
  Aura's and maps SRAM at 0 (`K6 TXDOG on cfgr1=00000002`; `OFF` if the remap doesn't read
  back, and then nothing changes). SysTick and USART1 still go through the bootloader's
  trampolines. A HardFault notes its pc and lr in unused vector slots and reboots at once
  (boot soft-resets the FD6818: transmitter off); the next boot says `K6 FAULT last boot pc=
  lr=`. TIM14, uptime's 10 kHz counter, interrupts at each wrap (6.55 s): a key-up older than
  its limit reboots (`K6 TXDOG last boot: key-up ran N ms, limit M ms`). Every enter_tx starts
  the clock with 200 s (Aura's PTT TOT tops out at 180 s; SSTV 330 s), and the pager tightens
  it: 30 s for an ARQ report, 20 s for the calibration send, a module's tx_on time + 10 s.
  Every reboot goes through `txdog::reset`, which maps flash at 0 again first. It can't catch
  a hang with interrupts off, or a fault while stacking a fault (the core locks up). The `lean`
  images (codebook install, -table fallbacks) never transmit and leave txdog out for room. Bench
  tests: `dogfault` (a deliberate UDF) and `dogtx` (CW ID, then keyed with the PA off and hung),
  both uploaded without ARQ.
- **txdog armed only around key-ups (2026-11-09).** On the K6, MEM_MODE 3 turns the flash
  prefetch off (FLASH_ACR 0x12 -> 0x02) and it won't come back in that mode; in MEM_MODE 2 it
  does. RAM modules measured it on 0b2a94c vlrh: `fdtime` timed one FD6818 register read at
  369 us and td_read's pair at 734 us; `pfprobe_m2` (MEM_MODE 2, PRFTBE) got the pair to 195 us
  and ACR 0x12, back to 0x02 in mode 3; the HSI switch PR #18 tried made no difference
  (`pfprobe_hsi`, `pfprobe_pf`, `pfprobe`: the K6 stayed up, ACR stayed 0x02). With the table
  mapped all the time, every txdog image ran flash code slower, and EXPLORE's DMR detector,
  which drains its DMA ring (356 us a lap) between those reads, never heard DMR. Now the table
  is mapped ("armed") from each key-up (`keyed*`, a module's `tx_on` included) to `unkeyed`; at
  rest main flash is at 0 with the prefetch on, TIM14's interrupt is masked (the bootloader has
  no handler for it) and its wraps are counted by `txdog::poll` in the main loops, in a module's
  `ms`/`ticks` calls and in every `ticks`. A module that doesn't key runs at rest, with the
  prefetch (a LISTEN slot module hears DMR). For room the `K6 TXDOG remap` and raw `K6 TXDOG on`
  boot lines are gone, the report's `limit=` with them, and the over-limit line is
  `K6 TXDOG over limit`.
  A fault at rest hangs the radio as before txdog, with the transmitter off (every key-up arms
  it first): `dogfault` now only reboots the radio when it faults keyed.
- **SysTick's interrupt off at boot (2026-11-09).** With the prefetch back (ACR 0x32 at rest),
  `fdtime` still timed td_read's pair at 382 us; `fdirq` timed it at 196 us under PRIMASK, with
  a counting loop losing only 3% to interrupts, and read SysTick CSR 0x00010006: the bootloader
  leaves TICKINT on and Aura never cleared it, so every 1 us `delay::us` of the FD6818 bit-bang
  ended in a SysTick exception through the bootloader's table. main now clears it.
- **The SPI library** (above) holds only images whose tag checked when they arrived, and checks
  the tag again on every run; a run from a slot doesn't lower the replay serial.
- **Part 97.** The code goes out in the clear and this format is published; the tag only
  proves who sent it.

## Running it

On the machine that builds the K6 image and drives the IC-7100:

```sh
python -m modem.k6 modkey ~/.config/sotto/k6-mod.key      # once
K6_MOD_KEY_FILE=~/.config/sotto/k6-mod.key sh firmware/k6-aura/build.sh
# flash out/aura-pager-lr.bin (sotto 1200) or out/aura-pager-only.bin, e.g. tools/k6_update.py
# (out/aura-pager-cbi.bin once first, on a radio without the codebook in SPI flash)
python -m modem.k6 mod hello.wav c/k6/mod/hello.c --key-file ~/.config/sotto/k6-mod.key \
    --phy F --payload XL
# key the IC-7100 and play hello.wav as for any other packet WAV
```

Without a toolchain, use the CI artifact of the *K6 pager firmware* job instead. Its images
carry a 16-byte placeholder where the key goes, and `mod-hello.bin` is the module's built code:

```sh
python -m modem.k6 keyimage aura-pager-lr.bin keyed-lr.bin --key-file ~/.config/sotto/k6-mod.key
# flash keyed-lr.bin
python -m modem.k6 mod hello.wav mod-hello.bin --key-file ~/.config/sotto/k6-mod.key --phy F --payload XL
```

The K6's serial report then shows, for a good run:

```
K6 MOD r=1 xfer=.. chunk=0 have=1/4
K6 MOD r=1 xfer=.. chunk=1 have=2/4
K6 MOD r=1 xfer=.. chunk=2 have=3/4
K6 MOD r=3 xfer=.. chunk=3 have=4/4
K6 MOD run serial=... code=366 B at 0x2000.... stack=... B scratch=... B
K6 MOD say hello world from RAM at 0x2000....
K6 MOD say sum of squares 0x9f31fc58 in 0x.... ticks of 100 us
K6 MOD ret=107 (0x6b) in .. ms stack_free=... B
```

`r` is `km_packet`'s result: 1 a new chunk, 2 a repeat, 3 the module checks and runs, -1 bad
CRC, -2 a chunk out of range for the store, -3 bad header, -4 wrong key, -5 no key in this
image, -6 a replay. If chunks are missing, send just those again with the same serial and
transfer id: `--only 2,3 --serial N --xfer X` (the first send prints both). A good voice packet
in the middle of a module transfer drops the transfer (`K6 MOD dropped`).

## Writing a module

Copy `c/k6/mod/hello.c`. The rules, which the build checks where it can:

- No libraries: no `memcpy`, no `printf`. The M0 has no divide instruction, so no `/` or `%`
  on variables either (clang would call a run-time helper the module doesn't have).
- No writable statics (`.data`, `.bss`): the linker script refuses them. Use the stack, and
  keep it small: the module runs on the pager's stack, with about 700 B to spare. Level 2's `scratch()` gives the rest of the shared block
  for tables and buffers (about 11.7 KB less the code). `K6 MOD run` reports the stack room at the call (`stack=`) and the scratch size,
  and `K6 MOD ret` the stack never touched since VOICE MSG started (`stack_free=`).
- No tables of pointers: they need relocation, and the two-address check refuses them.
- Return quickly, or give a way out: the pager doesn't receive while the module runs. A
  long level 2 module polls `key_down()` and stops on a key.

Tests: `tests/test_k6_mod.py` (the loader on this machine with a test key, the thumbv6m
loader running the hello module in an emulated Cortex-M0, and the settle module against a
modelled receiver).

## The settle-time module (explorer phase 0)

`c/k6/mod/settle.c` measures how long after a retune the FD6818's RSSI reads true, the number
every sweep speed in the explorer design rests on. With a steady carrier on the pager's
frequency F, for each way of arriving at F (from F - 10 kHz, from F - 1 MHz, from the other
band; and leaving, F to F + 1 MHz), at wide and at narrow, it parks 5 ms on the start
frequency, tunes, waits t and reads RSSI, for t from 0 to 20 ms, four times each. The settle
time is the first t from which every reading is within 2 dB of the reading after 30 ms.

```sh
python -m modem.k6 mod settle.wav c/k6/mod/settle.c --key-file ~/.config/sotto/k6-mod.key \
    --phy F --payload XL
# play settle.wav to the K6 as usual; when the report says "waiting for a carrier",
# key the IC-7100 on the same frequency (FM, no modulation) for about 10 s
```

It waits up to 60 s for the carrier (RSSI 15 dB over where it started), and any key stops
it. The report, for each case:

```
K6 MOD say settle 10k W ref -61 dBm 700 us
K6 MOD say curve 10k W 0:-118 100:-118 200:-118 400:-118 700:-61
K6 MOD say    1000:-61 1500:-61 ...
...
K6 MOD say settle: done in 6328 ms, worst on-case 1500 us
```

`curve` is the mean RSSI (dBm) at each t (us). `probe on|off` lines dump the noise, glitch
and AF registers (0x63 0x64 0x65 0x6f 0x4f) and the status (0x0c) with the carrier on and off,
for the next step, the noise-based quick check. The return value is the worst settle time
in us of the cases that arrive on the carrier, -1 if no carrier came, -2 if a key stopped it.

## The receive coverage module (rxmap)

`c/k6/mod/rxmap.c` sweeps RSSI across a segment (the K6 receives only) or measures the IC-7100's
carriers on 2 m / 70 cm points. `tools/k6_rxmap.py` patches the segment into CI's
`mod-rxmap.bin`, uploads it and decodes the over-air summary. See [k6-rxmap.md](k6-rxmap.md).

## The RAM probe (ramprobe)

`c/k6/mod/ramprobe.c` (2026-11-07, the hiss band thread): Aura's `memory.x` gives the application
0x20001028 + 17720 bytes, ending at 0x20005560, but the KD32F328 is sold with 24 KB of SRAM, which
would end at 0x20006000. Nobody has claimed those 2720 bytes. The module writes two patterns to
every word of that range, holds a third for 50 ms, restores what it found, and says
`ram probe good=2a8 bad=none held=2a8 alias=none` (0x2a8 = 680 words = all of it; alias = a lower address that moved with the probe, a mirror of a smaller SRAM; on 2026-11-07 the K6 said good=2a8 bad=none and ran on, so the RAM is there, all of
it). If the SRAM ends lower the first access past the end faults and the radio resets, so run it
when a reset is acceptable. If it holds, `memory.x` can grow by 2720 bytes: the hiss band image
(docs/k6-hiss-port.md) would get its fourth PWM segment and its 4 KB message store back.

## Band surfing (surf)

`c/k6/mod/surf.c` (2026-11-08, level 6): a bargraph of signal strength, the listening frequency
a bar with a caret (steady since 2026-11-10), always in the middle of a lens scale (2026-11-09:
2.5 kHz bins within +-20 kHz, all bins on the wide filter since 2026-11-10, wider ones out to a span of +-100 kHz to +-5 MHz, an axis with
log-paper ticks; it needs a voice image's arena), the side keys jumping from peak to
peak, UP/DOWN stepping a channel, BAND/zoom/monitor keys. It listens first (sweeps only while the bar's channel is
quiet) and scans the keypad matrix itself, since the API has no key identity. It marks the
repeaters the explorer saved (read-only, from the table in SPI flash) and names the one under the
bar. `surfauto.c` is the unattended bench build; it says its last bargraph over the telemetry.
See [k6-surf.md](k6-surf.md).

## GROUNDWAVE (gw)

`c/k6/mod/gw.c` (2026-11-09, level 6, the LED at 7) is a turn-based artillery game: two stations
on a hilly 128 x 30 field, wind, craters, a shop of radio-themed weapons (FCC Violator, 12V
Charger, CW Strike, Beverage, The Lid and more) and two shields (Ground Rod, Faraday Cage).
Hot seat passes one radio between two players. Radio play links two K6s on 440.850: each move
is three bytes in a 64-byte PHY F packet sent from the module's own over (tx_on, the FSK modem,
an MCW ID), ACKed, and both radios work out the flight from the same integer-only rules
(`gw_game.h`, also built for the PC as `c/k6/gw_host.c` and `modem/k6gw.py`). `gw_auto`,
`gw_call` and `gw_answer` are the hands-free bot builds. See [k6-gw.md](k6-gw.md).

## MIDI player (midi)

`c/k6/mod/midi.c` (2026-11-09, level 2, screen at 6, LED at 7): plays songs through the speaker,
receive side only. Four wavetable voices at 16 kHz on spksweep's PWM path; the songs are a table
at the end of the code (section `.k6tail`, last in `module.ld`), which `tools/k6_midi.py patch`
swaps for other MIDI files or text scores without a compiler. CI's build carries the
public-domain Nokia ring tones (Tarrega's Gran Vals phrase, Fur Elise, Mozart 40, Bumblebee,
Valkyrie) and Ode to Joy. See [k6-midi.md](k6-midi.md).

## Insights

Ideas from the K6 work that might change the plan live one per file in
[k6-insights/](k6-insights/README.md), so two threads adding ideas no longer edit the same
lines. `python3 tools/k6_insights.py` lists them; `python3 tools/k6_insights.py new "Title"`
starts the next one. Numbers 1-47 are the ones this page used to carry, so "insight 31"
elsewhere still means `k6-insights/31-...md`.
