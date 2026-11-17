/* txreq: ask for one send of the radio's stored message, as a PTT press sends it (the Morse ident
   first), once this module has ended: the bench runner's uv380.send step (tools/bench_runner.py).
   The image sends only on 441.850 or 440.850 MHz (SEND_HZ, SEND_K6_HZ) and at the channel's
   power; the runner reads back power 0 and the channel before it sends this.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 2           /* tx_request: level 2 */

int uv_main(const ortx_api_t *api) {
    int r = api->tx_request();
    api->say(r == 0 ? "txreq: send requested" : "txreq: send refused (channel, or TX off)");
    return r == 0 ? 0x55 : 0x5e;
}
