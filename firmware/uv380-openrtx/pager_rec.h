/* pager_rec: the C6000 codec's input on I2S3 (SPI3 slave, DMA1 stream 0) for PAGER mode's
   digital voice key: started while the radio transmits the mic (the only time the codec takes
   it, 11-14 08:56Z), read as 8 kHz samples. The same capture as mod/rxaudio.h's rx_start_as_is,
   which it includes. On anything but the MD-UV3x0 it does nothing.
   SPDX-License-Identifier: MIT */
#ifndef PAGER_REC_H
#define PAGER_REC_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t (*c6r)(uint8_t reg);
    void (*c6w)(uint8_t reg, uint8_t val);
    void (*sleep_ms)(uint32_t ms);
} pr_host;

/* ring: n halfwords (a power of 2) in DMA-capable RAM. Returns the tries it took to start in
   step with the codec's frames (1 to 16), or 0 if none looked right (it captures anyway) */
unsigned pager_rec_start(const pr_host *h, int16_t *ring, unsigned n);
/* up to n samples into x; 0 when none are waiting (a ring overrun skips ahead and counts) */
unsigned pager_rec_take(int16_t *x, unsigned n);
/* the overruns since pager_rec_start */
unsigned pager_rec_overruns(void);
void pager_rec_stop(void);

#ifdef __cplusplus
}
#endif

#endif
