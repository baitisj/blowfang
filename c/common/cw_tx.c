/* Morse ident (cw_tx.h).
 * SPDX-License-Identifier: MIT */
#include "cw_tx.h"

static const char *const LETTERS[26] = {
    ".-", "-...", "-.-.", "-..", ".", "..-.", "--.", "....", "..", ".---", "-.-", ".-..", "--",
    "-.", "---", ".--.", "--.-", ".-.", "...", "-", "..-", "...-", ".--", "-..-", "-.--", "--..",
};
static const char *const DIGITS[10] = {
    "-----", ".----", "..---", "...--", "....-", ".....", "-....", "--...", "---..", "----.",
};

static const char *code_of(char ch) {
    if (ch >= 'a' && ch <= 'z')
        ch = (char)(ch - 'a' + 'A');
    if (ch >= 'A' && ch <= 'Z')
        return LETTERS[ch - 'A'];
    if (ch >= '0' && ch <= '9')
        return DIGITS[ch - '0'];
    if (ch == '/')
        return "-..-.";
    return "";
}

/* sin(pi/2 * i/64) in Q15: a quarter wave */
static const int16_t QSINE[65] = {
    0, 804, 1608, 2410, 3212, 4011, 4808, 5602, 6393, 7179, 7962, 8739, 9512,
    10278, 11039, 11793, 12539, 13279, 14010, 14732, 15446, 16151, 16846, 17530, 18204, 18868,
    19519, 20159, 20787, 21403, 22005, 22594, 23170, 23731, 24279, 24811, 25329, 25832, 26319,
    26790, 27245, 27683, 28105, 28510, 28898, 29268, 29621, 29956, 30273, 30571, 30852, 31113,
    31356, 31580, 31785, 31971, 32137, 32285, 32412, 32521, 32609, 32678, 32728, 32757, 32767,
};

static int16_t sine(uint32_t phase) {
    unsigned q = phase >> 30, i = (phase >> 24) & 63;
    switch (q) {
    case 0: return QSINE[i];
    case 1: return QSINE[64 - i];
    case 2: return (int16_t)-QSINE[i];
    default: return (int16_t)-QSINE[64 - i];
    }
}

void cw_init(cw_tx *c, const char *text, uint32_t fs, unsigned wpm, uint32_t tone_hz, int16_t level) {
    c->text = text;
    c->el = "";
    c->phase = 0;
    c->inc = (uint32_t)(((uint64_t)tone_hz << 32) / fs);
    c->dit = fs * 6u / (5u * wpm);      /* 1.2 s / wpm */
    c->ramp = fs / 200;                 /* 5 ms */
    c->gain = 0;
    c->level = level;
    c->key = 0;
    c->left = 3 * c->dit;               /* lead-in silence */
    c->lead = 1;
    c->done = 0;
}

/* the next key segment: sets key and left (samples); done at the end of the text */
static void next(cw_tx *c) {
    if (*c->el) {                       /* inside a letter: a mark, then its 1-dit gap */
        if (c->key == 0) {
            c->key = 1;
            c->left = (*c->el == '-' ? 3 : 1) * c->dit;
        } else {
            c->key = 0;
            c->left = c->dit;
            c->el++;
            if (!*c->el)
                c->left += 2 * c->dit;  /* the letter's end: 3 dits in all */
        }
        return;
    }
    c->key = 0;
    while (*c->text) {
        char ch = *c->text++;
        if (ch == ' ') {
            c->left = 4 * c->dit;       /* with the letter's 3: 7 */
            return;
        }
        c->el = code_of(ch);
        if (*c->el) {
            next(c);
            return;
        }
    }
    c->done = 1;
}

unsigned cw_render(cw_tx *c, int16_t *out, unsigned n) {
    unsigned i = 0;
    while (i < n) {
        if (c->left == 0) {
            if (c->done)
                break;
            next(c);
            if (c->done) {
                c->left = c->ramp;      /* let the last edge finish */
                c->key = 0;
            }
            continue;
        }
        if (c->key && c->gain < c->ramp)
            c->gain++;
        else if (!c->key && c->gain > 0)
            c->gain--;
        int32_t s = (int32_t)sine(c->phase) * c->level >> 15;
        out[i++] = (int16_t)(s * (int32_t)c->gain / (int32_t)c->ramp);
        c->phase += c->inc;
        c->left--;
    }
    return i;
}
