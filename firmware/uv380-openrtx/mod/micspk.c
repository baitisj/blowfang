/* micspk: is the UV-380's mic live with the receiver on? The radio's own speaker is the sound
   source, so nothing wired to the bench PC is involved (micprobe2, 11-13 20:01Z: the codec input
   carries the IC-7100's audio through the shared USB ground, mic preamp on or off). With
   register 26 at 7d (the codec input open, the FM audio still on), the speaker plays the open
   receiver's hiss in 1 s bursts (AUDIO_AMP_EN PB9 on, then SPK_MUTE PB8 low after 10 ms, as
   OpenRTX does), and I2S3 is captured with the speaker off and on, the mic preamp (MIC_PWR) on
   and off, PD9 low and high. A live mic shows as more audio with the speaker on than off, with
   the preamp on only.

   Lines: as micprobe ("set K ...", "a" lines, "got K ..."), each "set" line after a
     mod say spk S mic=M pd9=P
   line; 3 rounds of 6. Receive only: touches no RF register. The speaker hisses for about 9 s
   in all, at the volume knob's level. Puts back 26, PD9, MIC_PWR, PB8, PB9 and the capture.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define MP_MS 1000u
#define MP_NO_MAIN 1
#include "micprobe.c"

#define GPIOB 0x40020400u

static void spk(const ortx_api_t *api, unsigned on) {
    if (on) {
        RXA_REG(GPIOB + 0x18u) = 1u << 9;           /* amp on */
        api->sleep_ms(10);
        RXA_REG(GPIOB + 0x18u) = 1u << (8 + 16);    /* unmute */
    } else {
        RXA_REG(GPIOB + 0x18u) = 1u << 8;           /* mute */
        RXA_REG(GPIOB + 0x18u) = 1u << (9 + 16);    /* amp off */
    }
}

int uv_main(const ortx_api_t *api) {
    char line[24 + BLK * 5u];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u)
        return -8;
    mp_t m;
    m.id = api->c6000_read(0x00);
    if (m.id == 0x00) {
        api->say("the C6000 doesn't answer: power-cycle first");
        return -1000;
    }
    for (unsigned i = 0; i < 6u; i++)
        m.e[i] = api->c6000_read((uint8_t)(0xE0u + i));
    m.pa_moder = RXA_REG(RXA_GPIOA);
    m.pa_odr = RXA_REG(RXA_GPIOA + 0x14u);
    uint32_t pb = RXA_REG(GPIOB + 0x14u);
    unsigned align = rx_start(api, &m.rs, (volatile int16_t *)mem, RING);
    m.r26 = m.rs.r26;
    m.seq = 0, m.k = 0;
    api->fmt(line, sizeof line, "micspk reg00=%02x r26=%02x pa13=%lu pb8=%lu pb9=%lu align=%u",
             m.id, m.r26, (unsigned long)((m.pa_odr >> MIC_PIN) & 1u),
             (unsigned long)((pb >> 8) & 1u), (unsigned long)((pb >> 9) & 1u), align);
    api->say(line);

    for (unsigned r = 0; r < 3u && !api->stop(); r++) {
        for (unsigned c = 0; c < 3u && !api->stop(); c++) {
            unsigned mc = c != 1u, p9 = c == 2u;
            for (unsigned s = 0; s < 2u && !api->stop(); s++) {
                spk(api, s);
                api->fmt(line, sizeof line, "spk %u mic=%u pd9=%u", s, mc, p9);
                api->say(line);
                one(api, &m, line, 0x7D, p9, mc, 0, 0);
            }
            spk(api, 0);
        }
    }

    spk(api, 0);
    RXA_REG(GPIOB + 0x18u) = (pb >> 8) & 1u ? 1u << 8 : 1u << (8 + 16);
    RXA_REG(GPIOB + 0x18u) = (pb >> 9) & 1u ? 1u << 9 : 1u << (9 + 16);
    rx_stop(api, &m.rs);
    RXA_REG(RXA_GPIOA + 0x18u) =
        (m.pa_odr >> MIC_PIN) & 1u ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
    RXA_REG(RXA_GPIOA) = m.pa_moder;
    api->fmt(line, sizeof line, "micspk end sets=%u", m.k);
    api->say(line);
    return (int)m.k;
}
