# LZ4 on the K6: packed uploads, library slots and help cards

Jeff, 2026-11-14: "pack OTA code uploads too, the decoder must be tiny". 2026-11-20: "use it on
SPI in general", "OK to have to reload the SPI module deck thereafter", "add help API too".
Module API level 15.

Module images, EXPLORE's code pages and help card decks can now travel and sit in the SPI
library packed with LZ4. The radio unpacks them with a decoder of under 180 bytes.

## What gets smaller

| What | Raw | Packed | |
| --- | --- | --- | --- |
| 25 release modules (Thumb code) | 140,607 B | 131,535 B | 6.5 % less to send and store |
| core help deck (slot 13) | 9,376 B (v1, 9 cards) | 7,780 B in SPI with a 10th card (STATIONS), ~5.4 KB on the air | v2 packs each card band by band |
| module help deck, 11 cards (slot 15) | | 8,022 B | new |
| EXPLORE's pages | 3,020-6,372 B | 4-6 % less | |

Code shrinks only a little. Thumb code is dense, and every module is its own block, so there is
no shared dictionary. The cards shrink more, because they are mostly blank screen.
`modem/lz4.py` packs with an optimal parse, which matches `lz4 -12` to within 2 bytes on the
core deck.

## The decoder (c/k6/lz4d.c)

`lz4d(src, n, dst, cap)` reads one LZ4 block (the standard block format: no frame, no size
header) and returns the bytes written, or -1.

- 170 B of Thumb in the voice + EXPLORE image (178 B on its own with ATfE -Os), and 28 B of stack.
- It checks every byte it reads and writes, one at a time. It never reads past `src + n` or
  writes past `dst + cap`, and it refuses offset 0 or a match that reaches before `dst`.
- It keeps whatever it wrote before it stopped. `kl_info` uses this to read a packed module's
  name from its first 30 packed bytes with a 16-byte cap.
- It can decode in place: the packed bytes at the top of a buffer and the output at its bottom.
- On an emulated M0 it costs about 10.5 instructions a byte. At 48 MHz that is roughly 1 ms for
  a 3 KB page and 3 ms for a 9 KB module.

`modem/lz4.py` holds the packer and a Python copy of the decoder (`_decode`), which follows the
C one step for step. `tests/test_k6_lz4.py` checks them against each other, against the
reference `lz4` package when it is installed, and against random and hostile blocks.

## Packed images (header flag KM_F_LZ4, 0x08)

A packed image keeps the 16-byte module header and the 8-byte SipHash tag. Only the body changes:

| Offset | Field |
| --- | --- |
| 4 | packed body length P (u16), in place of the code length |
| 12 | API level: 15 or more, so an older image refuses it |
| 13 | flags: `KM_F_LZ4` (0x08) set |
| 16 | the raw code length R (u16), then the LZ4 block (P - 2 bytes) |
| 16 + P | the tag, over the header and the packed body |

The entry offset must be less than R. `python -m modem.k6 mod ... --lz4` writes one, and so do
`tools/k6_upload.py --lz4` and `tools/k6_install.py` (every page, deck and module in a bundle).

### Unpacking (km_unpack, c/k6/k6_mod.c)

`km_unpack(a, img, cap)` moves the packed image to the top of the block and copies the header
to the bottom. It then decodes the body in place to `a + 16`, clears `KM_F_LZ4`, writes R as the
code length, and seals the result again with a new tag under the radio's key. So `km_again` and
`kl_load` check the unpacked image exactly as they would check one that came raw.

In-place decoding is safe when the write position never passes the read position. The block
must hold `16 + R + 8 + KM_LZ4_MARGIN (8) + P/128` bytes. Before an image is built, `modem/lz4.py`
measures how far each packed stream's writes run ahead of its reads (`inplace_excess`), and
`modem/k6mod.py` refuses an image that would break that margin.

### Where unpacking happens

- **Over the air, a module that runs:** it unpacks on arrival. `check()` verifies the tag on the
  packed bytes first, so a tampered image is refused before anything decodes.
- **Over the air, a help deck:** it also unpacks on arrival and goes into its slot raw, because
  the help viewer reads it one band at a time from SPI.
- **Library modules and EXPLORE's pages:** they are installed packed and stay packed in their
  slot. `kl_load` and `kl_page` read the packed image high in the block and unpack it on every
  load. This costs about 1-3 ms a load and saves 5-6 % of the SPI read.

## Help decks v2 (tools/k6_cards.py)

A deck is `K6HC`, the card count, version 2, then a 16-byte entry per card (its name in 8
bytes, its page, and its offset). Each card is 8 u16 band ends followed by its 8 bands of
128 B. Each band is either an LZ4 block or raw (128 bytes exactly). The viewer decodes one band
at a time into the screen's 128-byte draw band, using a 128-byte buffer on the stack.

- Up to 40 cards a deck (`HELP_CARDS`), up to 15,248 B (the 15,408-byte block less 160).
- The radio reads only v2 decks and passes over a v1 deck. That is why the decks must be
  installed again after this flash.
- Slot 13 holds the core deck (`docs/manual/cards/core.txt`). Slot 15 holds the modules' deck
  (`docs/manual/cards/modules.txt`). Slot 14 stays FM SOTTO's cell deck.

## The help API (level 15)

`int (*help)(const char *name)` comes last in `k6_api_t`. It shows screen `name`'s cards over
the module's screen. UP and DOWN turn the pages, and any other key closes the viewer and is used
up. It returns the number of pages, 0 at once when no deck has that card, or -1 when the call is
refused (keyed, or an image without the viewer). The module's screen comes back at its next
`lcd_flush`.

A card's name is the module's MENU name cut to 8 characters, with each space written as `_`:
AIS SHIPS is `AIS_SHIP`. Holding MENU on a module's row in the list shows that module's cards in
the same way. `tests/test_k6_cards.py` checks that every card in the module deck names a release
module.

## STATIONS in MENU

MENU's third core row, after EXPLORE and ARENA, opens EXPLORE's station table one entry at a
time.

- UP and DOWN step through the entries.
- 8 edits the entry, and EXIT from the editor comes back to STATIONS.
- 0 starts a new station.
- 3 pressed twice deletes the entry.
- EXIT goes back to the list.

Its card is `= STATIONS 1` in the core deck.

## Flash

The voice + EXPLORE image grows about 670 B for the C side: lz4d 170, km_unpack 214, the
library's packed paths about 460 (part of that is `kl_info`'s name decode). The Rust side adds
the help viewer and STATIONS. To pay for all of it, the 64-bit divide that `compiler_builtins`
links (`u64_div_rem`, 1,282 B) is replaced by `c/k6/k6_uldiv.c`, a 130-byte shift-and-subtract
`__aeabi_uldivmod`. The only callers are sotto's `fx_udiv_shl` (once a frame) and EXPLORE's load
meter. `tests/test_k6_lz4.py` checks it against Python on an emulated M0.

Jeff then asked to "trim the Rust off the Rust". Aura's text output already avoids core::fmt and
its panics are stubs, but two pieces of Rust's string code were still linked: UTF-8 checking
(`core::str::from_utf8`, 376 B, behind every `from_utf8(..).unwrap_or(..)` that turned a name or
a buffer into text) and character counting (`chars().count()`, 390 B, in the boot screen's
centring). Everything the pager images show is ASCII. So `out::ascii` (24 B) now gives the text up
to the first byte that isn't ASCII, and the boot screen counts bytes. That saves 1,464 B in all,
because each call site gets shorter too.

Built with ATfE 22.1 (CI's compiler), the image is 119,436 of 122,880 B with all of this and the outbox (#225), so
3,444 B are free. The image on the radio before this change had 1,980 B free. The EXPLORE-only
image has 9,684 B free. Not done: `compiler_builtins`' 32-bit divide (390 B) could be cut the
same way, but it is on hot paths, so it stays.

## Install after the flash

The format changed, so after the level 15 flash the library goes on again, packed:
`tools/k6_install.py` (pages, both decks, every release module). The old decks and modules in
SPI stay readable as raw images, but the help viewer passes over v1 decks.

SPDX-License-Identifier: MIT
