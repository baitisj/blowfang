/* c4voice: c4ftm (docs/k6-c4fm.md) with a voice call: after c4lock's calibration it sends one DN
   call in the FTM-300DR's layout three times, 1 s apart: header, FN 0-7, FN 0-3, terminator, the
   radio-type and GPS block (made-up position) in FN 6 and 7, and 60 AMBE+2 frames (1.2 s) in the
   comms frames. The repo's table (k6c4fm.voice_frames) holds AMBE silence, so the FTM shows the
   callsign and opens its speaker; for a real voice write a local header with
   python3 -m modem.k6c4fm header c4probe_frames.h --voice FRAMES.npy (never commit it). Then
   AG7EW in MCW, as c4lock. Pre-armed as c4ftm: a K6 key press or a DTMF '1' is the go.
   Build: python -m modem.k6 mod out.wav c/k6/mod/c4voice.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z     /* as c4lock.c: -Oz */
#define C4_DEMO 1
#define C4_CHAIN 1
#define C4_VOICE 1
#ifndef C4_WAIT_MS
#define C4_WAIT_MS 2400000  /* armed up to 40 minutes */
#endif
#define C4_KEYGO 1          /* armed after the upload: a K6 key press or DTMF '1' is the go */
#define K6_NAME "C4FM VOICE"
#include "c4lock.c"
