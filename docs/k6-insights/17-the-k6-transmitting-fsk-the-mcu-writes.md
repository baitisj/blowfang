# 17. The K6 transmitting FSK the MCU writes

- From: the "Transmit our own FSK from the K6" thread, 2026-11-06 ([k6-own-fsk.md](../k6-own-fsk.md)).
- Tested on air (xtalstep, PR #71): the crystal value REG_3B and the frequency word REG_38 are
  latched. Neither moves the synthesizer until RXON/TXON, so there is no per-symbol modulator
  there. After RXON, REG_3B steps exactly 84.8 Hz at 440.850 MHz.
- Untested: the tone oscillator (REG_70/71) as continuous-phase audio FSK, with the frequency
  word changed per symbol. It would give 4-tone audio FSK, matching the receive thread's 4FSK
  tones (900/1500/2100/2700 Hz).
- Buys: K6-to-PC and K6-to-K6 links in modes the chip's FSK modem lacks. The module TX API
  (level 3, PR #71) is the prerequisite; it isn't on the radio yet.
- Settled by: a module that switches REG_71 per symbol while keyed, recorded on the IC-7100.
