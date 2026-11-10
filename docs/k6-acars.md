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

#### The AM test on 6 m (`dfskcap_acars6`)

The owner allowed it (2026-11-10 16:49Z): minimum power, ident in every block and in CW, the
K6 receive only. `c/k6/mod/dfskcap_acars6.c` is `dfskcap_acars` on 50.650 MHz, the first front
end 60 s after its start (the module's own upload on 440.850 MHz ends and the IC-7100 changes
band and mode), then 45 s each. The signal: `python3 -m modem.acars probe OUT.wav --only R
--where 50.650` (real blocks only, about two a second, the frequency in their text), sent in
AM.

The transmission, one key-up of about four minutes on 50.650 MHz in AM (the IC-7100's AM-D,
audio from USB) at RF power 0.0: 13 s of PSK31 `AG7EW TEST` (an identified carrier for the
automatic tuner on the 6 m antenna to match on), the CW ident, 215 s of blocks, the CW ident.
Then the rig goes back to 440.850 MHz FM-D and is read back.

**First run (2026-11-10 17:03 to 17:08Z).** The K6's RSSI read -105 or -106 dBm in every
front end and the IC-7100's power meter 0 W. The carrier was up for the whole listen (keyed
28 s after the module's start, the front ends from 60 s), so nothing in the log told a
steady weak carrier from the floor. The narrow AM front end started 24 times in 45 s, the FM
one twice, none of them like a pre-key, so no words were said. Hence the `q` lines (the RSSI's
highest and lowest every 5 s before the first front end) and, in this build, the words of
every capture (`DFSK_DUMP_ALL`).

**Second run (17:17 to 17:21Z, build 82e5b49).**

```
q t=25066 p=-118 f=-120
q t=30079 p=-104 f=-121
q t=35092 p=-103 f=-105
v0 n=11 w=3098 b=0 p=-104 f=-106        FM, wide
v1 n=11 w=3104 b=0 p=-104 f=-106        AF source AM, wide
v2 n=0 w=0 b=0 p=-104 f=-105            FM, narrow, 2.5 kHz high
v3 n=26 w=9190 b=0 p=-104 f=-105        AF source AM, narrow
```

- **The K6 hears the carrier, 15 dB over its floor on 6 m** (-104 against -119 dBm): the step
  is in the `q` line of 30 s. The rig's meter still reads 0 W at RF power 0.0, and its own
  S-meter on the channel reads S0.
- The receiver starts every few seconds (48 captures), and **no capture has anything of a
  block**: no run of alternating samples longer than 20 (a pre-key is 250), pieces of the sent
  block match the samples no better than they match random bits (0.64 to 0.70 of 160 samples;
  random gives 0.65), no SYN SYN.
- The samples are not fair coin flips either: ones are 30% of them with the AF source FM or
  AM in the wide filter, 40% in the narrow AM front end, and the transitions come and go with
  the blocks' half-second beat in the FM front end as much as in the AM one.
- So at 15 dB over the floor the plain receiver gives noise shaped by the signal in every
  front end, and the AM setting of REG_47 makes no difference that shows. That fits a slicer
  fed from the FM discriminator whatever the AF source is (REG_47 chooses what goes to the
  speaker), but a weak signal with an unknown depth of modulation cannot prove it. A stronger
  one would: clean blocks in the AM front ends, or a quiet slicer in all four.

**Third run (18:36 to 18:40Z, same build, RF power 20 percent): cut short, 40 dB of carrier.**

The owner allowed one run at 20 percent. The bench script's own stop (SWR over 3.0 at or
after 13 s of key-up) ended the transmission 20 s in, in the Morse ident and before the first
block, so this run has no capture of a block either. The rig's meters, every 5 s of the
key-up:

```
 +3.0 s  SWR 6     PO 2.1 W
 +7.8 s  SWR 1     PO 0 W
+12.6 s  SWR 1     PO 2.1 W
+17.4 s  SWR 3.05  PO 0 W       the stop
```

What the K6 said:

```
q t=25066 p=-119 f=-122
q t=30079 p=-77 f=-122
q t=35092 p=-77 f=-83
q t=40105 p=-79 f=-81
q t=45118 p=-78 f=-83
q t=50131 p=-78 f=-120
q t=55144 p=-119 f=-122
v0 n=0 w=0 b=0 p=-119 f=-127
v1 n=0 w=0 b=0 p=-118 f=-126
v2 n=0 w=0 b=0 p=-121 f=-128
v3 n=0 w=0 b=0 p=-121 f=-128
```

- **At 20 percent the K6 has the carrier 40 dB over its floor** (-78 against -119 dBm), 26 dB
  more than at RF power 0.0. That is the signal the test needs.
- The antenna tuner on the rig's 6 m side does nothing at RF power 0.0 and starts to tune at
  20 percent: SWR 6, then 1, then 3.05 again, the power meter coming and going. 13 s of ident
  is not enough for it, and one reading taken at a power near nothing should not count. A
  next run opens with 30 s of ident and stops on two readings in a row.
- A control that came free: with the channel empty, the four front ends took **no capture in
  180 s**. The 48 captures of the second run were the signal's doing, not the receiver
  starting on noise.

**Fourth run (20:59 to 21:03Z, same build, RF power 5 percent): the pre-keys, and nothing of the data.**

The owner allowed one run at 5 percent. The opening was 30 s of PSK31 ident; the SWR read 1
at all 112 readings and the key-up ran whole (226 s, blocks on the air from 68 s after the
module's start).

```
q t=30079 p=-120 f=-123
q t=35092 p=-78 f=-121
q t=40105 p=-78 f=-80
v0 n=0 w=0 b=0 p=-76 f=-80              FM, wide
v1 n=0 w=0 b=0 p=-76 f=-80              AF source AM, wide
v2 n=0 w=0 b=0 p=-76 f=-79              FM, narrow, 2.5 kHz high
c3 1 t=195409 w=239 k=0 r=-79
c3 22 t=216460 w=262 k=4 r=-79
c3 29 t=223619 w=266 k=3 r=-78
v3 n=44 w=10462 b=0 p=-76 f=-80         AF source AM, narrow
```

- The carrier is again 40 dB over the floor (-78 dBm), at a quarter of the third run's power
  and with the tuner quiet.
- **With a strong AM signal three front ends take no capture at all.** FM in the wide filter
  hears nothing of an AM signal, as it should. The AF source AM in the wide filter hears
  nothing either: REG_47 alone does not put AM audio before the FSK slicer. Tuned 2.5 kHz to
  one side the slicer is quiet too (the carrier then sits 2.5 kHz off the discriminator's
  centre, far over to one level).
- **The narrow filter with the AF source AM takes a capture a second** (44 in 45 s: 512 bytes
  are a block, the gap and the next block's pre-key, so every second block starts one). Its
  words, said for the first twelve:

  ```
  d3 2 000 291552a9454a5155550aaaaaaaaa5555554a955400041100
  d3 2 00c 0804a0102000214081020040080000002000000002000040
  ```

  - the **pre-key** is there, about 250 samples of 2400 Hz as alternating samples with a slip
    every 20 to 40;
  - then for the length of the block's data (about 1000 samples) one sample in ten is a 1,
    and the ones follow nothing of the sent block (no feature of it, the audio's sign, the
    2400 Hz bits or the 1200 Hz ones, correlates over 0.2 at any offset);
  - then zeros for the quarter second between blocks, and the next pre-key.
- So the slicer follows a steady 2400 Hz tone and loses the data, where the tone changes
  every bit or two. Two readings fit, and the run cannot tell them apart, because the narrow
  filter and the AF source were changed together:
  1. the AF source does reach the slicer in the narrow setting, through something slow (a
     level that settles on a steady tone); or
  2. the slicer is fed by the FM discriminator whatever REG_47 says, and the narrow filter,
     not quite even about the carrier, turns a little of the AM into FM: enough for a steady
     tone, too little for the data.
- The fifth run's build (`DFSK_NARROW`, six front ends of 30 s, all narrow) asked that: FM as
  tuned (the filter alone), the AF source AM again, then FM tuned 0.5, 1.0 and 1.5 kHz high
  and 1.0 kHz low. It also says the words of every capture that goes on like a pre-key, past
  the first twelve.

**Fifth run (23:36 to 23:40Z, build dfe5808, 5 percent): it is the filter, not the AF source.**

```
q t=30079 p=-124 f=-128
q t=35092 p=-74 f=-127
v0 n=40 w=6101 b=0 p=-75 f=-79          FM, as tuned
v1 n=40 w=6278 b=0 p=-75 f=-79          AF source AM, as tuned
v2 n=0 w=0 b=0 p=-75 f=-79              FM, 0.5 kHz high
v3 n=0 w=0 b=0 p=-75 f=-79              FM, 1.0 kHz high
v4 n=0 w=0 b=0 p=-75 f=-79              FM, 1.0 kHz low
v5 n=0 w=0 b=0 p=-75 f=-78              FM, 1.5 kHz high
d0 33 000 5555555555555555555555552aaaaaa2aaa55549152aaa80
d0 33 00c 000808002001000840001014504020409426000440240900
```

- **FM and the AF source AM do the same in the narrow filter**: 40 captures each in 30 s, the
  pre-key as alternating samples (five captures open with three or four words of AAAA or
  5555), the data as near silence, no block read. So reading 2 holds: **the plain FSK
  receiver's slicer is the FM discriminator's whatever REG_47 says**, and what it sees of an
  AM signal is the little FM the narrow filter makes of it. REG_47 only chooses what goes to
  the speaker.
- **Half a kilohertz off tune it is deaf**, to either side: the carrier's offset moves the
  discriminator's level further from the slicer's than the made FM swings. That also explains
  the data as near silence on tune: the two radios are not on the same frequency to a few
  tens of hertz, the chip's AFC pulls the carrier towards the centre only slowly, and the
  made FM of the data (1200 Hz and 2400 Hz in turn) stays on one side of the slicer, where
  the steady 2400 Hz of the pre-key crosses it.
- The next build keeps `DFSK_NARROW` and asks for the place, if there is one, where the
  data's samples fall on both sides: eight front ends of 22 s, narrow and FM, seven with the
  AFC off (REG_73 bit 4) tuned 300, 200 and 100 Hz low, as tuned, and 100, 200 and 300 Hz
  high, the last as tuned with the AFC on (this run's front end 0 again).

**Sixth run (23:52 to 23:56Z, build f6b3004, 5 percent): 100 Hz off tune it is deaf.**

```
v0 n=0 w=0 b=0 p=-75 f=-79              AFC off, 300 Hz low
v1 n=0 w=0 b=0 p=-75 f=-79              AFC off, 200 Hz low
v2 n=0 w=0 b=0 p=-75 f=-79              AFC off, 100 Hz low
v3 n=43 w=3002 b=0 p=-75 f=-79          AFC off, as tuned
v4 n=0 w=0 b=0 p=-75 f=-79              AFC off, 100 Hz high
v5 n=0 w=0 b=0 p=-75 f=-79              AFC off, 200 Hz high
v6 n=0 w=0 b=0 p=-75 f=-79              AFC off, 300 Hz high
v7 n=28 w=4927 b=0 p=-75 f=-79          AFC on, as tuned
```

- Only as tuned does the receiver start, with the AFC off (43 captures in 22 s) or on (28),
  and what it gives is the same as before: pre-keys (none of the 43 with more than two words
  of them with the AFC off, three of the 28 with it on), no data, no block.
- **100 Hz to either side there is no capture at all.** So the FM the narrow filter makes of
  the AM signal swings less than 100 Hz about the carrier, and there is no tuning where the
  data's samples fall on both sides of the slicer: the place where the receiver starts at all
  is narrower than one step, and in it the data stays on one side.

### What the 6 m tests say

Six runs, the last three with the carrier 40 dB over the K6's floor:

1. **The plain FSK receiver's slicer is fed by the FM discriminator and nothing else.** REG_47's
   AF source AM changes what the speaker gets, not what the slicer gets (wide filter: nothing
   either way; narrow filter: the same either way).
2. Of an AM signal it sees only what a narrow filter turns into FM: under 100 Hz of swing
   from a fully modulated carrier at 40 dB over the floor. That is enough to start on a block's
   pre-key and never enough to read a bit of its data.
3. So **neither of the chip's modems reads ACARS**: the 1200/2400 one never syncs (first bench
   run), the plain one cannot be given AM audio. On this radio ACARS would have to come
   through a path that carries AM audio to the MCU, and the only one there is, the GP0 IF
   tap, needs about -79 dBm (docs/k6-aprs-clock.md); ACARS at the bench site peaks
   at -103 dBm. The question is closed for the chip as it is.

`dfskcap_acars6` stays as the instrument it became (the `q` lines, every capture's words, the
narrow front ends with the AFC off); `dfskcap_acars` on 131.550 MHz is unchanged in what it
does.

