# 43. Move the CW tone bank down to 400-1300 Hz

- From: the CW workbench, 2026-11-08. A real ID on 443.525 at 07:37:06Z ("...7 RPT", about
  400 Hz, 20-25 wpm, sent under voice) is below the 600-1350 Hz bank (c/k6/tonedet.h), so
  the K6 model locked onto voice at 1200 Hz and read noise.
- On the replay bench (`tools/k6_cwbench.py --td-lo 400 --td-step 60`, the five real ID
  clips): WA7DMR and VE7RVA still decode, and the 443.525 ID locks at 400 Hz and reads
  "EEZTRPT", as close as the ideal decoder gets ("E EEZERPT E"; the call is lost under the
  voice). 400/50 holds better in crowded audio (`--share 0.2`) but stops at 1150 Hz.
- Cost: the 7-bit coefficients bunch up near 128 at the low end (400 and 460 Hz are 122 and
  121), so the detector's "which tone" is coarse there; the keying bit is what decodes.
- Settled 2026-11-08 08:20Z: `mod-cwlisten_lo.bin` read the IC-7100's 'AG7EW TEST' at 400 and
  1250 Hz with q 99-100 (800 Hz was spoilt by a squelch tail, now dropped by the decoder).
  Built: tonedet.h has the 400-1300 Hz bank (docs/k6-cwid.md).
