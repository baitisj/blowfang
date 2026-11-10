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
