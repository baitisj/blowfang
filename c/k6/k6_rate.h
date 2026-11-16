/* k6_rate: the VOICE MSG pager's rate ladder (docs/k6-fsk24.md, "2400 baud in the image"; Jeff
   2026-11-16: "plan for 2400 baud mode in the K6 image ... backwards compatibility for lower
   rates based on link quality or error rates").

   Rungs: D/2400 (plain 2-FSK, REG_58 0x00E9, REG_72 0x60CB: what the chip read 234 of 234 from
   the Pluto, fsk24 run 3), F/1800 (FFSK 1200/1800 Hz, the default, which every sender and the
   IC-7100 reach) and F/1200 (a REG_72 write, as before). The K6 boots and idles on F/1800 and
   advertises `rates=F12,F18,D24`. A sender whose path is flat asks for D/2400 for a session with
   a control record that sets phy=D and baud=2400 together; the K6 drops back to F/1800 by itself:
     - KR_WHY_REVERT  no good packet within KR_REVERT_TICKS (10 s) of the switch
     - KR_WHY_CRC     KR_BAD_MAX (3) of the last KR_WINDOW (8) synced packets failed their CRC
     - KR_WHY_QUIET   no good packet for KR_QUIET_TICKS (30 s) after the last one (a session's end)
   It never steps up by itself: only the sender knows whether its transmit path is flat.

   This file is the policy only (no registers, no I/O), so the image (Aura's src/app/pager.rs,
   through the library) and the host tests (tests/test_k6_rate.py) run the same code. It also
   keeps the link's numbers for the PLAY line: synced packets, CRC failures, RSSI least and median.
   No division, no library calls. SPDX-License-Identifier: MIT */
#ifndef K6_RATE_H
#define K6_RATE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

enum { KR_F18 = 0, KR_D24 = 1 };
enum { KR_WHY_NONE = 0, KR_WHY_CRC = 1, KR_WHY_QUIET = 2, KR_WHY_REVERT = 3 };

#define KR_WINDOW 8u              /* synced packets the CRC rule looks back over */
#define KR_BAD_MAX 3u             /* CRC failures among them that send the K6 back to F/1800 */
#define KR_REVERT_TICKS 100000u   /* 100 us ticks: 10 s from the switch to the first good packet */
#define KR_QUIET_TICKS 300000u    /* 30 s from the last good packet */

/* control record bits (sotto_pager.h SP_CTL_PHY, SP_CTL_BAUD; the value is the same bit of
   byte 3: phy 1 = F, 0 = D; baud 1 = 2400) */
#define KR_CTL_PHY 1u
#define KR_CTL_BAUD 4u

/* the RSSI histogram for the median: KR_RSSI_BINS bins of KR_RSSI_STEP dB from KR_RSSI_LO */
#define KR_RSSI_LO (-136)
#define KR_RSSI_STEP 3
#define KR_RSSI_BINS 32

typedef struct {
  uint8_t hist;      /* the last KR_WINDOW synced packets on D/2400, bit set = CRC failed, newest bit 0 */
  uint8_t n;         /* how many of those there are (up to KR_WINDOW) */
  uint8_t good;      /* a good packet came since the switch to D/2400 */
  uint8_t why;       /* the last fallback (KR_WHY_*), until kr_take_why reads it */
  uint32_t quiet;    /* ticks since the switch or the last good packet */
  /* the link since kr_link_reset (the PLAY line) */
  uint16_t syncs;    /* synced packets */
  uint16_t bad;      /* of them, CRC failures (or cut short) */
  int16_t rssi_min;  /* least RSSI of a synced packet, dBm (0: none yet) */
  uint8_t bins[KR_RSSI_BINS]; /* synced packets in each RSSI bin (saturating at 255) */
} kr_t;

/* The rung a control record asks for, from the rung the K6 is on: phy=D with baud=2400 (both
   in the mask) is D/2400; phy=F or baud=1200 is F/1800; anything else leaves it. A lone
   baud=2400 (the old FFSK 1200/2400 variants, which never synced) leaves it too, so an old
   sender can't make the K6 deaf. */
int kr_control(int cur, unsigned mask, unsigned val);
/* The K6 moved up to D/2400: a fresh window and timers. */
void kr_enter(void);
/* A synced packet's outcome while on D/2400 (ok: its CRC checked). Returns KR_WHY_CRC when
   the K6 should go back to F/1800 now, else 0. */
int kr_packet(int ok);
/* dt ticks passed while on D/2400. Returns KR_WHY_REVERT or KR_WHY_QUIET when the K6 should go
   back to F/1800 now, else 0. */
int kr_tick(uint32_t dt);
/* The last fallback's reason once (KR_WHY_*), then KR_WHY_NONE: the ARQ report carries it. */
int kr_take_why(void);
/* Any synced packet at any rung, for the PLAY line: ok, and the RSSI at its sync (dBm). */
void kr_link(int ok, int rssi_dbm);
/* The median RSSI of the synced packets since kr_link_reset (dBm, the bin's middle; 0 if none). */
int kr_rssi_median(void);
void kr_link_reset(void);
const kr_t *kr_state(void);

#ifdef __cplusplus
}
#endif
#endif
