/* c4sotto: c4ftm (c4lock's demo, docs/k6-c4fm.md) with a sotto 400 voice clip in the C4FM calls:
   after the calibration, five whole YSF transmissions 1 s apart: a DN call in the FTM-300DR's
   layout (header, FN 6, FN 7, terminator: the callsign shows), the clip (c4sotto.bit: 240 bytes,
   40 superframes, 4.8 s at 400 bit/s) as YSF data full rate in three calls (header, FN 2-3, 4-5,
   6-7 with two 20-byte blocks each, terminator), then the other DN call (FN 5, 6, 7,
   terminator). Then AG7EW in MCW. A Pluto capture goes to
   python3 -m modem.k6c4fm analyze CAP.wav --plan sotto; the data blocks, in order, are the clip.
   Build: python -m modem.k6 mod out.wav c/k6/mod/c4sotto.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define C4_DEMO 1
#define C4_SOTTO 1
#define C4_WAIT_MS 1200000  /* armed up to 20 minutes: it keys after the job's trigger carrier */
#define K6_NAME "C4FM SOTTO"
#include "c4lock.c"
