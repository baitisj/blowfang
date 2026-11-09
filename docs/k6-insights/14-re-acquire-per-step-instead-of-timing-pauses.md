# 14. Re-acquire per step instead of timing pauses

- From: `iflive`. It starts the receiver over on a fresh lead every 6 s step, so it can
  print and change gain between steps without the tick + TIM17 lap reckoning `gp0cap`-style
  modules need. It saved about 200 B of code, and each run checks acquisition 10 times.
- Buys: simpler bench modules wherever the sender can repeat a preamble.
- Settled by: the first `iflive` bench run (every step should lock, `j=0`, with `b` near the step's full length).
