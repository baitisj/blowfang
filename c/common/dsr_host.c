/* dsr_host: dsr_tx on the host, for tests/test_uv380_dsr.py, which checks every byte against
   modem/dsr.py. Superframes in on stdin, the transmission out on stdout, read 36 bytes (a slot)
   at a time as the chip takes it.

     dsr_host 40|120 CALL < SOTTO
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dsr_tx.h"

static uint8_t in[1u << 16];

int main(int argc, char **argv) {
    if (argc < 3)
        return 2;
    unsigned fmt = !strcmp(argv[1], "40") ? DSRT_40 : !strcmp(argv[1], "120") ? DSRT_120 : 9u;
    unsigned n = (unsigned)fread(in, 1, sizeof in, stdin) / DSRT_SF;
    dsrt_msg m;
    if (dsrt_msg_begin(&m, fmt, in, n, argv[2]))
        return 1;
    uint8_t slot[36];
    unsigned got, total = 0;
    while ((got = dsrt_msg_read(&m, slot, sizeof slot)) > 0) {
        fwrite(slot, 1, got, stdout);
        total += got;
    }
    return total == dsrt_msg_bytes(&m) ? 0 : 3;
}
