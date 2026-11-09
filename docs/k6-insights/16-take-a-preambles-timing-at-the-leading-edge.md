# 16. Take a preamble's timing at the leading edge of its fit, not at its best

- From: `iflive` in the emulator (2026-11-06). The 4FSK lead's fit against the clock offset
  is a plateau a third of a symbol wide. Its best fell anywhere on it, and on the late side
  the timing loop held a side peak (0.26 symbol late, 8% BER all step). Walking back from
  the best while the fit stays over half of it found the true timing in every run.
- Buys: false locks fell from 4-6 in 18 streams to none, for about 70 B of code. The same applies to any
  module that sweeps a clock against a known preamble (FT8's Costas arrays, CW's dits).
- Settled: yes, on the emulator; the first `iflive` bench run checks it on air.
