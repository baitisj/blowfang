# [S12] sotto 1200 receive and decode on the UV-21 Pro

Status (2026-11-28): step 1 of the handoff plan (audit, fit check), the decoder build and its
benchmark are done, on the host and on the radio, with nothing flashed. The firmware integration
(stage B below) waits on one decision of Jeff's: whether the codebook may be written once to the
UV-21's SPI flash.

## Audit

| Part | What the UV-21 Pro has | Source |
|---|---|---|
| Speaker audio | PA11, TMR1 CH4 PWM with DMA: the K6's path, so `sp_pcm_to_pwm` carries over | [uv21-characteristics.md](uv21-characteristics.md), "Other" |
| Audio amplifier enable | PB2 (active high, from the stock image). PB7 is probably the flashlight, not the amp | `radios/uv21/fw/src/board/rf.rs` |
| Packet receive | the BK4829's own FSK modem (REG_58-5F), as the K6 pager uses it; `fd6818b.rs` already has the register names and an MDC1200 RX path, no sotto packet path yet | `radios/uv21/fw/src/drivers/fd6818b.rs` |
| Framing | the K6 pager's 0xA9 voice packets (profile L 64 B or XL 128 B, PN9 whitening, CRC-16/CCITT-FALSE, interleave groups), which `c/k6/sotto_pager.c` parses unchanged | `c/k6/sotto_pager.h` |
| RAM | the 12 KB `hal::arena`, which SURF (4,000 B) and RAM modules share and never hold at once | `radios/uv21/fw/src/hal/arena.rs` |
| SPI flash | 2 MB, blank from 0x14C000 to the end in the dump `uv21-flash-2e`; the port's `norflash` driver is READ_ONLY | `radios/uv21/fw/src/drivers/norflash.rs` |

## Fit: why the codebook goes in SPI flash

The K6's sotto 1200 decoder, built for the Cortex-M4 with ATfE clang 22 (`-Oz`, the K6's codec
flags, `ld.lld --gc-sections`):

| Build | Flash |
|---|---|
| codebook in MCU flash (`fx_cb0`-`fx_cb3`, 30,720 B packed) | 44,348 B |
| the same, codebook unpacked (what `FX_ARM_DSP` does on any ARMv7E-M by default) | 83,572 B |
| codebook in SPI flash (`-DFX_CB_SPI`, `radios/uv21/sotto/build.sh`) | **14,749 B** |

Port 21 (0c02e0a) is 26,104 B of the 61,440 B app area, leaving 35,336 B. A lossless repacking of
the codebook was estimated at 24.5 KB (a bit width per coefficient position), 23.7 KB (widths per
group of 32 vectors) and about 19.2 KB at best (order-0 entropy), which still leaves the
MCU-flash build 1 to 6 KB over once the firmware's own glue is added. A RAM module can't hold the
codebook either (12 KB arena). So the plan is the K6's SPI image (`sotto-cb.bin`: 32-byte header
and the 30,720-byte table, CRC 0xd5856ecd), written once into the blank area, with the port
reading it and never writing anywhere else. That one write needs Jeff's OK: it lifts READ_ONLY for
one verified range, and Jeff ruled out SPI writes for sotto on the K6 (2026-11-07).

## Build

`sh radios/uv21/sotto/build.sh` makes `radios/uv21/build/sotto/`:

- `libsotto_uv21.a`, what the port will link: `c/k6/sotto_pager.c -DSP_LR` and the fixed-point
  decoder, with the K6 SPI build's codec flags (bit-exact with the K6), the decoder's pool in the
  firmware's arena (`SP_DEC_POOL`, 3,232 B), synth's arrays in that pool rather than on the stack
  (`FX_SYNTH_STATE`: port 21 has about 2.5 KB of stack spare) and the yield hook (`FX_YIELD_HOOK`);
- `-DFX_NO_ARM_DSP -DFX_WIDE_MUL`, which keeps the K6's packed 20-byte codebook vectors (the
  unpacked ones exist only for the encoder's SMLAD search) and still takes 32 x 32 products whole;
- `sotto_fx_lr.c` at `-Os`: at `-Oz`, ATfE clang 22 builds it for thumbv7em so that it plays a
  different signal from slot 53 of a 70-frame test message on. `-O1`, `-Os` and `-O2` are
  bit-exact, `-fwrapv` and `-fno-strict-aliasing` don't change it, and a UBSan host build is
  clean, so it looks like the compiler's. The test below would catch a return of it;
- `bench.elf` (the library and the K6 bench's stand-in SPI flash) and `size.txt`.

## Benchmark and calibration

`radios/uv21/sotto/bench_uv21.py` runs the bench ELF in the Cortex-M4 emulator
(`c/fixed/cm4/run.py`'s timings), feeding it 0xA9 packets and decoding the message to the end.
The flash model was calibrated on the radio by the RAM module `radios/uv21/mod/wsprobe.c` (job
s12ws1, 13:29Z, port 21 at 120 MHz, no flash). It times the port's own `crc16_xmodem` running from
flash and then from a copy in RAM, using the DWT cycle counter:

| | Flash | RAM |
|---|---|---|
| crc16 over 1,024 B (the same 60 bytes of code) | 86,059 cycles | 135,218 cycles |
| word reads, 4 KB | 5,677 cycles | 4,111 cycles |

FLASH_PSR reads 0x01f3. The model gives that crc loop 103,451 cycles with no wait states and
249,892 at 3 wait states with no prefetch. So the prefetch hides the fetch wait states (code from
SRAM is slower still, being fetched over the system bus), and a data read from flash costs about
1.5 cycles more than one from RAM. The bench's defaults are 0 fetch wait states and 2 per flash
data read.

sotto 1200, 70 frames, profile XL, one packet lost (`bench_uv21.py ... --frames 70 --lose 1`):

| | Calibrated (default) | No prefetch at all (`--ws 3 --data-ws 3`, disproved) |
|---|---|---|
| decode, mean per 20 ms call | 567 k cycles (28.4 MHz) | 1,445 k (72.3 MHz) |
| decode, worst call | 970 k (48.5 MHz) | 2,495 k (124.8 MHz) |
| worst 40 ms slot vs 4.8 M cycles | **40 %** | 103 % |
| longest stretch between yields | 3.1 ms | 8.1 ms |
| per packet (`sp_packet`) | 10-11 k cycles | 21-24 k |
| stack | 1,444 B | |

The RF chip's 8-word FSK FIFO holds 71 ms of air at 1800 baud, so yields every 3 ms leave it
plenty of room. The SPI reads of the codebook (three 20-byte vectors a slot) aren't modelled; at
SPI1's speed they are tens of microseconds a slot.

`tests/test_uv21_sotto.py` (UV-21 CI) checks that the M4's PCM equals the K6's x86 build
(`c/k6/pager_host_lr`) sample for sample (XL and L, with and without a lost packet), and checks
the 40 % budget, the yield gap, the 16 KB flash budget, and that the build links no codebook and
no SPI write or erase.

## Stage B status (2026-11-28)

- Codebook: in the SPI flash at 0x1F0000 (job s12cb2, `radios/uv21/mod/cbspi.c`), read-back CRC
  de7d1e93 and header verified.
- Receive check: `radios/uv21/mod/sottorx.c` on `radios/uv21/mod/sottorx_core.h` (the K6 pager's
  F/1800 XL setup), staged as job s12rx1 (the Pluto sends a 23-packet sotto 1200 message on
  440.850, CW ID AG7EW); it runs in the background, which the runner allows from PR #310.
- Speaker path: `radios/uv21/mod/pwmtone.c` (TMR1 CH4 on PA11, AF2, 48 kHz carrier, 8 kHz
  samples), to run with someone listening.
- Firmware: `radios/uv21/fw/src/sotto.rs` and `radios/uv21/sotto/uv21_sotto.c`. **Long 0** in SURF
  (or `um s HHHHHHHH SSSS` on the cable) listens for sotto 1200 on the wake's frequency (440.850,
  or where a long MENU moved it) for 30 s, plays the message through TMR1 CH4 PWM with DMA1
  channel 5 as it streams in, and returns to SURF. The console's `so end ... pcm=` is the K6's
  PCM hash, so a bench run can be checked against `c/k6/pager_host_lr` with no one listening.
  Port image 42,144 B of 61,440 (local build). `tests/test_uv21_sottorx.py` runs it in the
  emulated radio: with the codebook it checks it and arms the receiver as specified, without it
  it plays nothing, and neither path writes the SPI flash or a transmit register.

### On air (2026-11-28, Pluto on 440.850, module sottorx, no flash)

| Job | PHY sent / set | Level at the UV-21 | Result |
|---|---|---|---|
| s12rx1 | F/1800 FFSK (the K6's default), Pluto -40 / -55 dB | (not logged) | 0 syncs |
| s12rx2b | F/1800, Pluto -40 / -30 dB | -76.5 / -66 dBm | 0 syncs, REG_0C bit 0 never set |
| s12rx3 | **D/2400** direct 2-FSK (REG_58 0x00E9, REG_72 0x60CB, RX HPF off), Pluto -45 / -55 dB, flat FM, 3 kHz | -81.5 / -91.5 dBm | **41 of 44 packets good, all 0xA9**, 22 of 23 heard each pass (packet 0 missed both times), 1 CRC fail each pass |

So the UV-21 receives the K6 pager's D/2400 rung, not its FFSK, as the RT-950's BK4829 did. Its
FIFO words carry the first byte on air in the low half (the K6's FD6818: the high half), so
`sottorx_core.h` swaps them (SR_SWAP). The port's receiver is built for D/2400; send with
`python -m modem.k6 tx MSG.sot OUT.wav --phy D --baud 2400 --payload XL --codec 1200`.

## Stage B plan

1. The codebook write. Either a one-time writer RAM module (uploaded in pieces, each verified by
   read-back against `sotto-cb.bin`'s CRC), or a narrow write window in `norflash` for that range
   only, held shut by default. Choose a 64 KB-aligned offset inside the blank 0x14C000-0x1FFFFF
   and confirm it is blank on this radio first (read it back from the radio and compare with
   `uv21-flash-2e`). `sp_flash_read` maps `SP_CB_ADDR` (0x100000, the K6's) onto it.
2. Link `libsotto_uv21.a` in `build.rs` beside SURF, and give `sp_flash_read` from the read-only
   `norflash` driver. `sp_flash_write` and `sp_flash_erase4k` are not linked (the test checks it).
3. A packet receiver in the port: the K6 pager's F/1800 setup (REG_58 0x3F01, REG_72 0x4898, the
   sync words, REG_5D's length, interrupt-style FIFO reads at 3 words) polled over the 3-wire bus,
   emptied from the decoder's yield hook while a message plays. First as a RAM module that counts
   good 0xA9 packets sent by the Pluto on 70 cm (`sp_check`'s PN9 + CRC), before any flash.
4. Playback: TMR1 CH4 PWM on PA11 with DMA, the amp on PB2, `sp_play_pwm` into a two-segment
   ring in the arena (SURF leaves while a message plays, as it does for a module).
5. SURF: a key that listens for sotto on SURF's centre, a screen line for the message state.
   Jeff decides the key.
