/* usbenc: sotto 1200 encoded on the UV-380 from audio sent over USB serial, so the radio's
   encoder (level 2: enc_begin, enc_push, enc_end, the fixed-point sotto 1200 at 8 kHz in the
   image) can be checked against sotto_enc -1200 on a PC before the mic is found. The audio comes
   as rrinject's lines ("a SEQ hhhh ...", 16-bit two's complement at 8 kHz; uv380_i2sprobe.py
   inject --module this sends them), ending with "q", Ctrl-C or 3 s with nothing. The recording
   becomes the message PTT sends and # plays.
   Lines:
     enc start
     enc end samples=N frames=N ms=N           frames: superframes (40 ms, 6 bytes each)
     m IDX hhhhhhhhhhhh ...                    the message, 12 superframes a line (say lines stop at 200 chars) from IDX
   With UE_SEND, it then asks for the message to be sent (tx_request: 440.850 MHz only).
   Returns the superframes, or -1 (no memory for the encoder, or it failed).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define UV_MODULE_LEVEL 2

static int hexv(int c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
           : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

int uv_main(const ortx_api_t *api) {
    char line[24 + 12 * 13];
    if (api->enc_begin() < 0) {
        api->say("enc start failed: no memory for the encoder");
        return -1;
    }
    api->say("enc start");
    uint8_t b[64];
    int16_t x[64];
    unsigned nx = 0, nd = 0, acc = 0, word = 0, quit = 0, full = 0;
    uint32_t samples = 0, idle = api->ms(), t0 = api->ms();
    while (!quit) {
        unsigned n = api->usb_read(b, sizeof b);
        if (n == 0) {
            if (api->ms() - idle > 3000u)
                break;
            api->sleep_ms(1);
            continue;
        }
        idle = api->ms();
        for (unsigned i = 0; i < n && !quit; i++) {
            int c = b[i], v = hexv(c);
            if (c == 'q' || c == 3) {
                quit = 1;
                break;
            }
            if (v >= 0) {
                acc = (acc << 4 | (unsigned)v) & 0xFFFFu;
                nd++;
                continue;
            }
            if (nd) {                    /* a word ended: a sample unless it's "a" or SEQ */
                if (word++ > 1 && nd == 4u) {
                    x[nx++] = (int16_t)(uint16_t)acc;
                    samples++;
                }
                nd = 0, acc = 0;
            }
            if (c == '\n' || c == '\r')
                word = 0;
            if (nx == 64u) {
                if (!full && api->enc_push(x, nx) < 0) {
                    full = 1;
                    api->say("enc full: the rest is dropped");
                }
                nx = 0;
            }
        }
    }
    if (nx && !full)
        api->enc_push(x, nx);
    int frames = api->enc_end();
    api->fmt(line, sizeof line, "enc end samples=%lu frames=%d ms=%lu", (unsigned long)samples,
             frames, (unsigned long)(api->ms() - t0));
    api->say(line);

    const uint8_t *m;
    unsigned nf = api->msg(&m);
    for (unsigned f = 0; m && f < nf; f += 12u) {
        unsigned k = (unsigned)api->fmt(line, sizeof line, "m %u", f);
        for (unsigned j = f; j < nf && j < f + 12u; j++) {
            line[k++] = ' ';
            for (unsigned q = 0; q < 6u; q++) {
                line[k++] = hexd(m[6u * j + q] >> 4);
                line[k++] = hexd(m[6u * j + q] & 15u);
            }
        }
        line[k] = 0;
        api->say(line);
    }
#ifdef UE_SEND
    if (frames > 0)
        api->say(api->tx_request() == 0 ? "send requested" : "send refused (not 440.850 MHz?)");
#endif
    return frames;
}
