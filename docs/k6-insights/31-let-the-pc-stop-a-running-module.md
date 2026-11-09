# 31. Let the PC stop a running module

- From: the WSPR thread, 2026-11-07 ([k6-ft8-wspr.md](../k6-ft8-wspr.md)). Built 2026-11-09 the
  no-firmware way (Jeff approved it in the thread clean-up): `c/k6/mod/k6_stop.h` hears two
  carriers of 2-6 s, 1-5 s apart, and `python3 tools/k6_bench.py stop` keys the IC-7100 twice
  with a PSK31 'AG7EW STOP' burst (about 3.5 s each, 2 s apart), so the signal carries the
  ident. `wsprtx`/`wsprbcn` listen for it while they wait (the sync mark and between frames)
  and return -8, "wspr stopped by the PC after N sent". Emulator-tested
  (`tests/test_k6_stop.py`); the bench check below is still to do. Other long modules can
  include the header and feed it RSSI from their wait loops.
- On the bench the WSPR beacon (`wsprbcn`, 6 frames over 50 minutes) could only be stopped
  by a key press on the radio, because the pager reads no control packets while a module
  runs. The bench was held for the rest of the hour. One option is a firmware `stop` flag the
  pager's receive path sets on a control packet, which modules poll with their key check
  (`api->stop()`, next to `key_down`). Another needs no firmware: a module that waits on RSSI
  (as `wsprbcn` does between frames) stops on a two-carrier pattern that a CW ID can't make,
  for example two 1 s carriers 2 s apart.
- It would buy: long-running modules (beacons, scanners, loggers) on an unattended bench.
- Settled by: `k6_bench.py stop` ending `wsprbcn` between frames without anyone at the radio.
