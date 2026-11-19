/* c6sotto: the radio's sotto message out as M17, YSF (C4FM) or DMR, through the shared HR_C6000
   core (c/common/c6_core.c) and the on-radio framers (c/common/m17_tx.c, ysf_tx.c, dmr_sotto.h +
   dmr_voice.h): step 3 of docs/uv380-multimode.md, run as a RAM module before any of it goes in
   flash. The body is c6air.h, which the image (milestone 4) runs from flash unchanged with the
   mode picked at run time; here the mode is a constant, so the other two drop out.

   SOTTO_AIR picks the mode:
     1 M17   sotto 1200 stream (TYPE stream data, META "SOTTO 1200"), two superframes a 40 ms
             frame, the physical layer mode at TX_DEV 123 % of the DMR pair (2400 Hz outer)
     2 YSF   the sotto softmodem's sotto VW frames, five superframes a 100 ms frame, FICH sotto
             flag, at 140 % (2712 Hz measured 11-18)
     3 DMR   a group voice call to TG 9 whose bursts carry dmr_sotto blocks under the voice FEC
             (c6dvtx's format, 60/60 blocks 11-17), at the DMR pair itself
   SOTTO_SRC 1 (the default) sends the radio's message (api->msg: the last recording or message
   heard, what PTT sends); 0 a test message (superframe k = bytes 6k..6k+5 counting up). The
   message is cut to what one key-up carries (tx_tone's 5 s): about 8 s of sotto as M17 or YSF,
   4 s as DMR. TX_DRY 1 (the default) runs everything with no keying.

   Keying as every proven module: api->tx_tone (the Morse ident AG7EW first, the image's transmit
   guard, 441.850 or 440.850 MHz only, the channel's power: the bench runs at power 0), the
   engine's registers put back TX_TAIL_MS before the tone ends, nothing said over USB while keyed.
   The raw mode sits about 6.7 kHz above the FM carrier: the bench sets the transmit trim first
   (uv380.cmd "trim", FM trim - 6680) and back after, as for c6m17tx and c6ysftx.

   Lines: "sotto AIR sf=N/M first=HEX last=HEX bytes=B tx_ms=T cal I= Q= -> 45= 46= dry=" (the
   first and last superframe sent, to check a capture's decode against), the keying's lines, "tx
   slots= bytes= edges= first= late= lost= chk= done=" (or for DMR "tx hdr= voice=/ term= ..."),
   "sotto done" or "sotto short", "end".
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "c6spi.h"
#include "c6slot.h"
#include "c6key.h"
#include "w25sec.h"
#include "c6vspi.h"

#ifndef SOTTO_AIR
#define SOTTO_AIR 1
#endif
#ifndef SOTTO_SRC
#define SOTTO_SRC 1
#endif
#ifndef TX_DRY
#define TX_DRY 1
#endif

#include "c6air.h"

int uv_main(const ortx_api_t *api) {
    int r = c6air_run(api, SOTTO_AIR, SOTTO_SRC, TX_DRY);
    if (r == -1)
        return -1;
    api->say("end");
    return r;
}
