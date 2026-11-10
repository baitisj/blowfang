/* The UV-380's API table for RAM modules: the only way a module reaches the radio.
 *
 * A module is position-independent Thumb-2 for the Cortex-M4F (clang -fropi, hard float), built
 * and signed by `python -m modem.uv380mod` with the same key file as K6 modules
 * (~/.config/sotto/k6-mod.key). Its header magic is "U3M" (c/common/mod_core.h), so a K6
 * module never runs against this table, nor a UV-380 module on a K6. It runs once, on the main
 * stack (8 KB), as
 *
 *     int uv_main(const uv380_api_t *api);
 *
 * The M4 has a divide instruction and an FPU (single precision, on from reset_handler), so
 * modules may divide and use float, unlike K6 modules. No libraries, no writable statics.
 *
 * The table is append-only within ABI major 1: a new call goes at the end with a new level.
 * Nothing in it can transmit: the audio calls drive the MCU -> RF path (TIM3 PWM into the
 * HR_C6000 line in), which reaches the air only while the core keys the radio, and the core has
 * no RF driver yet (rf.h).
 *
 * SPDX-License-Identifier: MIT */
#ifndef UV380_UV_API_H
#define UV380_UV_API_H
#include <stdint.h>

#define UVM_MAGIC "U3M"
#define UVM_LEVEL 1
#define UVM_ARENA 16384                 /* module image bytes at most (SRAM; CCM can't run code) */

typedef struct uv380_api {
    uint16_t abi, level;                /* MC_ABI, UVM_LEVEL */
    void (*say)(const char *s);         /* a report line, "UV380 MOD say <s>" (up to 60 chars) */
    uint32_t (*ms)(void);               /* ms since the module started */
    void (*delay_us)(uint32_t us);      /* busy wait */
    void (*led)(int green, int red);    /* the top LED; the core turns it off afterwards */
    int (*ptt)(void);                   /* 1 while the PTT key is held: a long module's way out */
    void *(*scratch)(uint32_t *bytes);  /* RAM free while the module runs (the rest of the module
                                           arena, 8-byte aligned), its size in *bytes */
    void (*audio_start)(uint32_t fs);   /* board.h's audio calls: samples to the MCU -> RF path */
    void (*audio_write)(const int16_t *s, unsigned n);
    void (*audio_stop)(void);
} uv380_api_t;

/* the core's side */
uint8_t *uvm_arena(void);               /* UVM_ARENA bytes, 8-byte aligned, in SRAM */
/* check the image in the arena (n bytes) and run it: the module's return value, or a
   MC_* refusal through *refused (0 when it ran). */
int uvm_load_run(unsigned n, int *refused);

#endif
