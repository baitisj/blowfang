/* The UV-380's module API table and loader glue (uv_api.h, c/common/mod_core.h).
 * SPDX-License-Identifier: MIT */
#include "uv_api.h"

#include "../../../c/common/mod_core.h"
#include "board.h"

static uint8_t arena[UVM_ARENA] __attribute__((aligned(8)));
static unsigned image_bytes;
static uint32_t t0;

static void api_say(const char *s) {
    char line[16 + 60 + 2];
    unsigned n = 0;
    for (const char *p = "UV380 MOD say "; *p; p++)
        line[n++] = *p;
    for (unsigned i = 0; i < 60 && s[i]; i++)
        line[n++] = s[i];
    line[n++] = '\n';
    line[n] = 0;
    host_log(line);
}

static uint32_t api_ms(void) {
    return board_ms() - t0;
}

/* about 4 cycles a pass at 168 MHz; QEMU runs it as fast as it likes */
static void api_delay_us(uint32_t us) {
    for (volatile uint32_t i = us * 42u; i; i--) {
    }
}

static int api_ptt(void) {
    return board_ptt();
}

static void *api_scratch(uint32_t *bytes) {
    unsigned off = (image_bytes + 7u) & ~7u;
    *bytes = off < UVM_ARENA ? UVM_ARENA - off : 0;
    return arena + off;
}

static const uv380_api_t api = {
    .abi = MC_ABI,
    .level = UVM_LEVEL,
    .say = api_say,
    .ms = api_ms,
    .delay_us = api_delay_us,
    .led = board_led,
    .ptt = api_ptt,
    .scratch = api_scratch,
    .audio_start = audio_start,
    .audio_write = audio_write,
    .audio_stop = audio_stop,
};

uint8_t *uvm_arena(void) {
    return arena;
}

int uvm_load_run(unsigned n, int *refused) {
    int r = mc_check(arena, n, UVM_MAGIC, UVM_LEVEL);
    if (r != MC_READY) {
        *refused = r;
        return 0;
    }
    *refused = 0;
    image_bytes = MC_HDR + (arena[4] | arena[5] << 8) + MC_TAG;
    t0 = board_ms();
    int ret = mc_run(arena, &api);
    board_led(0, 0);
    return ret;
}
