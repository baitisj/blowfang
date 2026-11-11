# POCSAG pagers on the K6

Jeff asked on 2026-11-11 for new receive formats on the K6's RF chip. POCSAG came first: it is
direct FSK, the one format here that the FD6818's plain FSK receiver was built for.

## The format

POCSAG (ITU-R M.584) keys the carrier itself high or low for each bit, at 512, 1200 or 2400 baud.
Behind an FM discriminator that is a plain NRZ line. A transmission is a preamble of at least 576
bits of 1010..., then batches. Each batch is the sync codeword `7CD215D8` and eight frames of two
codewords. A codeword is 32 bits, top bit first:

| bits | address codeword | message codeword |
| --- | --- | --- |
| 31 | 0 | 1 |
| 30..13 | the top 18 bits of the 21-bit address (RIC) | 20 bits of message |
| 12..11 | function (0 to 3) | (message) |
| 10..1 | BCH(31,21) check, generator x^10+x^9+x^8+x^6+x^5+x^3+1 | the same |
| 0 | even parity | the same |

The low 3 bits of the RIC are the frame its address codeword sits in. The message codewords that
follow carry 7-bit characters (alphanumeric) or 4-bit digits (numeric), each with its low bit
first. A message ends at the next address codeword or at the idle codeword `7A89C197`.

Where to listen: DAPNET, the amateur paging network, sends 1200 baud on 439.9875 MHz. Commercial
and hospital paging sits around 152-159 MHz. **Only the amateur band gets text.** In the US
(ECPA) a page's text is protected. So the module says addresses and text only when it is built
for 430-450 MHz. Any other build says counts only (transmissions, sync codewords, codewords right,
put right and past help), and the code that would print text isn't compiled into it.

## The module: `c/k6/mod/pocsag.c`

Level 2, receive only, about 3.7 KB. Builds:

| build | frequency | what it does |
| --- | --- | --- |
| `pocsag` | 439.9875 MHz (DAPNET) | one setting, 15 minutes, text on |
| `pocsag_440` | 440.850 MHz (the bench) | six settings of 45 s after a 32 s wait (the bench's questions, below) |

How it works:

- **Hunt.** The chip's sync word is `AAAA`, sixteen bits of the preamble. The module tries 1200,
  2400 and 512 baud in turn (45, 30 and 90 ms each), so the chip starts inside a preamble at
  whatever baud is on the air. The preamble's words then go by, and the module finds the sync
  codeword in the bits itself: at any bit offset, either way up, with up to two wrong bits. A
  capture whose words stop looking like a preamble (four words in a row) before a sync codeword
  turns up is a false start. It is counted (`z=`) and nothing is said.
- **Direct.** In the bench's settings 3 and 4 the chip's sync word is the sync codeword itself,
  four bytes, at 1200 baud: one way up, then the other. Does the chip start on it, and does it
  also take it upside down by itself?
- **Reading the FIFO.** The FIFO holds eight words and is read by time, a word every 16 bits, two
  words behind the chip. An empty FIFO gives the word from eight reads before
  (docs/k6-aprs-clock.md). So a word equal to that one is read again. If the second read is the
  same, the FIFO was empty and the time base starts over. Otherwise both are real words. Inside
  a preamble every word is the same, so reads go by time alone until the first sync codeword.
- **Decoding.** BCH and parity put up to two wrong bits right in each codeword. The module works
  out the 31 single-bit syndromes at the start and tries their pairs, so it needs no table in
  flash. After 16 codewords the next 32 bits must be the sync codeword again, with up to four
  wrong bits. If it is missing after a clean batch, the transmission is over. If it is missing
  after a noisy batch, the module keeps hunting for up to 1,100 bits.
- **Long transmissions.** The chip's packet is 512, 1024 or 2048 bytes (about 7-8 s at 512, 1200
  or 2400 baud). If a packet ends inside a transmission, the chip's sync word becomes the sync
  codeword's last two bytes (`15D8`, or `EA27` upside down), and the next batch continues the
  same transmission. The message cut at the gap gets `e16` or more.
- **Lines** come after the transmission, never during it, because a line costs milliseconds of
  listening:

```
pocsag start hz=440850000 n=6 text=1
s 0 t=32000 cfg=00e9 a=0000 h=1        a setting: REG_58, REG_2B bits set, 1 hunt / 2, 3 direct
p 3 s=0 b=1200 t=40012 w=180 i=0 e=0 r=-41
                                       a transmission: setting, baud, ms at its first word,
                                       words read, upside down, empty reads, RSSI peak
k 3 q=4 ok=60 f=3 x=1 c=1 m=3          sync codewords, codewords right / put right / past help,
                                       chip packets, messages
m 1234 3 e0 AG7EW TEST 1 1200          address, function, codewords lost, text (430-450 MHz only)
+ ...                                  more of a long message
v 0 n=12 m=30 z=4                      a setting's end: transmissions, messages, false starts
pocsag end n=12 m=30 z=4
```

## The bench test

```sh
python3 -m modem.pocsag wav poc.wav --rounds 26     # 276 s: every round 512, 1200, 2400 baud
python3 tools/k6_bench.py send poc.wav --max-s 290  # right after the pocsag_440 upload
python3 -m modem.pocsag report LOG                  # per setting and baud
```

Each round sends three messages at each baud: `AG7EW TEST <round> <baud>` (RIC 1234,
alphanumeric), `<round>-<baud>` (RIC 2005, numeric), and a 75-character message that runs across
three batches (RIC 4711). Every second round is sent upside down. The report rebuilds the
messages from the round and the baud, so no key file is needed.

The six settings ask:

| setting | REG_58 | REG_2B | start | question |
| --- | --- | --- | --- | --- |
| 0 | 00E9 | as found | hunt | does the plain receiver take POCSAG at all, and at which bauds? |
| 1 | 00E9 | + 0700 | hunt | do the AF path's de-emphasis, 300 Hz high-pass and 3 kHz low-pass hurt an NRZ line? |
| 2 | 00E9 | + 0100 | hunt | de-emphasis off alone |
| 3 | 00E9 | as found | direct `7CD215D8` | does the chip start on the sync codeword? |
| 4 | 00E9 | as found | direct `832DEA27` | and the other way up, or does it take either by itself? |
| 5 | 00E5 | as found | hunt | the RX bandwidth bits at 2 (00E9 has 4) |

## Tested on the emulator

`tests/test_k6_pocsag.py` runs `pocsag_440` on the emulated M0 against a model of the plain FSK
receiver at the line's own baud. The model's clock is locked to the data, it finds the sync word
at any bit, it puts 16 bits in each FIFO word, and an empty FIFO's read gives the word from eight
before. The test sends 25 rounds at all three bauds, every second round upside down, some rounds
0.3% fast or slow, with one bit in 2,000 wrong. In the hunting settings every transmission's
three messages come out exact. In the direct settings only the 1200 baud ones whose sense matches
the sync word come out. Another test checks that a 157.45 MHz build says counts only and is
smaller.

The model can't say how the real receiver takes a 512 or 2400 baud line, which way up the
IC-7100 sends it, or what the 7100's audio filters do to an NRZ line. Only the bench can.

## Bench run 1 and the probe `pocdump`

Run 1 (2026-11-11 08:21-08:27Z, `pocsag_440` at 1b5a174, RSSI -23 dBm) read **512 baud
perfectly** with the AF de-emphasis off (setting 2) and with REG_58 00E5 (setting 5): 9 of 9
transmissions exact. With the AF path as found (setting 0) 5 of 16, so the de-emphasis hurts an
NRZ line. At 1200 baud the chip found the sync codewords, in both direct settings too (so it may
take either sense), but the codewords were mostly wrong and the FIFO read empty 27 to 92 times a
transmission: the chip made words more slowly than one every 16 bits. At 2400 it never started.

`pocdump` (2.3 KB) records what the chip hands over at 1200 and 2400 baud, two ways: the
receiver at the line's own baud, and the receiver at 4800 baud as a sampler of the line (four
samples a bit of 1200, two of 2400), the way `aprsdfsk` reads Bell 202. If the samples are clean,
the bits can be had in the MCU without the chip's clock. Six settings of 40 s, all with the
de-emphasis off but the last:

| setting | REG_58 | REG_72 | sync | what |
| --- | --- | --- | --- | --- |
| 0 | 00E9 | 1200 | `AAAA` | the chip's own clock at 1200 |
| 1 | 00E9 | 4800 | `F0F0` | 1200 sampled four times a bit |
| 2 | 00E9 | 2400 | `AAAA` | the chip's own clock at 2400 |
| 3 | 00E9 | 4800 | `CCCC` | 2400 sampled twice a bit |
| 4 | 00E5 | 1200 | `AAAA` | RX bandwidth bits 2 |
| 5 | 00E9 | 4800 | `F0F0` | as 1, de-emphasis on |

It skips the preamble's words and says the next 150 (`c` and `d` lines). On the host,
`python3 -m modem.pocsag dump LOG` slices the samples back to bits, finds the round each capture
came from and lays the bits against what was sent: bits wrong, dropped and added and where, and
how many codewords of the first batch the module would read. On the emulator every capture lays
with nothing wrong.

```sh
python3 -m modem.pocsag wav poc12.wav --bauds 1200,2400 --rounds 60   # 279 s
python3 tools/k6_bench.py send poc12.wav --max-s 290                  # right after the pocdump upload
python3 -m modem.pocsag dump LOG
```

## NOAA SAME: the probe `samecap`

SAME (the weather radio's alert headers) is AFSK, not direct FSK: mark 2083.3 Hz, space 1562.5 Hz,
520.83 baud, bytes with the low bit first, sixteen bytes of AB before the text, each header sent
three times. The datasheet lists a "NOAA SAME RX" mode: the plain receiver with RX bandwidth bits 2
(REG_58 00E5) and REG_72 about 5377. A plain receiver slices the discriminator's sign, which means
nothing for two audio tones, unless that setting puts a tone discriminator in front. `samecap`
(1.6 KB) records what the chip gives. Its sync word is D5D5 (AB with the low bit first), or 2A2A,
in four settings of 40 s: 00E5 and 00E9, each sync word. It says each capture's 64 words
(`python3 -m modem.same report LOG`).

- Bench: `python3 -m modem.same wav same.wav` (33 rounds, 161 s) sent right after the
  `samecap` upload. The bursts have SAME's format but no EAS header (no ZCZC, no event code).
  Their text is `AG7EW SAME FORMAT TEST <n>`.
- Bench (2026-11-11 08:31Z): no captures in any of the four settings. The plain receiver at
  REG_72 5377 never finds D5D5 or 2A2A in SAME's tones, so it can't read SAME this way.
- `samedf` (2.4 KB, pocdump.c with other settings) tries the plain receiver as a sampler of the
  audio instead, as `aprsdfsk` reads Bell 202: at 4166.7 samples a second (eight a bit; the mark
  at two a cycle, sync `AAAA`, if the chip's clock locks onto it as it did onto 2200 Hz), at 4800
  (sync `952A`) and at REG_72's top, 6347 (sync `6666`): words the host's model finds in every
  preamble at a fixed clock, at any phase. It says each capture's first 256 words, and
  `python3 -m modem.same df LOG` reads them: the tones told apart over each bit's samples, the
  bit clock kept on them, the samples a second searched from 0.8 to 1.2 of REG_72's. On the
  emulator all three rates read the bursts; at 6347 a second the host reads with noise of a
  third of the tone's amplitude.
- `samerx` (2.5 KB) reads the headers in the radio from those samples: each bit's samples laid
  against both tones (tables of cos and sin), the bit clock found over the first 24 bits and kept
  on the early/late difference, bytes set by the preamble's AB, text to the first unprintable
  byte, cut at a header's last `-`. A capture without at least two AB bytes is noise or speech,
  only counted (`z`), so it can listen to the broadcast's speech for hours. It reads the FIFO by
  time; at 4800 and 6347 a second a word read twice alike is the empty FIFO, but at 4166.7 the
  mark's 1010 makes words alike all the time, so there it reads by time alone. `samerx_440`
  tries its three rates on the bench (45 s each, `h` and `+` lines); `samerx` listens to
  162.550 for two hours at the rate `SAME_RATE` names (the bench's best). On the emulator every
  burst reads exactly at all three rates (noise of a fifth of the tone at 6347, a tenth at 4800).
- Live: `samecap_nwr` listens to 162.550 MHz (KHB60 Seattle) for an hour with the datasheet's
  setting. It says only captures that go on like a preamble, because noise gives D5D5 every couple
  of minutes. The weekly test is on Wednesdays around midday Pacific.

## Next

- Bench run on 440.850 with `pocsag_440` (receive only).
- If it reads: listen on DAPNET 439.9875 with `pocsag`. DAPNET's time messages could become a
  third time source after AIS and APRS. It may not be in range at hamlet.
- A counts-only census of the VHF paging channels: does the chip's FM sensitivity beat the IF
  tap there?
- MDC1200 (the 33xx tone receiver's own 1200/1800 Hz format) and NOAA SAME (REG_58 bandwidth bits
  010 by the datasheet) follow.
