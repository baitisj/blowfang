// pluto_chan -f: each channel's audio, squelch gated, one HamFLAC stream an over.
//
// stdout, little-endian records:
//   'L' u8 nch u32 tick  then nch x 10 int8   the channels' levels each 0.1 s over the last second
//                                             (dBFS, rounded, clamped to -127..0)
//   'O' u8 ch  u32 tick  u32 nbytes  FLAC     one over: 8 kHz mono 16-bit FLAC (HamFLAC), from
//                                             tick (0.1 s from the start, lead-in included)
//   'E' u8 0   u32 tick                       the end of the stream
// The gate: a floor per channel that follows the level down at once and up 0.2 dB/s; the
// squelch opens 12 dB over it with the audio quieted (a noise squelch: the variance of the
// first difference over the variance, about 1.6 for the discriminator's noise and 0.1-0.6 under
// a carrier, measured 11-22; open under 1.2) and closes after 0.5 s under floor + 8 dB or noisy.
// The level gate alone opened on broadband bursts that lifted every channel at once. An over keeps 0.3 s
// before the opening and the 0.5 s hang, and is cut every 60 s on a long carrier.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <vector>

#include "hamflac/hamflac.hpp"

namespace {

constexpr int kTick = 800;            // 0.1 s at 8 kHz
constexpr int kMaxCh = 16;
constexpr float kOpenDb = 12.0f, kCloseDb = 8.0f, kRiseDb = 0.02f;
constexpr int kHang = 5, kPre = 3, kMaxTicks = 600;
constexpr float kQuietOpen = 1.2f, kQuietClose = 1.4f;

float noise_ratio(const int16_t* a, int n)
{
    double m = 0, v = 0, d = 0;
    for (int i = 0; i < n; i++)
        m += a[i];
    m /= n;
    for (int i = 0; i < n; i++)
        v += (a[i] - m) * (a[i] - m);
    for (int i = 1; i < n; i++)
        d += double(a[i] - a[i - 1]) * (a[i] - a[i - 1]);
    return v > 0 ? float(d / v) : 2.0f;
}

struct Gate {
    bool init = false, open = false;
    float floor = 0;
    int below = 0;
    uint32_t start = 0;
    std::deque<std::vector<int16_t>> pre;   // the last kPre ticks while closed
    std::vector<int16_t> over;
    int8_t lv[10] = {};
};

Gate g[kMaxCh];
int nch_seen = 0;
uint32_t tick = 0;                          // ticks done for channel 0 (all advance together)

void put(const void* p, size_t n) { fwrite(p, 1, n, stdout); }
void put_u32(uint32_t v) { uint8_t b[4] = {uint8_t(v), uint8_t(v >> 8), uint8_t(v >> 16), uint8_t(v >> 24)}; put(b, 4); }

void flush_over(int ch, Gate& s)
{
    if (s.over.empty())
        return;
    hamflac::EncodeOptions o;
    o.seek_every_frames = 0;                // small clips: no SEEKTABLE
    std::string f = hamflac::encode(s.over, 1, 8000, o);
    put("O", 1);
    uint8_t c = uint8_t(ch);
    put(&c, 1);
    put_u32(s.start);
    put_u32(uint32_t(f.size()));
    put(f.data(), f.size());
    fflush(stdout);
    s.over.clear();
}

}  // namespace

extern "C" void flac_tick(int ch, int nch, const int16_t* aud, int n, float level)
{
    if (ch < 0 || ch >= kMaxCh || nch > kMaxCh)
        return;
    nch_seen = nch;
    Gate& s = g[ch];
    if (!s.init) {
        s.floor = level;
        s.init = true;
    }
    s.floor = std::fmin(level, s.floor + kRiseDb);
    float v = std::fmax(-127.0f, std::fmin(0.0f, std::round(level)));
    s.lv[tick % 10] = int8_t(v);
    std::vector<int16_t> a(aud, aud + n);
    const float nr = noise_ratio(aud, n);
    if (!s.open) {
        if (level > s.floor + kOpenDb && nr < kQuietOpen) {
            s.open = true;
            s.below = 0;
            s.start = tick - uint32_t(s.pre.size());
            for (auto& p : s.pre)
                s.over.insert(s.over.end(), p.begin(), p.end());
            s.pre.clear();
            s.over.insert(s.over.end(), a.begin(), a.end());
        } else {
            s.pre.push_back(std::move(a));
            if (int(s.pre.size()) > kPre)
                s.pre.pop_front();
        }
    } else {
        s.over.insert(s.over.end(), a.begin(), a.end());
        s.below = (level < s.floor + kCloseDb || nr > kQuietClose) ? s.below + 1 : 0;
        if (s.below >= kHang) {
            flush_over(ch, s);
            s.open = false;
        } else if (int(s.over.size()) >= kMaxTicks * kTick) {
            flush_over(ch, s);
            s.start = tick + 1;
        }
    }
    if (ch == nch - 1) {                            // the last channel of this tick
        tick++;
        if (tick % 10 == 0) {
            put("L", 1);
            uint8_t c = uint8_t(nch_seen);
            put(&c, 1);
            put_u32(tick - 10);
            for (int j = 0; j < nch_seen; j++)
                put(g[j].lv, 10);
            fflush(stdout);
        }
    }
}

extern "C" void flac_finish(void)
{
    for (int j = 0; j < nch_seen; j++)
        if (g[j].open)
            flush_over(j, g[j]);
    put("E", 1);
    uint8_t z = 0;
    put(&z, 1);
    put_u32(tick);
    fflush(stdout);
}
