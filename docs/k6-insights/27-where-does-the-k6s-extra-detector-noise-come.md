# 27. Where does the K6's extra detector noise come from?

- From: the softmodem thread (2026-11-07). On the gp0cap captures the timing and AFC
  detectors are 3-10 times noisier than on the model at 70 dB-Hz (their variance 0.13-0.57
  against 0.004-0.015), while the decisions stay clean (FFSK no errors down to -72 dBm).
  The noise is white from one symbol to the next and doesn't depend on the symbol pattern.
  FM noise added on the model (to 200 Hz rms) moves the AFC detector but hardly the timing
  one, so it isn't simply LO phase noise. Candidates: the sigma-delta's real behaviour on
  code 13, sample-time jitter in the DMA, or the IC-7100's audio chain.
- Buys: whatever it is caps 4FSK. Narrower loops (iflive, 2026-11-07) only shrink its
  effect, and the detectors' biases on real signals (timing -0.3 half-samples, AFC +15 Hz
  from the best) may be the same thing.
- Settled by: a gp0cap pass on an unmodulated carrier (the IC-7100's FM-D with no audio):
  the turn between half symbols should be flat. If it's as noisy, it's the K6's IF chain,
  not the sender.
