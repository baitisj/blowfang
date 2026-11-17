/* c6vcheck: is the HR_C6000's voice SPI port (V_SPI, the 27-byte voice burst payloads) on PB12-PB15
   of the UV-380, as on the DM-1701? No RF, no keying: the first step of DMR voice bursts
   (docs/uv380-c6000-modem.md, "Sotto in DMR voice bursts", step V0).

   Lines:
     "pins PB12-15 modes=XX"            2 bits a pin, PB12 lowest (00 input, 11 analog: free)
     "06=XX"                            the vocoder select register as the image has it
     "idle pu=HEX pd=HEX driven=N"      page 3 read (0x83) with MISO pulled up, then down: the
                                        same bytes both times = the chip drives MISO (1); all FF
                                        then all 00 = nothing there (0)
     "wK HEX", "rK pu=HEX pd=HEX       with 0x06 = 0x21 (voice over V_SPI under MCU control):
       same=N driven=N"                 two patterns written (0x03), each read back; same = bytes
                                        that read back as written (27: the read returns the
                                        encode buffer, 0: it returns something else, such as the
                                        decode buffer)
     "end"                              0x06 back as found, PB12-PB15 back as found
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6vspi.h"
#include "../../../c/common/dmr_voice.h"

static unsigned same(const uint8_t *a, const uint8_t *b) {
    unsigned n = 0;
    for (unsigned i = 0; i < VS_N; i++)
        n += a[i] == b[i];
    return n;
}

static void hex(char *out, const uint8_t *b) {
    const char *d = "0123456789abcdef";
    for (unsigned i = 0; i < VS_N; i++) {
        out[2 * i] = d[b[i] >> 4];
        out[2 * i + 1] = d[b[i] & 15u];
    }
    out[2 * VS_N] = 0;
}

static int driven(const uint8_t *pu, const uint8_t *pd) {
    return same(pu, pd) == VS_N;
}

/* page 3 read with MISO pulled up, then down */
static void read2(uint8_t *pu, uint8_t *pd) {
    vs_pull(1);
    vs_wait(200);
    vs_read(pu);
    vs_pull(2);
    vs_wait(200);
    vs_read(pd);
    vs_pull(0);
}

int uv_main(const ortx_api_t *api) {
    char line[200], h1[2 * VS_N + 1], h2[2 * VS_N + 1], h3[2 * VS_N + 1];
    uint8_t pu[VS_N], pd[VS_N], w[VS_N];
    vs_port port;

    api->fmt(line, sizeof line, "pins PB12-15 modes=%02x", vs_modes());
    api->say(line);
    if (vs_open(&port)) {
        api->say("err PB12-15: a pin is an output or alternate function (claimed), nothing done");
        return -1;
    }
    unsigned r06 = api->c6000_read(0x06);
    api->fmt(line, sizeof line, "06=%02x", r06);
    api->say(line);

    read2(pu, pd);
    hex(h1, pu);
    hex(h2, pd);
    api->fmt(line, sizeof line, "idle pu=%s pd=%s driven=%d", h1, h2, driven(pu, pd));
    api->say(line);

    api->c6000_write(0x06, 0x21);
    for (unsigned k = 1; k <= 2u; k++) {
        if (k == 1u) {
            for (unsigned i = 0; i < VS_N; i++)
                w[i] = (uint8_t)(0xA5u ^ (i * 9u));
        } else {
            dv_pn(1, w);
        }
        vs_write(w);
        read2(pu, pd);
        hex(h1, w);
        hex(h2, pu);
        hex(h3, pd);
        api->fmt(line, sizeof line, "w%u %s", k, h1);
        api->say(line);
        api->fmt(line, sizeof line, "r%u pu=%s pd=%s same=%u driven=%d", k, h2, h3, same(w, pu),
                 driven(pu, pd));
        api->say(line);
    }
    api->c6000_write(0x06, (uint8_t)r06);
    vs_close(&port);
    api->say("end");
    return 0;
}
