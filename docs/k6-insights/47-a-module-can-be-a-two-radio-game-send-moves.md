# 47. A module can be a two-radio game: send moves, not state

- From: Jeff, 2026-11-09 (the GROUNDWAVE thread). Built and emulator-tested
  (`c/k6/mod/gw.c`, docs/k6-gw.md); the bot calls went out and decoded on the bench.
- GROUNDWAVE's rules are integer-only C with their own random generator, built both into the
  module and for the PC, so a move (angle, power, item) is all that crosses the air. Each
  MOVE carries the sender's game hash, so a desync shows at once. The same trick would fit
  any turn game, or a shared map, sent as edits.
- The module sends with chip FSK from its own `tx_on` over (the pager's PHY F registers),
  not through the core's ARQ. Each over is the packet twice plus an MCW ID.
- It would buy: two-way data between K6s from a RAM module, with no new firmware.
- Half settled 2026-11-09: a K6's HELLO overs, recorded on the IC-7100 (receive only),
  decoded with `tools/k6_gw.py`, so chip FSK from a module's own `tx_on` works. Still to do: a
  game between two K6s (or a K6 and a PC peer).
