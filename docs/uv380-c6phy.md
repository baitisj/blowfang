# Flat FSK from the UV-380's HR_C6000 physical layer mode

*2026-11-16. Code: `firmware/uv380-openrtx/mod/c6phytx.c` (and `_live`, `_live1200`),
`c/common/c6phy_fsk.h`, `modem/c6phy.py`, `c/k6/mod/fsk24rx_uv.c`, `tests/test_uv380_c6phy.py`.*

## Why

The K6 read none of the UV-380's FFSK on 11-14, and only 6 of 7 packets on 11-16 after the C6000's
line-in gain went up (PR #135): that path runs the MCU's PWM through the C6000's line input, its
pre-emphasis option, its 300 Hz cut and its limiter, and the deviation came out about 10 dB low
before the fix. The K6's chip FSK reads 2400 baud perfectly from a flat FM transmitter (Pluto
run 3, 234/234, `docs/k6-fsk24.md`). RadioFoundryC4FM showed that an HR_Cx000's physical layer
mode is a general 4800-baud 4FSK transmitter that stays keyed for whole calls
(`docs/uv380-c4fm-foundry.md`). So the UV-380 can be that flat transmitter: no audio path at all.

## How

The HR_C6000 in physical layer mode (0x10 = 0x66; RadioFoundry `modem-hrc6000.md` 9.1-9.2, MIT)
sends TX RAM page 2 as DMR 4FSK: 36 bytes a 30 ms slot, MSB first, a dibit a symbol (01 +3,
00 +1, 10 -1, 11 -3), outer deviation +-1944 Hz, RRC shaped, with its two-point modulation
(flat to DC). Halves 0x00-0x23 and 0x24-0x47 go out in alternate slots, each latched at its slot
start, so the module writes the next slot's bytes to **both** halves at every timeslot interrupt
(PC0) and sets 0x41 = 0x80. The carrier never runs out of symbols.

Each of our bits as two equal outer symbols is plain 2FSK at 2400 baud, +-1944 Hz:

| bit | 2400 baud (2 symbols) | 1200 baud (4 symbols) |
| --- | --- | --- |
| 1 | +3 +3 = nibble 0x5 | byte 0x55 |
| 0 | -3 -3 = nibble 0xF | byte 0xFF |

A slot carries 9 data bytes at 2400, 4.5 at 1200. The stream is `fsk24rx`'s test stream, pager
framing (150 ms of 0x55, 16 preamble bytes a packet, 50 ms more before the second, sync
07 6B 3E 36) around 64-byte packets (0xF2, variant, sequence, PN9, CRC-16), then 0x55 for as long
as the key-up lasts. A 4.6 s key-up carries 13 packets at 2400 baud, 6 at 1200
(`python3 -m modem.c6phy plan`).

Which way the UV-380 swings for +3 wasn't known (run 3: above the carrier), and the K6's chip
was thought to sync only one way up (run 3: it read both). So the bench modules alternate
polarity packet by packet: even packets send bit 1 as +3 (variant
20 at 2400, 22 at 1200), odd ones as -3 (21, 23). Whichever variant the K6 reads is the answer.

## The module

`c6phytx` borrows the image's FM keying as `c6dmrtx` does: `api->tx_tone` keys 440.850 MHz only,
at the channel's power, under the image's transmit guard (API level 4: the Morse ident first; a
key-up that outlives its length by 1.5 s resets the radio). Once the tone's own key-up shows, it
saves the registers it touches, turns the FM chain off (0x60 = 0), sets two-point modulation with
the radio's DMR calibration pair (`TX_DEV` percent of it: the deviation), 0x10 = 0x66 before
active timing (0x40 = 0xE3; RadioFoundry: written later it moves the slot clock), prefills both
halves with preamble, and feeds the slots until the packets and two slots of fill are out. Every
register goes back 350 ms before the tone ends. Nothing is said over USB between `tx_tone` and the
key-down; the lines are kept and said after it.

Lines after a run: `cal` (the pair and what it's scaled to), `ident keyed / done`, `key ok`,
`phy on mode=66 npk=13`, `chk 00=36/36 24=36/36` (both halves read back through page 6 after the
first slot's write), `tx slots= pk=13/13 bytes= edges= first= rftx= late= lost=`, `end`. `late`
counts slot interrupts the module answered more than 33 ms after the last: each lost slot repeats
the previous 9 bytes on air and costs that packet.

| Module | RF | Settings |
| --- | --- | --- |
| `c6phytx.c` | none (`TX_DRY 1`): the chip clocks slots and takes TX RAM, no PA | 2400 baud, alternating |
| `c6phytx_live.c` | ident, then 4.6 s at the channel's power, 440.850 | 2400 baud, alternating, seq from 0 |
| `c6phytx_live1200.c` | as live | 1200 baud, alternating, seq from 100 |

Knobs for later wrappers: `TX_WRITE` 0 (both halves, run 2's failure) / `TX_HALF` 1, `TX_POL`
0/1 (one polarity), `TX_DEV` (percent; C4FM's ratio is 139),
`TX_MODE` 0x02 (continuous physical layer, untested on a C6000), `TX_MS` (at most 5000).

## The K6 end

`c/k6/mod/fsk24rx_uv.c` is `fsk24rx` with four 60 s settings after its 30 s wait: three of
D/2400 (run 3's setting 2: REG_58 00e9, REG_2B +0400) then one of D/1200 (00e1, +0400).
`python3 -m modem.fsk24 report LOG` lists the packets by variant.

## The Pluto

`tools/pluto_bench.py rx CAP.cf32 --hz 440900000 --seconds 14` (50 kHz up, so the RX LO isn't on
the K6's channel), then `python3 -m modem.c6phy decode CAP.cf32`: packets with variant, sequence,
CRC, PN9 bits wrong and whether the discriminator saw them inverted; which way a +3 symbol
swings; the carrier offset from 440.850; the deviation peaks; any gaps in the carrier (there
should be none: the mode is continuous).

## What the tests check

The C stream and slots against `modem/c6phy.py` byte for byte (every rate and polarity), the
stream against `pager.Air`'s framing, the symbol packing, the chip's 4FSK (RRC, +-1944 Hz) read
back as plain FSK by our demodulator with and without noise, `fsk24rx_uv` on the emulated K6
reading the upright packets with no bits wrong, a synthetic Pluto capture decoded, and the
transmit rules (dry by default, one `tx_tone`, level 4 only, no say while keyed, registers saved
before and restored before the key-down).

## Bench

Keys: `/mnt/project-files/uv380-c6phy/bench-keys.md`. `sh tools/uv380_c6phy_bench.sh stage RUN`
builds and signs everything; `live` is runs 1 and 2's sequence, `live2` run 3's.

**Runs 1 and 2 (11-16, UV-380 at 0 W, PA on, 440.850; the Pluto and the K6 receive).** The raw
mode works on air: the ident first, every slot answered on time (`late=0`, `lost=0`), both halves
read back 36/36, the carrier continuous, the radio's registers and settings back as found. The
Pluto synced every packet, but every one had 16-35 bits wrong and failed its CRC, at both rates;
the K6 synced none. `python3 -m modem.c6phy errors` on the captures puts every wrong bit in the
first ~40 TX RAM bits of a slot (about 4 ms: data bits 0-9 at 2400, 0-4 at 1200) and none in the
middle, where a DMR sync would go. So the chip was still sending the head of a half when the
module rewrote it: writing both halves at every slot edge is too late for the half on air.
`errors` confirms it on all five captures: every wrong bit (253, 244, 323, 178, 178) is the
previous slot's bit at the same place, and a model that sends each slot's first 5 bytes from the
slot before gives k2400b's per-slot histogram exactly. The FSK's own carrier (the mean
discriminator over its packets, calibrated Pluto) sat +6.65 to +6.70 kHz from 440.850, where the
FM ident just before it sat +0.6 kHz (+1.3 once): the raw mode adds about 6 kHz of its own,
enough to keep the K6's narrow chip FSK from syncing. (The decode's first line, about +3.4 kHz,
averages the ident and the FSK.)

The K6's module upload from the Pluto: `--preemph --dev 3700 --gain -52` (at -40 the K6 heard
nothing; it keeps a partial upload, so the script sends up to 3 times).

**Run 3 (11-16 22:45-22:50Z, `live2`): it works.** One half a slot edge, the idle one
(`TX_WRITE 1`), and the transmit trim lowered by 6680 Hz (5745 to -935): the FSK then sat +9 to
+39 Hz from 440.850 (the ident about -6 kHz, which doesn't matter). Every key-up keyed cleanly
(130 slots at 2400, 121 at 1200, `late=0 lost=0`, both halves 36/36).

| Key-up | Half | Pluto | K6 |
| --- | --- | --- | --- |
| 2400 a | 0 | 13/13 CRC right, 0 of 472 PN9 bits wrong | 13/13 (setting 0) |
| 2400 b | 1 | 0/13, 27-28 bits wrong each | 0 |
| 2400 c | 0 | 13/13, 0 wrong | 13/13 (setting 1) |
| 2400 d | 1 | 0/13 | 0 |
| 1200 a | 0 | 6/6, 0 wrong | 12/12 for both (setting 3) |
| 1200 b | 0 | 6/6, 0 wrong | |

So `TX_HALF 0` is the phase: half 0 is written at the first slot edge. The module's defaults are
now `TX_WRITE 1`, `TX_HALF 0`. The eye at 2400 was 1746 Hz of the ideal 1944 (RRC); the Pluto
saw a +3 symbol go out above the carrier. The K6 read both polarities (variants 20 and 21, 22
and 23); it reports the inverted ones as upside down and the module flips them back. The trim
stays a bench step (`FSK_OFF`, default 6680); a module can't set it.

The K6's module took 4 more Pluto sends (resuming from 10/18) at -52: about 1 chunk in 4 gets
through a send there, so the script now sends up to 6 times.

## Next

- Sotto over the raw channel (experiment 6): 9600 bit/s raw is room for sotto 2400 with rate 1/2
  FEC, live from the mic, once a keying call without the 5 s cap is in an image.
- K6 uploads at 2400 baud from the UV-380 (the K6's 2400 mode plan, `docs/k6-fsk24.md`).
- Deviation (0x45 / 0x46 linearity) and 0x10 = 0x02, measured on the Pluto.
