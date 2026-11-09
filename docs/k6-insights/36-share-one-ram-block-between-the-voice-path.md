# 36. Share one RAM block between the voice path and the modules

- From: the memory plan thread, 2026-11-08 (Claude Doc "K6 memory plan: RAM, modules and
  image switching"). Built (this section's "How much room"); not yet on air.
- The message store, the PWM ring and the decoder's pool are only busy while a message is
  received or played, and a module never runs then, so the three sit side by side in
  `hal::arena` and a module gets all 11,776 bytes instead of the 4 KB store (864 of them from shrinking two arrays in `App` that only stock Aura screens use: insight 38, half done; 2,016 from the screen buffer leaving main's frame: insight 37). The 8 kHz images
  pay about 2 KB of the stack they had spare, so every SPI image offers the same block as
  the hiss image.
- It buys: modules up to 11.5 KB of code and scratch, room for the whole `ft8rx` (7.2 KB of
  code and about 4 KB of RAM, about 11.2 KB) with about 0.5 KB over.
- Settled by: `K6 ARENA` at boot, a module over 4 KB running, and voice playback with a
  matching `pcm=` and a sane `stack_free=` on a v3.7 and a hiss image.
