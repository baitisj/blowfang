/* c4dmr: c4lock's chained calls (c4ftm, docs/k6-c4fm.md) carrying DMR in place of C4FM. The
   table (k6c4fm.dmr_frames) is tools/uv380_dmrburst.py's repeater-style stream (BS data sync,
   CACH, slot 2 idle): lead-in idle frames, CSBK preambles, a dmrsotto test message (head, the
   'AG7EW' text, 36 bytes counting up, end) as rate 1/2 data in slot 1, idle frames: 20 frames of
   60 ms, sent as one 1.2 s call of 4 packets back to back (3 table frames = 5 DMR frames each),
   twice, the second with every sign flipped. The UV-380's C6000 (c6dmrrx) locked on this stream
   from the IC-7100; the Pluto capture goes to python3 tools/uv380_dmrburst.py decode CAP.wav.
   Keys after the job's trigger carrier, as c4sotto. Deviation +-1.94 kHz outer, DMR's.
   Build: python -m modem.k6 mod out.wav c/k6/mod/c4dmr.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z     /* as c4lock.c: -Oz */
#define C4_DEMO 1
#define C4_CHAIN 1
#define C4_DMRS 1
#define C4_GAIN 56          /* 78 gave +-2.7 kHz: DMR's +-1944 Hz */
#define C4_INNER 19         /* 27 gave +-0.9..1.1 kHz: DMR's +-648 Hz */
#define C4_WAIT_MS 1200000  /* armed up to 20 minutes: it keys after the job's trigger carrier */
#define K6_NAME "DMR STREAM"
#include "c4lock.c"
