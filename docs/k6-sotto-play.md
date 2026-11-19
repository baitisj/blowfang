# Playing what a sotto receiver hears (API level 11)

Jeff, 2026-11-19, on the playback design: "Please proceed with playback". His answers on the
design: keep the played message in the store ("I assume the store will be treated as a ring
buffer"), show the caller's callsign while it plays ("Yes please"), and look at DMR room "once a
new flash is required".

The K6 can't synthesize sotto while it receives C4FM: the receiver and the decoder each want the
CPU and most of the 11,776 B arena. So a receiver module stores the call, returns, and the core
plays it. This is the same store and the same player as VOICE MSG.

## The calls

- **`sotto_play(sf, n, codec, flags, from)`** only notes the message and returns 0.
  - `sf` points to n slots of 6 bytes in sotto 1200 (`KM_SOTTO_1200`, the voice packet magic 0xA9).
  - `from` is the caller's callsign, up to 10 characters (`KM_FROM_MAX`), or 0.
  - `KM_PLAY_RERUN` in `flags` reloads the module from its SPI library slot after playback, so
    the radio listens again. A key during playback stops that. A RAM module sent over the air is
    never run again, because the store took its place.
  - The last call before the module returns counts. n 0 cancels.
  - It returns -1 when n is more than the store holds or `sf` is not in the module's memory, and
    -2 for a codec the image doesn't play.
- After `k6_main` returns, the core moves the slots into the message store with `sp_put`
  (c/k6/sotto_pager.c), which also works when they overlap it. The core plays them as VOICE MSG
  plays a message and leaves them there, so VOICE MSG's play key replays the last call.
  - Serial says `K6 PLAY put r= n= codec= crc= from= rerun=`.
  - The arena screen shows `FROM <call>` while it plays.
- **`msg_mem(&bytes)`** gives a receiver memory outside the arena for the message it is
  hearing: the bootloader's RAM, 0x20000100-0x20000FFF (3,840 B, about 25 s of sotto 1200).

## The bootloader's RAM

SRAM 0x20000000-0x20001027 lies below Aura's region. The bootloader uses it only before Aura
starts. Once Aura runs, only hal::txdog's vector table (0x20000000-0x200000BF) is there, and the
`loram` module saw nothing else write to it. docs/k6-hiss-port.md calls RAM0 off limits because
of that vector table; `msg_mem` starts above it.

ysfrx puts its DMA ring (1 KB) and the message there, which leaves the arena for code and the
decoder. Without it, the callsign decoder and the playback hand-off don't fit next to the
receiver.

**On the radio (k6-ysfcsd1, 2026-11-19 17:18-17:21Z, image 8a78fb5).** A `YX_RAM0` build kept its
DMA ring and message there for 2.5 minutes. All six Pluto calls came out byte-exact (crc d821 and
5d15) with the callsign, and the radio carried on normally.

**The soak test (`c/k6/mod/ram0soak.c`).** Jeff: make it safe, so that nobody has to reboot the
radio by hand. A write check can't see a read: if the bootloader's interrupt code read a flag in
this range, filling it could hang the radio. So the module:

- arms the independent watchdog (IWDG) first, about 6 s, and feeds it while it runs. A hang
  anywhere, interrupts included, resets the radio.
- leaves txdog's crash note (0x20005FF0, which a reset keeps) naming the block it is on. A
  watchdog reset's next boot prints `K6 FAULT last boot pc=0a30000N lr=0a3050ac`, where N is the
  256-byte block (f = 0x20000F00, 0 = the hold that follows).
- fills from the top down, a block at a time, starting at the end farthest from the bootloader's
  own 0x20000000-0x2000046F. It holds each block for a second with interrupts running, then
  reads back everything filled so far: `r0 b=N bad=0`.
- holds the whole range for a minute, reading it back every second. It checksums the two ends
  it never writes, before and after: txdog's table (0x20000000-0x200000FF) and the UserVectors.
  `r0 done bad= first= blk= lo=same uv=same`
- ends with a planned reset (`r0 reset`), clearing the note first, because the IWDG can't be
  stopped once started.

Tests: `tests/test_k6_ram0soak.py`.

## ysfrx

- It reads the caller from the header's CSD1 field, or from the terminator's when the carrier
  detect lost the header (c/k6/ysfdec.h, `yd_csd_end`). The summary line ends ` from=AG7EW`. It
  shows `fich=` only when that differs from `fr=`, so the callsign fits `say`'s 60 characters.
- When the call ends with at least one frame's worth of superframes decoded (5; noise calls
  have none), it calls `sotto_play` with `KM_PLAY_RERUN` and the callsign (level 11 images only,
  read at run time), and returns. Lost superframes stay in the message as the receiver left
  them (tests/test_k6_ysfrx.py checks a call with frames lost).
- Bench builds for a level 10 image use `YX_RAM0=1`, the same RAM at a fixed address, and
  `YX_DUMP=1` to also print the superframes.

## Not done yet

- **A ring of past calls.** The store keeps one message. A ring wants the core to own
  `msg_mem` between runs, with the receiver writing into the free part.
- **DMR room.** Jeff: "once a new flash is required".
