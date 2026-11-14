/* micduplex_tone: micduplex with the MCU's line in playing: the radio keys with tx_tone (a 1 kHz
   tone from the MCU through the line in, the path a sotto stream takes; level 3 image) while the
   module switches the C6000's transmit source and reads I2S3, both its level and its 1 kHz
   content:
     set 0  E0 83  line in only (the tone; is the mic on I2S3?)
     set 1  E0 c3  both (does the mic mix into what goes on air?)
     set 2  E0 c1  mic only
     set 3  E0 81  neither
     set 4  E0 83  line in again
   The IC-7100's recording shows what went on air for each. Same cues as micrec_tx: blinking,
   unplug at the radio; orange or red, talk without stopping; dark, plug back in.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 3           /* the signer reads only this file */
#define MR_TX 1
#define MR_DUPLEX 1
#define MR_TONE 1
#include "micrec.c"
