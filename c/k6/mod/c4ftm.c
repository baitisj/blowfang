/* c4ftm: c4lock (docs/k6-c4fm.md) as a test for a real C4FM radio, such as an FTM-300DR on 440.850
   in DN mode. After c4lock's calibration it sends five whole YSF transmissions at the lag that run 4
   found best, 1 s apart: two DN calls in the FTM-300DR's own layout (call mode 1, FT 7, AMBE
   silence, DCH frames 6 and 7 = the radio-type and GPS block the FTM needs before it shows a
   callsign): header, FN 6, FN 7, terminator; then FN 5, FN 6, FN 7, terminator; the two again;
   then data (header, two data frames, terminator). Plain signs (11-18: the FTM's polarity).
   Then AG7EW in MCW, as c4lock. Pre-armed (Jeff 11-18): once the upload's carrier is gone it says
   "c4 armed: a key sends" and shows ARMED; a K6 key press (and release) is the go: it says "c4 go"
   (the job starts its Pluto capture on that line) and keys 5 s later. A Pluto capture goes to
   python3 -m modem.k6c4fm analyze CAP.wav --plan demo.
   Build: python -m modem.k6 mod out.wav c/k6/mod/c4ftm.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z     /* as c4lock.c: -Oz */
#define C4_DEMO 1
#define C4_CHAIN 1          /* 11-18: the FTM shows a callsign only for a whole FN 0-7 call (10 frames) */
#ifndef C4_WAIT_MS
#define C4_WAIT_MS 2400000  /* armed up to 40 minutes */
#endif
#define C4_KEYGO 1          /* armed after the upload: a K6 key press is the go, keyed 5 s later */
#define K6_NAME "C4FM DEMO"
#include "c4lock.c"
