/* c4fifo: a timing probe for c4lock (docs/k6-c4fm.md). Since 11-18 04:24Z the K6's MCU clock
   measures 850 ppm off the chip (run 4: 520) and the TX-done fit leaves 0.38-symbol residuals
   (run 4: 0.05), so whole YSF calls slip. This asks how the MCU's clock moves against the chip's
   inside one packet and from packet to packet, with chip-timed marks mid-packet: the FSK modem's
   FIFO almost-empty flag (REG_3F bit 14, REG_02 bit 14; threshold REG_5E<9:3>, P_T words). NP
   packets of 128 words of PN signs at the outer gain, 1.5 s of carrier between; each starts with
   40 words in the FIFO, and each flag refills 2 words, so the flag comes back every 32 chip
   symbols (the 0x5E write is put back on exit, with the rest of c4lock's registers). No YSF.
   It says, after the key-up,
     K6 MOD say c4fifo p=J x=... m0=... e=...
     K6 MOD say c4fifo p=J n=... bad=... b=... r=...
                         packet J's enable (MCU cycles at 96 MHz), its first mark and TX done
                         against it, the marks, flags with neither bit (or past NM), the REG_02
                         bits seen, -9 if a flag never came
     K6 MOD say c4fifo dJ.K d d d ...   mark K on: its time less the first mark's and K x 32
                         symbols at C4_PPM, in 16-cycle units (a straight line: the rate; bends:
                         the MCU's clock moving inside the packet)
   then c4lock's w= line, AG7EW in MCW, and returns NP. Host side:
   python3 -m modem.k6c4fm fifo k6.log.
   Build: python -m modem.k6 mod out.wav c/k6/mod/c4fifo.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define C4_FIFOPROBE 1
#define C4_WAIT_MS 600000   /* armed up to 10 minutes: it keys after the job's trigger carrier */
#define K6_NAME "C4FM FIFO"
#include "c4lock.c"
