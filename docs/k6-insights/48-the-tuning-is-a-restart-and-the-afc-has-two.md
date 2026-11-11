# 48. The tuning is a restart and the AFC has two right answers

- From: the APRS sensitivity thread, 2026-11-11, reading the POCSAG thread's runs 4 to 8
  ([k6-pocsag.md](../k6-pocsag.md)) against the other decoders at Jeff's asking. The control
  run is built (`c/k6/mod/dfskcap_dmr4.c`, [k6-dmr.md](../k6-dmr.md), run M) and not yet run.
- `api->tune` restarts the receiver. After one, the POCSAG sampler read noisy bits for a
  minute or two, and far fewer when the module left the tuning out because the radio was on
  the frequency already (`api->rx_hz()` and REG_43, as `pocsag.c` asks). Three things follow
  for every module:
  - A bench build that sits on the upload's own frequency should not tune. One that must tune
    (a live channel) should count its first two minutes apart.
  - Settings tried one after another are not alike in those minutes. Say the first again at
    the end, or go round the table more than once.
  - A table that tunes at every setting (`dfskcap.c` does) measures the receiver just after a
    restart every time. Run L's weak steps for DMR were all taken so.
- The chip's AFC (REG_73 bit 4 = off; it takes hold at the next tuning) has two right answers:
  - **On** for a decoder that slices the chip's own discriminator at the tuned centre, when
    there is a steady carrier for it to pull in (the POCSAG sampler found nothing with it
    off). Its time constant is about 30 s, so a burst on a live channel never settles it: the
    bench's settled state does not exist on the air.
  - **Off** for a decoder that does its own frequency sums on the IF tap (`gp0rx.h`: FT8,
    WSPR, ifcal), where the pull shows as drift. The softmodem read the same either way.
  - Not explained: DMR's sync is found with the AFC off and a restart every 0.8 s (runs K and
    L), POCSAG's preamble is not, and the two radios of that bench are only 145 Hz apart.
- What does not carry over: the sampler's slipping clock (3.93 samples a bit in text). The
  APRS slicer loses a fixed 4 to 9 units at each change of tone whatever the run's length, and
  learns that from the flags already. FT8 has a cousin, the second CIC's rate picked in the
  set-up loop and 1.5% off in the run loop ([k6-ft8-wspr.md](../k6-ft8-wspr.md), "The DMA rate
  moves with the CPU loop").
- The POCSAG reader that follows the FIFO word by word knows nothing of POCSAG. It is the one
  to share between the chip-receiver modules; the APRS reader leans on Bell 202's tones.
- It would buy: bench figures that mean the same from run to run, a truer edge for the DMR
  detector, and a rule for how long the explorer should stay on a channel it has just tuned.
- Settled by: run M (blocks tuned at every setting, not tuned, and not tuned with the AFC
  on), and for WSPR a receive run judged from its second two-minute slot on.
