# One bigger arena: the core's buffers in the bootloader's RAM (module API level 12)

Jeff, 2026-11-19, after the RAM0 watchdog soak passed: "can we more effectively consolidate the
RAM arena somehow so other modules can benefit?"

## The idea

SRAM 0x20000000-0x20001027 lies below Aura's region (memory.x starts RAM at 0x20001028). The
bootloader uses it before it starts Aura and clears it at every reset. Once Aura runs, two parts
of it are live: hal::txdog's vector table (0x20000000-0x200000BF, while it is mapped) and the
bootloader's UserVectors (0x20001000-0x20001027, through which SysTick and USART1 reach Aura).
0x20000100-0x20000FFF (3,840 B) is idle: `loram` saw nothing write there, `ram0soak` filled and
held every block for a minute with interrupts running (k6-ram0soak2, docs/k6-sotto-play.md), and
a YX_RAM0 ysfrx kept its DMA ring and message there through nine calls.

PR #213 (API level 11) gave that RAM to one kind of module: `msg_mem` hands it to a receiver
for its message. A module that doesn't ask for it gets nothing from it. This change puts the
core's own fixed buffers there instead and gives the RAM they leave in Aura's region to the
shared block (hal::arena), which every module runs in:

| moved to `.ram0` | bytes | images |
|---|---|---|
| Aura's `App` (main.rs `APP_SLOT`) | 2,164 | pager-only, so all of sotto's |
| the serial report ring (hal::uart `TX_RING`) | 528 | all but lean |
| the serial receive ring (hal::uart `RX_RING`) | 336 | all |
| the module screen (app::pager `MOD_SCREEN`) | 196 | all |
| EXPLORE's known stations (`KNOWN`) | 256 | explore, voice-explore |

That is 3,224 bytes in every image (3,480 with EXPLORE). The block grows by 3,200, from 11,776
to **14,976 bytes**, in every image that plays voice from SPI codebooks (pager-only, lr, vlr, vlrh,
voice-explore); the stack keeps what it had, plus 24 bytes (280 in voice-explore). EXPLORE without
voice and the lean images keep their 5,376-byte block and get the 3.2 KB as stack.

Not moved: anything with a non-zero initial value (`.data`: EXPLORE's state `S`), the C library's
statics, txdog's crash note (it must survive a reset: 0x20005FF0) and the stack.

## How

- memory.x: `RAM0 : ORIGIN = 0x20000100, LENGTH = 0xF00` and a NOLOAD output section `.ram0`
  with `__sram0` / `__eram0`, inserted after `.uninit`. The linker fails the build if it
  overflows (it would run into UserVectors), and build.sh's fit line says `RAM0 n of 3840`.
- hal::ram0 (new): `clear()` zeroes the section, the first thing main does, so everything there
  starts out as it would in `.bss`; `used()` for the boot line.
- `#[link_section = ".ram0"]` on the five statics. The App only with `pager-only`: in all of
  Aura (a size report, it doesn't fit the flash) the App is bigger than the room.
- hal::arena `ARENA_BYTES` 14,976. The decoder pool (the block's tail) grows with it: EXPLORE's
  code pages in voice-explore get 3.2 KB more room too.
- Boot line: `K6 ARENA bytes=14976 at 0x2000.... ram0=3480 store=4096 ring=... dec=... need=...`.

## Module API level 12

No new calls. Level 12 means:

- the block is 14,976 bytes: header, code and tag up to 14,952; scratch 3,200 B bigger than on
  a level 11 image with the same code;
- 0x20000100-0x20000FFF belongs to the core: `msg_mem` returns 0 with `*bytes` 0. ysfrx already
  falls back to keeping its DMA ring and message in scratch, and gets more room there than
  `msg_mem` gave: 524 slots, 21.0 s of sotto 1200 with ATfE (18.4 s in `msg_mem`; the receive
  thread's numbers). `sotto_play` takes a message only from scratch;
- a module that needs more than 11,776 bytes declares `K6_MODULE_LEVEL 12`: an older image refuses
  its header (level > its KM_LEVEL) instead of running it short. `modem.k6mod.arena_bytes(level)`
  and `tlm_room(level)` give the tools the block for a level; `modem.k6 mod --telemetry` uses it.

Modules that write RAM0 directly must not run on a level 12 image. `ram0soak` and `ram0zero`
check `api->level` and refuse (`r0 refused: core RAM0`, return -2, nothing written). ysfrx's
YX_RAM0 bench path is only taken below level 11, so it never runs there.

## The checks before a flash (RAM modules, on the image already on the radio)

The 2026-11-04 images that put the message store at 0x20000100 hung at boot when the store was
cleared (commit bbe33d76), and nobody found out why. The soak since then wrote a hashed pattern,
never zeros, and the core now clears its buffers there at every boot. So before an image with
this layout goes on the radio:

1. **What the bootloader leaves there.** `ram0soak` (and `ram0zero`) now map the range before
   they write: `r0 map nz= first= last= blk=`, then up to 12 of the non-zero words
   (`r0 w 0120=08000abc ...`). Run right after a reset, that is what the bootloader left in its
   0x20000000-0x2000046F before starting Aura.
2. **Zeros.** `ram0zero` is `ram0soak` filling with zeros (watchdog-guarded, top-down, a minute's
   hold, planned reset). If a bootloader interrupt path tested a flag there for "not 0", this is
   where the radio would hang, and the watchdog would bring it back with a `K6 FAULT last boot
   pc=0a30000N` note naming the block.

Bench job: two `ram0zero` runs back to back, each woken by the CW ID first (after the planned
reset the K6 boots into EXPLORE): the first maps whatever the last module left, the second maps
a fresh boot.

**On the radio (k6-ram0zero2, 2026-11-19 19:12-19:20Z, image 8a78fb5).** Both runs passed. Each
map said `r0 map nz=000 first=00000000 last=00000000 bm=0000`. The second ran on a fresh boot, so
the bootloader leaves nothing at all in 0x20000100-0x20000FFF: clearing it at boot changes nothing,
and the 2026-11-04 hang had another cause. Every block held its zeros (`r0 b=f` .. `r0 b=1`, all
`bad=000`), then the minute's hold: `r0 done bad=000 first=00000000 bm=0000 lo=same uv=same`.
Each planned reset came back in under half a second (`K6 ARENA`, then EXPLORE), with no
`K6 FAULT`, and the K6 answered the CW-ID wake and went back to VOICE MSG afterwards. (The first
job, k6-ram0zero1, lost its log: its map line said `blk=f806`, the ysfrx call it found there from
k6-ysfcsd2, and the bench logger, modem.k6_harness, reads any `blk=` as decimal numbers and
stopped. The key is `bm=` since.)

Then the flash (Jeff's typed line), with: the boot line above, a module run (any, then one that
uses more than 11,776 B), VOICE MSG playback, EXPLORE, and the ysfrx playback regression
(k6-ysfcsd2's job: three Pluto calls byte-exact, `from=AG7EW`).

## The first flash hung (f541fbd, 2026-11-19 19:48Z)

The keyed ATfE image (CI run 44600504184, build ID 14ee47f5e3f21ed3) flashed completely
(117/117 KB) and then said nothing: it never answered the cable session, a 75 s listen got
0 bytes, and `k6_update.py --manual` found no bootloader window in 150 s, so it hung hard
(no reset loop) and needs a power cycle. The transmitter was never keyed. The radio checks
above all ran on the old image at run time, after a normal boot, never on an image that uses
the range from its first instruction right after an update.

**The cause: the free-RAM paint.** VOICE MSG's first entry paints the free RAM (from cortex-m-rt's
`__sheap` up to the stack pointer) to measure the stack later. link.x sets `__sheap` to the
location counter after `.uninit`, and memory.x's `.ram0` is inserted right there, so in f541fbd
`__sheap` was the end of RAM0, 0x20000E98, not the end of the statics (0x20005030). The paint
wrote 0xA5A5A5A5 from 0x20000E98 to the stack: over the bootloader's UserVectors at 0x20001000
(the SysTick and USART1 trampolines), `.data`, `.bss` and the arena. With interrupts back on, the
next USART1 interrupt jumped through a painted UserVector and the K6 stopped.

What the radio showed fits it: a cold boot (Jeff's power cycle, 20:15Z) printed `K6 ARENA
bytes=14976 at 0x200012dc ram0=3480 ...`, `K6 TXDOG on cfgr1=00000002` and the library list up
to slot 25, then nothing; the screen stayed white with the backlight on. The last ~500 bytes
missing are one serial ring's worth: `lib_list` is the last report before `sp_init` and
`paint_free_ram`, and the ring's tail never went out. In the f541fbd binary 0x20000E98 appears
five times (`clear`, `used` and the three `__sheap` reads), the end of the statics once.

Fix (aura-pager.patch, app::pager): the paint, `free_ram` and run_module's `stack=` use
`__euninit`, the end of `.uninit`, the last of the static RAM. The layout is unchanged. The
bootloader's RAM had nothing to do with it: `ram0soak` and `ram0zero` (map, fill, hold, with
`say` lines going out the whole time) showed that before. The 2026-11-04 store hang at 0x100 is
still unexplained, but that image had no `.ram0`-after-`.uninit` either way.

The boot-trace image (`aura-voice-explore-boottrace`, Cargo feature `boottrace`, built in CI
next to the release image, never bundled) stays as a tool for the next image that dies early:

- `K6 BT 0000000N sp x` lines straight to USART1 at each step of main: 1 UART up, 2 USART1
  trampoline, 3 txdog, 4 interrupts on, 5/6 around placing the App, 7 the main loops.
- `K6 BT 0000001N` (0x10-0x1B): what the bootloader handed over, read before RAM0 is cleared:
  DMA channels on and pointing below 0x20001000, USART1 CR1/CR3, NVIC ISER, SysTick CSR, the
  reset flags, MSP, non-zero words in RAM0, SYSCFG CFGR1, PRIMASK, and 0x20000440-0x2000046F.
- The independent watchdog as a net (about 13 s, kicked in hal::uptime::now): a hang resets
  into the bootloader's window, so `k6_update.py --manual` gets the radio back with nobody at
  it.

## Not done

- **A movable floor for a ring of past calls.** Jeff wants the store kept as a ring of calls.
  With one 14,976-byte block, the core can keep earlier calls at the block's bottom and load a
  receiver above them (modules are position-independent), so a receiver still gets about 11 KB
  and the calls survive its runs; a module that needs the whole block drops them.
- **More to move.** EXPLORE's `S` (600 B, `.data`) could start zeroed and be set up at run time;
  the C library's statics (about 300 B) could take `__attribute__((section(".ram0")))`. The
  room left in RAM0 is 616 bytes (360 with EXPLORE).

Tests: tests/test_k6_ram0soak.py (map, refusal at 12, zeros), tests/test_k6_ysfrx.py (level 12:
the call in scratch, more than 459 slots, handed to sotto_play), tests/test_k6_mod.py (the loader
takes level 12, refuses 13; arena by level).
