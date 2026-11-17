/* c6key.h: keying a DMR transmission from a RAM module, as c6dmrtx.c does it (it keeps its own copy,
   proven on air 11-16 and 11-17): api->tx_tone keys FM (at API level 4 the image sends the Morse
   ident first, unkeys for 100 ms, then keys the tone, under its transmit guard), and the module
   waits for the tone's own key-up on 0x60 bit 7 (the C6000's analog TX) before it switches the
   chip to DMR. Lines said while keyed are kept and said after the key-down: at 1 W the RF stops
   the radio's USB while keyed (11-16), and a say then blocks.

   Header only, no statics (RAM modules can't have writable ones).
   SPDX-License-Identifier: MIT */
#ifndef C6KEY_H
#define C6KEY_H

#include "ortx_mod.h"

#define C6K_FN static __attribute__((unused))
#define C6K_NOTES 6u
#define C6K_NOTE 120u
#define C6K_SETTLE_MS 80u       /* after 0x60 shows the analog TX: AT1846S TX + PA (OpenRTX 50 ms) */
#define C6K_IDENT_MS 12000u     /* the image's Morse ident before the tone, at most */

typedef struct {
    char s[C6K_NOTES][C6K_NOTE];
    unsigned n, quiet;
} c6k_notes;

C6K_FN void c6k_note(const ortx_api_t *api, c6k_notes *q, const char *s) {
    if (!q->quiet) {
        api->say(s);
        return;
    }
    if (q->n >= C6K_NOTES)
        return;
    unsigned i = 0;
    for (; s[i] && i < C6K_NOTE - 1u; i++)
        q->s[q->n][i] = s[i];
    q->s[q->n][i] = 0;
    q->n++;
}

/* after the key-down (0x60 bit 7 low for 300 ms, at most most_ms): the kept lines out */
C6K_FN void c6k_flush(const ortx_api_t *api, c6k_notes *q, uint32_t most_ms) {
    if (q->quiet) {
        uint32_t t0 = api->ms(), low = t0;
        while (api->ms() - t0 < most_ms) {
            if (api->c6000_read(0x60) & 0x80u)
                low = api->ms();
            else if (api->ms() - low >= 300u)
                break;
            api->sleep_ms(10);
        }
    }
    q->quiet = 0;
    for (unsigned i = 0; i < q->n; i++)
        api->say(q->s[i]);
    q->n = 0;
}

/* key ms of FM (the ident first) and wait for the tone's key-up plus the PA's settling: 0 and
   *t_key = when the tone keyed, or -1 (refused or a step never came; the lines are out) */
C6K_FN int c6k_key(const ortx_api_t *api, c6k_notes *q, uint32_t ms, uint32_t *t_key) {
    char line[80];
    if (api->level < 4u) {      /* the ident after the tone, no transmit guard */
        api->say("err image below level 4: no transmit guard, nothing sent");
        return -1;
    }
    if (api->tx_tone(ms, 1000)) {
        api->say("err tx_tone refused (not on 441.850 or 440.850 MHz, or TX off)");
        return -1;
    }
    q->quiet = 1;               /* keyed within ms: no say until the key-down */
    c6k_note(api, q, "tx_tone ok");
    for (unsigned k = 0; k < 3u; k++) {
        /* no pointer tables: the module is position-independent */
        const char *what = k == 0u ? "ident keyed" : k == 1u ? "ident done" : "key ok";
        uint8_t want = k == 1u ? 0x00u : 0x80u;
        uint32_t most = k == 1u ? C6K_IDENT_MS : 1000u, t0 = api->ms();
        while ((api->c6000_read(0x60) & 0x80u) != want) {
            if (api->ms() - t0 > most || api->stop()) {
                api->fmt(line, sizeof line, "err waiting for %s: no DMR sent", what);
                c6k_note(api, q, line);
                c6k_flush(api, q, C6K_IDENT_MS + ms + 1000u);
                return -1;
            }
            api->sleep_ms(1);
        }
        if (k == 2u) {
            *t_key = api->ms();
            api->sleep_ms(C6K_SETTLE_MS);
        }
        api->fmt(line, sizeof line, "%s after %lu ms", what, (unsigned long)(api->ms() - t0));
        c6k_note(api, q, line);
    }
    return 0;
}

#endif
