/* UV-380 app, first milestone: send a sotto message as K6 voice-pager packets in FFSK
 * (1200/1800 Hz, 1800 baud, XL), the format the K6's pager receives (modem/pager.py), then
 * the station ident in Morse (AG7EW, 800 Hz, 20 wpm) on the same carrier, as modem.k6 tx does.
 *
 * QEMU build: the message comes from msg.sot on the host and the audio goes to audio.raw, so
 * tests/test_uv380_emu.py can check that the K6's receiver model decodes it. If msg.raw (8 kHz
 * 16-bit mono PCM) is there instead, the radio encodes it itself with the fixed-point sotto 1200
 * encoder first (tests/test_uv380_codec.py checks the bits against the x86 build).
 * Radio build: the message is the built-in test pattern, and nothing is sent: rf.h has no
 * driver yet, so rf_tx_begin fails and the app only blinks the red LED.
 *
 * SPDX-License-Identifier: MIT */
#include <stdint.h>

#include "../../../c/common/cw_tx.h"
#include "../../../c/common/ffsk_tx.h"
#include "../../../c/common/pager_tx.h"
#include "../../../sotto/c/sotto.h"
#include "board.h"
#include "rf.h"
#include "sotto_arena.h"
#include "uv_api.h"

#define FS 48000
#define BAUD 1800
#define TX_HZ 440850000u                /* 70 cm, the project's test frequency */
#define CALLSIGN "AG7EW"                /* every test transmission carries the ident */

static uint8_t msg[6 * 1500];           /* up to 60 s of sotto 1200 */
static int16_t buf[480];
static pt_air air;
static ffsk_tx mod;
static cw_tx cw;

static char *put_int(char *p, int v) {
    char d[12];
    int n = 0;
    unsigned u = v < 0 ? 0u - (unsigned)v : (unsigned)v;
    if (v < 0)
        *p++ = '-';
    do
        d[n++] = (char)('0' + u % 10);
    while (u /= 10);
    while (n)
        *p++ = d[--n];
    return p;
}

/* QEMU: a signed module in mod.bin on the host runs instead of the pager (tests/test_uv380_mod.py).
   The radio has no way to take one yet (host_read_file fails there). */
static void run_host_module(void) {
    int n = host_read_file("mod.bin", uvm_arena(), UVM_ARENA);
    if (n <= 0)
        return;
    int refused, ret = uvm_load_run((unsigned)n, &refused);
    char line[48], *p = line;
    for (const char *s = "uv380: mod r="; *s; s++)
        *p++ = *s;
    p = put_int(p, refused ? refused : 3);
    for (const char *s = " ret="; *s; s++)
        *p++ = *s;
    p = put_int(p, ret);
    *p++ = '\n';
    *p = 0;
    host_log(line);
    host_exit(0);
}

static void log_num(const char *what, int v) {
    char line[64], *p = line;
    for (const char *s = "uv380: "; *s; s++)
        *p++ = *s;
    for (const char *s = what; *s; s++)
        *p++ = *s;
    *p++ = '=';
    p = put_int(p, v);
    *p++ = '\n';
    *p = 0;
    host_log(line);
}

/* QEMU: msg.raw (8 kHz PCM) through the fixed-point sotto 1200 encoder into m; frames or -1 */
static int encode_host_pcm(uint8_t *m, unsigned max) {
    static int16_t pcm[SOTTO_LR_SAMPLES_PER_FRAME];
    int got = host_read_at("msg.raw", (uint8_t *)pcm, 0, sizeof pcm);
    if (got < 0)
        return -1;
    struct sotto_lr_enc *e = sotto_lr_enc_create();
    if (!e) {
        host_log("uv380: sotto_lr_enc_create failed\n");
        host_exit(1);
    }
    unsigned n = 0, off = 0;
    for (;;) {
        got = host_read_at("msg.raw", (uint8_t *)pcm, off, sizeof pcm);
        if (got < (int)sizeof pcm)
            break;                      /* a part frame at the end is dropped, as sotto_enc_fx does */
        off += sizeof pcm;
        if (n < max && sotto_lr_encode(e, pcm, m + 6 * n))
            n++;
    }
    while (n < max && sotto_lr_enc_flush(e, m + 6 * n))
        n++;
    sotto_arena_stats st;
    sotto_arena_get_stats(&st);
    log_num("enc frames", (int)n);
    log_num("enc ccm", (int)st.ccm_used);
    log_num("enc sram", (int)st.sram_used);
    sotto_lr_enc_destroy(e);
    sotto_arena_get_stats(&st);
    log_num("arena live", (int)st.live);
    return (int)n;
}

static unsigned test_pattern(uint8_t *m) {
    for (unsigned i = 0; i < 6 * 100; i++)
        m[i] = (uint8_t)(i * 37u + 11u);
    return 100;
}

int main(void) {
    board_init();
    run_host_module();
    int n = host_read_file("msg.sot", msg, sizeof msg);
    unsigned total;
    if (n > 0)
        total = (unsigned)n / 6;
    else if ((n = encode_host_pcm(msg, sizeof msg / 6)) > 0)
        total = (unsigned)n;
    else
        total = test_pattern(msg);
    static const pt_profile pr = PT_PROFILE_XL(PT_MAGIC_1200);
    pt_message m = {&pr, msg, total, 1, pt_msg_id(msg, total)};
#ifndef BOARD_QEMU
    if (rf_tx_begin(TX_HZ) != 0) {
        for (;;) {                      /* no RF driver yet: blink red, send nothing */
            board_led(0, (board_ms() / 250) & 1);
        }
    }
#endif
    pt_air_defaults(&air, pr.payload, BAUD, pt_message_source, &m);
    ffsk_init(&mod, &air, FS, BAUD, 0, 0, 16000);
    audio_start(FS);
    unsigned got;
    while ((got = ffsk_render(&mod, buf, sizeof buf / sizeof buf[0])) != 0)
        audio_write(buf, got);
    cw_init(&cw, CALLSIGN, FS, 20, 800, 13107);
    while ((got = cw_render(&cw, buf, sizeof buf / sizeof buf[0])) != 0)
        audio_write(buf, got);
    audio_stop();
    rf_tx_end();
    host_log("uv380: sent\n");
    host_exit(0);
    return 0;
}
