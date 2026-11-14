/* The OpenRTX image's module loader (ortx_mod.c) on an emulated STM32F405 (QEMU netduinoplus2,
 * radios/uv380's start-up and semihosting board): every line of the host file in.txt goes to
 * om_command as a "mod ..." line from USB serial would, and its answers come back on QEMU's
 * output. usb.bin (if any) is what USB serial holds while a module runs (a 0x03 is Ctrl-C).
 * OpenRTX's side is faked: C6000 register r reads r ^ 0x5a, the RSSI is -77 dBm, stop() is 0.
 * tests/test_uv380_ortx_mod.py builds it (make -C radios/uv380 omqemu) and drives it.
 * SPDX-License-Identifier: MIT */
#include <string.h>

#include "../../../radios/uv380/src/board.h"
#include "ortx_mod.h"

static char in[64 * 1024];
static unsigned usb_at;
static uint32_t now;

static void q_line(const char *s) {
    host_log(s);
    host_log("\n");
}

static void q_write(const void *b, unsigned n) {
    char hex[3 * 32 + 16];
    unsigned i, k = 0;
    const uint8_t *p = b;
    memcpy(hex, "usb_write", 9), k = 9;
    for (i = 0; i < n && i < 32; i++) {
        hex[k++] = ' ';
        hex[k++] = "0123456789abcdef"[p[i] >> 4];
        hex[k++] = "0123456789abcdef"[p[i] & 15];
    }
    hex[k] = 0;
    q_line(hex);
}

/* usb.bin a piece at a time (it can be long: audio for a module to read) */
static unsigned q_read(void *b, unsigned n) {
    int got = host_read_at("usb.bin", b, usb_at, n);
    if (got <= 0)
        return 0;
    usb_at += (unsigned)got;
    return (unsigned)got;
}

static uint32_t q_now(void) {
    return now++;
}

static int q_stop(void) {
    return 0;
}

static void q_sleep(uint32_t ms) {
    now += ms;
}

static uint8_t q_c6r(uint8_t r) {
    return r ^ 0x5a;
}

static void q_c6w(uint8_t r, uint8_t v) {
    (void)r, (void)v;
}

static int32_t q_rssi(void) {
    return -77;
}

/* the heap: the module arena (ortx_mod.c takes it with malloc) and what vsnprintf wants */
void *_sbrk(int incr) {
    static uint8_t heap[24 * 1024] __attribute__((aligned(8)));
    static unsigned used;
    if (incr < 0 || used + (unsigned)incr > sizeof heap)
        return (void *)-1;
    used += (unsigned)incr;
    return heap + used - incr;
}

/* the probe's commands: echoed, "ok" unless the line says "bad" */
static int q_probe(const char *line) {
    host_log("probe ");
    q_line(line);
    if (strstr(line, "bad")) {
        q_line("err probe bad");
        return -1;
    }
    q_line("ok probe");
    return 0;
}

/* level 2 stand-ins: a "superframe" a 320 samples, its 6 bytes the first sample's and the
   count's; tx_request refuses (no radio here) */
static uint8_t q_msgbuf[6 * 64];
static unsigned q_frames, q_fill;
static int q_on;

static int q_enc_begin(void) {
    q_frames = q_fill = 0;
    q_on = 1;
    return 0;
}

static int q_enc_push(const int16_t *x, unsigned n) {
    if (!q_on)
        return -1;
    for (unsigned i = 0; i < n; i++) {
        if (q_fill == 0) {
            if (q_frames == 64u)
                return -1;
            uint8_t *f = q_msgbuf + 6u * q_frames;
            f[0] = (uint8_t)x[i], f[1] = (uint8_t)((uint16_t)x[i] >> 8), f[2] = (uint8_t)q_frames;
            f[3] = 0xA5, f[4] = 0x5A, f[5] = 0;
        }
        if (++q_fill == 320u)
            q_fill = 0, q_frames++;
    }
    return (int)q_frames;
}

static int q_enc_end(void) {
    if (!q_on)
        return -1;
    q_on = 0;
    if (q_fill)
        q_fill = 0, q_frames++;
    return (int)q_frames;
}

static unsigned q_msg(const uint8_t **b) {
    *b = q_msgbuf;
    return q_frames;
}

static void q_preview(void) {
    q_line("preview");
}

static int q_tx_request(void) {
    q_line("tx_request refused");
    return -1;
}

static const om_host host = {q_line, q_write, q_read, q_now, q_stop, q_sleep, q_c6r, q_c6w, NULL, q_rssi,
                             q_probe, q_enc_begin, q_enc_push, q_enc_end, q_msg, q_preview,
                             q_tx_request, NULL, NULL};

int main(void) {
    int n = host_read_file("in.txt", (uint8_t *)in, sizeof in - 1);
    if (n < 0)
        host_exit(1);
    in[n] = 0;
    for (char *p = in, *e; *p; p = e) {
        e = strchr(p, '\n');
        e = e ? (*e = 0, e + 1) : p + strlen(p);
        if (strncmp(p, "mod", 3) == 0)
            om_command(&host, p + 3);
    }
    q_line("om_qemu: done");
    host_exit(0);
    return 0;
}
