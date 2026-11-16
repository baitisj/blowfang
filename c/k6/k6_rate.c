/* k6_rate: the VOICE MSG pager's rate ladder, the policy alone (k6_rate.h).
   SPDX-License-Identifier: MIT */
#include "k6_rate.h"

static kr_t R;

int kr_control(int cur, unsigned mask, unsigned val) {
  int phy = (mask & KR_CTL_PHY) != 0, baud = (mask & KR_CTL_BAUD) != 0;
  if ((phy && (val & KR_CTL_PHY)) || (baud && !(val & KR_CTL_BAUD))) return KR_F18;
  if (phy && baud) return KR_D24; /* phy=D baud=2400 */
  return cur;
}

void kr_enter(void) {
  R.hist = 0, R.n = 0, R.good = 0, R.quiet = 0;
}

static unsigned ones(unsigned x) {
  unsigned n = 0;
  while (x) x &= x - 1, n++;
  return n;
}

static int fall(int why) {
  R.why = (uint8_t)why;
  return why;
}

int kr_packet(int ok) {
  R.hist = (uint8_t)(R.hist << 1 | !ok);
  if (R.n < KR_WINDOW) R.n++;
  if (ok) {
    R.good = 1, R.quiet = 0;
    return 0;
  }
  return ones(R.hist) >= KR_BAD_MAX ? fall(KR_WHY_CRC) : 0;
}

int kr_tick(uint32_t dt) {
  uint32_t lim = R.good ? KR_QUIET_TICKS : KR_REVERT_TICKS;
  R.quiet = R.quiet + dt < R.quiet ? 0xFFFFFFFFu : R.quiet + dt;
  if (R.quiet < lim) return 0;
  return fall(R.good ? KR_WHY_QUIET : KR_WHY_REVERT);
}

int kr_take_why(void) {
  int w = R.why;
  R.why = KR_WHY_NONE;
  return w;
}

void kr_link(int ok, int rssi_dbm) {
  int b = KR_RSSI_LO, i = 0;
  if (R.syncs < 0xFFFF) R.syncs++;
  if (!ok && R.bad < 0xFFFF) R.bad++;
  if (!R.rssi_min || rssi_dbm < R.rssi_min) R.rssi_min = (int16_t)rssi_dbm;
  while (i < KR_RSSI_BINS - 1 && rssi_dbm >= b + KR_RSSI_STEP) b += KR_RSSI_STEP, i++;
  if (R.bins[i] < 0xFF) R.bins[i]++;
}

int kr_rssi_median(void) {
  unsigned want = 0, sum = 0;
  int i;
  for (i = 0; i < KR_RSSI_BINS; i++) want += R.bins[i]; /* (bins saturate: not R.syncs) */
  if (!want) return 0;
  want = (want + 1u) >> 1;
  for (i = 0; i < KR_RSSI_BINS; i++) {
    sum += R.bins[i];
    if (sum >= want) break;
  }
  if (i == KR_RSSI_BINS) i--;
  return KR_RSSI_LO + i * KR_RSSI_STEP + KR_RSSI_STEP / 2;
}

void kr_link_reset(void) {
  int i;
  R.syncs = 0, R.bad = 0, R.rssi_min = 0;
  for (i = 0; i < KR_RSSI_BINS; i++) R.bins[i] = 0;
}

const kr_t *kr_state(void) { return &R; }
