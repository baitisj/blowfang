/* fusoslot: fusotx (FuSotto from the K6) for a K6 without API level 15: the call's frames come
   ready made from library slot 15 (python3 tools/k6_fusotx.py deck SF.bit OUT.bin, installed with
   k6_upload.py OUT.bin --cards --install 15; up to 650 superframes, 26 s of speech) and nothing
   is written to SPI. A bench build: the sender's chained C4FM on the radio before the flash.
   Build: python -m modem.k6 mod out.wav c/k6/mod/fusoslot.c --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 13  /* the 15,408-byte block */
#define K6_MODULE_OPT z     /* as c4lock.c: -Oz */
#define FUSO_SLOT 15
#define K6_NAME "FUSOTTO SLOT"
#include "fusotx.c"
