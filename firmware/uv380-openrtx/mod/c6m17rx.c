/* c6m17rx: M17 receive on the unmodified UV-380, on the radio (docs/uv380-m17-rx.md). Receive
   only (no RF out). The HR_C6000's 8 kHz FM tap, set up as c6fmtap with 0x34 = 0x1C (flat
   10 Hz-3.4 kHz, DC coupled, measured 11-18), feeds c/common/m17_rx.c, and what it decodes goes
   to the USB serial: LSF callsigns, SMS text, stream frames, BERT bit errors.

   Set-up, all of it put back at the end (or at PTT, mode change, Ctrl-C): the AT1846S in its DMR
   receive set (at1846.h), RX_AUDIO_MUX (PD9) high, the C6000 in FM receive (0x10 = 0x80,
   0x60 = 0x00, 0x26 = 0xFD), 0x34 = M17RX_V34, 0x81 |= 0x02. Halves as c6fmtap's TAP_FLAG 1: on
   0x82 bit 1 the complete half is the one whose last sample changed since the last flag. The tap
   reads +3 negative, so the receiver negates (M17_INVERT). Its state (about 6.6 KB) lives in
   scratch, the SMS being put together after it.

   Lines ("mod say ..."; T is ms from the first sample the receiver had, AT/24):
     m17 start s=150 v34=1c at=ok scratch=N need=N
     f T lsf ok=1 sc=993 g=2570 m=0 src=AG7EW dst=ALL type=0002
     f T str fn=0005 lich=1 sc= g= m=             fn's bit 15 is the last frame
     f T pkt n=1 eof=0 sc= g= m=                  n: the frame number, or on EOF the bytes used
     f T bert err=0 sc= g= m=                     err: bits failing the PRBS9 check, over 3
     f T eot
     sms T AG7EW: TEXT                            a packet SMS whose CRC checks
     st T halves= gaps= frames= lost= rssi=       once a second
     end halves= gaps= frames= lost= lsf= str= pkt= bert= eot= sms= bert_bits= bert_err=
   sc and m are thousandths (sync score, Viterbi disagreement); g is tap units per level.
   Voice (API level 5 images): stream frames' Codec2 3200 goes to the speaker through the image's
   own decoder (api->c2_start/c2_push/c2_stop, OpenRTX's M17 playback). The module keeps a
   jitter buffer (M17RX_C2Q frames), starts the decoder once M17RX_PREBUF frames are in, feeds it
   as it takes them, and stops it M17RX_TAIL_MS after the last frame went (the last stream frame,
   an EOT, or no stream frame for M17RX_GAP_MS). Lines:
     spk start q=N | spk err | spk stop frames=N gap=MS drop=N
   (gap: the longest wait between stream frames, ms; over about 120 the decoder ran dry; drop:
   frames the full buffer had no room for). On a level 4 image: "spk none level=4", no sound.
   M17RX_TAP 1 (c6m17rx_tap.c) also sends each half as c6fmtap's four "a" lines, so the host
   receiver (c/common/m17_rx_host) can be run on exactly what the radio's had.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "at1846.h"

#define M17_INVERT 1
#include "../../../c/common/m17_rx.c"

#ifndef M17RX_S
#define M17RX_S 150u
#endif
#ifndef M17RX_V34
#define M17RX_V34 0x1C
#endif
#ifndef M17RX_TAP
#define M17RX_TAP 0
#endif

#define GPIOD 0x40020C00u
#define MUX 9u
#define HALF0 0x030u
#define HALF1 0x230u
#define BLK 64u
#ifndef M17RX_SPK
#define M17RX_SPK 1
#endif
#ifndef M17RX_PREBUF
#define M17RX_PREBUF 6u                 /* 120 ms of Codec2 before the decoder starts */
#endif
#ifndef M17RX_GAP_MS
#define M17RX_GAP_MS 400u               /* no stream frame this long: the call has ended */
#endif
#ifndef M17RX_TAIL_MS
#define M17RX_TAIL_MS 120u              /* after the last push: the decoder's queue (4) plays out */
#endif
#define M17RX_C2Q 32u                   /* frames (power of 2) */
#define SMS_MAX 256u                    /* an SMS this long (M17 allows 823 bytes; longer is cut) */

typedef struct {
    m17_rx rx;
    m17_frame fr;
    uint8_t sms[SMS_MAX];
    unsigned sms_n;
    char src[10];
    unsigned n[M17_KINDS];
    unsigned sms_ok;
    uint32_t bert_bits, bert_err;
    uint8_t c2q[M17RX_C2Q][8];
    unsigned c2_in, c2_out;             /* ring counters */
    int spk;                            /* -1 none, 0 idle, 1 buffering, 2 playing, 3 ending */
    uint32_t spk_last;                  /* ms: the last stream frame (moved back at a call's end) */
    uint32_t spk_prev;                  /* ms: the stream frame before, for the longest gap */
    unsigned spk_frames, spk_gap, spk_drop;
} m17_mod;

static unsigned rd16(const ortx_api_t *api, unsigned a) {
    uint8_t b[2];
    if (c6_read(api, C6_DATA, a, b, 2))
        return 0x10000u;                 /* port busy: never equals a sample */
    return ((unsigned)b[0] << 8) | b[1];
}

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

static unsigned milli(float v) {
    return v <= 0.0f ? 0u : (unsigned)(v * 1000.0f + 0.5f);
}

/* bits of a BERT frame failing b[i] = b[i-9] ^ b[i-5] (PRBS9, self-synchronising): a wrong bit
   fails up to three checks */
static unsigned bert_check(const uint8_t *d, unsigned nbits, unsigned *checked) {
    unsigned bad = 0;
    for (unsigned i = 9; i < nbits; i++) {
        unsigned b = (d[i >> 3] >> (7u - (i & 7u))) & 1u;
        unsigned b9 = (d[(i - 9u) >> 3] >> (7u - ((i - 9u) & 7u))) & 1u;
        unsigned b5 = (d[(i - 5u) >> 3] >> (7u - ((i - 5u) & 7u))) & 1u;
        bad += b != (b9 ^ b5);
    }
    *checked = nbits - 9u;
    return bad;
}

static void report(const ortx_api_t *api, m17_mod *m) {
    m17_frame *f = &m->fr;
    char line[200];
    unsigned long t = (unsigned long)(f->at / 24u);
    unsigned sc = milli(f->score), g = f->gain > 0.0f ? (unsigned)f->gain : 0u, q = milli(f->metric);
    m->n[f->kind]++;
    switch (f->kind) {
    case M17_LSF: {
        char dst[10];
        m17_callsign(f->data + 6, m->src);
        m17_callsign(f->data, dst);
        api->fmt(line, sizeof line, "f %lu lsf ok=%d sc=%u g=%u m=%u src=%s dst=%s type=%02x%02x", t, f->ok,
                 sc, g, q, m->src, dst, f->data[12], f->data[13]);
        m->sms_n = 0;
        break;
    }
    case M17_STREAM:
        api->fmt(line, sizeof line, "f %lu str fn=%02x%02x lich=%d sc=%u g=%u m=%u", t, f->data[0], f->data[1],
                 f->ok, sc, g, q);
        if (m->spk >= 0) {
            for (unsigned h = 0; h < 2u; h++) {
                if (m->c2_in - m->c2_out >= M17RX_C2Q) {
                    m->spk_drop++;
                    continue;
                }
                api->memcpy(m->c2q[m->c2_in & (M17RX_C2Q - 1u)], f->data + 2 + 8 * h, 8);
                m->c2_in++;
            }
            if (m->spk == 0)
                m->spk = 1;
            if (m->spk == 3)
                m->spk = 2;             /* a new call before the old one stopped */
            uint32_t now = api->ms();
            if (m->spk == 2 && now - m->spk_prev > m->spk_gap)
                m->spk_gap = now - m->spk_prev;
            m->spk_prev = m->spk_last = now;
            if (f->data[0] & 0x80u)
                m->spk_last -= M17RX_GAP_MS;    /* the last frame: end once it has played */
        }
        break;
    case M17_PACKET: {
        unsigned meta = f->data[25], eof = meta >> 7, cnt = (meta >> 2) & 31u;
        api->fmt(line, sizeof line, "f %lu pkt n=%u eof=%u sc=%u g=%u m=%u", t, cnt, eof, sc, g, q);
        unsigned take = eof ? (cnt <= 25u ? cnt : 25u) : 25u;
        for (unsigned i = 0; i < take && m->sms_n < SMS_MAX; i++)
            m->sms[m->sms_n++] = f->data[i];
        if (eof) {
            api->say(line);
            unsigned n = m->sms_n;
            if (n >= 4u && m->sms[0] == 0x05u &&
                m17_crc(m->sms, n - 2u) == (uint16_t)((m->sms[n - 2u] << 8) | m->sms[n - 1u])) {
                unsigned k = (unsigned)api->fmt(line, sizeof line, "sms %lu %s: ", t, m->src);
                for (unsigned i = 1; i < n - 2u && m->sms[i] && k + 2u < sizeof line; i++)
                    line[k++] = (m->sms[i] >= 32u && m->sms[i] < 127u) ? (char)m->sms[i] : '?';
                line[k] = 0;
                m->sms_ok++;
            } else {
                api->fmt(line, sizeof line, "sms %lu bad n=%u", t, n);
            }
            m->sms_n = 0;
        }
        break;
    }
    case M17_BERT: {
        unsigned chk, bad = bert_check(f->data, 197, &chk);
        m->bert_bits += chk;
        m->bert_err += (bad + 2u) / 3u;
        api->fmt(line, sizeof line, "f %lu bert err=%u sc=%u g=%u m=%u", t, (bad + 2u) / 3u, sc, g, q);
        break;
    }
    default:
        api->fmt(line, sizeof line, "f %lu eot", t);
        if (m->spk >= 1)
            m->spk_last = api->ms() - M17RX_GAP_MS;
        break;
    }
    api->say(line);
}

/* the speaker: start the decoder with enough buffered, feed it what it takes, stop after a call */
static void speaker(const ortx_api_t *api, m17_mod *m) {
    char line[80];
    uint32_t now = api->ms();
    if (m->spk == 1 && (m->c2_in - m->c2_out >= M17RX_PREBUF || now - m->spk_last >= M17RX_GAP_MS)) {
        if (api->c2_start()) {
            api->say("spk err");
            m->spk = -1;
            return;
        }
        api->fmt(line, sizeof line, "spk start q=%u", m->c2_in - m->c2_out);
        api->say(line);
        m->spk = 2;
        m->spk_frames = m->spk_gap = m->spk_drop = 0;
    }
    if (m->spk == 2) {
        while (m->c2_in != m->c2_out && api->c2_push(m->c2q[m->c2_out & (M17RX_C2Q - 1u)]) == 0) {
            m->c2_out++;
            m->spk_frames++;
        }
        if (m->c2_in == m->c2_out && now - m->spk_last >= M17RX_GAP_MS) {
            m->spk = 3;                 /* the call is over: let the decoder's queue play out */
            m->spk_last = now;
        }
    }
    if (m->spk == 3 && now - m->spk_last >= M17RX_TAIL_MS) {
        api->c2_stop();
        api->fmt(line, sizeof line, "spk stop frames=%u gap=%u drop=%u", m->spk_frames, m->spk_gap,
                 m->spk_drop);
        api->say(line);
        m->spk = 0;
        m->c2_in = m->c2_out = 0;
    }
}

/* one complete half: to the receiver (and as "a" lines with M17RX_TAP) */
static void feed(const ortx_api_t *api, m17_mod *m, unsigned base, uint32_t *seq) {
    uint8_t b[2 * BLK];
    for (unsigned k = 0; k < 4u; k++) {
        if (c6_read(api, C6_DATA, base + k * 2u * BLK, b, sizeof b)) {
            (*seq)++;
            continue;
        }
        if (M17RX_TAP) {
            char line[24 + BLK * 5u];
            unsigned n = (unsigned)api->fmt(line, sizeof line, "a %lu", (unsigned long)*seq);
            for (unsigned i = 0; i < BLK; i++) {
                unsigned v = ((unsigned)b[2 * i] << 8) | b[2 * i + 1];
                line[n++] = ' ';
                for (int s = 12; s >= 0; s -= 4)
                    line[n++] = hexd((v >> s) & 15u);
            }
            line[n++] = '\r';
            line[n++] = '\n';
            api->usb_write(line, n);
        }
        (*seq)++;
        for (unsigned i = 0; i < BLK; i++) {
            int16_t s = (int16_t)(((unsigned)b[2 * i] << 8) | b[2 * i + 1]);
            if (m17_push(&m->rx, s, &m->fr))
                report(api, m);
        }
    }
}

int uv_main(const ortx_api_t *api) {
    static const uint8_t regs[] = {0x10, 0x60, 0x26, 0x81, 0x34, 0x36, 0xE0, 0};
    char line[200];
    uint32_t sz = 0;
    m17_mod *m = (m17_mod *)api->scratch(&sz);
    if (sz < sizeof(m17_mod)) {
        api->fmt(line, sizeof line, "m17 err scratch=%lu need=%u", (unsigned long)sz, (unsigned)sizeof(m17_mod));
        api->say(line);
        return -1;
    }
    uint8_t *z = (uint8_t *)m;
    for (unsigned i = 0; i < sizeof(m17_mod); i++)
        z[i] = 0;
    m17_init(&m->rx);
    m->src[0] = '?';
    m->spk = (M17RX_SPK && api->level >= 5u) ? 0 : -1;
    if (M17RX_SPK && m->spk < 0) {
        api->fmt(line, sizeof line, "spk none level=%u", (unsigned)api->level);
        api->say(line);
    }

    c6_saved sv;
    at_saved as;
    as.ok = 0;
    c6_save(api, &sv, regs);
    uint32_t mux = (C6_REG(GPIOD + 0x14u) >> MUX) & 1u;
    C6_REG(GPIOD + 0x18u) = 1u << MUX;
    int atr = at_dmr_rx(api, &as, 0);
    uint8_t m81 = api->c6000_read(0x81);
    api->c6000_write(0x10, 0x80);
    api->c6000_write(0x60, 0x00);
    api->c6000_write(0x26, 0xFD);
    api->c6000_write(0x81, (uint8_t)(m81 | 0x02u));
    api->c6000_write(0x34, M17RX_V34);
    api->c6000_write(0x83, 0xFF);
    api->fmt(line, sizeof line, "m17 start s=%u v34=%02x at=%s scratch=%lu need=%u tap=%d", M17RX_S, M17RX_V34,
             atr ? "err" : "ok", (unsigned long)sz, (unsigned)sizeof(m17_mod), M17RX_TAP);
    api->say(line);

    uint32_t seq = 0, t0 = api->ms(), last_st = 0;
    unsigned halves = 0, gaps = 0, last = 2, pe[2];
    pe[0] = rd16(api, HALF0 + 510u), pe[1] = rd16(api, HALF1 + 510u);
    while (api->ms() - t0 < M17RX_S * 1000u && !api->stop()) {
        uint32_t t = api->ms() - t0;
        uint8_t f = api->c6000_read(0x82);
        if (f)
            api->c6000_write(0x83, f);
        if (f & 0x02u) {
            unsigned se0 = rd16(api, HALF0 + 510u), se1 = rd16(api, HALF1 + 510u);
            unsigned c0 = se0 != pe[0], c1 = se1 != pe[1];
            int done = c0 != c1 ? (c0 ? 0 : 1) : (last < 2u ? (int)(1u - last) : 0);
            pe[0] = se0, pe[1] = se1;
            if ((unsigned)done == last) {
                gaps++;
                seq += 4u;
            }
            last = (unsigned)done;
            feed(api, m, done ? HALF1 : HALF0, &seq);
            halves++;
        }
        if (m->spk > 0)
            speaker(api, m);
        if (t - last_st >= 1000u) {
            last_st = t;
            api->fmt(line, sizeof line, "st %lu halves=%u gaps=%u frames=%lu lost=%lu rssi=%ld", (unsigned long)t,
                     halves, gaps, (unsigned long)m->rx.frames, (unsigned long)m->rx.lost, (long)api->rssi());
            api->say(line);
        }
        api->sleep_ms(1);
    }

    if (m->spk > 0) {
        api->c2_stop();
        api->fmt(line, sizeof line, "spk stop frames=%u gap=%u drop=%u", m->spk_frames, m->spk_gap,
                 m->spk_drop);
        api->say(line);
    }
    api->c6000_write(0x81, m81);
    c6_restore(api, &sv);
    api->c6000_write(0x83, 0xFF);
    at_restore(api, &as);
    C6_REG(GPIOD + 0x18u) = mux ? (1u << MUX) : (1u << (MUX + 16u));
    api->fmt(line, sizeof line, "end halves=%u gaps=%u frames=%lu lost=%lu lsf=%u str=%u pkt=%u bert=%u eot=%u "
             "sms=%u bert_bits=%lu bert_err=%lu", halves, gaps, (unsigned long)m->rx.frames,
             (unsigned long)m->rx.lost, m->n[M17_LSF], m->n[M17_STREAM], m->n[M17_PACKET], m->n[M17_BERT],
             m->n[M17_EOT], m->sms_ok, (unsigned long)m->bert_bits, (unsigned long)m->bert_err);
    api->say(line);
    return 0;
}
