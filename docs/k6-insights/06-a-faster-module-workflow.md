# 6. A faster module workflow

- From: the CW ID thread (every module cost a 43 s upload, and modules started while the
  upload's repeat copies were still on the air, which caused false decodes).
- Keep modules in SPI flash and start one with a 2 s "run module N" packet.
- Make the loader wait for the carrier to drop before km_run.
- Optionally have the K6 acknowledge chunks. That needs the K6 to transmit, which is
  Jeff's call.
