# 37. Keep the screen buffer only while the screen is drawn

- From: the memory plan thread, 2026-11-08 (Jeff: page the screen buffer out). Built in the
  images that play voice from SPI codebooks (cfg `shared_lcd`); not yet on air.
- Aura kept a whole 1 KB copy of the 128x64 screen (1,120 B with the driver's dirty marks)
  in main's stack frame for good, though the ST7565 holds its own copy. Two tricks instead
  of u8g2's eight passes of all the drawing code:
  - The main loop's screen (VOICE MSG) is drawn into a buffer made afresh in the PWM ring
    (`hal::arena::lcd`) and sent whole: the ring is idle whenever the screen is drawn (no
    redraw while a message plays or streams; `draw_pager_only` checks). The driver is
    rebuilt around the buffer for each frame, so nothing reads what a playback left there.
  - A RAM module has the ring (it has the whole block), so its screen (`lcd_flush`) goes out
    a 128-byte page at a time from the stack: `ui::pager::draw_mod_page` draws only the
    lines that reach each page, straight from the font's bitmap, no embedded-graphics code.
    Pixel for pixel the same as `draw_mod_screen` (checked on the host over 3,000 random
    screens of text, bars and waterfall), and 330 B less flash.
- Main's frame went from 2,952 to 928 B (the buffer was in it twice over, it seems, with
  the copy it was built in), so the block grew by 2,016 B to 11,776 with the deepest chain of
  direct calls (main through the coded 4FSK receiver, 4,588 B) keeping the 244 B to spare
  it had. `build.sh` now prints that chain's total (`running stack:`, from
  `tools/k6_frames.py --deep`).
- The EXPLORE image keeps the buffer in main: its sweep holds the ring while it draws.
- Settled by: VOICE MSG and a module's screen looking right on the radio, `K6 ARENA
  bytes=11776`, a voice playback with a matching `pcm=` and a sane `stack_free=` on a hiss
  image, and a module's screen updating while it runs.
