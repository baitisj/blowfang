# 48. The tuning is a restart and the AFC has two right answers

- From: the APRS sensitivity thread, 2026-11-11, reading the POCSAG thread's runs 4 to 8
  ([k6-pocsag.md](../k6-pocsag.md)) against the other decoders at Jeff's asking. The control
  run for DMR is done (`c/k6/mod/dfskcap_dmr4.c`, [k6-dmr.md](../k6-dmr.md), run M).
- `api->tune` restarts the receiver. After one, the POCSAG sampler read noisy bits for a
  minute or two, and far fewer when the module left the tuning out because the radio was on
  the frequency already (`api->rx_hz()` and REG_43, as `pocsag.c` asks). Three things follow
  for every module:
  - A bench build that sits on the upload's own frequency should not tune. One that must tune
    (a live channel) should count its first two minutes apart.
  - Settings tried one after another are not alike in those minutes. Say the first again at
    the end, or go round the table more than once.
  - A table that tunes at every setting (`dfskcap.c` does) measures the receiver just after a
    restart every time. Run L's weak steps for DMR were all taken so. Run M says it did them
    no harm: with the AFC off the plain receiver reads DMR's sync as well half a second
    after a restart as a quarter of an hour after. So what POCSAG saw is likely the AFC's
    pull or the sampler's own need, not the restart by itself.
- The chip's AFC (REG_73 bit 4 = off; it takes hold at the next tuning) has two right answers:
  - **On** for a decoder that slices the chip's own discriminator at the tuned centre, when
    there is a steady carrier for it to pull in (the POCSAG sampler found nothing with it
    off). Its time constant is about 30 s, so a burst on a live channel never settles it: the
    bench's settled state does not exist on the air.
  - **Off** for a decoder that does its own frequency sums on the IF tap (`gp0rx.h`: FT8,
    WSPR, ifcal), where the pull shows as drift. The softmodem read the same either way.
  - Not explained: DMR's sync is found with the AFC off and a restart every 0.8 s (runs K, L
    and M), POCSAG's preamble is not, and the two radios of that bench are only 145 Hz apart.
    A lead from run M: with the AFC on more of DMR's sync fields come whole (72% against 54
    to 59%, 83% in a call long enough for the AFC to settle), so where the signal sits
    against the slicer does change what the chip's clock makes of it, and a sampler that
    needs every bit four samples long would feel that far more than a sync word does.
- What does not carry over: the sampler's slipping clock (3.93 samples a bit in text). The
  APRS slicer loses a fixed 4 to 9 units at each change of tone whatever the run's length, and
  learns that from the flags already. FT8 has a cousin, the second CIC's rate picked in the
  set-up loop and 1.5% off in the run loop ([k6-ft8-wspr.md](../k6-ft8-wspr.md), "The DMA rate
  moves with the CPU loop").
- The POCSAG reader that follows the FIFO word by word knows nothing of POCSAG. It is the one
  to share between the chip-receiver modules; the APRS reader leans on Bell 202's tones.
- The rule for chip-sliced decoders then reads: AFC on (latched with one tune) unless the
  radio's offset is known and tuned out.
- It would buy: bench figures that mean the same from run to run, and a rule for how long
  the explorer should stay on a channel it has just tuned (run M: no longer than the burst
  it is after).
- Run N (2026-11-12, `dfskcap_dmr5`, [k6-dmr.md](../k6-dmr.md)) found why the AFC helps a
  chip-sliced decoder: with the AFC off the plain receiver slices at the tuned centre, not
  at the signal's mean (the share of ones in DMR moved from 68% to 40% over 1.1 kHz of
  tuning), and DMR's sync is found only within some 0.4 kHz of the best tuning, which on this
  bench is 0.2 to 0.5 kHz below the nominal frequency. Every run before tuned on the nominal
  and sat near the edge: hence run M's short sync fields, and the AFC's gain. For POCSAG the
  same is likely, though its sender was only 145 Hz off: its 1-bit sampler needs every bit,
  not 24 of them.
- Half settled 2026-11-12 by run M (blocks tuned at every setting, not tuned, and not tuned
  with the AFC on): for DMR's sync the restart costs nothing and the AFC on helps a little.
  Still to do: for WSPR a receive run judged from its second two-minute slot on, and the
  POCSAG sampler with the AFC off on a signal tuned a few hundred Hz either way (run N says
  what to expect: a narrow window round the true centre).
