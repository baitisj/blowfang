# One K6 image: 8 kHz voice + EXPLORE

Jeff, 2026-11-09: the bench switches between the voice image and the EXPLORE image too often,
so the target is one image that does both. He picked the 8 kHz voice (v3.7, sotto 1200, 600 and
400, as `aura-pager-vlr`) over the hiss image, which has no room at all.

`firmware/k6-aura/build.sh` builds it as `aura-voice-explore` (Aura feature `voice-explore`:
EXPLORE with the voice library, the voice images' 11,776-byte shared block, EXPLORE's code
pages and banded drawing, below). Until it fits, it's a size report only:
`out/size-aura-voice-explore.txt` says how much flash and RAM are still to find, and no `.bin`
is written; its last lines are the three pages.

## Where it stood (2026-11-09, local clang 18 build; CI's is about 1.1 KB smaller)

- Flash 143,420 of 122,880 B: about 20.5 KB over (with FX_MP_ROWGEN, blowfang #21, and the SPI
  module library, #22).
- Static RAM 18,208 of 20,440 B: 2,232 B left for the stack against the 4,588 B the deepest
  call (a voice playback) needs.
- Without sotto 600 / 400 (sotto 1200 only): 139,404 B, 4 KB less.

## The plan

Each a separate PR; the report line shows what's left after each.

RAM:
- xdmr's hot path copied to SRAM (1,940 B of `.data`, blowfang #19) goes: it didn't help (the
  slow FD6818 reads were flash prefetch off under txdog, explorer PRs #20 / #23).
- ifpager's `run` frame (svc_t + ifp_t, 2,736 B on the stack) into the shared block, which is
  free while a coded 4FSK session runs.

Flash:
- EXPLORE's code pages (below): done, 12.0 KB.
- the decoder's 2 KB `fx_cs512` table: tried, rejected (FX_OA_UNPACKED costs 5 MHz of the M0's
  mean decode and 87.8 of 96 MHz at worst).
- EXPLORE's serial dump and trace lines: about 1 KB.
- the compiler thread's savings: Arm Toolchain for Embedded with -Oz (blowfang #24, 2-2.7 KB),
  an address-only panic report (about 3 KB), a compact memcpy (about 1.5 KB).

## EXPLORE's code pages

EXPLORE's sweep (c/k6/xscan.c), Listen for ID (xlisten.c, xdmr.c) and its repeater table code
(rt.c) don't fit beside the voice decoder, so they are kept in SPI flash and run from RAM. Each
is a page in the SPI module library (c/k6/k6_lib.h, header flags KM_F_LIB and KM_F_PAGE):

| page   | slot | code  | functions                      | stack (direct calls) |
|--------|------|-------|--------------------------------|----------------------|
| sweep  | 10   | 5,864 | xs_* (12)                      | 328 B, xs_step       |
| listen | 11   | 5,904 | xl_*, xd_* (14)                | 2,400 B, xd_step     |
| table  | 9    | 3,108 | rt_* (8)                       | 472 B, rt_cw_apply   |

How it works (c/k6/kp.h):

- The window is the decoder pool of the shared block (6,400 B after the store and the ring):
  no voice message plays while EXPLORE runs, and EXPLORE's own data stays where it was (the
  sweep state and the candidates in the store, the floors and Listen for ID's state in the
  ring). One page is there at a time.
- A page is linked for one image (tools/k6_pages.py, after the image): at the pool's address
  plus the module header, with the image's symbols (`ld.lld --just-symbols`), so its calls into
  the image (explore.c's ex_*, cwid.c's cw_*) are plain calls through linker thunks. The image
  keeps those functions although nothing in it calls them (build.rs links
  `out/page-imports.x`, EXTERN lines from `k6_pages.py imports`).
- The image calls a page through the table at its start (magic, the table's own address, then
  the functions in kp.h's order); explore.rs's `paged!` declares the same lists. Each call
  checks that its page is in the pool and loads it if not (`page::func`): k6_lib.c's kl_page
  reads the slot, checks the header and the SipHash tag (as kl_load does, without arming
  km_run), then explore.rs checks the image's build ID at the page's end (the image's
  `.note.gnu.build-id`, 8 bytes, at `k6_build_id` in MCU flash) and the table.
- A page is never run as a module: kl_load refuses KM_F_PAGE, and kl_page refuses a module.
- EXPLORE starts only when all three slots hold this image's pages (`page::check`: the header
  and the build ID read from SPI, the pool untouched). Otherwise VOICE MSG stays, with the
  serial line `K6 EX pages missing: sweep r= listen r= table r=`, and tries again 2 minutes
  later. A load that fails later (the SPI flash itself) restarts the radio rather than run what
  the pool holds.
- Loads: two when EXPLORE starts, then the sweep's in the list view, Listen for ID's in a
  listen, the table's for a look-up or a write: three or four a listen. The table is looked up
  before the DMR detector starts, so its load costs the detector nothing.
- The pool is the decoder's again when EXPLORE leaves (`page::forget`); the voice decoder sets
  itself up afresh for each message.

Install, after flashing a new image (its build ID changes with every build, and a page made for
another image is refused):

```
python3 -m modem.k6 mod out/page-sweep.bin --page --install 10 --key-file ~/.config/sotto/k6-mod.key ...
python3 -m modem.k6 mod out/page-listen.bin --page --install 11 --key-file ~/.config/sotto/k6-mod.key ...
python3 -m modem.k6 mod out/page-table.bin --page --install 9 --key-file ~/.config/sotto/k6-mod.key ...
```

Each is about 6 KB over the air, as a module upload; the K6 writes it to its slot once (never
while keyed), as any library install.

## Drawing a band at a time

EXPLORE keeps its sweep's and Listen for ID's state in the PWM ring, and the voice images' page
buffer lives in the ring too (`shared_lcd`), so this image (`banded`) has no page buffer at
all: every screen is drawn once for each controller page of 8 rows into 128 bytes of stack
(ui::Band, `screen!`, device::display draw_pages), and a glyph or a box that misses the band is
skipped at once. One draw target for every screen builds embedded-graphics' text and boxes once:
the banding costs 0.76 KB of flash in all.

## Measured (2026-11-09, local clang 18, without FX_MP_ROWGEN)

| | flash | static RAM |
|---|---|---|
| voice-explore before | 151,860 | 18,208 |
| sweep + listen pages | 141,900 | 16,268 (xdmr's SRAM copy is in its page now) |
| + banded drawing | 142,660 | 16,268 |
| + table page | 139,856 | 16,268 |

Of the 12.0 KB, 1.9 KB is xdmr's SRAM copy of its hot path (`.data`, blowfang #19), which the
explorer's #20 takes out of every image anyway: on top of #20 the pages save about 10.1 KB.

With FX_MP_ROWGEN (-8.3 KB) that is about 131.6 KB locally, about 130.5 KB on CI: about 7.6 KB
over, against about 7 KB the compiler thread expects to save.
