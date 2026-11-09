# 20. The tone oscillator is a module's transmitter (no firmware change)

- From: the FT8/WSPR thread, bench run 6 (2026-11-06).
- Tested on air: with `tx_on`'s REG_30 = 0xC1FA (no AF DAC bit, unlike Aura's 0xC3FA), a
  module's REG_70 = 0x8000 | gain << 8 and REG_71 = f x 10.32444 go out as FM audio. ft8tx
  stepped REG_71 every 5 ms for 12.64 s and the IC-7100 decoded FT8 at snr -14; the MCW ID
  (REG_70 keyed) came through clean. Tone gain 60 gave about -28 dBFS of tone on the
  IC-7100's USB audio.
- `tx_on` blocks about 1.4 s (Aura's `enter_tx`); time a transmission from its return.
- Buys: any audio-FSK mode (AFSK, 4FSK, MFSK, slow-scan) from a level 3 module, at
  0.097 Hz resolution and 5 ms steps or finer. Insight 17's FSK question is answered for
  audio FSK.
- Settled by: run 6; a gain sweep for deviation is still to do.
