/* c4ftm: c4lock (docs/k6-c4fm.md) as a test for a real C4FM radio, such as an FTM-300DR on 440.850
   in DN mode. After c4lock's calibration it sends five whole YSF transmissions at the lag that run 4
   found best, 1 s apart: DN (V/D mode 2: header, two comms frames of AMBE silence with the DCH
   remarks "AG7EW K6 C4FM", terminator), data (header, two data frames, terminator), DN, data, DN.
   Then AG7EW in MCW, as c4lock. A Pluto capture goes to
   python3 -m modem.k6c4fm analyze CAP.wav --plan demo.
   Build: python -m modem.k6 mod out.wav c/k6/mod/c4ftm.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define C4_DEMO 1
#define K6_NAME "C4FM DEMO"
#include "c4lock.c"
