#!/usr/bin/env python3
"""rt950_ble_bridge.py: the RT-950 Pro's Bluetooth link as a serial port, so rt950_backup.py can
read a radio that has no programming cable (Stage A of docs/rt950-backup.md).

The radio's BLE module is a transparent bridge to the MCU's USART1, and the stock firmware takes
CPS sessions there (it is how Radtel's phone app programs the radio). This connects, runs the
module's authentication, opens a pseudo-terminal and forwards bytes both ways, writes paced to
the module's UART rate because it drops what it can't buffer. Adapted from jaymart1983's
tools/ble_serial_bridge.py and firmware_upload_ble.py (GPL-3; docs/bluetooth.md there).

The authentication's byte 4 picks the module's UART rate for the connection, and the MCU can't
follow a change, so it must match the firmware's USART1. Measured by jaymart1983: selectors 0, 5
and 8 give 9600 (the module's default, which the stock firmware is built for), 2 gives 115200
(their firmware), 1 3 4 7 pass nothing on theirs. Jeff's RT-950 Pro on stock firmware
(2026-11-16) answered CPS on 2 only: 5 and 0 authenticated but never ACKed the handshake. So the
default here is 2. Nothing it sends writes to the
radio: the authentication is the module's per-connection setup, and the bytes it forwards are
whatever the tool on the pty sends (rt950_backup.py only reads).

  rt950_ble_bridge.py scan                       list nearby BLE devices with the module's services
  rt950_ble_bridge.py bridge ADDRESS PTYLINK [--seconds 1800] [--sel 2]
      PTYLINK becomes a symlink to the pty; then:  rt950_backup.py read PTYLINK OUT --addr 2
Needs bleak and a running BlueZ. SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import asyncio
import os
import pty
import sys
import termios
import tty

UUID_DATA_PREFERRED = "0000ff32-0000-1000-8000-00805f9b34fb"
UUID_DATA_FALLBACK = "0000ffe1-0000-1000-8000-00805f9b34fb"
UUID_CONTROL = "0000ff31-0000-1000-8000-00805f9b34fb"
CHUNK = 20                      # bytes per BLE write: larger chunks overrun the module
SELECTORS = (                   # every value byte 4 can take; it only sets the module's UART rate
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
)


def pace_s(sel):
    """seconds between 20-byte writes for the UART rate the selector gives, with 25 % spare"""
    baud = 115200 if sel == 2 else 9600
    return CHUNK * 10 / baud * 1.25


def auth_serial(sel):
    """the 20-byte serial the module expects: 0x3F x4, the baud selector, then 15 values 0-98"""
    s = bytearray(20)
    s[0:4] = b"\x3f\x3f\x3f\x3f"
    s[4] = sel
    for i in range(5, 20):
        s[i] = (i * 7) % 99
    return bytes(s)


async def scan(seconds=8.0):
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    for addr, (dev, adv) in sorted(found.items(), key=lambda kv: -kv[1][1].rssi):
        uu = " ".join(u[4:8] for u in adv.service_uuids)
        mark = "  <- has the module's services" if any(x in uu for x in ("ff31", "ff32", "ffe0", "ffe1")) else ""
        print(f"{addr}  {adv.rssi:4d} dBm  {dev.name or adv.local_name or '?':24s} {uu}{mark}")


async def authenticate(client, sel):
    got = bytearray()

    def on_notify(_s, data):
        got.extend(data)

    await client.start_notify(UUID_DATA_FALLBACK, on_notify)
    await client.write_gatt_char(UUID_CONTROL, auth_serial(sel), response=True)
    for _ in range(30):
        await asyncio.sleep(0.1)
        if len(got) >= 20:
            break
    await client.stop_notify(UUID_DATA_FALLBACK)
    ok = len(got) >= 5 and all(b == 0x21 for b in got[0:5])
    print(f"authentication {'ok' if ok else 'unclear: ' + bytes(got).hex()} (selector {sel})", flush=True)
    return ok


async def bridge(address, link, seconds, sel):
    from bleak import BleakClient
    master, slave = pty.openpty()
    for fd in (master, slave):       # raw both ends: no echo, no newline translation
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    os.set_blocking(master, False)
    if os.path.lexists(link):
        os.unlink(link)
    os.symlink(os.ttyname(slave), link)
    print(f"serial port: {link} -> {os.ttyname(slave)}", flush=True)
    pace = pace_s(sel)
    try:
        async with BleakClient(address, timeout=20.0) as client:
            uuids = {c.uuid.lower() for s in client.services for c in s.characteristics}
            data_uuid = UUID_DATA_PREFERRED if UUID_DATA_PREFERRED in uuids else UUID_DATA_FALLBACK
            await authenticate(client, sel)
            await client.start_notify(data_uuid, lambda _s, d: os.write(master, bytes(d)))
            print(f"bridging on {data_uuid[4:8]}, {pace * 1000:.0f} ms per {CHUNK} bytes", flush=True)
            loop = asyncio.get_event_loop()
            deadline = loop.time() + seconds
            while loop.time() < deadline and client.is_connected:
                try:
                    data = os.read(master, 4096)
                except BlockingIOError:
                    await asyncio.sleep(0.003)
                    continue
                for i in range(0, len(data), CHUNK):
                    await client.write_gatt_char(data_uuid, data[i:i + CHUNK], response=False)
                    await asyncio.sleep(pace)
            print("disconnected" if not client.is_connected else "time limit reached", flush=True)
    finally:
        if os.path.islink(link):
            os.unlink(link)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("--seconds", type=float, default=8.0)
    p = sub.add_parser("bridge")
    p.add_argument("address")
    p.add_argument("link")
    p.add_argument("--seconds", type=float, default=1800.0)
    p.add_argument("--sel", type=int, default=2, choices=SELECTORS)
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        asyncio.run(scan(a.seconds))
    else:
        asyncio.run(bridge(a.address, a.link, a.seconds, a.sel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
