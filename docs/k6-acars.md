# ACARS on the K6: can the chip's own modem read it?

Jeff asked (2026-11-10) how AIS and ACARS reception could be made more sensitive, after the APRS
receiver moved from the 1-bit IF tap to the FD6818's own FSK modem (`docs/k6-aprs-clock.md`,
"The receiver on the chip's modem"). The tap's own noise puts every tap receiver some 20 dB
short of the chip's receiver, so the question for each mode is whether the chip can do the
demodulating.

- **AIS** is GMSK at 9600 bit/s. The modem's bit clock is REG_72 = baud x 10.32444 in 16 bits,
  so it tops out near 6,347 baud. AIS stays on the tap unless a faster clock turns up (the AIS
  thread has one cheap probe for that: direct mode with REG_72 = FFFF).
- **ACARS** is MSK at 2400 bit/s on 1200 and 2400 Hz. That is the native format of the chip's
  1200/2400 Hz receiver (REG_58<12:10> = 4), the one that reads Bell 202 for `aprsfsk`. But
  ACARS is AM, and the modem sits behind the chip's FM demodulator.

## What ACARS looks like to the modem

A bit that differs from the one before is half a cycle of 1200 Hz; a bit that repeats it is a
whole cycle of 2400 Hz. So the chip's tone bits are the differences of the data bits, and the
MCU undoes that with one XOR a bit. A block opens with 16 characters of all ones (a steady 2400
Hz), then `+` `*`, SYN SYN, SOH. As tone bits (1 = 1200 Hz) SYN SYN is **5C5C**, and with the
`+*` before it 3FFE 5C5C: those are the sync words. Characters are 7 bits with odd parity, low
bit first; the block ends with ETX, a CRC-CCITT check over everything after the SOH, and DEL.
The longest block (220 characters of text) fits the chip's 256-byte packet.

Two unknowns:

1. **Does the receiver start on a real block?** The chip's own packets open with alternating
   tones for its bit clock. ACARS has a steady tone and then only the few transitions of
   `+*` SYN SYN.
2. **Can the modem hear an AM signal?** Either REG_47's AF source (7 = AM) also feeds the
   modem, or it does not. If not, there is one more thing to try: an AM signal with one
   sideband cut off by the narrow filter (tuned 2.5 to 4 kHz to one side) is partly FM, and the
   FM demodulator then gives the tones at a reduced level (slope detection).

## The probe (`acarsfsk`, `acarsfsk_am`)

Bench channel only, 440.850 MHz, K6 receive only; nothing is ever sent on airband. The test
signal (`python3 -m modem.acars probe OUT.wav`) is K and R units in turn, a quarter second
apart: R is a real ACARS-format block (address `AG7EW..`, text `AG7EW TEST 440.850 ...`), K the
same block after the chip's own kind of opening (alternating tones and the pager's sync word),
as a control for the framing.

- `acarsfsk` (the control): the IC-7100 sends the signal in **FM**. Twelve settings, 5 s each,
  twice round: REG_58 = 33E9, 33A9, 33A3, 33E3 at 2400 baud, each with the sync words 076B 3E36
  (K units), 5C5C (R units) and 3FFE 5C5C (R units). Answers unknown 1 and picks the modem
  setting.
- `acarsfsk_am`: the IC-7100 sends the same signal in **AM**. Six front ends by three modem
  settings by two sync words, 5 s each, once round (3 minutes):

| Front end | What it tries |
| --- | --- |
| 0 | as found: FM, wide, on the frequency (expect nothing) |
| 1 | REG_47's AF source = AM, wide |
| 2 | the same, narrow |
| 3 | FM, narrow, tuned 2.5 kHz high |
| 4 | FM, narrow, tuned 2.5 kHz low |
| 5 | FM, narrow, tuned 4 kHz high |

The module reads each packet's 40 words by time and runs the block reader on them for either
sense of the tones. Lines: `c` and `d` for the first three captures of a setting (the words,
for `python3 -m modem.acars report LOG`), and at each slot's end
`vNN f=<front end> <REG_58> <sync> sy= soh= ok= r=`: syncs, blocks that opened with SOH, blocks
with a good check, the strongest RSSI.

## Next, by result

- AM front end works: a receiver module on 131.550 MHz (the main North American channel),
  receive only, sync 5C5C, blocks to the serial log; then flights and messages on the screen.
- Only the control works: ACARS stays on the IF tap (`aiscap_acars` heard nothing in a 2-minute
  listen with the AGC on; it needs the gain frozen and a longer listen).
- Not even the control's R units: the receiver needs its alternating preamble, and the chip's
  modem cannot start on ACARS.

Tested on the emulator (`tests/test_k6_acars.py`) against the model of packet mode from the
APRS probes; the model cannot answer the two unknowns, only the bench can.

## Bench run, 2026-11-10 10:05 to 10:12Z

K6 image 35e982f, receive only (uploads with no reply asked). IC-7100 on 440.850 MHz at about
-21 dBm at the K6.

| Run | Result |
| --- | --- |
| `acarsfsk`, the blocks sent in FM, two rounds of the 12 settings | `sy=0` in every one: no sync on a real block's SYN SYN, none on "+*" SYN SYN, and **none on the K units either** (alternating tones and the pager's own sync word, at 2400 bit/s) |
| `acarsfsk_am`, the blocks sent in AM | void: the K6's RSSI fell to its floor (-127 to -130 dBm) the moment the IC-7100 went to AM. Three chance syncs on noise, no SOH |

What that says:

- **The 1200/2400 Hz receiver did not start on anything at 2400 bit/s**, not even on an opening
  of its own kind. At 1200 baud the same receiver starts on plain flags (`aprsfsk`). The likely
  reading: REG_58's "FFSK 1200/2400" is a 1200 baud scheme (a bit is a cycle of 1200 Hz or two
  of 2400 Hz), and half a cycle of 1200 Hz is not a bit to it. The probe has no unit at
  1200 baud, so a fault of the probe's own at 2400 (REG_72 = 60CA) is not ruled out: a K unit
  at 1200 baud in the same run would tell.
- **The IC-7100 sends no AM on 70 cm.** hamlib lists AM for its transmitter only on HF and
  50 MHz, and the K6 saw no carrier. So AM cannot be tested on the bench with this radio; the
  AM front end can only be tried on real ACARS, receive only (131.550 MHz), or with another
  AM source.

So the third case of the list above, with a caveat: ACARS on the chip's modem looks closed
unless a 1200 baud control shows the probe itself was at fault. The IF tap (`aiscap_acars`)
stays the way to ACARS.

## The plain FSK receiver on ACARS (bench, 2026-11-10 14:39 to 14:43Z)

The APRS work found a second receiver in the chip: REG_58 = 00E9 at 4800 baud samples the
audio's sign, its clock following the zero crossings (`dfskcap`, docs/k6-aprs-clock.md). For
ACARS the numbers fall right: a half cycle of 2400 Hz is one sample, of 1200 Hz two, so a bit
is two samples (`10` or `01` unchanged, `11` or `00` changed), the pre-key is the sync word
AAAA, and the clock has nothing to follow that it does not already run at. `dfskcap` ran
unchanged on 440.850 MHz while the IC-7100 sent the probe's signal (`python3 -m modem.acars
probe`) in FM; K6 receive only.

- **The chip starts on every ACARS unit**: 12 captures in about 6 s in each 4800 baud setting
  (the units come 0.5 s apart), runs of one sample 80%, of two 20%, none longer. The
  1200/2400 Hz receiver never started on one.
- **No block reads from those samples.** Laid against the block's own tones, runs of 1200 Hz
  half cycles come one short (13 pairs sent, 12 read) and a lone one between 2400 Hz cycles is
  mostly gone, read as single samples: the zero crossings sit up to half a sample from where
  the bits change. The same audio through an ideal comparator and a clock that follows the
  crossings gives the block's pattern exactly (the tones change at zero crossings), with a
  flat channel and with any plain de-emphasis or low pass put in front. So it is the path
  between the IC-7100's modulator and the chip's slicer that moves them, and a decoder fitted
  to this FM bench signal would say little about ACARS off the air, which is AM.

What is left to ask can only be asked of a real signal, receive only: does the plain receiver
hear an AM signal at all (it sits behind the FM demodulator), and what do its samples look
like there? `dfskcap_acars` is `dfskcap` on 131.550 MHz for that (below).

### `dfskcap_acars`: the listen on 131.550 MHz

`c/k6/mod/dfskcap_acars.c` (`dfskcap.c` with `DFSK_ACARS`, 2.9 KB), receive only: 32 s after
its start, four front ends of 150 s each (75 s in the first run), all REG_58 = 00E9 at 4800 baud with the sync word
AAAA and 512 bytes a packet (REG_5D = ff20; a long block is 500 bytes):

| | Front end | Asks |
| --- | --- | --- |
| 0 | FM, wide, as tuned | does the receiver hear an AM signal at all? |
| 1 | REG_47's AF source = AM, wide | does it follow the AF source? |
| 2 | FM, narrow, tuned 2.5 kHz high | an AM signal with a sideband cut is partly FM |
| 3 | AF source AM, narrow | |

Noise gives the sync word by chance, so every capture gets a `c` line (`c1 3 t=91234 w=256
k=4 r=-87`: front end, capture, ms, words, how many of the first four words are AAAA or 5555,
RSSI) and only those with `k` of 3 or 4, where the pre-key goes on, their words (12 a front
end at most). `python3 -m modem.acars plain LOG` reads them: the runs' shares, and a block if
SYN SYN and a good check are in them (a run of one sample half a bit of 2400 Hz, of two a bit
of 1200 Hz).

What the log can say:

- No capture with a strong RSSI in any front end, though the K6 hears carriers there: the
  plain receiver does not start on an AM signal. ACARS stays on the IF tap.
- Captures with `k` 3 or 4 in a front end: the receiver hears it. Then the words say whether
  a bit is two clean samples (a block reads) or the crossings sit elsewhere, as in FM on the
  bench, and what a slicer for them needs.

The emulated test (`test_dfskcap_acars_on_the_model`) hears the blocks only with the AF source
set to AM and noise with chance sync words otherwise: `c` lines everywhere, words and blocks
in front ends 1 and 3 only, a `b` line for every block's carrier in all four, the registers
put back.

#### First listen (2026-11-10 15:34 to 15:39Z): no traffic to judge by

The K6 on 131.550 MHz for 5.5 minutes at hamlet, the IC-7100 silent (it sent the module on
440.850 MHz and its idents, nothing on airband):

```
s 0 t=32022 cfg=00e9 b=c195 sy=aaaa l=ff20
v0 n=0 w=0
s 1 t=107027 cfg=00e9 b=c195 sy=aaaa l=ff20
c1 1 t=156961 w=276 k=0 r=-113
v1 n=1 w=276
s 2 t=182032 cfg=00e9 b=c195 sy=aaaa l=ff20
c2 1 t=207660 w=273 k=0 r=-116
v2 n=1 w=273
s 3 t=257037 cfg=00e9 b=c195 sy=aaaa l=ff20
v3 n=0 w=0
```

- Two captures in 300 s, both at the floor (-113 and -116 dBm) and with no pre-key after the
  sync word: noise. REG_5D = ff20 gives packets of about 512 bytes (273 and 276 words with the
  stale reads), so the length bits work as reckoned.
- Noise starts the receiver about once in 150 s. A real listen loses next to nothing to it.
- **Nothing to judge the question by**: the log does not say whether a block was on the air in
  those minutes. A capture's RSSI is taken only when the receiver starts, so an AM carrier the
  receiver slept through left no mark.

So the module now reads the RSSI every 50 ms as well. A rise of 10 dB over the front end's
floor (its lowest reading, let up half a dB every 2 s) is a carrier, said when it ends:
`b1 3 t=91230 ms=450 r=-84` (front end, burst, ms at its start, its length, its peak), and
the `v` line ends with `b=` (bursts), `p=` (the highest RSSI) and `f=` (the floor). With that
a listen answers either way: bursts and no capture at their times in a front end mean the
receiver does not start on AM there; no bursts mean no traffic reached the K6's antenna. Four
front ends of 150 s make it 10.5 minutes.

#### Second listen (2026-11-10 15:52 to 16:03Z): weak carriers, and no start on any

Build ffe2e09, 632 s on 131.550 MHz, the IC-7100 silent after the upload:

```
s 0 t=32022 cfg=00e9 b=c195 sy=aaaa l=ff20
b0 1 t=64226 ms=150 r=-103
v0 n=0 w=0 b=1 p=-103 f=-113
s 1 t=182027 cfg=00e9 b=c195 sy=aaaa l=ff20
c1 1 t=237412 w=278 k=0 r=-114
b1 1 t=289380 ms=100 r=-104
b1 2 t=289580 ms=50 r=-104
v1 n=1 w=278 b=2 p=-104 f=-114
s 2 t=332033 cfg=00e9 b=c195 sy=aaaa l=ff20
c2 1 t=404281 w=277 k=0 r=-114
b2 1 t=423230 ms=150 r=-105
v2 n=1 w=277 b=1 p=-105 f=-116
s 3 t=482038 cfg=00e9 b=c195 sy=aaaa l=ff20
b3 1 t=516230 ms=50 r=-106
b3 2 t=629880 ms=350 r=-104
v3 n=0 w=0 b=2 p=-104 f=-116
```

- **Six carriers in ten minutes, all weak**: -103 to -106 dBm at their peaks, 9 to 12 dB over
  a floor of -113 to -116 dBm, 50 to 350 ms over the threshold (the length of a short block,
  or the top of a fading longer one). Whether they are ACARS blocks the log cannot say; on
  this channel little else is likely.
- **The plain receiver started on none of them**, with the AF source FM (b0, b2) or AM (b1,
  b3): the two captures are noise, at the floor and minutes from any carrier.
- At 10 dB over the floor that proves little: a sync word wants 16 clean samples of the
  pre-key, and the 1200/2400 Hz bench runs lost their starts at such levels too.

So hamlet's antenna hears ACARS traffic only faintly, and the question (does the plain
receiver read an AM signal's audio with the AF source set to AM?) wants a strong AM signal.
The IC-7100 has AM on HF and 50 MHz only, and the K6 receives from 18 MHz up: a bench test
of the same blocks in AM on 6 m would answer it. That is outside the bench's rule (transmit
on 440.850 MHz only) and is the owner's call.
