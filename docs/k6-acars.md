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

**Seventh run (2026-11-11 01:30 to 01:34Z, build 12f0cf1, 5 percent): at the zero IF the
receiver takes in the whole block.** Jeff asked whether the chip's double sideband mode would
do it. That mode is REG_3D = 0, a zero IF where both sidebands fold onto each other, with
REG_47's AF source 5. `DFSK_ZIF`: eight front ends of 22 s, the AFC off in all, REG_3D written
before the tuning (which kept it: every `s` line says `if=` as written):

```
v0 n=13 w=5106 b=14 p=-79 f=-93         zero IF, AF source 5 (DSB), wide, as tuned
v1 n=11 w=6145 b=0 p=-80 f=-86          the same, narrow
v2 n=0 w=0 b=0 p=-75 f=-78              zero IF, AF source 5, wide, 2.4 kHz high
v3 n=0 w=0 b=0 p=-75 f=-79              zero IF, AF source 5, wide, 1.0 kHz high
v4 n=12 w=5119 b=12 p=-80 f=-94         zero IF, AF source 4 (raw), wide
v5 n=11 w=5675 b=11 p=-80 f=-94         zero IF, AF source 7 (AM), wide
v6 n=11 w=5350 b=12 p=-80 f=-94         zero IF, AF source as found (FM), wide
v7 n=40 w=2766 b=0 p=-75 f=-79          the IF as found, FM, narrow: the sixth run's front end 3
```

- **Every front end at the zero IF and on tune gives long captures full of data**: 250 to
  1,056 words each, the pre-key as 5555 for 15 words, then dense words that come again from
  block to block, then near silence until the next pre-key. Runs of one sample 80%, of two
  18%, as the FM bench had them. The control at the IF as found gives the old picture (short
  pre-keys, single bits).
- **It is the zero IF that does it, not the AF source**: 5, 4, 7 and FM as found are alike,
  wide and narrow alike. 1.0 and 2.4 kHz off tune there is nothing.
- At the zero IF the RSSI follows the blocks (floor -93 dBm between them, a burst for each):
  the steady carrier at 0 Hz is taken out there, and what is left is the sidebands.
- No block reads with the reader as it was, and the reason is the same as on the FM bench.
  The words fit this to the run (`python3 -m modem.acars plain LOG --fit "AG7EW TEST 50.650 R"`
  lays every capture against the blocks' known opening):

  | Interval between two turns of the slicer | The chip gave |
  | --- | --- |
  | 1 sample of 4800 (inside 2400 Hz) | one sample 100% |
  | 1.5 (a change of tone) | one sample 96%, two 4% |
  | 2 (inside 1200 Hz) | two samples 98 to 99% |

  1. **The slicer sees the audio's derivative.** Its sign turns over at the audio's peaks, not
     at its zero crossings: a quarter and three quarters into a bit of 2400 Hz, the middle of
     one of 1200 Hz. So the turns are 1 sample of 4800 apart inside 2400 Hz, 2 inside 1200 Hz,
     and 1.5 at every change of tone. (At the zero IF the discriminator makes a phase of the
     AM envelope, and its output is that phase's rate of change. In FM it was the sender's
     pre-emphasis. The FM bench's "13 pairs sent, 12 read, a lone one gone" is this.)
  2. **The chip's clock starts again at every turn** (docs/k6-aprs-clock.md found the same on
     Bell 202), so each interval is given a whole number of samples by itself:
     floor(x - 0.55) + 1 for an interval of x periods of the chip's baud, about. Nothing is
     carried from one interval to the next.
  3. At 4800 baud that makes 1.5 one sample, the same as 1. A lone half cycle of 1200 Hz among
     2400 Hz (`HLH`) reads as five single samples, like two and a half bits of 2400 Hz, and
     where it stood in them is lost: **at 4800 baud the samples do not hold the block.**
     Parity and the check cannot make up for it.
- The module read the FIFO by time at 4800 words' pace, and with 1.5 as one sample the chip
  made 6% fewer: about every tenth word of a capture's data is a read of the empty FIFO (the
  word of eight reads before, `modem/aprs.py` `dfsk_destale`). The fit above does not mind
  (a place with such a word in it does not count).

**What follows: a faster clock.** With the chip at 5400 to 6000 baud an interval of 1.5 is
1.7 to 1.9 periods and should come as two samples, while 1 stays one and 2 stays two. Then a
bit of 1200 Hz after 2400 Hz is a run of two, every further one a run of two, and the first
bit of 2400 Hz after them a run of two and a run of one: a run of n bits of 1200 Hz is n + 1
runs of two, a run of n of 2400 Hz is 2n - 1 runs of one, and nothing is lost.
`modem/acars.py` has the model (`chip_samples`) and the reader (`follow_tones`: the likeliest
bits for the runs, a bit of 2400 Hz two intervals and one of 1200 Hz one; `plain` uses it for
a front end over 5000 baud). On the model every block reads from 5400 to 6300 baud, as long
as the turns' jitter stays under about 10 us; where the window sits depends on when after a
turn the chip takes its sample, which only the chip can say. So `DFSK_SWEEP`: eight front
ends of 22 s at 5200, 5400, 5550, 5700, 5850, 6000, 6150 and 6300 baud (REG_72 tops out at
6,347), the pre-key still the sync word AAAA. The FIFO is read at 4800 words' pace still,
which is now the slowest the chip makes them on a block. Two builds:

- `dfskcap_acarsb`: in FM on 440.850 MHz (the IC-7100 at power 0, blocks only:
  `python3 -m modem.acars probe OUT.wav --only R`), the IF as found. The FM bench signal has
  the same turns, so this finds the baud without a key-up on 6 m.
- `dfskcap_acars6`: the same at the zero IF on 50.650 MHz, for AM.

#### The clock sweep (2026-11-11, build 7000991)

K6 receive only in both. Run A: `dfskcap_acarsb`, the IC-7100 in FM on 440.850 MHz at RF power
0, keyed 02:08:52 to 02:12:36Z (hamlet `~/blowfang-work/runs/acarsb-fm1-20261111/`). Run B,
the eighth on 6 m: `dfskcap_acars6` at the zero IF, the IC-7100 in AM on 50.650 MHz at 5%,
keyed 02:15:58 to 02:19:41Z, SWR 1 on all 110 readings (`runs/acars6-am8-20261111/`); the rig
back on 440.850 MHz FM-D at power 0 by 02:19:52Z. About 35 openings a front end in each run.
**No block read in any front end.** What the chip gave each interval:

| Baud | FM: 1.5 as one, two | FM: 2 as two, three | AM, zero IF: 1.5 as one, two | AM: 2 as two, three |
| --- | --- | --- | --- | --- |
| 5200 | 88%, 12% | 97%, 0 (3% one) | 95%, 5% | 98%, 0 |
| 5400 | 88%, 12% | 97%, 0 | 95%, 5% | 98%, 0 |
| 5550 | 54%, 46% | 100%, 0 | 60%, 40% | 100%, 0 |
| 5700 | 55%, 45% | 100%, 0 | 60%, 40% | 100%, 0 |
| 5850 | 55%, 45% | 88%, 12% | 60%, 39% | 98%, 2% |
| 6000 | 55%, 45% | 89%, 11% | 60%, 40% | 98%, 2% |
| 6150 | 55%, 45% | 89%, 11% | 60%, 40% | 98%, 2% |
| 6300 | 53%, 47% | 88%, 12% | 60%, 40% | 98%, 2% |

An interval of 1 came as one sample 98 to 100% of the time everywhere.

- **The clock does not start again at every turn; it locks onto the signal.** With the chip
  set to 5550 or 5700 baud the samples of a block still come at 0.999 a sample of 4800: the
  clock is pulled 16 to 19% down to the audio's own turns, and time is kept, not rounded
  away interval by interval. The first reading (floor(x - 0.55) + 1, nothing carried over) is
  what such a clock looks like at 4800 baud only.
- **Three steps, not eight bauds**: 5200 and 5400 are alike, 5550 and 5700, and 5850 to 6300,
  in FM and in AM. The chip's bit period seems to be a whole number of some inner tick (15,
  14 and 13 of about 76 kHz would fit, 16 at 4800). REG_72 = FFFF may be one step more.
- **An interval of 1.5 is half a period out for a clock locked at 4800, and the chip gives it
  one sample or two by what went before.** In AM from 5550 baud up: after two or more
  bits of 2400 Hz one sample (93 to 99%), after a single one two (half the time to nearly
  always), after one bit of 1200 Hz one sample (90 to 99%), after two or more bits of 1200 Hz
  two samples (about 80%, but 20 to 35% after exactly three). The same place in the
  same block does not always come the same: the best guess from the tone bits around it is
  still wrong for 3 to 6 intervals in a hundred, with the signal 40 dB over the floor.
- A reader that learns those odds from the known openings and looks for the likeliest bits
  (a Viterbi over the bits with the intervals before as its state) gets 8 to 13 bits of an
  opening's 320 wrong in FM and about 30 in AM. A lone bit of 1200 Hz among 2400 Hz is
  mostly five single samples still. **From 5200 to 6300 baud the samples do not hold the
  block either.**

**What follows: one sample a bit.** The sweep sampled twice a bit and more, where the clock
has to choose between the turns of 2400 Hz (a quarter and three quarters into a bit) and
those of 1200 Hz (the middle): they are half a sample apart, and that is the 1.5. At 2400
baud there is nothing to choose. The audio is zero at every bit's edge and its slope there is
the data bit itself: a bit of 1200 Hz (the bit changed) turns the slope over once, in its
middle, a bit of 2400 Hz (the same bit again) turns it twice and leaves it as it was. The
slope is what this receiver slices. A clock of one period a bit whose cell edges sit on the
1200 Hz turns has the 2400 Hz turns a quarter period either side of them, pulling it equally
both ways, and its samples, mid cell, fall on the bits' edges where the slope is at its
largest: **the plain FSK receiver at 2400 baud would then give the block's bytes as they
are**, low bit first, SYN SYN as the word 6868 (9797 with the slope the other way up), and
the chip's own sync word could be SYN SYN. `modem/acars.py` `nrz_samples` is that reading,
`nrz_blocks` the reader (`plain` uses it for a front end under 3000 baud, with a `nrz` line
in the fit line's place: blocks read, and how many of the known opening's 272 bits each
capture has off). It holds on a model clock that moves a sixteenth to a half of the way to
each turn, with the turns up to 20 us off their places; it does not hold for a clock that
starts again at every turn. Which the chip's is at 2400 baud is for the bench.

`DFSK_SETS`, sixteen settings of 11 s (`dfskcap_acarsc` in FM on 440.850 MHz, and
`dfskcap_acars6` at the zero IF on 50.650 MHz):

| | REG_58 | Baud | Sync | Asks |
| --- | --- | --- | --- | --- |
| 0 to 3 | 00E9 | 2400 | 6868, 9797, D554, 2AAB | SYN SYN as it comes and upside down; "+*" before it, both ways (SYN SYN then stands in the capture) |
| 4, 5 | 00A9 | 2400 | 6868, 9797 | bits 7:6 = 10, which made the 1200/2400 Hz receiver slice Bell 202 right |
| 6, 7 | 00E5 | 2400 | 6868, 9797 | RX bandwidth bits = 2 |
| 8 | 00E9 | REG_72 = FFFF (6348) | AAAA | one step more of the clock? |
| 9 | 00E9 | 4800 | AAAA | the control |
| 10 to 12 | 00E1, 00E5, 00ED | 4800 | AAAA | RX bandwidth bits 0, 2, 6: does any show the slicer the audio and not its slope? |
| 13, 14 | 00A9, 0069 | 4800 | AAAA | bits 7:6 |
| 15 | 04E9 | 4800 | AAAA | RX mode 1, unnamed |

#### Run C: the settings (2026-11-11 03:00:59 to 03:04:42Z, build c7fb47e)

`dfskcap_acarsc`, the IC-7100 in FM on 440.850 MHz at RF power 0, K6 receive only (hamlet
`~/blowfang-work/runs/acarsc-fm2-20261111/`; the IC-7100 was found on 7.066 MHz PKTUSB at 80%
power from other work and set to the bench state and read back before keying). **No block in
any of the sixteen settings.**

```
nrz0 2400 baud: 0 blocks read, 0 captures       00e9, sync 6868
nrz1 2400 baud: 0 blocks read, 0 captures       00e9, 9797
nrz2 2400 baud: 0 blocks read, 9 captures; of the opening's 272 bits off: 65 66 67 67 68 68 68 69 70     d554
nrz3 2400 baud: 0 blocks read, 10 captures; of the opening's 272 bits off: 65 65 66 66 66 67 67 68 68 240   2aab
nrz4 to nrz7: 0 captures                        00a9 and 00e5, 6868 and 9797
fit8 6348 baud: 0 blocks read, 21 openings; 1 -> 1:99% 2:1%; 1.5 -> 1:53% 2:47% 3:0%; 2 -> 1:0% 2:89% 3:11%
fit9 4800 baud: 0 blocks read, 22 openings; 1 -> 1:100%; 1.5 -> 1:88% 2:12%; 2 -> 1:3% 2:97%
fit10 to fit12: no capture                      00e1, 00e5, 00ed at 4800
fit13 4800 baud: 0 blocks read, 22 openings; 1 -> 1:100% 2:0% 3:0%; 1.5 -> 1:98% 2:2%; 2 -> 1:2% 2:98% 3:0%    00a9
fit14 4800 baud: 0 blocks read, 10 openings; 1 -> 1:89% 2:10% 3:1% 4:0%; 1.5 -> 1:77% 2:22% 3:0% 4:1%; 2 -> 1:0% 2:100%   0069
fit15 4800 baud: 0 blocks read, 22 openings; 1 -> 1:100%; 1.5 -> 1:87% 2:13%; 2 -> 1:3% 2:97%    04e9
```

- **At 2400 baud the receiver does not give the bytes.** SYN SYN never comes out of it, either
  way up, with any of the three REG_58 values. It does start on "+*" (which is mostly bits
  that alternate, so it matches both ways up, 9 and 10 captures of about 18 blocks), and what
  follows is three quarters single samples: the clock takes a sample for nearly every turn of
  the slicer, two a bit inside 2400 Hz, as at 4800 baud, though it is set to one a bit. Runs
  of two to four samples stand in the same places from block to block, so the block shapes
  them, but nothing read them back. The clock that would have made this work, one that is
  pulled gently and keeps its place between the turns, is not what the chip has: it follows
  each turn at once, at every baud from 2400 to 6348.
- **REG_72 = FFFF is no further step**: 6348 baud is like 5850 to 6300.
- **REG_58's RX bandwidth bits 0, 2 and 6 are deaf here** (no pre-key found at 4800 baud; 4 is
  the only one of them that passes the tones). Bits 7:6 = 10 make an interval of 1.5 one
  sample still more surely (98%), 01 is worse all round, RX mode 1 is mode 0.
- So the plain FSK receiver has no setting left that reads ACARS. What it would take is a
  sample clock that either runs at 9600 baud (1, 1.5 and 2 as two, three and four samples;
  the chip stops at 6348) or does not follow the turns, and neither is on offer.

#### The tone receiver once more (`dfskcap_acarsd`, 2026-11-11)

Point 5 below rests on two runs: the pager's at 2400 baud (2026-11-04, six REG_58 values) and
`acarsfsk` (2026-11-10, four), both in FM from the IC-7100, both waiting for the chip to find
a sync word, and both with no sync at all, not even on an opening of the chip's own kind.
Three things were left open by them, and a reader's list of ideas for this page (2026-11-11
04:17Z) asks for them:

1. **What does the tone receiver make of the block, sync word or not?** The chip gives the MCU
   no unframed bit stream (REG_47's "FSK out" is the audio pin, which the K6's MCU cannot
   read). But the pre-key is a steady 2400 Hz, which is 0000 as tone bits (FFFF the other way
   up), and a receiver given that as its sync word starts on the pre-key and hands over the
   tone bits of everything after it. The framing is then the host's: SYN SYN looked for in the
   bits, either way up, the differences undone, the check tried (`modem.acars tone`).
2. **Was the probe at fault at 2400 baud?** A control the receiver is known to read: the K
   unit at 1200 baud (an L unit: a cycle of 1200 Hz or two of 2400 Hz a bit, the format
   `aprsfsk` reads).
3. **AM at the zero IF.** Every run of the tone receiver was in FM, through a transmitter
   whose audio chain is hard on MSK at 2400 baud (docs/k6-if-modem.md: 4% bit errors at any
   level). Real ACARS would reach it as AM at the zero IF, where the plain receiver showed
   that the discriminator gives the tones whole.

`dfskcap` with `DFSK_TONE` is the recorder for it: sixteen settings of 11 s, the units K, R, L
and T in turn with 0.4 s between them (`python3 -m modem.acars probe OUT.wav --only KRLT --gap
0.4`). A T unit is no block but a ladder, the first rungs of a receiver's test: 80 ms of
2400 Hz alone, 80 ms of 1200 Hz alone, then the two in turn, 8, 4, 2 and 1 bits of each. (The
K unit's opening is the tones in turn bit by bit, and the R unit's the pre-key and the bit
sync, so the ladder is whole with them.)

| Setting | REG_58 | Baud | Sync word | What it asks |
| --- | --- | --- | --- | --- |
| 0, 1 | 33A9 | 2400 | 0000, FFFF | the tone bits as they come, 512 bytes a capture |
| 2, 3 | 33A9 | 2400 | 5C5C, 3FFE 5C5C | does the chip find SYN SYN itself (`acarsfsk`'s question, read by time now and not by the sync interrupt) |
| 4 | 33A9 | 2400 | 076B 3E36 | the K units |
| 5, 6 | 33A9 | 2400 | 5C5C, 076B 3E36 | the same with REG_59's preamble length 0 (it was 15 in every run so far) |
| 7, 8 | 33E9 | 2400 | 0000, 076B 3E36 | REG_58's bits 7:6 = 11 (10 slices Bell 202 best) |
| 9 | 33A3 | 2400 | 0000 | the 1200/1800 Hz bandwidth, `aprsfsk`'s setting |
| 10 | 3329 | 2400 | 0000 | bits 7:6 = 00 |
| 11 | 33A9 | 2400 | 0000 | REG_2B + 0100: the AF de-emphasis off |
| 12 | 33A9 | 2400 | 0000 | REG_2B + 0700: and the 300 Hz high-pass and 3 kHz low-pass off |
| 13 | 33A9 | 4800 | 0000 | two samples a tone bit: where the tones change, for a clock held by the host |
| 14, 15 | 33A3, 33A9 | 1200 | 076B 3E36 | the control: the L units |

The report gives, a setting, the blocks read, and for every capture how many of the 264 tone
bits that each unit's block opens with (the SOH to the text's `AG7EW TEST 440.850 `) are off
at the best place, with those bits laid under the sent ones (`--show`), and the same for the
ladder's 336 bits wherever a capture has them. So a receiver that gets the tones nearly
right, or wrong in a regular way (a clock that slips, a tone it does not tell, the bits
upside down), shows as that and not as "no sync".

`dfskcap_acarsd` is the build for FM on 440.850 MHz (the IC-7100 at RF power 0) and
`dfskcap_acars6` the same at the zero IF on 50.650 MHz (AM, with the owner's word; there the
signal has every second round of units 1.8 times the level, `--levels 1,1.8`, since the
sender's depth of modulation was never measured). REG_31 (the compander and the scrambler)
reads 0000 on this K6 and the AFC is off at the zero IF; the receiver's AGC is left as it is.

#### The two FM rounds (2026-11-11, builds 2075259 and 8f5a2b8)

K6 receive only in both, the IC-7100 in FM on 440.850 MHz at RF power 0. Run E:
`dfskcap_acarsd`, keyed 04:44:27 to 04:48:13Z (hamlet
`~/blowfang-work/runs/acarsd-fm3-20261111/`). Run G: `dfskcap_acarse` (`DFSK_TONE 2`, the
units K, R and T), keyed 05:08:42 to 05:12:27Z (`runs/acarse-fm4-20261111/`). **No block read
in any of the thirty-two settings.**

- **The pre-key as the sync word works.** With 0000 (or FFFF) the receiver starts on every
  unit and hands over what it makes of the rest, a unit a capture. This is the first sight of
  the tone receiver's own bits on ACARS.
- **The chip's own sync never comes**: no capture with 5C5C, 3FFE 5C5C or the pager's word,
  with either preamble length. The 1200 baud control gave none either (the L units on the
  pager's word), so it proved nothing and was dropped.
- **At 2400 baud a tone must last about a cycle of 1200 Hz to be told.** The steady tones and
  the ladder's runs of 8 and 4 come exact but for one bit at each change (8 of 1200 Hz as 7,
  8 of 2400 Hz as 9), a pair of bits of 1200 Hz as one bit or none, and a lone one not at
  all. A block's SYN SYN has lone ones in it, which is why the chip never finds it. The
  ladder is placed with 53 to 62 of its 336 bits off, a block's opening with 55 to 65 of 264.
  33A3 is the most regular, bits 7:6 = 11 or 00 are worse, de-emphasis and filters change
  nothing.
- **At 4800 baud (two samples a bit) the bandwidth bits decide what is seen**:

  | REG_58 | 2400 Hz, n bits | 1200 Hz, n bits | a pair of 1200 Hz | a lone bit of 1200 Hz |
  | --- | --- | --- | --- | --- |
  | 33A9 (bits 3:1 = 4) | 2n + 1 samples, exact | fewer than 2n - 1, often split by a lone 0 | one sample (59%), `101` (14%), two (8%) | one sample; 4% missed in a block, a third missed where they come every other bit |
  | 33A1, 33AB (0, 5) | 2n + 1 | 2n - 1: time is kept | two samples (half), one or three | seen 61% and 78% in a block, never where they come every other bit (the ladder's last rung reads as silence) |
  | 33A3, 33AD (1, 6) | 2n + 1 | 2n - 2 | one sample or two | mostly lost |
  | 33A5 (2) | 2n + 2 | 2n - 2 | lost | lost |

  Only 33A9, the setting the chip's notes give for these tones, shows a lone bit of 1200 Hz,
  and it cannot tell it from a pair: both are mostly one sample, the pair with the zeros
  after it one sample longer about two times in three. 33A1 and 33AB keep time and tell a
  pair, and lose the lone bits that 33A9 shows.
- **Faster clocks gain nothing**: at 5400, 6000 and 6348 baud the runs of 2400 Hz keep their
  time and those of 1200 Hz lose more of it (8 bits as 14 or 15 samples of 21); a lone bit is
  one sample still. RX mode 7 and 5 (3FA9, 37A9) give nothing like the tones. Bits 7:6 = 11,
  01 or 00 break the long runs of 1200 Hz up with lone zeros.
- **How far from a block**: with each setting's runs read by its own rule, a block's 512 tone
  bits come with 54 to 90 wrong at 33A9 and 58 to 65 at 33AB and 33A1. A block needs them
  all. Part of it is not the pattern's doing: two captures of the same block (but for the
  three digits of its number) differ in a tenth of their samples at 33A9, and in 2 to 3 in a
  hundred at 33AB, at -21 dBm. Per character parity and the check cannot carry that.

So the tone receiver, like the plain one, falls short on the half cycle: MSK at 2400 baud
has a bit in half a cycle of 1200 Hz, and this receiver wants about a whole one (it was made
for a cycle a bit: 1200 baud). What FM cannot say is whether the audio of an AM signal at the
zero IF suits it better (the IC-7100's FM audio is hard on these tones, point 3 above), so
`dfskcap_acars6` now has `DFSK_TONE 3`, the pick of both tables, for one run on 6 m: 33A9 and
33A3 at 2400 baud unframed, the chip's own sync on 5C5C, 3FFE 5C5C and the pager's word,
33A9 with de-emphasis and filters off, then at 4800 baud 33A9 (and with de-emphasis off),
33A1, 33AB, 33AD, 33A3, 33E9 and 3329, and 33A9 at 6348 baud. The signal there is `--only
KRT --gap 0.4 --where 50.650 --levels 1,1.8`.

#### The tone receiver in AM at the zero IF (run H, 2026-11-11, build 99ab713)

The ninth run made on 6 m (its folder is `am11`: `am9` and `am10` were staged and not made): `dfskcap_acars6` with `DFSK_TONE 3`, the IC-7100 in AM on 50.650 MHz at
5%, keyed 05:37:11 to 05:40:57Z, SWR 1 on all 110 readings, the K6 receive only (hamlet
`~/blowfang-work/runs/acars6-am11-20261111/`); the rig back on 440.850 MHz FM-D at power 0 by
05:41:05Z. The carrier was -80 to -82 dBm at the K6, 12 to 15 dB over the zero IF's floor
there (-93 to -97). **No block read in any of the sixteen settings, and the chip's own sync
(5C5C, 3FFE 5C5C, the pager's word) never came.**

- **The tone receiver does hear an AM signal at the zero IF**, and at 4800 baud 33A9 gives
  the cleanest picture of the tones of any run: a run of n bits of 2400 Hz is 2n + 1 zeros,
  and a run of n bits of 1200 Hz is n single ones a sample apart (`1 0 1 0 1`), a pulse a
  half cycle. The ladder's 192 bits of 1200 Hz come as 192 pulses, its runs of 8 as 8
  and of 4 as 4, time kept, and its last rung (the tones in turn bit by bit) as 24 pulses
  three zeros apart: every lone bit there. The level (1 or 1.8) and the de-emphasis make no
  difference at 4800 baud.
- **But in a block the first half cycle of 1200 Hz after two or more bits of 2400 Hz is
  dropped about half the time**, its two samples left in the gap (the time is kept, the bit
  is not): a lone bit is then gone, a pair is one pulse. Of the runs of 1200 Hz that follow a
  single bit of 2400 Hz about four in five come whole (234 of 297), of those that follow two
  or more under half (160 of 345). The ladder's pairs, two bits of 2400 Hz before each, come
  as one pulse more often than as two. With the runs read by that
  rule a block's 504 tone bits come with 63 to 90 wrong (33A9, three long captures each with
  and without de-emphasis), no better than FM's 54 to 90. The other bandwidth bits and bits
  7:6 = 11 or 00 are worse here (101 to 190 wrong), where in FM 33A1 and 33AB kept time.
- **At 2400 baud** 33A9 with the AF filters as found gives hardly a 1 at all in AM (3 samples
  in 100); with de-emphasis and both filters off the ladder is placed with 56 to 64 of 336
  bits off, as in FM.
- Between units the zero IF's noise keeps the receiver's words changing, so a capture does
  not end with its unit as in FM: where the tones are told (33A9 at 4800 and over, 33E9,
  3329, 33A1) a setting is three or four captures of 3.5 s, several units in each. The
  report's count of an opening's bits off decimates the samples, which does not suit pulses
  a sample wide; the numbers above are from the runs.

**So the tone receiver does not read ACARS in FM or in AM.** It was made for a cycle of
1200 Hz a bit, and MSK at 2400 baud asks it to tell half a cycle: it does that where the half
cycles come in a steady pattern and loses them where they stand alone, in every setting of
REG_58, REG_72, REG_59 and REG_2B tried (forty-eight in three runs). With the plain receiver
closed the same way, **ACARS on the K6's chip is parked** (Jeff, 2026-11-11 05:55Z).

### What the 6 m tests say

Nine runs on 6 m (eight on the plain receiver, the last five of those with the carrier 40 dB
over the K6's floor, then the tone receiver's) and six in FM on 440.850 MHz:

1. **The plain FSK receiver's slicer is fed by the FM discriminator and nothing else.** REG_47's
   AF source AM changes what the speaker gets, not what the slicer gets.
2. At the IF as found it sees of an AM signal only what a narrow filter turns into FM: under
   100 Hz of swing, enough to start on a block's pre-key and never enough to read its data.
3. **At the zero IF (REG_3D = 0, the chip's double sideband mode) the discriminator turns the
   AM into something the slicer follows through the whole block**, on tune, in either filter.
4. What the receiver gives there is the sign of the audio's slope, on a clock that follows
   every turn of it. That cannot be read back into bits at any setting tried: 4800 baud,
   eight clocks from 5200 to 6348 baud, 2400 baud (a sample a bit, where a steadier clock
   would give the bytes themselves), REG_58's bandwidth bits, its bits 7:6 and RX mode 1.
   The turns of the two tones are half a sample of 4800 apart, and the chip's clock gives
   that half away each time.
5. The 1200/2400 Hz tone receiver never syncs on ACARS, in FM or in AM at the zero IF. Given
   the pre-key as its sync word it hands over its tone bits, and they are the block's but for
   the lone half cycles of 1200 Hz, which it drops: one tone bit in six to nine wrong at the
   best settings, in FM and in AM alike (runs E, G and H above).
6. Parked, 2026-11-11. What would open it again: a chip or a mode with a receiver clock over
   6348 baud that the host can hold, or a way for the MCU to read the audio itself (REG_47's
   "FSK out" goes to the audio pin, which the K6's MCU cannot read without a hardware change).

Not measured, and of no use since no block reads: how weak a signal this path takes in. The zero IF's floor was -93 dBm in the
run against -117 at the IF as found, and ACARS at the bench site peaks at -103 dBm.

`dfskcap_acars6` stays the instrument for the bench (the `q` lines, every capture's words,
`DFSK_NARROW`, `DFSK_ZIF`, `DFSK_SWEEP`, `DFSK_SETS` and `DFSK_TONE`); `dfskcap_acars` on 131.550 MHz is unchanged in
what it does.

