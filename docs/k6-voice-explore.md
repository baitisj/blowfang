# One K6 image: 8 kHz voice + EXPLORE

Jeff, 2026-11-09: the bench switches between the voice image and the EXPLORE image too often,
so the target is one image that does both. He picked the 8 kHz voice (v3.7, sotto 1200, 600 and
400, as `aura-pager-vlr`) over the hiss image, which has no room at all.

`firmware/k6-aura/build.sh` builds it as `aura-voice-explore` (Aura feature `voice-explore`:
EXPLORE with the voice library, the voice images' 11,776-byte shared block and the shared page
buffer). Until it fits, it's a size report only: `out/size-aura-voice-explore.txt` says how
much flash and RAM are still to find, and no `.bin` is written.

## Where it stood (2026-11-09, local clang 18 build; CI's is about 1.1 KB smaller)

- Flash 143,420 of 122,880 B: about 20.5 KB over (with FX_MP_ROWGEN, blowfang #21, and the SPI
  module library, #22).
- Static RAM 18,208 of 20,440 B: 2,232 B left for the stack against the 4,588 B the deepest
  call (a voice playback) needs.
- Without sotto 600 / 400 (sotto 1200 only): 139,404 B, 4 KB less.

## The plan

Each a separate PR; the report line shows what's left after each.

RAM:
- xdmr's hot path copied to SRAM (1,940 B of `.data`, blowfang #19) goes: it didn't help (the
  slow FD6818 reads were flash prefetch off under txdog, explorer PRs #20 / #23).
- ifpager's `run` frame (svc_t + ifp_t, 2,736 B on the stack) into the shared block, which is
  free while a coded 4FSK session runs.

Flash:
- EXPLORE's sweep (xscan, 6.1 KB) and listen (xlisten + xdmr, 5.9 KB) as SPI pages: kept in the
  module library (k6_lib.h) and read into the shared block when EXPLORE starts that step (about
  16 ms for 11.7 KB). The window is what EXPLORE's own data (the store and 1,280 B of the ring)
  leaves: about 6.4 KB, so the CW ID decoder (cwid, 2.9 KB), the repeater table (rt, 3.2 KB) and
  explore.c stay in flash. About 10 KB net.
- the decoder's 2 KB `fx_cs512` table, and EXPLORE's serial dump and trace lines: about 3 KB.
- the compiler thread's savings: Arm Toolchain for Embedded with -Oz (blowfang #24, 2-2.7 KB),
  an address-only panic report (about 3 KB), a compact memcpy (about 1.5 KB).

Open: EXPLORE's sweep holds the PWM ring while it draws, and the shared page buffer lives in the
ring, so the sweep needs its ring elsewhere (or drawn between blocks) before this image can run.
