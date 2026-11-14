/* alarm: the BLOWFANG alarm clock (module API level 8, the screen at 6, the LED at 7). It waits
   for a UTC time on the core's clock (level 8: set by aisclk or aprsclk, docs/k6-aprs-clock.md),
   showing UTC, the alarm time and the countdown on the screen, then plays midi.c's songs through
   the speaker. Receive side only: it never keys the transmitter.

   The time is in the module, not the build: `python3 tools/k6_midi.py alarm mod-alarm.bin
   OUT.bin --at 07:30 [SONG ...]` sets it (and the songs, as `patch` does), with no compiler.
   CI's mod-alarm.bin has no time set and refuses to run (-7).

   say lines: "alarm at 07:30 utc now 06:58:12 w=412" (then every 10 minutes), "alarm ring",
   then midi.c's. Returns midi.c's (the songs played), or -2 (a held key cancelled it), -6 (no
   clock: run aisclk or aprsclk first), -7 (no time set), -8 (an image before level 8).
   While it waits the pager hears no packets, as with any module. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 8
#define K6_MODULE_OPT z
#define MIDI_ALARM 1
#define K6_NAME "ALARM"   /* the module list's name, not midi's */
#include "midi.c"
