/* micduplex: can the UV-380 transmit the MCU's modem audio and still hear its mic? (Jeff 11-14
   09:08Z: hold PTT and stream sotto as it is encoded.) The mic reaches I2S3 only while the C6000
   transmits it (micrec_tx 08:56Z). This is micrec_tx (unplug, preamp on, 25 s, tx_mic 5 s at 1 W
   on 440.850, then the ident) with the C6000's transmit source switched every 800 ms while keyed,
   I2S3's level read for each, nothing recorded:
     set 0  E0 c1  mic (the control: the 08:56Z setting)
     set 1  E0 83  MCU line in only (silent here: the PWM isn't running)
     set 2  E0 c3  both
     set 3  E0 81  neither
     set 4  E0 c1  mic again
   If set 1 keeps set 0's level, the radio can send modem tones and hear the mic at once. Talk the
   whole time the LED is orange or red. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 2           /* the signer reads only this file */
#define MR_TX 1
#define MR_DUPLEX 1
#include "micrec.c"
