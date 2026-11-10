/* RF side of the UV-380: AT1846S (frequency, FM/DMR mode, TX/RX), HR_C6000 (FM modulator line
 * in, DAC), PA and LNA switching, the radio's own calibration from SPI flash.
 *
 * Not written yet: every call fails, so no build of this tree can key the transmitter. The
 * driver work and its bench checks (70 cm only, low power, AG7EW CW ID) come next; see
 * docs/uv380-port.md.
 *
 * SPDX-License-Identifier: MIT */
#ifndef UV380_RF_H
#define UV380_RF_H

#include <stdint.h>

/* 0 on success; -1 now (no driver). */
static inline int rf_tx_begin(uint32_t hz) {
    (void)hz;
    return -1;
}
static inline void rf_tx_end(void) {}

#endif
