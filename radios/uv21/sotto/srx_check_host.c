/* srx_check_host: [S12] the UV-21 receiver's packet check (radios/uv21/mod/sottorx_core.h) on this
   machine, for tests/test_uv21_sotto.py: whitened packets of LEN bytes on stdin, one line each on
   stdout, "ok MM" (good CRC as read, MM the de-whitened magic), "swap MM" (good with each word's
   bytes swapped) or "bad". SPDX-License-Identifier: MIT */
#include <stdio.h>
#include "../mod/sottorx_core.h"

int main(void) {
  uint8_t raw[SR_LEN], out[SR_LEN];
  while (fread(raw, 1, SR_LEN, stdin) == SR_LEN) {
    if (srx_check(raw, out, SR_LEN, 0)) printf("ok %02x\n", out[0]);
    else if (srx_check(raw, out, SR_LEN, 1)) printf("swap %02x\n", out[0]);
    else printf("bad\n");
  }
  return 0;
}
