# Band surfing on the K6 (surf)

*2026-11-08.* Jeff's idea: a screen that shows signal strength across frequency as a bargraph,
the orange side keys jumping from peak to peak, UP/DOWN stepping along the frequency axis, and the
listening frequency drawn as a blinking bar with a caret above it.

**The lens scale (Jeff, 2026-11-09).** The listening frequency now always sits in the middle of
the screen and the scale moves with it. The scale is logarithmic out from the middle (Jeff,
2026-11-10: "the log scale to stretch out smoothly in and out of that area"): each bin's step is
a fixed ratio larger than the one inside it, the 8th bin each side always lands on +-20 kHz (the
lens, 16 bins, a quarter of the screen) and the last on the span (+-100 kHz to +-5 MHz, keys 1
and 3). Until then the lens was 2.5 kHz bins, flat, with the growth starting at its edge.
Each wide bin is the loudest of quick reads 20 kHz apart across its range, so a carrier between
two bin centres still shows. Every bin reads with the wide filter, so a strong carrier fills the
bins within about +-12 kHz of it. The narrow filter (2026-11-09) gave the lens a flat
top and a floor lower than the outer bins (one early read through the narrower filter against
the loudest of several), and Jeff, seeing it on the radio on 2026-11-10, chose the wide one.
An axis under the bars has ticks as on log paper (every 5 kHz to 20 kHz, 10 kHz to 100 kHz,
100 kHz to 1 MHz, then every 1 MHz; taller at 20 kHz, 100 kHz and 1 MHz), so the squeeze toward
the ends shows as ticks crowding together, and line 5 labels it: `-1M   -20k +20k   +1M`.
The code is now 5.3 KB (surfauto 5.5 KB), so it needs a voice image's 11,776 B module arena;
EXPLORE's 5,376 B is too small. Tested on the emulator only so far; the bench runs below are of
the earlier even 64-bin scale.

**Status: runs on the radio** (bench run 1, 2026-11-08, below). It is also tested on the emulated M0
against a modelled receiver and the real keypad matrix. It needs API level 6 (the module screen), so
it runs on the image already on the K6 (ffa7dbf, level 7) with no reflash: 3.8 KB of code in
the 4 KB store, receive only.

![four emulated screens](img/k6-surf-screens.png)

*Emulated screens, drawn pixel for pixel as the firmware's `draw_mod_screen` draws them
(`tools/k6_screen.py`): sweeping with 440.600 in the middle (+-1 MHz); SIDE1 jumped to the -70 dBm
carrier on 440.850, which moved to the middle and fills the lens's 2.5 kHz bins as wide as the
narrow filter, and the audio opened (RX); SIDE1 again, to 441.200, a repeater the explorer
saved, so line 5 gives its call, offset and tone (instead of the axis's labels); 3 zoomed out to
+-2.5 MHz (442.900 shows on the right). The dots in the top row mark saved repeaters.*

## Using it

Upload `mod-surf.bin` (CI builds it with the other modules) the usual way, for example
`tools/k6_upload.py mod-surf.bin --api-level 6 --key-file ~/.config/sotto/k6-mod.key`. It
starts on the band holding the pager's frequency (70 cm on the bench), centred on it.

| key | does |
|---|---|
| UP / DOWN | the middle one channel up / down (the band's step); held, it repeats. The scale moves with it, each bin keeping the level of the old one nearest it |
| SIDE1 / SIDE2 (orange) | the nearest caret (remembered peak, below) above / below, on screen or off it, whether or not it is on the air now; it moves to the middle on the channel raster. No search: the sweeps go on as they were. If only a coarse outer bin heard the caret, the loudest channel of the lens within that bin's width is then taken instead; a caret a fine bin heard is its channel and stays (until 2026-11-11 any louder channel in the whole lens won, which pulled a jump to the weaker of two close stations straight back: Jeff on the radio). With no caret that way, line 5 says "no peak" and the middle stays (Jeff, 2026-11-11; until then a press searched what was on the air at that moment) |
| A/B | how fast the carets fall: 1 min (the start), 5 min, 20 min, 15 s for the whole height (line 5 says "fall 5m" for 1.5 s) |
| BAND | next band: 2m (144-148, 10 kHz), 1.25m (222-225, 10 kHz), 70cm (430-450, 25 kHz), GMRS (462.550-467.725, 12.5 kHz), MAR/WX (156.000-162.550, 25 kHz: marine and NOAA weather), AIR (118-137, 25 kHz, AM, receive only). Each starts at its centre with its default step and span, but MAR/WX opens on NOAA weather's 162.475, the marine coast channels below it (Jeff, 2026-11-12: from the band's centre, 159.275 at +-1 MHz, neither was on screen) |
| # | next channel step: 1, 5, 6.25, 8.33, 10, 12.5, 20, 25, 50, 100 kHz (line 5 says "step 50k" for 1.5 s). The middle stays put; UP / DOWN then move onto the new raster. BAND resets the step to the band's default |
| 5 | the band's centre (MAR/WX: 162.475) |
| 4 | the demodulator: FM, DSB, AM, FM (Jeff, 2026-11-11). DSB is the chip's "USB": REG_47 AF 5 at the zero IF (REG_3D 0), the AFC off, both sidebands heard (docs/k6-afsweep.md). The title always shows the mode at its far right (Jeff, 2026-11-12: before, airband's own AM showed as SURF); line 5 says the mode for 1.5 s. The floor is measured again, since the zero IF's is another. At the zero IF a steady carrier is taken out of the RSSI (docs/k6-acars.md, seventh run), so DSB is for listening, not for finding carriers. BAND resets it; EXIT puts REG_3D back |
| 7 / 9 | fixed receiver gain down / up: 0, -7, -12, -17, -22, -27, -33, -45 dB. From auto, 9 starts at the top (0 dB) and 7 at -7 dB (Aura's own top AGC step). The title shows it after the step (`70cm 25k G-12`), line 5 says "gain -12 dB" for 1.5 s |
| 8 | the receiver's own AGC again ("gain auto") |
| 1 / 3 | zoom in / out: the span +-100 kHz, 250 kHz, 500 kHz, 1 MHz, 2.5 MHz, 5 MHz (defaults: 2m and 1.25m 500 kHz, 70cm and MAR/WX 1 MHz, GMRS 2.5 MHz). The lens stays +-20 kHz |
| * | monitor: audio open on the bar all the time, no sweeping (the title shows a speaker). The chip's own squelch opens all the way too, egzumer's squelch 0 (REG_78 RSSI 0, REG_4F noise 127, REG_4D/4E glitch 255): Jeff heard MON cut weak signals on 2026-11-11 though the module never closes the audio in MON. Leaving MON and EXIT put the four registers back as Aura had them. Not yet tried on the radio |
| 0 | hold: no refresh sweeps while a signal plays (the title shows a pause sign). While the scan is paused (hold on a station that plays, or monitor) the carets stop falling, and fall on from where they were after it (Jeff, 2026-11-13) |
| MENU | says the bar's frequency and level on the serial port |
| EXIT | leave; the module waits for the key's release so the pager doesn't see it |

Screen: the title bar has a surfer at its left edge, the band and its channel step, a speaker in MON or a pause sign in HLD, a fixed gain (`G-12`), and the demodulator (FM, AM, DSB) always at the far right. The glyphs need module API level 9 (docs/k6-modules.md); older images show `~` for the surfer, `*` for MON and `=` for HLD. In the busiest title (MAR/WX, a 12.5 kHz step, MON, a gain and DSB) the gain drops its G, then goes. Jeff picked the surfer (rider F) and the speaker (S2b) from mock-ups on 2026-11-12; line 1 the bar's frequency and its
level (a settled read, dBm) and RX while the audio is open; the bargraph (63 bins x 2 pixels on
the lens scale, the bar always in the middle under the caret, a small caret over each remembered
peak, a dot in the top row over each repeater the explorer saved, the axis and its ticks under the bars, nothing
past the band's edge); line 5 the axis's labels (`-1M   -20k +20k   +1M`), or with the bar on a
saved repeater its call, offset and TX tone (`N0CALL +5.0 103.5`), or "no peak" for 1.5 s.

**Remembered peaks.** Jeff, 2026-11-11: "SURF is doing a great job catching transient signals
when it's doing a band scan, but sometimes those signals go away when people stop keying." So
every peak a sweep hears gets a small caret, `^` 4 px wide and 2 tall, XORed over the bars so it
shows inside a bar too. Each sweep that hears it again (a peak whose read held its bar up, not a
bar falling 3 dB a sweep) pushes it up 8 px, to 19 px over the floor at most; the rest of the
time it falls, by time and not by sweeps (a sweep takes 0.1 to 2.5 s): the whole 19 px in about
1 minute at the start, A/B for 5 min, 20 min or 15 s. A caret on the floor is forgotten and no
longer hoppable. A new peak first needs a settled read 10 dB over the settled floor (4 a sweep at
most), so the quick-read noise bumps of bench run 1 get no caret; one that fails is kept as
unconfirmed (no caret, not hoppable) and read again after NBAD (6) more hearings, so a station
that was between overs at its first check gets its caret later (Jeff, 2026-11-11: 463.950 at
-75 dBm had none and the side keys passed it). The confirm read is at the peak's own frequency.
A coarse bin's peak is as wide as its 20 kHz read spacing at most, so it can't swallow a
station further off.
A settled read SQ_DB over the floor on the bar's own channel (the squelch opening on it, or a
parked read, monitor too) also confirms: its caret turns good at once, or a new one starts there.
Jeff's 464.500 (2026-11-11) played on the bar with a tall bar and never got a caret; its one
confirm read had landed between overs.
The confirm read is taken in the sweep itself when it can be: a lens bin (or any one-read bin)
whose quick read rises to a peak, with no confirmed caret there yet, is read again settled on the
spot (9 ms more, 4 a sweep at most), and that read decides. A read after the sweep came 0.1 to
2.5 s after the sweep heard the peak, so a station that pulses was always off by then (Jeff,
2026-11-12: an FSK station on 464.495, 0.3 s bursts about once a second, -87 dBm on a burst,
never got a caret). A wide outer bin does the same at its first sub-read that rises to a peak,
once a bin. In the emulator, with bursts like that at six places from one channel to 900 kHz
out: main got 4 of 24 runs, confirming in the lens only 17, every bin 23. A peak matches a caret
within half the wider of the two bins (or half a channel); a finer bin moves the caret to its
frequency, and a carrier's flat top in the lens counts at its middle. A side key's jump puts the
caret on the channel it lands on. A caret that would sit inside its own bar rides on top of it.

How many: 128, 8 bytes each (frequency, bin width, height, confirmed), 1 KB in the module store's
free RAM after the code (about 4.9 KB free on the voice images' 11,776 B arena, so room is not
the limit). One screen can show at most 63
distinct carets and in practice about 30 (a carrier lights a few bins), so 128 keeps two screens'
worth: what is on screen plus what was heard before a zoom or a move along the band. They are the
band's: BAND forgets them, zoom and UP / DOWN keep them. With all 128 in use, a new peak takes
the lowest caret's place. Matching costs 63 x 128 compares a sweep (about 2 ms).

**The scale.** Bin C+-k sits `off[k]` x 500 Hz from the middle, `off[k] = a (r^k - 1)` worked in
fixed point (`g += g * k >> 12` from `g = 256 a`), each step at least one unit. The ratio and `a`
for each span (`LK`, `LG` in surf.c) were found offline so that bin 31 lands exactly on the span
and bin 8 on 20 kHz (20.5 kHz at +-5 MHz). At +-1 MHz the steps grow from 1.5 kHz in the middle
by about 17 % a bin to 145 kHz at the edge (4 kHz at the lens's edge); at +-100 kHz the scale is
nearly even (2.5 to 4.5 kHz); at +-5 MHz from 1 kHz to about 1 MHz. A bin wider than 20 kHz takes quick reads
20 kHz apart across its range (halfway to each neighbour) and keeps the loudest and where it was:
a jump goes to that frequency's channel, not the bin's centre.

**Airband (AM).** BAND's sixth stop is 118-137 MHz in AM: REG_47 AF 7 and the AFC off (REG_73
bit 4), as Aura's `apply_modulation` does. Aura's AM AGC values (REG_14 0, REG_49 (50 << 7) | 32)
are left out: with them the RSSI never rose on the IC-7100's AM carrier (docs/k6-if-modem.md,
2026-11-06), and surf's bars and squelch are all RSSI. Every other band puts back the REG_73 the
module found at its start, and so does EXIT. Receive only: the module has no transmit calls at
all, and airband is outside Aura's TX band lock anyway. Airband sits below the K6's 136-174 MHz
front-end filter, so expect it deafer than 2m (the ACARS listen on 2026-11-09 heard nothing in a
quiet hour). On a 6.25 or 8.33 kHz raster line 1 shows five decimals (`118.00833`).

**Fixed gain.** REG_7E bit 15 fixes the gain at the AGC table entry its bits 14:12 name (3: REG_13),
so the module writes REG_13 (LNA short, LNA, mixer and PGA fields) and sets 7E to fixed index 3.
The dB values are egzumer's per-field table (LNA short 0, LNA -24..0, mixer -8..0, PGA -33..0), not
measured on the K6. RSSI moves with the gain, so each change measures the settled floor again and
sweeps afresh. 8 puts back REG_13 and REG_7E as the module found them.

**Keys during a long sweep.** Every read looks at the keypad too, and a key read the same twice
in a row is kept for the loop, so a press during a sweep isn't lost (before, a 250 ms press there
could vanish). Since 2026-11-11 the key also cuts the sweep short: the 5 s refresh, a wide bin's
sub-reads and the new-peak confirms all stop at once, the key is acted on, and the screen redraws
straight away. Jeff found jumps and UP / DOWN slow on the radio: a refresh or the sweep before
the audio opened held the keys 1-2 s. A jump or step onto a live signal now reads only the lens
(17 single reads, about 0.1 s) before the audio opens, for the refine and the station's shape;
the rest of the scale keeps the levels the old scale lent it until the first refresh, 5 s later.
The floor's 8 settled reads (start, BAND, gain keys) still run to the end, about 0.1-0.2 s.

**The stack.** The K6 gives a module about 2.3 KB of stack, and the emulator counts only the
module's own frames, not what Aura's calls (lcd_flush, tune, say) use under them. The #69 build
measured 2,016 B in the emulator and ran out on the radio: the log said `stack_free=0 B`, then the
stack guard rebooted the K6 at EXIT (2026-11-11 08:08Z). Since then the whole state (bins, levels,
bitmap, work arrays) lives at the start of the module store's free RAM (`api->scratch`), the carets
after it, and the module's own stack is about 300 B. With too little free RAM it returns -4.

## How it works

**One receiver, so it listens first.** While the bar's channel is quiet the module sweeps with
the audio muted. Every 16 bins it goes back to the bar for a settled read (12 ms after the
tune). A signal there 10 dB over the settled floor opens the audio (REG_47 FM and the speaker
amplifier on PB2, as `dmrdet`), and the module parks on it. It closes 1.5 s after the signal
drops and sweeps again. While parked, one refresh sweep every 5 s (about 0.3 s of muted audio)
keeps the bars current, unless hold or monitor is on. Whenever the receiver tunes with the audio
open (a refresh, a lens read after a jump, a step in monitor) the speaker amplifier goes off too and comes
back once the bar's frequency has settled: with only REG_47 muted, the retunes buzzed in the
speaker (Jeff, on the radio, 2026-11-10).

**Levels.** Sweep reads are quick: 3 ms after the tune, like Aura's own spectrum. That is
about 59% of the way in dB from the -173 dBm restart (explorer Phase 0), so the bars are
relative. The floor is the quartile of the window's bins, one pixel is about 1.1 quick dB, and
a bar rises at once but falls at most 3 dB a sweep. A peak is a local maximum 6 quick dB over
the floor. The squelch and line 1 use settled reads against a settled floor: the second lowest
of 8 bins' settled reads, measured when the window changes, again at the end of a new band's
first sweep (reads right after the big retune may not have settled: Jeff, 2026-11-12, no carets
after BAND until UP / DOWN), and every 30 s of sweeping. A quieter read of the bar lowers it. (Moving
off a strong signal, the AGC comes back up and the band fills with small peaks: Jeff,
2026-11-12, "we would expect to see more activity on a band than nothing". That's the band as
it is, and stays.) When the squelch opens on a window that has had no whole sweep yet
(BAND, the start), the first refresh sweep comes at once rather than 5 s later, so the band's
other stations get their carets while the centre plays (Jeff, 2026-11-12: no carets after BAND
until UP / DOWN). The AGC is left alone, because freezing the gain, as `dmrdet`
does, shifts RSSI by up to 15 dB.

**Speed (estimated from the emulator and Phase 0's ~0.9 ms tune).** A read costs about 3.9 ms.
At +-1 MHz a sweep is about 115 reads plus a check of the bar every 70 ms, about 0.5 s; at
+-5 MHz about 520 reads, 2 s; at +-100 kHz 63 reads, 0.3 s.

**Keys without a key API.** The module API has `key_down` but no key identity, so the module
scans Aura's matrix itself. The rows are PB0/PB4/PB10/PB11/PB9 and the columns
PC13/PC14/PF6/PB14; the side keys read with no row driven (Aura `src/device/keypad.rs` at
edf3b98). It writes only the row bits' BSRR/BRR, as `key_poll` does, and leaves the rows idle.

**Saved repeaters.** At the start the module reads the explorer's repeater table (c/k6/rt.h,
0x0F0000, level 5's `fl_read`, 8 bytes a slot, about 0.1 s) and keeps the frequency and slot of
up to 24 valid records. With the bar on one it reads that record's call, offset and TX tone for
line 5. It never writes the flash. This is the first piece of the VFO idea below: the bargraph
knows which peaks are repeaters, and PTT could later use the offset and tone it shows.

**Memory.** The 480 B bitmap, the ~1 KB state (the bins' frequencies and loudest-read places)
and a move's copy of the old scale are on the stack (`-fstack-usage`: about 2.1 KB deepest, of the
3 KB a module has at its call on the voice images and 6.7 KB on EXPLORE). The code is 4.8 KB
(surfauto 5.1 KB) before the axis and the narrow lens, 5.3 KB (5.5 KB) after: it needs the voice
images' 11,776 B module arena (EXPLORE's is 5,376 B).

## Bench build of the 2026-11-10 keys (surfkeys)

`mod-surfkeys.bin` presses its own keys, one every 3 s: 9, then 7 seven times (0 dB down to
-45 dB), 8 (AGC), BAND three times (airband, AM), #, 5, then BAND three times (back to 70 cm).
Before each press it says what the last one left, so with an identified carrier from the
IC-7100 on the start frequency the first nine lines measure the fixed-gain steps on the radio:

```
k9 440.8500 -70 -137 g1 70cm 25k     key, middle, settled dBm, settled floor, gain (0 auto), band, step
```

It leaves after the last line (about 54 s). Receive only, like surf.

## Bench build (surfauto)

`mod-surfauto.bin` is the same module for unattended runs: SIDE1 presses itself every 4 s. Each
caret it jumps to says `sel <MHz> <dBm> <floor>`: a settled read where it landed and the
settled floor (runs 1 and 2 said the bar's level 4 s later, before the jump). With no caret above,
it wraps to the band's bottom; carets from before the wrap are still there, so it goes round the
peaks it has heard. It leaves after 60 s. The keys
still work. Before it ends it says its last bargraph: `bar <middle MHz> <span> lens` (before the
lens: `bar <first bin MHz> <step>`), then two
lines of 32 letters, one a bin, `a` for 1 pixel up to `x` for 24 (about 112 bytes of the 684 the
telemetry carries). `python tools/k6_screen.py RUN.txt OUT.png` draws it as the screen showed
it, so a run's spectrum comes back with no serial log. With `--telemetry 12` the K6 sends those lines back over the air, so the bench needs
no one at the radio and no serial cable:

```
tools/k6_upload.py mod-surfauto.bin --api-level 6 --telemetry 12 --run-s 70 --key-file ~/.config/sotto/k6-mod.key
```

A run on the bench should list the active 70 cm repeaters (441.000, 441.100, 441.200, 440.925
DMR when up). For a known peak, the IC-7100 can send PSK31 `AG7EW TEST` on a free 70 cm
frequency during the run (`modem/psk31.py`), back on 440.850 before the module ends so the
report gets through. In the emulator (tests/test_k6_surf.py) it finds 440.850, then 441.200,
wraps from 450 to 430, then finds 430.300 and the others again.

## Bench run 1 (2026-11-08 08:08Z, hamlet, receive only)

`mod-surfauto.bin` (CI 5877cc6) went up by `tools/k6_upload.py` on 440.850. All 30 XL chunks
arrived on the first send. It ran 60,053 ms, returned 5 (sweeps), and had 2,128 B of stack
free. Its 13 `sel` lines came back over the air:

```
surf 70cm 440.850 25k
sel 440.8500 -35      the IC-7100 still keyed after the upload
sel 440.9250 -103     sel 440.9750 -103     sel 441.0750 -110     sel 441.1250 -108
sel 441.5500 -103     sel 441.6250 -107     sel 441.8000 -118     sel 441.8500 -122
sel 441.9250 -104     sel 442.2000 -104     sel 442.3250 -104     sel 442.4250 -103
end sw=5
```

What it showed:
- The module, the screen calls and the keypad scan run on the real image, and the stack has
  room to spare.
- **The side keys stopped on noise.** Each peak was a bump in the quick reads that a settled read
  put at only -103 to -122 dBm, so the climb took 13 presses to reach 442.4 and never wrapped.
  Each of those bumps opened the squelch, so the module spent most of the run parked, which is why
  it made only 5 sweeps. Fix (the PR after #143): a candidate peak counts only when a settled read
  puts it 10 dB over the settled floor (12 ms a candidate). The emulator now has bumps that show
  only in quick reads (`test_side_keys_skip_bumps_a_settled_read_doesnt_confirm`), and the old
  code fails that test.
- No serial log: auto mode refused the read-only logger, so the run's bargraph itself isn't
  recorded. Next run: allow `k6_harness listen` on ttyUSB2 (read-only) or watch the screen.

## Bench run 2 (2026-11-08 16:3xZ, hamlet, receive only)

`mod-surfauto.bin` from main bf7990a (#153) went up on 440.850: 69 of 69 chunks on the first
pass. It ran 60,051 ms, returned 9 sweeps (5 in run 1), and had 5,040 B of stack free.

```
surf 70cm 440.850 25k
sel 440.8500 -25      the upload's own carrier: the IC-7100 was still sending
sel 440.9250 -97      sel 441.0250 -113     sel 441.0750 -108     sel 441.1250 -112
sel 441.9500 -116     sel 442.0000 -104     sel 442.0750 -130     sel 445.3000 -102
bar 447.2500 25k
aaaaaaaaaaaaaaaabaaaaaaaaaaaaaaa
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
end sw=9
```

What it showed:
- The bargraph telemetry works end to end: `tools/k6_screen.py` drew the last window
  (447.250-448.825), flat but for one bin a pixel up at 447.650. The climb ended up there,
  where nothing is on the air and no saved repeater falls, so no marks showed.
- The settled-read confirm made the climb go further (9 jumps to 445.3 against 13 to 442.4) and
  sweep more. 440.925 at -97 is the DMR repeater.
- The levels can't be judged yet. Each `sel` said the bar's level when the next press came 4 s
  later, not the read that confirmed it, and not the floor it beat. A -130 on 442.075 means
  that channel had dropped by then. The build after run 2 says the confirming read and the
  floor at the jump, and drops the line for the starting bar, which caught the upload carrier.
  Run 3 can then tell whether the -108 to -116 stops are short signals or a floor read too low.

## Bench run 3: the lens scale (2026-11-09 06:40Z, hamlet, receive only)

`mod-surfauto.bin` from ac68a40 (PR #170, 5,466 B) went up without ARQ (so the K6 never keyed) on
image cf9516e vlrh: 45 XL chunks. It ran 60,060 ms, returned 46 sweeps of the +-1 MHz scale
(about 0.8 s each, waits included) and had 764 B of stack free (3,044 B at its call).

```
surf 70cm 440.850 25k
sel 440.8750 -115 -131    (8 of these to 06:41:37Z: the upload's own carrier, still on air,
                           one channel up)
sel 442.0000 -116 -131    sel 444.9500 -108 -131    sel 450.0000 -116 -131
sel 433.9750 -111 -134
bar 438.0000 1000k lens
dededddfddcccdedcccddccaaaaaaaaa
aaaaaaaadcbbbccccaaaaaaaaaaaaaa
end sw=46
```

The lens scale, the wide bins' reads and the jumps run on the radio; each stop was 15-23 dB over
the settled floor. The stack is tighter than the even scale's (run 2: 5,040 B free on the larger
EXPLORE stack) but over the 512 B the emulator rule asks.

## The bargraph as the radio's main VFO? (Jeff's thought, 2026-11-08)

It fits, and this module is the way to try it. Points for and against:

- **Listening comes first, which a VFO must do.** The module never sweeps while a signal plays
  on the bar, except for the refresh, which hold turns off. So as a VFO it behaves like a normal
  receiver with a live spectrum whenever the channel is quiet. A single-receiver radio can't
  do better. The cost is that the bars go stale during a long QSO.
- **Fit with Aura.** The pager-only and EXPLORE images already drop Aura's standby screen and
  menus, so the surf screen could be their home screen, with no change to stock Aura. As a flash
  feature it is about 3 KB of C, and the EXPLORE image has about 14 KB of flash to spare (108.9
  of 122.9 KB). It draws straight into Aura's display buffer, so the bitmap goes away. RAM is the
  64-byte bar array and about 200 B of state.
- **What a VFO adds:** PTT on the bar, which the module API can't do. TX belongs in the core
  with its band lock. The repeater table (c/k6/rt.h) gives offset and tone, so PTT on a known
  repeater's output keys its input. Frequency entry by digits and a squelch setting are the
  other additions.
- **Suggested path:** run `surf` on the radio first, so Jeff can judge the feel (sweep rate,
  blink, how stale the bars get while listening). If he likes it, move the C core into the
  EXPLORE image as its home screen, with EXPLORE's candidate list one key away and PTT through
  the repeater table. Combined with the planned page-buffer screen, it can replace the EXPLORE
  list screen.

## Files

- `c/k6/mod/surf.c`: the module; `c/k6/mod/surfauto.c`: the bench build.
- `tests/test_k6_surf.py`: the emulated runs (the lens's steps, bargraph, side keys, UP/DOWN
  moving the scale, the axis's ticks and labels, band and zoom, the band's edge, monitor, squelch hang, EXIT, saved
  repeaters from a table in the emulated flash, the bench build and its bargraph lines). `python tests/test_k6_surf.py OUT.png`
  redraws the screens above.
- `tools/k6_screen.py`: draws any level-6 module's emulated frames as the LCD shows them, and
  (run as a script) the bargraph a surfauto run said.
- `tests/test_k6_i2s.py`: `run(..., keypad=[(from_us, to_us, key)])` drives the real key matrix
  for modules that scan it.
