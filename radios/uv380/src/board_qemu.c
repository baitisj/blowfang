/* UV-380 board layer under QEMU (machine netduinoplus2, an STM32F405 with no radio around it).
 *
 * Same API as board_radio.c, so the apps and the portable code run unchanged; what would go to
 * the hardware goes to the host instead through Arm semihosting (qemu -semihosting-config
 * enable=on,target=native): audio_write appends the samples to audio.raw (signed 16-bit
 * little-endian) in QEMU's working directory, host_read_file reads a host file, host_exit ends
 * QEMU with an exit code. QEMU leaves RCC and GPIO unmodelled, so there is no clock set-up here.
 *
 * SPDX-License-Identifier: MIT */
#include "board.h"

enum { SYS_OPEN = 1, SYS_CLOSE = 2, SYS_WRITE0 = 4, SYS_WRITE = 5, SYS_READ = 6, SYS_SEEK = 10, SYS_FLEN = 12,
       SYS_EXIT_EXTENDED = 0x20 };

static int semihost(int op, void *arg) {
    register int r0 __asm__("r0") = op;
    register void *r1 __asm__("r1") = arg;
    __asm__ volatile("bkpt 0xAB" : "+r"(r0) : "r"(r1) : "memory");
    return r0;
}

static unsigned slen(const char *s) {
    unsigned n = 0;
    while (s[n])
        n++;
    return n;
}

static int audio_fd = -1;
static uint32_t ticks;

void board_init(void) {}

uint32_t board_ms(void) {
    return ticks++;
}

void board_led(int green, int red) {
    (void)green;
    (void)red;
}

int board_ptt(void) {
    return 1;
}

void audio_start(uint32_t fs) {
    (void)fs;
    uint32_t a[3] = {(uint32_t)"audio.raw", 5 /* "wb" */, 9};
    audio_fd = semihost(SYS_OPEN, a);
}

void audio_write(const int16_t *s, unsigned n) {
    if (audio_fd < 0)
        return;
    uint32_t a[3] = {(uint32_t)audio_fd, (uint32_t)s, 2u * n};
    semihost(SYS_WRITE, a);
}

void audio_stop(void) {
    if (audio_fd >= 0) {
        uint32_t a[1] = {(uint32_t)audio_fd};
        semihost(SYS_CLOSE, a);
        audio_fd = -1;
    }
}

int host_read_file(const char *path, uint8_t *buf, unsigned max) {
    uint32_t o[3] = {(uint32_t)path, 1 /* "rb" */, slen(path)};
    int fd = semihost(SYS_OPEN, o);
    if (fd < 0)
        return -1;
    uint32_t l[1] = {(uint32_t)fd};
    int len = semihost(SYS_FLEN, l);
    if (len < 0 || (unsigned)len > max)
        len = (int)max;
    uint32_t r[3] = {(uint32_t)fd, (uint32_t)buf, (uint32_t)len};
    int left = semihost(SYS_READ, r);
    semihost(SYS_CLOSE, l);
    return len - left;
}

int host_read_at(const char *path, uint8_t *buf, unsigned off, unsigned n) {
    uint32_t o[3] = {(uint32_t)path, 1 /* "rb" */, slen(path)};
    int fd = semihost(SYS_OPEN, o);
    if (fd < 0)
        return -1;
    uint32_t l[1] = {(uint32_t)fd};
    int len = semihost(SYS_FLEN, l);
    int got = 0;
    if (len > 0 && off < (unsigned)len) {
        if ((unsigned)len - off < n)
            n = (unsigned)len - off;
        uint32_t s[2] = {(uint32_t)fd, off};
        if (semihost(SYS_SEEK, s) == 0) {
            uint32_t r[3] = {(uint32_t)fd, (uint32_t)buf, n};
            got = (int)n - semihost(SYS_READ, r);
        }
    }
    semihost(SYS_CLOSE, l);
    return got;
}

void host_log(const char *s) {
    semihost(SYS_WRITE0, (void *)s);
}

void host_exit(int code) {
    uint32_t a[2] = {0x20026 /* ADP_Stopped_ApplicationExit */, (uint32_t)code};
    semihost(SYS_EXIT_EXTENDED, a);
    for (;;) {
    }
}
