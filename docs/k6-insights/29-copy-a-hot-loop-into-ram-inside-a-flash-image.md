# 29. Copy a hot loop into RAM inside a flash image

- From: the coded 4FSK thread, 2026-11-07 ([k6-if-fec.md](../k6-if-fec.md)). Tested on the
  emulator only.
- The image runs from flash at two wait states, about 1.33x slower than a RAM module (23%
  idle against 60% in the same loop). ifpager puts its four hot functions in one section,
  `.text.ifrx_ram` (390 B), copies it onto the stack keeping each word's alignment, and calls
  the copies through pointers. Tables are reached only through a struct pointer, so the block
  has no relocations and works both in the image and under `-fropi`. A test checks for
  relocations and the section's order.
- It would buy: any image feature with a tight inner loop (a decoder, an FFT, a filter) gets
  module speed without a RAM module. With a section of the RAM region instead of the stack,
  it costs no stack at all.
- Settled by: PR #108's on-air `ram=` and `idle=` against the emulator's.
