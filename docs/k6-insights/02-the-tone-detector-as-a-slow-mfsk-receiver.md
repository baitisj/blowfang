# 2. The tone detector as a slow MFSK receiver

- From: the CW ID thread (on air it follows a keyed tone to about 10 ms and says which of
  16 tones it is).
- 16 tones with 30-40 ms symbols is roughly 100-200 bit/s. Plain audio tones should pass
  any FM repeater and hold on to weaker signals than FFSK.
- Buys: a fallback when packets don't get through: control and wake-up packets, short
  pages, beacons.
- Settled by: sweeping signal level and comparing where `cwlisten` stops decoding with
  where FFSK packets start failing.
