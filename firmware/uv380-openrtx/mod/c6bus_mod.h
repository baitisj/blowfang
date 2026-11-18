/* c6bus_mod.h: c/common/c6_core.c's bus (c6c_bus) for a RAM module, from the module's own
   HR_C6000 helpers: configuration registers through the image's c6000_read/c6000_write, the TX
   RAM and page 3 through c6spi.h, the slot clock through c6slot.h's latched EXTI lines, V_SPI
   through c6vspi.h. The image's own bus (OpenRTX's HR_C6000 driver and chip-select lock, real
   interrupts) replaces this once the core is in flash (docs/uv380-multimode.md).

   Include ortx_mod.h, c6spi.h, c6slot.h, c6vspi.h (or define C6BUS_NO_VSPI) and c6_core.h first.
   Header only, no statics (RAM modules can't have writable ones).
   SPDX-License-Identifier: MIT */
#ifndef C6BUS_MOD_H
#define C6BUS_MOD_H

typedef struct {
    const ortx_api_t *api;
    c6s_state *ss;
} c6bm_ctx;

#define C6BM_FN static __attribute__((unused))

C6BM_FN void c6bm_wr(void *c, uint8_t r, uint8_t v) {
    ((c6bm_ctx *)c)->api->c6000_write(r, v);
}

C6BM_FN uint8_t c6bm_rd(void *c, uint8_t r) {
    return ((c6bm_ctx *)c)->api->c6000_read(r);
}

C6BM_FN int c6bm_ram_wr(void *c, unsigned space, unsigned addr, const uint8_t *d, unsigned n) {
    return c6_write(((c6bm_ctx *)c)->api, space, addr, d, n);
}

C6BM_FN int c6bm_ram_rd(void *c, unsigned space, unsigned addr, uint8_t *d, unsigned n) {
    return c6_read(((c6bm_ctx *)c)->api, space, addr, d, n);
}

#ifndef C6BUS_NO_VSPI
C6BM_FN int c6bm_vspi(void *c, const uint8_t *d, unsigned n) {
    (void)c;
    (void)n;
    vs_write(d);
    return 0;
}
#endif

/* c6slot.h's bits are c6_core's: C6S_SLOT 1, C6S_SYS 2, C6S_RFTX 4 */
C6BM_FN unsigned c6bm_edges(void *c) {
    return c6s_poll(((c6bm_ctx *)c)->ss);
}

C6BM_FN uint32_t c6bm_ms(void *c) {
    return ((c6bm_ctx *)c)->api->ms();
}

C6BM_FN void c6bm_sleep(void *c, unsigned ms) {
    ((c6bm_ctx *)c)->api->sleep_ms(ms);
}

C6BM_FN int c6bm_stop(void *c) {
    return ((c6bm_ctx *)c)->api->stop();
}

/* fill b for this module; vspi: use V_SPI for DMR voice payloads */
C6BM_FN void c6bm_bus(c6c_bus *b, c6bm_ctx *ctx, int vspi) {
    b->ctx = ctx;
    b->wr = c6bm_wr;
    b->rd = c6bm_rd;
    b->ram_wr = c6bm_ram_wr;
    b->ram_rd = c6bm_ram_rd;
#ifndef C6BUS_NO_VSPI
    b->vspi = vspi ? c6bm_vspi : 0;
#else
    (void)vspi;
    b->vspi = 0;
#endif
    b->edges = c6bm_edges;
    b->ms = c6bm_ms;
    b->sleep_ms = c6bm_sleep;
    b->stop = c6bm_stop;
}

#endif
