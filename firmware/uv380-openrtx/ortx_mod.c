/* RAM modules for the UV-380's OpenRTX image (ortx_mod.h): the "mod" commands, the arena and the
 * API table. Plain C: the tests run it on the host and on an emulated STM32F405.
 * SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mod_core.h"

/* The arena comes from the heap at the first "mod begin" and stays: images that never load a
   module pay nothing. On the radio the heap is in the 128 KB RAM (linker_script_MDx.ld), where code
   runs and DMA reaches (.bss is CCM there: neither). OM_ARENA, or half of it if the heap is short. */
static uint8_t *arena;
static unsigned arena_size;

static int get_arena(void) {
    if (!arena && (arena = malloc(OM_ARENA)) != NULL)
        arena_size = OM_ARENA;
    if (!arena && (arena = malloc(OM_ARENA / 2)) != NULL)
        arena_size = OM_ARENA / 2;
    return arena != NULL;
}

static const om_host *H;                /* while a command runs */
static unsigned want, have, image_bytes;
static uint32_t t0;
static uint8_t cancel;
static uint8_t rx[256];                 /* bytes stop() took from USB serial, for usb_read */
static unsigned rx_n;

static void outf(const char *f, ...) {
    char b[240];
    va_list ap;
    va_start(ap, f);
    vsnprintf(b, sizeof b, f, ap);
    va_end(ap);
    H->line(b);
}

static void api_say(const char *s) {
    outf("mod say %.200s", s);
}

static int api_fmt(char *buf, unsigned size, const char *f, ...) {
    va_list ap;
    va_start(ap, f);
    int n = vsnprintf(buf, size, f, ap);
    va_end(ap);
    return n;
}

static uint32_t api_ms(void) {
    return H->now_ms() - t0;
}

static void api_sleep_ms(uint32_t ms) {
    H->sleep_ms(ms);
}

/* Ctrl-C from USB serial ends the module; any other byte is kept for usb_read. Takes no more
   than rx has room for: the rest waits in USB serial's own buffer (a module streaming data in
   loses none to its stop() checks) */
static void take_rx(void) {
    uint8_t b[64];
    unsigned n, i, room;
    while ((room = (unsigned)sizeof rx - rx_n) != 0 &&
           (n = H->read(b, room < sizeof b ? room : sizeof b)) != 0)
        for (i = 0; i < n; i++) {
            if (b[i] == 3)
                cancel = 1;
            else
                rx[rx_n++] = b[i];
        }
}

static int api_stop(void) {
    if (!cancel)
        take_rx();
    return cancel || H->stop();
}

static void *api_scratch(uint32_t *bytes) {
    unsigned off = (image_bytes + 7u) & ~7u;
    *bytes = off < arena_size ? arena_size - off : 0;
    return arena + off;
}

static uint8_t api_c6000_read(uint8_t reg) {
    return H->c6000_read ? H->c6000_read(reg) : 0;
}

static void api_c6000_write(uint8_t reg, uint8_t val) {
    if (H->c6000_write)
        H->c6000_write(reg, val);
}

static unsigned api_usb_read(void *buf, unsigned n) {
    unsigned k = n < rx_n ? n : rx_n;
    memcpy(buf, rx, k);
    memmove(rx, rx + k, rx_n - k);
    rx_n -= k;
    if (k < n)
        k += H->read((uint8_t *)buf + k, n - k);
    return k;
}

static void api_usb_write(const void *buf, unsigned n) {
    H->write(buf, n);
}

static unsigned api_adc(int16_t *buf, unsigned n, unsigned fs) {
    return H->adc_capture ? H->adc_capture(buf, n, fs) : 0;
}

static int32_t api_rssi(void) {
    return H->rssi ? H->rssi() : -999;
}

static int api_probe(const char *line) {
    if (H->probe)
        return H->probe(line);
    outf("err probe %.40s: not here", line);
    return -1;
}

static void *api_memcpy(void *d, const void *s, unsigned n) {
    return memcpy(d, s, n);
}

static void *api_memset(void *d, int c, unsigned n) {
    return memset(d, c, n);
}

static int api_enc_begin(void) {
    return H->enc_begin ? H->enc_begin() : -1;
}

static int api_enc_push(const int16_t *x, unsigned n) {
    return H->enc_push ? H->enc_push(x, n) : -1;
}

static int api_enc_end(void) {
    return H->enc_end ? H->enc_end() : -1;
}

static unsigned api_msg(const uint8_t **bytes) {
    if (H->msg)
        return H->msg(bytes);
    *bytes = NULL;
    return 0;
}

static void api_preview(void) {
    if (H->preview)
        H->preview();
}

static int api_tx_request(void) {
    return H->tx_request ? H->tx_request() : -1;
}

static int api_tx_mic(unsigned ms) {
    return H->tx_mic ? H->tx_mic(ms) : -1;
}

static const ortx_api_t api = {
    .abi = MC_ABI,
    .level = OM_LEVEL,
    .say = api_say,
    .fmt = api_fmt,
    .ms = api_ms,
    .sleep_ms = api_sleep_ms,
    .stop = api_stop,
    .scratch = api_scratch,
    .c6000_read = api_c6000_read,
    .c6000_write = api_c6000_write,
    .usb_read = api_usb_read,
    .usb_write = api_usb_write,
    .adc_capture = api_adc,
    .rssi = api_rssi,
    .memcpy = api_memcpy,
    .memset = api_memset,
    .probe = api_probe,
    .enc_begin = api_enc_begin,
    .enc_push = api_enc_push,
    .enc_end = api_enc_end,
    .msg = api_msg,
    .preview = api_preview,
    .tx_request = api_tx_request,
    .tx_mic = api_tx_mic,
};

uint8_t *om_arena(void) {
    get_arena();
    return arena;
}

static int has_key(void) {
    /* a well-formed header with 2 bytes of code: MC_NO_KEY comes before the tag check fails */
    static const uint8_t probe[MC_HDR + 2 + MC_TAG] = {'U', '3', 'R', MC_ABI, 2};
    return mc_check(probe, sizeof probe, OM_MAGIC, OM_LEVEL) != MC_NO_KEY;
}

static const char *why(int r) {
    switch (r) {
    case MC_BAD_HDR:
        return "bad header (magic, ABI, level, flags, entry or length)";
    case MC_BAD_TAG:
        return "bad tag (not signed with this radio's key, or a byte changed)";
    case MC_NO_KEY:
        return "this image has no module key (python -m modem.k6 keyimage)";
    case MC_OLD:
        return "serial not newer than the last module run (a replay)";
    }
    return "?";
}

static int hexval(char c) {
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

static int cmd_hex(const char *p) {
    char *end;
    unsigned long off = strtoul(p, &end, 16);
    unsigned n = 0;
    if (end == p || *end != ' ') {
        outf("err mod hex: want OFF HEX");
        return -1;
    }
    if (off > have) {
        outf("err mod hex %lx: send the image in order (%u bytes so far)", off, have);
        return -1;
    }
    p = end + 1;
    while (p[0] && p[0] != ' ' && p[0] != '\r') {
        int a = hexval(p[0]), b = p[1] ? hexval(p[1]) : -1;
        if (a < 0 || b < 0 || n >= OM_HEX_MAX) {
            outf("err mod hex %lx: bad hex, or more than %u bytes", off, OM_HEX_MAX);
            return -1;
        }
        if (off + n >= want) {
            outf("err mod hex %lx: past the image's %u bytes", off, want);
            return -1;
        }
        arena[off + n++] = (uint8_t)(a << 4 | b);
        p += 2;
    }
    if (off + n > have)
        have = off + n;
    outf("ok mod hex %lx %u", off, n);
    return 0;
}

static int cmd_run(void) {
    if (want == 0 || have != want) {
        outf("err mod run: %u of %u bytes loaded", have, want);
        return -1;
    }
    int r = mc_check(arena, want, OM_MAGIC, OM_LEVEL);
    want = have = 0;                    /* one run an upload */
    if (r != MC_READY) {
        outf("err mod run refused r=%d: %s", r, why(r));
        return -1;
    }
    image_bytes = MC_HDR + (arena[4] | arena[5] << 8) + MC_TAG;
    cancel = 0;
    rx_n = 0;
    outf("mod run serial=%lu code=%u", (unsigned long)mc_serial(), arena[4] | arena[5] << 8);
    t0 = H->now_ms();
    int ret = mc_run(arena, &api);
    outf("ok mod run ret=%d ms=%lu%s", ret, (unsigned long)(H->now_ms() - t0),
         cancel ? " (Ctrl-C)" : "");
    return 0;
}

int om_command(const om_host *h, const char *p) {
    H = h;
    while (*p == ' ')
        p++;
    if (*p == 0 || strncmp(p, "info", 4) == 0) {
        get_arena();                    /* so info says whether the heap has room */
        outf("ok mod info magic=%s level=%u arena=%08lx size=%u loaded=%u/%u key=%s serial=%lu",
             OM_MAGIC, OM_LEVEL, (unsigned long)(uintptr_t)arena, arena_size, have, want,
             has_key() ? "yes" : "no", (unsigned long)mc_serial());
        return 0;
    }
    if (strncmp(p, "begin ", 6) == 0) {
        unsigned long n = strtoul(p + 6, NULL, 0);
        want = have = 0;
        if (!get_arena()) {
            outf("err mod begin: no heap for the arena");
            return -1;
        }
        if (n < MC_HDR + MC_TAG || n > arena_size) {
            outf("err mod begin %lu: want %u to %u bytes", n, MC_HDR + MC_TAG, arena_size);
            return -1;
        }
        want = (unsigned)n;
        memset(arena, 0, arena_size);
        outf("ok mod begin %u", want);
        return 0;
    }
    if (strncmp(p, "hex ", 4) == 0)
        return cmd_hex(p + 4);
    if (strcmp(p, "run") == 0)
        return cmd_run();
    outf("err mod %.40s: info, begin N, hex OFF HEX, run", p);
    return -1;
}
