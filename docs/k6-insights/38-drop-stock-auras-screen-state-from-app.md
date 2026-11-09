# 38. Drop stock Aura's screen state from `App` in the pager images

- From: the memory plan thread, 2026-11-08. Half built: `MAX_SATELLITES` 1 and the spectrum's
  `MAX_BINS` 1 under `pager-only` took `App` from 3,028 to 2,164 B, and `place_app`'s boot
  frame from 3,448 to 2,296 B; the 864 B went into the shared block (insight 36).
- Left: SSTV 204, settings 156, the satellite screen 152, the channel manager 132 B, which
  would take `cfg` on code the pager images never reach rather than one constant each.
- It would buy: about 0.6 KB more for the module block.
- Settled by: `tools/k6_frames.py --sizes` showing `App` under 1.6 KB and the image booting.
