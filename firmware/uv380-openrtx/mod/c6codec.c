/* c6codec: the HR_C6000 codec registers' bits, one at a time, with I2S3 looked at after each.
   The first sweep (c6sweep, 11-13 14:05Z) only rescaled the idle waveform, and its last two
   registers (B9, BA) put the chip to sleep, so this one sweeps E0-E5, the codec block (OpenRTX:
   E0 routing and I2S mode, E2 DAC and mic preamp, E3 ADC-DAC passthrough, E4 gains), and after
   every flip
     - writes the register back and reads it back, and checks that register 00 still reads its
       start value: if not, it says so and ends (the chip stopped answering; power-cycle);
     - captures SPI3 slave S16 at 8 kHz (the probe's "cap" line: rms, strongest band, its share)
       and dumps the first 32 samples ("raw a 32"), so hamlet can tell noise (flat spectrum, small
       peak share) from the rescaled idle wave.
   Returns the flips done, negative if it ended early.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

static const uint8_t REGS[] = {
    0xE0,
    0xE1,
    0xE2,
    0xE3,
    0xE4,
    0xE5,
};

#define CAP "cap SPI3 s S16 8000"

static void look(const ortx_api_t *api) {
    api->sleep_ms(20);
    api->probe(CAP);
    api->probe("raw a 32");
}

int uv_main(const ortx_api_t *api) {
    char line[48];
    int done = 0;
    uint8_t id = api->c6000_read(0x00);
    api->fmt(line, sizeof line, "c6codec reg00=%02x", id);
    api->say(line);
    if (id == 0x00) {
        api->say("the C6000 doesn't answer: power-cycle first");
        return -1000;
    }
    api->say("baseline");
    look(api);
    for (unsigned r = 0; r < sizeof REGS; r++) {
        uint8_t was = api->c6000_read(REGS[r]);
        for (unsigned b = 0; b < 8; b++) {
            if (api->stop())
                return -done;
            uint8_t v = (uint8_t)(was ^ (1u << b));
            api->fmt(line, sizeof line, "sweep %02x %02x->%02x", REGS[r], was, v);
            api->say(line);
            api->c6000_write(REGS[r], v);
            look(api);
            api->c6000_write(REGS[r], was);
            api->sleep_ms(5);
            uint8_t back = api->c6000_read(REGS[r]), alive = api->c6000_read(0x00);
            done++;
            if (back != was || alive != id) {
                api->fmt(line, sizeof line, "STOP %02x reads %02x (want %02x), reg00 %02x", REGS[r],
                         back, was, alive);
                api->say(line);
                return -done;
            }
        }
    }
    api->say("baseline");
    look(api);
    return done;
}
