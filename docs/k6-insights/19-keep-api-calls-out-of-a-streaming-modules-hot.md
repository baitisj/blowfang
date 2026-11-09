# 19. Keep API calls out of a streaming module's hot loop

- From: the FT8/WSPR thread, run 1 (2026-11-06).
- `api->key_down()` costs about 60 us on the radio: Aura's `any_pressed` scans six keypad
  rows with a 5 us SysTick wait each. Called on every drain pass, it overran ft8wf's 93 us
  ring about 6,450 times a second. `tests/k6_live_emu.py` charges 1 us for it; at 60 us the
  emulator reproduces the radio's overrun rate (6,140 a second).
- Buys: emulator runs that predict the radio, and a rule for module authors: check the key
  once per row, block or line, never per DMA poll.
- Settled by: run 2 of ft8wf (key checked once a row) showing `lost=0` on the radio; then
  cost key_down at 60 us in the emulator.
