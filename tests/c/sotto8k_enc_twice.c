/* tests/test_uv380_sotto8k.py: sotto 1200 encoded twice (an encoder made and destroyed twice)
   with the UV-380's encoder wrappers (firmware/uv380-openrtx/sotto8k: calloc and free from a
   static arena). argv: 8 kHz s16 speech, then the two .bit files to write.
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <string.h>
#include "sotto/sotto.h"
/* encode the file twice with the arena build (create/destroy twice), print bytes */
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb"); static short x[80000]; size_t n = fread(x, 2, 64000, f); fclose(f);
    for (int round = 0; round < 2; round++) {
        struct sotto_lr_enc *e = sotto_lr_enc_create();
        if (!e) { printf("create failed\n"); return 1; }
        unsigned char out[6]; unsigned frames = 0; FILE *o = fopen(argv[2 + round], "wb");
        for (size_t i = 0; i + 320 <= n; i += 320) if (sotto_lr_encode(e, x + i, out)) { fwrite(out, 1, 6, o); frames++; }
        while (sotto_lr_enc_flush(e, out)) { fwrite(out, 1, 6, o); frames++; }
        fclose(o); sotto_lr_enc_destroy(e); printf("round %d frames %u\n", round, frames);
    }
    return 0;
}
