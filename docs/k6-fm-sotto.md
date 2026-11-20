# FM in, sotto out: the K6 codes what it hears (fmsotto)

Jeff, 2026-11-20: "Well, I guess the next solution is to receive FM audio and then encode." The
K6 has no path from its mic to the MCU (docs/k6-mic-probe.md, measured), so the voice comes in
by radio. `c/k6/mod/fmsotto.c` (MENU name FM SOTTO, API level 13) demodulates the FM channel the
K6 is tuned to from the FD6818's low IF and feeds the slim sotto 1200 encoder (sotto
`c/fixed/sotto_fx_slim.c`, docs/sotto-slim.md there) live. It says the superframes over the
serial cable; sending them on as FuSotto is the next step.

## The chain

```
GP0 code 13 on PB1 -> DMA channel 1, flat out (~5.6 M samples/s) -> 512 B ring
  half and full interrupts (46 us apart), in the handler:
    ones in each 64 samples -> 2 * ones - 64 (~87.8 kHz)
    -> mixed down by 26 MHz / 1536 (64-entry sine), CIC 2nd order by 6 (~14.6 kHz)
    -> CORDIC phase, its step = the FM discriminator (aprsclk.c's front end)
    -> DC out, 750 us de-emphasis (pluto_bench --preemph's inverse, 0 dB at 1 kHz),
       2nd-order 3.5 kHz low-pass
    -> linear interpolation to 8 kHz -> a ring of 320 samples
  the foreground: 80 samples at a time into slim_encode10 -> 6 bytes each 40 ms -> "s N hex"
```

**The interrupt.** The ring holds 0.18 ms (1 KB) or 0.09 ms (512 B), and the encoder runs for
several ms at a time, so the polling the FT8 and APRS modules do can't work beside it. The
KD32F328 has no VTOR, but the firmware keeps an SRAM copy of the vector table for txdog and maps
SRAM at 0 (SYSCFG CFGR1 MEM_MODE 3) when it needs it. The module checks the copy is the
bootloader's table (but HardFault and TIM14), points entry 25 (DMA1_Channel1, IRQ 9) at its
handler with its state pointer in unused entry 46, maps SRAM at 0, and puts back CFGR1,
FLASH_ACR (MEM_MODE 3 drops the prefetch bit), the two entries and the NVIC bit when it ends.
The handler stops itself after 40,000 halves with no word from the foreground.

`c/k6/mod/dmairq.c` tried exactly this on the radio first (2026-11-20, job fmq1a, keyed main
305eb041): 22,035 halves of 512 B in 2,007 ms with 0 lost while the foreground burned 4 ms
bursts, the handler 22 % of the CPU (the popcount alone), 5,621 k transfers a second; then 579
halves and 0 lost across a say and an fl_read; CFGR1, FLASH_ACR and entry 25 read back as they
were.

**The clock.** The DMA's rate moves with the CPU's load (docs/k6-ysf-rx.md), so a fixed mixer
step and resampling ratio would wander. The handler times each half on TIM17 (96 MHz), smooths
it over 16 halves, and sets the mixer's step and the interpolator's step from it; the
foreground updates the interpolator's reciprocal each block (one division).

**The level.** The discriminator gives 4.48 a Hz at 14.6 kHz; the samples into the encoder put
a 1 kHz tone at 3 kHz deviation at 13,440 (-7.7 dBFS). The AGC is frozen at its maximum
(REG_13 0x03FF), as aprsclk does: the tap is a 1-bit path and doesn't care about level.

## Memory

| | bytes |
| --- | --- |
| code and constants (-Oz) | 10,622 |
| header and tag | 24 |
| DMA ring | 512 |
| front end (filters, clock, 320-sample ring) | 768 |
| slim encoder state | 3,148 |
| left of level 13's 15,408 | about 330 |
| stack, deepest (k6_main, encode, coder, search; -fstack-usage) | about 1,210 + fl_read + 116 for the handler, of the 2,136 the module gets |

The first build was 13.6 KB of code, 2.7 KB over. Three hooks added to the slim encoder (sotto
PR "hooks for its cell tables and DCT rows") move its read-only data out with the same bits:

- the codebook cells (`slim_cells.h`, 2.9 KB) come from a deck in the SPI module library,
  `out/fmsotto-cells.bin` (4,182 B, `tools/k6_fmsotto.py deck`), installed once into a free
  slot (14) with `--cards --install 14`. A deck is never run and the help viewer skips anything
  not 'K6HC'. The module finds it by scanning the slots for 'K6SC' with this build's sizes and
  checks its byte sum; no deck: returns -10.
- the DCT rows (800 B) are computed from their 21 magnitudes.
- the shape codebook was already read from SPI (0x100000).

A search reads 4 or 2 blocks of 16 centroids, then 2 reads per probed cell (its range, its list),
on top of the codebook vectors: about 200 SPI reads a superframe at 34 us, 7 ms of each 40 ms.

## Quality, on the model

`sim/k6if/frontend.py` (the GP0 path, calibrated against bench run 6: a bare carrier's FM noise
in ±6 kHz is 167 Hz rms in the model, 166 measured at 5.7 MS/s) on the two demo voices, sent as a
radio would (750 us pre-emphasis, the deviation limited at 3 kHz with 2 % of samples at the
limit), PESQ-NB against the original:

| voice | demodulated 8 kHz audio | sotto 1200 from it | slim encoder on the clean voice |
| --- | --- | --- | --- |
| 3536_f | 2.53 | 2.06 | 2.60 |
| 251_m | 2.98 | 2.22 | 2.55 |

The tap's own noise sets this: a 3 kHz-deviation tone comes out 33 dB above it, and speech sits
15-20 dB lower than that tone. With an ideal IF (no 1-bit path) the same code gives 2.95, and a
finer mixer (256 steps: +0.2 there) or a steeper low-pass (+0.2 there) change nothing once the
tap's noise is in. 5 kHz deviation helps the female voice (2.17) and not the male (2.13), and
starts to clip the encoder's input; the bench uses 3 kHz.

## Lines

```
fms start ks=5621 cfgr1=00000002 v25=080001d1 spi=1
s 0 0123456789ab...                    superframes 0-3, 4 x 6 bytes as hex
fms 1 sps=8000 pk=13021 skip=0 miss=0 isr=31 rd=15        each second
fms end n=750 lost=0 miss=0 h=440000
```

`sps`: 8 kHz samples made in the second (8,000 when the clock is right); `pk`: their largest
value; `skip`: 10 ms blocks the encoder fell behind on and skipped in the second (`lost` at the
end: in all); `miss`: DMA halves lost; `isr`: the handler's share of the CPU; `rd`: the SPI reads'. Returns the superframes coded, or -2 (a key before any),
-3, -4, -5, -7, -8, -9 as dmairq, -10 (no cell deck), -11 (the DMA never started).

`python3 tools/k6_fmsotto.py log k6.log out.bit --wav out.wav` turns a run's lines back into a
sotto 1200 file (gaps as silence) and decodes it with sotto's fixed-point decoder.

## Tests

`tests/test_k6_fmsotto.py` builds the module's C for the PC (`-DFMS_HOST`): a 700 Hz FM tone
through the tap model comes out at 8,000 samples a second, 25 dB over the noise; the module's
SPI reads (codebook, deck, DCT from 21 values) give exactly the superframes sotto's own tables
give on FM voice; a deck that doesn't add up is refused; the deck holds every vector once; the
code and scratch fit level 13; a log turns back into superframes.

## On the radio

**fmso1a (2026-11-20 08:19-08:32Z, keyed main 305eb041).** The deck went into slot 14 on the
first send (35/35 chunks, `K6 LIB put slot=14 r=4206`). fmsotto (from main 449e94d) started on
the first send too (`fms start ks=5737 cfgr1=00000002 v25=08000105`) and said superframes from
then on, 10,336 of them by the end of the log. Two things went wrong:

- **It ran 80 % of real time.** The superframe count rose by about 20 a second, not 25, so the
  encoder skipped one 10 ms block in five. The cause: the encoder's ~210 SPI reads a superframe
  (5.7 KB, measured on the host build) went through the core's `fl_read`, which runs from main
  flash, and with SRAM mapped at 0 (MEM_MODE 3) the flash prefetch is off (docs/k6-modules.md,
  txdog), so that code runs about half speed. The 34 us a read in the earlier estimate was
  measured with the prefetch on, on 20-byte reads.
- **It never stopped.** The foreground only looked at the clock and the keys when no block was
  waiting, and running behind, a block always was. No `fms N` line, no `fms end`, no key; the
  K6 kept coding until it was switched off (a 60 s log at 08:35Z still showed `s 14988`).
  Receive only, so nothing went on air.

The uploader's own retries (it heard no report, because the module was running) also went out
while fmsotto coded, so most of the run is the upload's data, then the voice at 08:29:13-47Z.

**The fix.** The loop checks the clock and the keys every time round; the handler also stops
itself after (RUN_MS + 30 s) of halves whatever happens; and the encoder's reads go straight to
SPI1 from RAM (READ 0x03 only, as `stockfont.c` does; `spi=0` in the start line falls back to
`fl_read`). The second's line now says `skip=` (blocks skipped in that second) and `rd=` (the
reads' share of the CPU), so the next run shows where the time goes. Jobs upload
with `--rounds 1` so no retry lands on a running module.

**fmso1d (09:28-09:33Z, the fix, after Jeff switched the K6 off and on at 08:56Z).** It
stopped on time (`fms end`, `ret=0` after 90 s), but `h=0`: the DMA never moved. gp0rx.h and
aprsclk.c turn on TIM17's update DMA request (DIER UDE), which is what starts channel 1 (it then
runs flat out); fmsotto and dmairq didn't, and only ran before because an earlier module had left
that bit set (the timer's registers keep their values with its clock off). Now fmsotto sets it
and clears it at the end, and returns -11 if no half arrives in the first 20 ms. The run also
started while the upload was still on the air (its quiet wait gave up after 30 s), so the voice
came after the module's 60 s. The quiet wait is now up to 150 s and RUN_MS 90 s.
(fmso1c had waited for a `K6 BOOT` line after the power cycle; the line came out garbled as the
radio powered up, so the job never started.)

## The bench run

One job, hands-free: install the deck into slot 14 (`k6_upload.py out/fmsotto-cells.bin --cards
--install 14 --via pluto`), upload fmsotto, wait for `fms start`, the Pluto sends a demo voice
(3536_f, then 251_m) on 440.850 with 3 kHz deviation and pre-emphasis, the K6 codes it, then
`tools/k6_fmsotto.py log` makes the WAV.

## Next

Sending it on as FuSotto from the K6 itself: the C4FM sender (c4sotto, 11.3 KB) and this module
don't fit the block together, so the superframes need a handoff between two modules (the
message store, as sotto_play uses, or a second run that keys up with what the first left in
RAM).
