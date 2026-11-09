# 31. Let the PC stop a running module

- From: the WSPR thread, 2026-11-07 ([k6-ft8-wspr.md](../k6-ft8-wspr.md)). Not built.
- On the bench the WSPR beacon (`wsprbcn`, 6 frames over 50 minutes) could only be stopped
  by a key press on the radio, because the pager reads no control packets while a module
  runs. The bench was held for the rest of the hour. One option is a firmware `stop` flag the
  pager's receive path sets on a control packet, which modules poll with their key check
  (`api->stop()`, next to `key_down`). Another needs no firmware: a module that waits on RSSI
  (as `wsprbcn` does between frames) stops on a two-carrier pattern that a CW ID can't make,
  for example two 1 s carriers 2 s apart.
- It would buy: long-running modules (beacons, scanners, loggers) on an unattended bench.
- Settled by: `k6_bench.py stop` ending `wsprbcn` between frames without anyone at the radio.
