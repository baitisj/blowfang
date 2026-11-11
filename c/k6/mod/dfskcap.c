/* dfskcap: what does the FD6818's plain FSK receiver (REG_58 RX mode 0) make of Bell 202 audio?

   The chip's 1200/2400 Hz receiver reads flat Bell 202 but no packet with a sender's
   pre-emphasis and none with noise (docs/k6-aprs-clock.md, the fourth bench run). Its plain FSK
   receiver slices the audio itself: probe 2's one capture at 4800 baud (REG_58 = 00E9), read
   again, shows 2200 Hz as alternating bits (a transition every sample) and 1200 Hz as 0011 (one
   every second sample), on a time base within 1% of 4800 a second. Runs of two and more mark
   bits read clean; a lone mark bit comes as one missing transition or two, and a first decoder
   gets 85% of the bits. A slicer of the audio's sign does not care which tone is the louder,
   which is what the 1200/2400 receiver trips on. But a packet of the chip's is 256 bytes, 0.43 s
   at 4800 baud, and an APRS packet is a second.

   This module records, for the host to read (python3 -m modem.aprs dfsklog LOG): words of that
   receiver on the bench's rehearsal of real packets (python3 -m modem.aprs wav OUT.wav --now
   --kinds --repeat N). It asks:
     - does REG_5D take a length over 256 bytes (its bits 7:5, as on a BK4819)? w= says: 128
       words for 256 bytes (427 ms at 4800 baud), 1024 for 2048 (3413 ms).
     - which sync word starts it on a plain packet: AAAA (16 samples of 2200 Hz, in an opening of
       flags whose long tone is the space) or 6666 (of 1200 Hz)?
     - what do the words look like for a pre-emphasised, a weak and a noisy packet?
     - is 6000 baud (five samples a bit) a better sampler than 4800, and does the receiver start
       there at all (sync words: E739 = 1200 Hz, D2DA = 2200 Hz as a true sampler would read
       them)?
   The settings, one after the other from WAIT_MS (32 s) after the start, each for its time:
      0  00e9  4800 baud  sync aaaa  REG_5D ffe0   45 s   the main one
      1  00e9  4800       aaaa       ff00           8 s   probe 2's length
      2  00e9  4800       6666       ffe0          15 s
      3  03e9  4800       aaaa       ffe0          15 s   RX gain bits
      4  00e9  6000       e739       ffe0          20 s
      5  00e9  6000       d2da       ffe0          20 s
      6  00e9  4800       aaaa       ffe0          15 s   REG_2B + 0100 (de-emphasis off)
   The FIFO is read by time, a word every 16 bits of the setting's baud, three words behind the
   chip (REG_5E: almost full at 6, and then a word more is read). A word unlike the one before
   starts a capture (turning the modem on raises the sync flag by itself, and an empty FIFO
   gives its last word again); it ends when STALE reads in a row gave the same word (the FIFO
   is empty: the chip's packet is over), at CAPW words or after 5 s. Then the modem is turned
   off and on for the next. RX-done in REG_02 is noted and not believed: the first bench run
   (2026-11-10 12:00Z) had it 1 ms after every capture's first word, whatever REG_5D said,
   with the chip's words going on. Lines:
     dfskcap start hz=440850000 n=7
     s 0 t=1234 cfg=00e9 b=c195 sy=aaaa l=ffe0      a setting begins
     c0 3 t=12345 dn=1 dl=3413 w=1024 i=3002 f=2 r=-21
                                        setting, capture, ms at its first word, ms from there
                                        to the first RX-done flag and to the last (0: none),
                                        words (without the stale ones at the end), REG_02's
                                        flags seen, extra reads for almost-full, RSSI
     d0 3 000 aaaaaaaa5555...           its words, 12 a line, as on the air (first bit first);
                                        the offset counts words, in hex
     v0 n=9 w=9288                      at the setting's end: captures, words
     dfskcap end n=31
   Only the first CAPN captures of a setting are said. Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E,
   72, 2B, 3F and 02, put back as read. Receive only. Returns the captures, or -3 (old image),
   -8 (scratch). Any key ends it.

   DFSK_ACARS (dfskcap_acars.c): the same recorder on a real ACARS channel, 131.550 MHz, receive
   only (docs/k6-acars.md). A block opens with 53 ms of 2400 Hz, which at 4800 baud is the sync
   word AAAA, and a bit is two samples. Four front ends, FE_S seconds each, all 4800 baud and
   512 bytes a packet (REG_5D = ff20; a long block is 500):
      0  FM, wide, as tuned               does the receiver hear an AM signal at all?
      1  REG_47's AF source = AM (7), wide    does it follow the AF source?
      2  FM, narrow, tuned 2.5 kHz high   an AM signal with a sideband cut is partly FM
      3  AF source AM, narrow
   DFSK_NARROW (the 6 m bench build, docs/k6-acars.md): in the narrow filter the receiver
   starts on an AM block's pre-key whatever REG_47's AF source is (the slicer is the FM
   discriminator's; the filter turns a little of the AM into FM), reads nothing of its data,
   and is deaf 500 Hz and more off tune. So: eight front ends, all narrow and FM, the first
   seven with the AFC off (REG_73 bit 4; on, it pulls the carrier to the centre) and tuned
   300, 200 and 100 Hz low, as tuned, 100, 200 and 300 Hz high; the last as tuned with the AFC
   on. Is there a place where the data's samples fall on both sides of the slicer?
   DFSK_ZIF (the 6 m bench build's last question): the chip's "USB" mode is REG_3D = 0, a zero
   IF where both sidebands fold onto each other (double sideband), with REG_47's AF source 5.
   Does the plain receiver get anything of an AM signal there? Eight front ends, AFC off:
      0  zero IF, AF source 5 (DSB), wide, as tuned
      1  the same, narrow
      2  zero IF, AF source 5, wide, 2.4 kHz high   the carrier then beats at the pre-key's tone
      3  zero IF, AF source 5, wide, 1.0 kHz high
      4  zero IF, AF source 4 (raw), wide
      5  zero IF, AF source 7 (AM), wide
      6  zero IF, AF source as found (FM), wide     the zero IF alone
      7  the IF as found, FM, narrow                the control: the sixth run's front end 3
   The s line ends with if= (REG_3D read back, and a ! if the tuning had put it back and it
   was written again with a restart of the receiver).
   DFSK_SWEEP (dfskcap_acarsb.c in FM on 440.850 MHz; with DFSK_ZIF the 6 m build): the seventh
   6 m run's words fit a receiver that sees the audio's derivative (its sign turns over at the
   audio's peaks: 1, 1.5 or 2 samples of 4800 apart in ACARS) and whose clock starts again at
   every turn, so it gives each interval a whole number of samples by itself. At 4800 baud 1.5
   comes as one sample, like 1, and the block cannot be told; a clock a little faster should
   make it two. Eight front ends, the same receiver at 5200, 5400, 5550, 5700, 5850, 6000,
   6150 and 6300 baud (the pre-key stays the sync word AAAA: a sample an interval). The FIFO
   is read at 4800 baud's pace all the same, which is the slowest the chip makes words on a
   block when 1.5 is two samples, and the almost-full reads make up the rest.
   What the bench said (2026-11-11, docs/k6-acars.md): no. The chip's clock locks onto the
   signal's own 4800 turns a second at every one of these settings, and an interval of 1.5
   comes as one sample or as two by what went before; no block reads.
   DFSK_SETS (dfskcap_acarsc.c in FM on 440.850 MHz; with DFSK_ZIF at the zero IF): the
   settings not yet tried, sixteen of FE_S seconds each. The first eight ask a new question.
   The audio's slope at a bit's edge IS the ACARS data bit (a bit of 1200 Hz turns it over,
   one of 2400 Hz turns it twice), and the slope is what this receiver slices: at 2400 baud,
   one sample a bit, a clock that settles between the turns reads the block's bytes as they
   are, no tones to tell. The 1200 Hz turns sit on its cell edges and the 2400 Hz ones a
   quarter either side, so a clock that is pulled gently holds there; one that starts again
   at every turn does not. The sync word is then SYN SYN as it comes (6868, low bit first),
   or "+*" before it (d554), each the other way up too (the slope's sense is not known):
      0..3   00e9 at 2400 baud, sync 6868, 9797, d554, 2aab
      4, 5   00a9 at 2400, 6868 and 9797   REG_58's bits 7:6 = 10 (the 1200/2400 Hz receiver
                                           slices better with them)
      6, 7   00e5 at 2400, 6868 and 9797   its RX bandwidth bits 3:1 = 2 (e9 has 4)
   The rest, with the pre-key as the sync word as before: does any give an interval of 1.5
   its own sample count every time, or show the slicer the audio and not its slope?
      8      00e9 at REG_72 = ffff   the fastest clock there is (6348 baud)
      9      00e9 at 4800 baud       the control
      10..12 00e1 00e5 00ed at 4800  RX bandwidth bits 0, 2, 6
      13, 14 00a9 0069 at 4800       bits 7:6 = 10, 01
      15     04e9 at 4800            RX mode bits 12:10 = 1 (0 is this receiver, 4 and 7 the
                                     tone receivers)
   DFSK_TONE (dfskcap_acarsd.c in FM on 440.850 MHz; with DFSK_ZIF at the zero IF): not the
   plain receiver but the chip's 1200/2400 Hz tone receiver (REG_58<12:10> = 4), whose format
   ACARS is but for the framing. acarsfsk asked it for SYN SYN as its sync word, in FM, and
   got no sync. Here the pre-key is the sync word (0000 as tone bits, ffff the other way up:
   a steady 2400 Hz), so the chip hands over whatever its tone receiver makes of the rest of
   the block, unframed, and the host looks for the block in it (python3 -m modem.acars tone
   LOG). Sixteen settings of FE_S seconds; a setting has its own REG_5B and REG_59 (the s
   line: sy= with eight digits for a sync word of four bytes, c= REG_59's low byte, a= the
   bits set in REG_2B's 10:8, no l=):
      0, 1   33a9 at 2400 baud, sync 0000, ffff   the tone bits as they come (512 bytes)
      2, 3   33a9 at 2400, sync 5c5c, 3ffe 5c5c   SYN SYN, and "+*" before it (128 bytes)
      4      33a9 at 2400, sync 076b 3e36         the K units: the chip's own kind of opening
      5, 6   as 2 and 4 with REG_59<7:4> = 0      the shortest preamble (f: sixteen bytes)
      7, 8   33e9 at 2400, sync 0000, 076b 3e36   REG_58's bits 7:6 = 11
      9      33a3 at 2400, sync 0000              the 1200/1800 Hz bandwidth (aprsfsk's)
      10     3329 at 2400, sync 0000              bits 7:6 = 00
      11     33a9 at 2400, sync 0000, REG_2B + 0100   the AF de-emphasis off (it takes 6 dB
                                                  off 2400 Hz against 1200 Hz)
      12     33a9 at 2400, sync 0000, REG_2B + 0700   and the 300 Hz high-pass and the 3 kHz
                                                  low-pass off
      13     33a9 at 4800 baud, sync 0000         two samples a bit: where the tones change
      14, 15 33a3 33a9 at 1200 baud, sync 076b 3e36   the control: the L units (a cycle of
                                                  1200 Hz or two of 2400 Hz a bit)
   What the bench said (2026-11-11 04:44Z, docs/k6-acars.md): with the pre-key as its sync
   word the receiver starts on every unit and gives its tone bits; with SYN SYN or the
   pager's word it never starts. At 2400 baud a tone must last a cycle of 1200 Hz to be
   told, so a lone bit of 1200 Hz is lost; at 4800 baud a lone bit shows as one sample.
   DFSK_TONE 2 (dfskcap_acarse.c) follows that up, every setting with the sync word 0000:
      0..3   33a9 at 4800, 5400, 6000 baud and REG_72 = ffff (6348)   more samples a bit
      4, 5   33a3 at 4800 and 6000 baud       the 1200/1800 Hz bandwidth (the cleanest at 2400)
      6..8   33e9 3369 3329 at 4800           bits 7:6 = 11, 01, 00 (33a9 has 10)
      9..12  33a1 33a5 33ab 33ad at 4800      RX bandwidth bits 3:1 = 0, 2, 5, 6
      13, 14 3fa9 37a9 at 4800                RX mode 7 (the 1200/1800 Hz receiver) and 5
      15     33a9 at 4800, REG_2B + 0100      the AF de-emphasis off
   A capture of these ends when the FIFO is empty (the words of eight reads before come
   again for 53 ms), and its c line's k= counts its words unlike the first: one with none is
   not said. The receiver is off while a capture's words are said.
   Noise gives a sync word now and then, so every capture gets its c line (with the RSSI and
   k=, how many of its first four words are AAAA or 5555: the rest of a pre-key) and only those
   with k of 3 or 4 their d lines. And is there a signal at all? The RSSI is read every 50 ms:
   a rise of BURST_UP half dB over the front end's floor (its lowest, let up one step every
   2 s) is a carrier, said when it ends (not inside a capture) as
     b1 3 t=91230 ms=450 r=-84          front end, burst, ms at its start, its length, its peak
   and the v line ends with b= (bursts), p= (the highest RSSI) and f= (the floor). Before the
   first front end a q line every 5 s has the same p= and f= (a steady carrier that comes on
   in the wait shows as a step there and nowhere else). DFSK_DUMP_ALL says every capture's
   words, whatever its k.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"

#ifndef PROBE_HZ
#ifdef DFSK_ACARS
#define PROBE_HZ 131550000u
#else
#define PROBE_HZ 440850000u
#endif
#endif
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u /* ms a unit of a setting's time (the emulated test shortens it) */
#endif
#ifndef WAIT_MS
#define WAIT_MS 32000u   /* before the first setting: the module starts about 29 s before its own
                            upload's audio ends, and that is no Bell 202 */
#endif
#ifdef DFSK_ACARS
#if defined(DFSK_SETS) || defined(DFSK_TONE)
#define NV 16
#elif defined(DFSK_NARROW) || defined(DFSK_ZIF) || defined(DFSK_SWEEP)
#define NV 8
#else
#define NV 4
#endif
#ifndef FE_S
#define FE_S 150
#endif
#define BURST_UP 20u     /* half dB over the floor: a carrier */
#define BURST_SKIP 300u  /* ms after a front end's start with no RSSI taken (the retune) */
#define BURSTN 30u       /* bursts said per front end */
#ifndef QN
#define QN 100u          /* RSSI readings (50 ms each) a q line before the first front end */
#endif
#ifdef DFSK_DUMP_ALL
#define DUMP_K 0u        /* every capture's words (the bench's AM test) */
#else
#define DUMP_K 3u        /* only those that go on like a pre-key */
#endif
#else
#define NV 7
#endif
#define CAPW 1056u       /* words kept of a capture: 2048 bytes, the stale reads, and a few */
#define CAPN 12u         /* captures said per setting */
#define LAG 3u           /* words the timed reads stay behind the chip (its FIFO holds 8) */
#ifdef DFSK_TONE
#define STALE 40u        /* (a K unit's opening is 24 words of one kind at 4800 baud) */
#else
#define STALE 24u        /* reads of the same word in a row: the FIFO is empty (80 ms at 4800 baud;
                            no packet holds a tone that long) */
#endif
#define CAP_MS 5000u     /* a capture's longest */

#define IRQ_DONE 0x2000u
#define IRQ_FULL 0x1000u
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u
#define CTRL 0x00F0u     /* a sync word of two bytes */

typedef struct {
  uint16_t cfg, baud, sync, len, af;
  uint16_t wticks3; /* three word times in 100 us ticks: 100 at 4800 baud, 80 at 6000, 200 at 2400 */
  uint16_t secs;
#ifdef DFSK_ACARS
  int8_t off, wide, src; /* tuning offset in 100 Hz; the wide filter (bit 0), the AFC off (bit 1,
                            DFSK_NARROW and DFSK_ZIF) and the zero IF (bit 2, DFSK_ZIF); REG_47's
                            AF source (0: as found) */
#endif
#ifdef DFSK_TONE
  uint16_t sync2, ctrl;  /* REG_5B (a sync word of four bytes: REG_59 bit 3); REG_59 */
#endif
} var_t;

#ifdef DFSK_ZIF
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47, 0x73, 0x3D};
#define SAVED_47 10
#define SAVED_3D 12
#elif defined(DFSK_NARROW)
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47, 0x73};
#define SAVED_47 10
#elif defined(DFSK_ACARS)
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47};
#define SAVED_47 10
#else
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F};
#endif
#define SAVED_2B 8

#if defined(DFSK_SWEEP) || defined(DFSK_SETS) || defined(DFSK_TONE)
/* REG_72 for a baud: 10.32444 a baud, rounded (C195 at 4800, F1FB at 6000) */
#define B72(baud) ((uint16_t)(((baud) * 1032444ull + 50000ull) / 100000ull))
#ifdef DFSK_ZIF
#define SWEEP_FE 7, 5 /* the zero IF, the AFC off, wide; AF source 5 */
#else
#define SWEEP_FE 1, 0 /* FM as found, wide */
#endif
#if defined(DFSK_TONE) && DFSK_TONE == 2
/* a setting of the tone receiver with the pre-key as its sync word, 512 bytes a capture */
#define TV(cfg, baud, af, w3) {cfg, baud, 0x0000, 0xFF20, af, w3, FE_S, 0, SWEEP_FE, 0x0000, 0xF0}
static const var_t V[NV] = {
    TV(0x33A9, 0xC195, 0, 100),
    TV(0x33A9, B72(5400u), 0, 89),
    TV(0x33A9, B72(6000u), 0, 80),
    TV(0x33A9, 0xFFFF, 0, 76),
    TV(0x33A3, 0xC195, 0, 100),
    TV(0x33A3, B72(6000u), 0, 80),
    TV(0x33E9, 0xC195, 0, 100),
    TV(0x3369, 0xC195, 0, 100),
    TV(0x3329, 0xC195, 0, 100),
    TV(0x33A1, 0xC195, 0, 100),
    TV(0x33A5, 0xC195, 0, 100),
    TV(0x33AB, 0xC195, 0, 100),
    TV(0x33AD, 0xC195, 0, 100),
    TV(0x3FA9, 0xC195, 0, 100),
    TV(0x37A9, 0xC195, 0, 100),
    TV(0x33A9, 0xC195, 0x0100, 100),
};
#elif defined(DFSK_TONE)
static const var_t V[NV] = {
    {0x33A9, B72(2400u), 0x0000, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE, 0x0000, 0xF0},
    {0x33A9, B72(2400u), 0xFFFF, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE, 0xFFFF, 0xF0},
    {0x33A9, B72(2400u), 0x5C5C, 0x7F00, 0, 200, FE_S, 0, SWEEP_FE, 0x5C5C, 0xF0},
    {0x33A9, B72(2400u), 0x3FFE, 0x7F00, 0, 200, FE_S, 0, SWEEP_FE, 0x5C5C, 0xF8},
    {0x33A9, B72(2400u), 0x076B, 0x7F00, 0, 200, FE_S, 0, SWEEP_FE, 0x3E36, 0xF8},
    {0x33A9, B72(2400u), 0x5C5C, 0x7F00, 0, 200, FE_S, 0, SWEEP_FE, 0x5C5C, 0x00},
    {0x33A9, B72(2400u), 0x076B, 0x7F00, 0, 200, FE_S, 0, SWEEP_FE, 0x3E36, 0x08},
    {0x33E9, B72(2400u), 0x0000, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE, 0x0000, 0xF0},
    {0x33E9, B72(2400u), 0x076B, 0x7F00, 0, 200, FE_S, 0, SWEEP_FE, 0x3E36, 0xF8},
    {0x33A3, B72(2400u), 0x0000, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE, 0x0000, 0xF0},
    {0x3329, B72(2400u), 0x0000, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE, 0x0000, 0xF0},
    {0x33A9, B72(2400u), 0x0000, 0xFF20, 0x0100, 200, FE_S, 0, SWEEP_FE, 0x0000, 0xF0},
    {0x33A9, B72(2400u), 0x0000, 0xFF20, 0x0700, 200, FE_S, 0, SWEEP_FE, 0x0000, 0xF0},
    {0x33A9, 0xC195, 0x0000, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE, 0x0000, 0xF0},
    {0x33A3, B72(1200u), 0x076B, 0x7F00, 0, 400, FE_S, 0, SWEEP_FE, 0x3E36, 0xF8},
    {0x33A9, B72(1200u), 0x076B, 0x7F00, 0, 400, FE_S, 0, SWEEP_FE, 0x3E36, 0xF8},
};
#elif defined(DFSK_SETS)
static const var_t V[NV] = {
    {0x00E9, B72(2400u), 0x6868, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(2400u), 0x9797, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(2400u), 0xD554, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(2400u), 0x2AAB, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00A9, B72(2400u), 0x6868, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00A9, B72(2400u), 0x9797, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00E5, B72(2400u), 0x6868, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00E5, B72(2400u), 0x9797, 0xFF20, 0, 200, FE_S, 0, SWEEP_FE},
    {0x00E9, 0xFFFF, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E1, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E5, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00ED, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00A9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x0069, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x04E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
};
#else
static const var_t V[NV] = {
    {0x00E9, B72(5200u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(5400u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(5550u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(5700u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(5850u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(6000u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(6150u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
    {0x00E9, B72(6300u), 0xAAAA, 0xFF20, 0, 100, FE_S, 0, SWEEP_FE},
};
#endif
#elif defined(DFSK_ZIF)
static const var_t V[NV] = {
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 7, 5},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 6, 5},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 24, 7, 5},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 10, 7, 5},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 7, 4},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 7, 7},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 7, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 2, 0},
};
#elif defined(DFSK_NARROW)
static const var_t V[NV] = {
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, -3, 2, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, -2, 2, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, -1, 2, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 2, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 1, 2, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 2, 2, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 3, 2, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 0, 0},
};
#elif defined(DFSK_ACARS)
static const var_t V[NV] = {
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 1, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 1, 7},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 25, 0, 0},
    {0x00E9, 0xC195, 0xAAAA, 0xFF20, 0, 100, FE_S, 0, 0, 7},
};
#else
static const var_t V[NV] = {
    {0x00E9, 0xC195, 0xAAAA, 0xFFE0, 0, 100, 45},
    {0x00E9, 0xC195, 0xAAAA, 0xFF00, 0, 100, 8},
    {0x00E9, 0xC195, 0x6666, 0xFFE0, 0, 100, 15},
    {0x03E9, 0xC195, 0xAAAA, 0xFFE0, 0, 100, 15},
    {0x00E9, 0xF1FB, 0xE739, 0xFFE0, 0, 80, 20},
    {0x00E9, 0xF1FB, 0xD2DA, 0xFFE0, 0, 80, 20},
    {0x00E9, 0xC195, 0xAAAA, 0xFFE0, 0x0100, 100, 15},
};
#endif

typedef struct {
  uint16_t cap[CAPW];
} st_t;

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  if (!d) return 0;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}

static char *dec(char *p, uint32_t v) {
  char t[10];
  int n = 0;
  do {
    uint32_t q = udiv(v, 10);
    t[n++] = (char)('0' + v - 10 * q);
    v = q;
  } while (v);
  while (n) *p++ = t[--n];
  return p;
}

static char *hexn(char *p, uint32_t v, int digits) {
  int i;
  for (i = 4 * (digits - 1); i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }
static char *kx(char *p, const char *k, uint32_t v) { return hexn(put(p, k), v, 4); }

#ifdef DFSK_TONE
#define ARM(api, v) arm(api, (v)->ctrl)
#define SYNC2(v) ((v)->sync2)
static void arm(const k6_api_t *api, uint32_t ctrl) {
  api->fd_write(0x59, (uint16_t)(ctrl | CTRL_CLR));
  api->fd_write(0x59, (uint16_t)(ctrl | CTRL_EN));
  api->fd_write(0x02, 0);
}
#else
#define ARM(api, v) arm(api)
#define SYNC2(v) ((v)->sync)
static void arm(const k6_api_t *api) {
  api->fd_write(0x59, CTRL | CTRL_CLR);
  api->fd_write(0x59, CTRL | CTRL_EN);
  api->fd_write(0x02, 0);
}
#endif

/* the modem off and on with this setting, armed twice (a new sync word alone does not take
   hold, and arming alone does not end a packet the chip is in); -> the FIFO's stale last word */
static uint32_t seek(const k6_api_t *api, const var_t *v, uint32_t af) {
  uint32_t i, w = 0;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x2B, (uint16_t)af | v->af);
  api->fd_write(0x5A, v->sync);
  api->fd_write(0x5B, SYNC2(v));
  api->fd_write(0x58, v->cfg);
  api->fd_write(0x72, v->baud);
  api->fd_write(0x5C, 0x5625); /* no CRC of the chip's */
  api->fd_write(0x5D, v->len);
  api->fd_write(0x5E, 0x0206); /* RX almost full at 6 words */
  ARM(api, v);
  api->delay_us(2000);
  ARM(api, v);
  for (i = 0; i < 8; i++) w = api->fd_read(0x5F);
  return (w & 0xFF) << 8 | w >> 8;
}

/* a setting begins: its s line, the modem set; -> the FIFO's stale last word */
#ifdef DFSK_ZIF
#define BEGIN(c) begin(api, c, af, saved[SAVED_3D])
static uint32_t begin(const k6_api_t *api, int cur, uint32_t af, uint32_t if0) {
#else
#define BEGIN(c) begin(api, c, af)
static uint32_t begin(const k6_api_t *api, int cur, uint32_t af) {
#endif
  const var_t *v = &V[cur];
  char ln[64], *p = ln;
#ifdef DFSK_ZIF
  int again = 0;
#endif
#ifdef DFSK_ACARS
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
#if defined(DFSK_NARROW) || defined(DFSK_ZIF)
  /* REG_73 bit 4: the AFC off (or it pulls the carrier back to the centre in half a minute),
     or on. It takes hold at the receiver's restart, which the tuning is */
  api->fd_write(0x73, (uint16_t)((api->fd_read(0x73) & ~0x10u) | (v->wide & 2 ? 0x10u : 0)));
#endif
#ifdef DFSK_ZIF
  api->fd_write(0x3D, (uint16_t)(v->wide & 4 ? 0 : if0)); /* the IF: zero, or as found */
#endif
  api->tune((uint32_t)((int32_t)PROBE_HZ + 100 * v->off), v->wide & 1);
#ifdef DFSK_ZIF
  /* should the tuning have put the IF back: once more, and the receiver off and on (REG_30)
     for it to take hold, as the firmwares do after REG_38/39 */
  if (api->fd_read(0x3D) != (uint16_t)(v->wide & 4 ? 0 : if0)) {
    uint16_t r30 = api->fd_read(0x30);
    api->fd_write(0x3D, (uint16_t)(v->wide & 4 ? 0 : if0));
    api->fd_write(0x30, 0);
    api->fd_write(0x30, r30);
    again = 1;
  }
#endif
  api->fd_write(0x47, (uint16_t)(v->src ? ((af >> 16) & 0xF0FF) | (uint32_t)v->src << 8 : af >> 16));
#endif
  *p++ = 's', *p++ = ' ', p = dec(p, (uint32_t)cur);
  p = kv(p, " t=", api->ms());
  p = kx(p, " cfg=", v->cfg);
  p = kx(p, " b=", v->baud);
  p = kx(p, " sy=", v->sync);
#ifdef DFSK_TONE
  if (v->ctrl & 8) p = hexn(p, v->sync2, 4);
  p = hexn(put(p, " c="), v->ctrl, 2);
  p = hexn(put(p, " a="), v->af >> 8, 1);
#else
  p = kx(p, " l=", v->len);
#endif
#ifdef DFSK_ZIF
  p = kx(p, " if=", api->fd_read(0x3D));
  if (again) *p++ = ' ', *p++ = '!';
#endif
  *p = 0;
  api->say(ln);
  return seek(api, v, af);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now = 0, tkey = 0, t0, tcap = 0, dn = 0, prev, total = 0, af;
  uint32_t got = 0, dl = 0, same = 0, irq = 0, nfull = 0, ncap = 0, nw = 0, rssi = 0, tlast;
#ifdef DFSK_ACARS
  uint32_t ndump = 0, rmin = 0x1FF, rup = 0, rpk = 0, bon = 0, bt = 0, bms = 0, bpk = 0, nb = 0;
#endif
  int32_t acc = 0;
  uint16_t saved[sizeof(SAVED)];
  int cur = 0, in = 0, key = 0;
  char ln[64], *p;
  const var_t *v = &V[0];
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(SAVED); i++) saved[i] = api->fd_read(SAVED[i]);
  af = saved[SAVED_2B];
#ifdef DFSK_ACARS
  af |= (uint32_t)saved[SAVED_47] << 16; /* begin() wants REG_47 as found too */
#endif
  api->tune(PROBE_HZ, 1);
  p = kv(ln, "dfskcap start hz=", PROBE_HZ);
  p = kv(p, " n=", NV);
  *p = 0;
  api->say(ln);
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
#ifdef DFSK_ACARS
    /* the channel before the first front end, every QN readings: the floor, and a carrier's step */
    i = api->fd_read(0x67) & 0x1FF;
    if (i < rmin) rmin = i;
    if (i > rpk) rpk = i;
    if (++rup >= QN) {
      p = kv(ln, "q t=", api->ms());
      p = kv(p, " p=-", (uint32_t)-KM_RSSI_DBM(rpk));
      p = kv(p, " f=-", (uint32_t)-KM_RSSI_DBM(rmin));
      *p = 0;
      api->say(ln);
      rmin = 0x1FF, rpk = 0, rup = 0;
    }
#endif
  }
#ifdef DFSK_ACARS
  rmin = 0x1FF, rpk = 0, rup = 0;
#endif
  prev = BEGIN(cur);
  t0 = api->ms(), tlast = api->ticks() & 0xFFFF;
  for (;;) {
    uint32_t f, tk, n = 0, over = 0;
    now = api->ms();
    if (key) break;
    if (now - tkey >= 50) {
      tkey = now;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key) break;
#ifdef DFSK_ACARS
      if (now - t0 >= BURST_SKIP) {
        uint32_t r = api->fd_read(0x67) & 0x1FF;
        if (r < rmin) rmin = r, rup = 0;
        else if (++rup >= 40) rup = 0, rmin++;
        if (r > rpk) rpk = r;
        if (bon < 2 && r >= rmin + BURST_UP) {
          if (!bon) bon = 1, bt = now, bpk = 0;
          if (r > bpk) bpk = r;
        } else if (bon == 1) {
          bon = 2, bms = now - bt;
        }
      }
      if (bon == 2 && !in) {
        bon = 0;
        if (++nb <= BURSTN) {
          p = ln, *p++ = 'b', p = dec(p, (uint32_t)cur), *p++ = ' ', p = dec(p, nb);
          p = kv(p, " t=", bt);
          p = kv(p, " ms=", bms);
          p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(bpk));
          *p = 0;
          api->say(ln);
        }
      }
#endif
    }
    if (!in && now - t0 >= v->secs * SLOT_SCALE) {
      p = ln, *p++ = 'v', p = dec(p, (uint32_t)cur);
      p = kv(p, " n=", ncap);
      p = kv(p, " w=", nw);
#ifdef DFSK_ACARS
      p = kv(p, " b=", nb + (bon != 0));
      p = kv(p, " p=-", (uint32_t)-KM_RSSI_DBM(rpk));
      p = kv(p, " f=-", (uint32_t)-KM_RSSI_DBM(rmin));
      ndump = 0, rmin = 0x1FF, rup = 0, rpk = 0, bon = 0, nb = 0;
#endif
      *p = 0;
      api->say(ln);
      total += ncap, ncap = nw = 0;
      if (++cur == NV) break;
      v = &V[cur];
      prev = BEGIN(cur);
      t0 = api->ms(), acc = 0, tlast = api->ticks() & 0xFFFF;
      continue;
    }
    f = api->fd_read(0x02) & (IRQ_DONE | IRQ_FULL | 2u);
    if (f) api->fd_write(0x02, 0);
    tk = api->ticks() & 0xFFFF;
    acc += (int32_t)(((tk - tlast) & 0xFFFF) * 3), tlast = tk;
    while (acc >= (int32_t)v->wticks3 && n < 8) acc -= (int32_t)v->wticks3, n++;
    if (in) {
      irq |= f;
      if ((f & IRQ_FULL) && n < 8) n++, nfull++;
      if (f & IRQ_DONE) {
        dl = now - tcap + !(now - tcap);
        if (!dn) dn = dl;
      }
    }
    while (n && !over) {
      uint32_t w = api->fd_read(0x5F), x = (w & 0xFF) << 8 | w >> 8;
      n--;
      if (!in) {
        if (x == prev) continue;
        /* the first word of a packet: from here the reads stay LAG words behind the chip */
        in = 1, got = 0, dn = 0, dl = 0, same = 0, irq = f, nfull = 0, tcap = now;
        rssi = api->fd_read(0x67) & 0x1FF;
        acc = -(int32_t)(LAG * v->wticks3), n = 0;
      } else {
        same = x == prev ? same + 1 : 0;
      }
      prev = x;
      if (got < CAPW) s->cap[got] = (uint16_t)x;
      got++;
      if (same >= STALE) got -= STALE, over = 1;
      else if (got >= CAPW) over = 1;
#ifdef DFSK_TONE
      /* the tone receiver's words need not stand still in silence, so the FIFO is empty
         when the last words are those of eight reads before again (which is what an empty
         read gives), for 53 ms: 16 words at 2400 baud. A pre-key is 8 words there, a K
         unit's opening 12 */
      else {
        uint32_t m = udiv(3200, v->wticks3);
        if (got >= m + 8) {
          for (i = 1; i <= m && s->cap[got - i] == s->cap[got - i - 8]; i++) {}
          if (i > m) got -= m, over = 1;
        }
      }
#endif
    }
    if (in && now - tcap >= CAP_MS) over = 1;
    if (in && over) {
      ncap++, nw += got;
#ifdef DFSK_ACARS
#ifdef DFSK_TONE
      api->fd_write(0x59, 0); /* the receiver off while the words are said (512 bytes take a while) */
      /* k: words unlike the capture's first (0: a steady tone or silence, and not said) */
      for (i = 1, irq = 0; i < got && i < CAPW; i++) irq += s->cap[i] != s->cap[0];
#else
      /* a pre-key goes on after the sync word: AAAA or 5555 in the first words */
      for (i = 0, irq = 0; i < 4 && i < got; i++) irq += s->cap[i] == 0xAAAAu || s->cap[i] == 0x5555u;
#endif
      p = ln, *p++ = 'c', p = dec(p, (uint32_t)cur), *p++ = ' ', p = dec(p, ncap);
      p = kv(p, " t=", tcap);
      p = kv(p, " w=", got);
      p = kv(p, " k=", irq);
      p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(rssi));
      *p = 0;
      api->say(ln);
#ifdef DFSK_TONE
      if (irq && ndump < CAPN) {
#else
      if (irq >= DUMP_K && ndump < (irq >= 3 ? 3 * CAPN : CAPN)) { /* a pre-key's: three times as many */
#endif
        ndump++;
#else
      if (ncap <= CAPN) {
        p = ln, *p++ = 'c', p = dec(p, (uint32_t)cur), *p++ = ' ', p = dec(p, ncap);
        p = kv(p, " t=", tcap);
        p = kv(p, " dn=", dn);
        p = kv(p, " dl=", dl);
        p = kv(p, " w=", got);
        p = kx(p, " i=", irq);
        p = kv(p, " f=", nfull);
        p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(rssi));
        *p = 0;
        api->say(ln);
#endif
        for (i = 0; i < got; i++) {
          uint32_t col = i - 12 * udiv(i, 12);
          if (!col) {
            p = ln, *p++ = 'd', p = dec(p, (uint32_t)cur), *p++ = ' ', p = dec(p, ncap);
            *p++ = ' ', p = hexn(p, i, 3), *p++ = ' ';
          }
          p = hexn(p, s->cap[i], 4);
          if (col == 11 || i + 1 == got) {
            *p = 0;
            api->say(ln);
          }
        }
      }
      prev = seek(api, v, af);
      in = 0, acc = 0, tlast = api->ticks() & 0xFFFF;
    }
    api->delay_us(300);
  }
  for (i = 0; i < sizeof(SAVED); i++) api->fd_write(SAVED[i], saved[i]);
#ifdef DFSK_ZIF
  api->tune(PROBE_HZ, 1); /* the IF and the AFC as found take hold at a restart */
#endif
  api->fd_write(0x02, 0);
  if (cur < NV) total += ncap; /* a key, mid-setting */
  p = kv(ln, "dfskcap end n=", total);
  *p = 0;
  api->say(ln);
  return (int)total;
}
