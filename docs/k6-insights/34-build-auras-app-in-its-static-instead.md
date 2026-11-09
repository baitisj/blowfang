# 34. Build Aura's `App` in its static instead of on the boot stack

- From: the hiss band thread, 2026-11-07 ([k6-hiss-port.md](../k6-hiss-port.md)). Not built.
- `place_app` (firmware/k6-aura, main.rs) builds `App::new`'s 3 KB result in its own stack
  frame and copies it into `APP_SLOT`: 3448 B of stack at boot, on top of main's 2952, that
  nothing else ever uses. Writing the result through the slot's raw pointer changed nothing
  (LLVM kept the temporary), so it would take `App::new` writing its fields into a
  `MaybeUninit<App>` one by one. The hiss band image first got its RAM by cutting its
  message store to 2.5 KB; then the 2720 B above Aura's old region turned out to be real
  SRAM (`ramprobe`, 2026-11-07) and `memory.x` grew, so this is less pressing.
- It would buy: about 3.4 KB of boot-time stack in every image, so the stores could grow
  back and a 16 kHz ring of four segments would fit.
- Settled by: `tools/k6_frames.py` showing `place_app` under 500 B and the image booting.
