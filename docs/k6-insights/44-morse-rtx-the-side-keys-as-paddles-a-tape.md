# 44. Morse RTX: the side keys as paddles, a tape of what is heard

- From: the Morse RTX thread, 2026-11-08 (Jeff's idea). Built and emulator-tested
  (`c/k6/mod/morse.c`, docs/k6-morse.md); not on air. Needs insight 36's block (5.4 KB).
- The side keys sit on keypad columns with no row driven, so a module reads both straight
  from the GPIO input registers (PC14, PB14) in a few cycles, squeezes included, where
  Aura's scan sees one key at a time. Any module could use them as two fast buttons.
- It would buy: CW from the K6 itself (iambic or straight, MCW on the tone oscillator, auto
  ident), a receive tape for MCW, practice with a sidetone off the air.
- Settled by: the bench steps in docs/k6-morse.md (IC-7100 MCW in, the over recorded and
  decoded back, which orange key is Side 1, the sidetone's level).
