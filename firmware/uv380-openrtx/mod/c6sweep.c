/* c6sweep: does any one HR_C6000 configuration bit put the FM receiver's audio on I2S3? For each
   candidate register (the FM-mode ones and the codec block A0-A8, B9, BA, from the 11-13 register
   dump), each of its 8 bits is flipped in turn, an I2S3 capture (SPI3 slave, S16, 8 kHz) taken
   through the probe's own "cap" command, and the register put back. With no signal and the
   squelch open, receiver audio is broadband noise far above the idle pattern's rms 17, so this
   needs no transmitter. A baseline capture comes first and last. Ctrl-C or PTT ends it (after
   putting the register back).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

static const uint8_t REGS[] = {
    0x06,
    0x26,
    0xA0,
    0xA1,
    0xA2,
    0xA3,
    0xA4,
    0xA5,
    0xA6,
    0xA7,
    0xA8,
    0xB9,
    0xBA,
};

#define CAP "cap SPI3 s S16 8000"

int uv_main(const ortx_api_t *api) {
    char line[48];
    int tried = 0;
    api->say("baseline");
    api->probe(CAP);
    for (unsigned r = 0; r < sizeof REGS; r++) {
        uint8_t was = api->c6000_read(REGS[r]);
        for (unsigned b = 0; b < 8; b++) {
            if (api->stop()) {
                api->c6000_write(REGS[r], was);
                return -tried;
            }
            uint8_t v = (uint8_t)(was ^ (1u << b));
            api->fmt(line, sizeof line, "sweep %02x %02x->%02x", REGS[r], was, v);
            api->say(line);
            api->fmt(line, sizeof line, "c6w %02x %02x", REGS[r], v);
            api->probe(line);
            api->sleep_ms(20);
            api->probe(CAP);
            api->c6000_write(REGS[r], was);
            tried++;
        }
    }
    api->say("baseline");
    api->probe(CAP);
    return tried;
}
