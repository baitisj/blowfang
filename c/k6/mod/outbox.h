/* outbox.h: the FuSotto outbox's layout in the K6's SPI flash, KM_OB_LO .. KM_OB_HI (API level 15,
   k6_mod.h), shared by the module that records into it (fmsotto.c) and the one that sends it
   (fusotx.c); tools/k6_fusotx.py writes the same bytes on the PC.
     0x190000  the recording's header, 16 bytes, written last: words "K6OB", 1 | rate << 8 (rate:
               KM_SOTTO_1200), superframes, the superframes' byte sum
     0x190100  the superframes, 6 bytes each, up to OB_PL (2,645: 105 s)
     0x194000  the sender's frames (OB_PL): words "K6OP", frames, superframes sent, the byte sum of
               the groups; from OB_PL + OB_GRP the frames in groups of four (a chip packet's): the
               four frames' signs (60 bytes each, as the FSK FIFO sends them, MSB first, 1 =
               +deviation), then their sizes (1 = outer); a short last group padded with zeros.
               101 groups fit: 404 frames, a header, 402 VW frames of five superframes (2,010:
               80.4 s), a terminator.
   SPDX-License-Identifier: MIT */
#ifndef K6_OUTBOX_H
#define K6_OUTBOX_H

#define OB_MAGIC 0x424F364Bu        /* "K6OB" */
#define OB_DATA 256u                /* the superframes, from KM_OB_LO */
#define OB_SF 6u                    /* bytes a superframe (sotto 1200, 40 ms) */
#define OB_PL 0x194000u             /* the sender's frames */
#define OB_REC_CAP (OB_PL - KM_OB_LO - OB_DATA)
#define OP_MAGIC 0x504F364Bu        /* "K6OP" */
#define OB_GRP 256u                 /* the groups, from the frames' header */
#define OB_GFRAMES 4u               /* frames a group */
#define OB_GBYTES (OB_GFRAMES * 120u)
#define OB_GROUPS ((KM_OB_HI - OB_PL - OB_GRP) / OB_GBYTES)
#define OB_SF_MAX ((OB_GROUPS * OB_GFRAMES - 2u) * 5u)

#endif
