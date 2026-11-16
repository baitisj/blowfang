#!/usr/bin/env python3
"""Put the running UV-380 into TYT's flash (DFU) mode with no keys held, and reflash it hands-free.

The radio must run a blowfang OpenRTX image with the RAM module loader (BLOWFANG_I2SPROBE=1, on
the radio since 11-13). Everything goes over its USB serial (/dev/ttyACM0, 0483:5740); the
bootloader shows up as 0483:df11. How it works and why it is safe: docs/uv380-dfu.md.

    uv380_dfu.py info  /dev/ttyACM0                 bootinfo: read only (RDP, write protection,
                                                    both vector tables, bootloader literals)
    uv380_dfu.py enter /dev/ttyACM0 --dry           dfuboot's checks and handshake, no erase
    uv380_dfu.py enter /dev/ttyACM0                 dfuboot: erase sector 3, reset, wait for
                                                    0483:df11. The radio then has no application
                                                    until it is flashed (it stays in DFU at every
                                                    power-on, so retries need no hands)
    uv380_dfu.py flash /dev/ttyACM0 IMAGE_wrap.bin --sha256 HEX --radio-tool PATH
                                                    enter (unless already in DFU), radio_tool
                                                    -d 0 -f -i IMAGE, radio_tool usb-leave, wait
                                                    for the serial port and "mod info"

The flash image must be the keyed Plus build (build.sh mduv3x0p, keyed before wrapping; never a
plain mduv3x0 image on Jeff's MD-UV390 PLUS). --sha256 is required so a stale file is never sent.
Exit status 0 on success. No RF: nothing here keys the radio.
SPDX-License-Identifier: MIT"""
import argparse
import glob
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uv380_i2sprobe as probe  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
DFU_ID = ("0483", "df11")
SYSFS = "/sys/bus/usb/devices"


def dfu_devices(sysfs=SYSFS):
    """USB devices that are 0483:df11 (TYT's bootloader, or ST's ROM DFU)"""
    found = []
    for d in sorted(glob.glob(os.path.join(sysfs, "*"))):
        try:
            v = open(os.path.join(d, "idVendor")).read().strip()
            p = open(os.path.join(d, "idProduct")).read().strip()
        except OSError:
            continue
        if (v, p) == DFU_ID:
            found.append(os.path.basename(d))
    return found


def wait_for(cond, seconds, step=0.25):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if cond():
            return True
        time.sleep(step)
    return cond()


def handshake(dev, img, dry=False, out=print, log=None, timeout=15.0):
    """load img (signed dfuboot), run it, answer its nonce. -> "dry", "erasing", or an error
    string ("refused ...", "cancelled", "timeout", ...)"""
    import termios
    import tty
    lines = probe.mod_lines(img)
    if probe.command(dev, lines[:-1], log, out=probe.quiet(out), stop_on_err=True):
        return "upload failed"
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
    g = open(log, "ab") if log else None
    try:
        try:
            tty.setraw(fd)
            at = termios.tcgetattr(fd)
            at[6][termios.VMIN], at[6][termios.VTIME] = 0, 2
            termios.tcsetattr(fd, termios.TCSANOW, at)
        except termios.error:
            pass
        os.write(fd, b"mod run\n")
        buf, t0 = b"", time.time()
        while time.time() - t0 < timeout:
            try:
                b = os.read(fd, 512)
            except OSError:             # the port went away: the radio reset
                return "gone"
            if g and b:
                g.write(b)
                g.flush()
            buf += b
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                ln = ln.decode(errors="replace").strip()
                if not ln:
                    continue
                out(ln)
                w = ln.split()
                if ln.startswith("mod say dfuboot armed nonce="):
                    nonce = w[4].split("=", 1)[1]
                    os.write(fd, f"{'dry' if dry else 'erase'} {nonce}\n".encode())
                elif ln.startswith("mod say dfuboot erasing"):
                    return "erasing"
                elif ln.startswith("mod say dfuboot dry"):
                    return "dry"
                elif ln.startswith("mod say dfuboot "):
                    return " ".join(w[3:])
                elif ln.startswith(("ok mod run", "err mod run", "err busy")):
                    return ln
        return "timeout"
    finally:
        os.close(fd)
        if g:
            g.close()


def enter(dev, key_file, dry=False, log=None, wait=20.0, out=print, sysfs=SYSFS):
    if dfu_devices(sysfs):
        out("already in DFU: " + " ".join(dfu_devices(sysfs)))
        return 0
    img = probe.module_image(os.path.join(MOD, "dfuboot.c"), key_file)
    r = handshake(dev, img, dry, out, log)
    if dry:
        out(f"dry run: {r}")
        return 0 if r == "dry" else 1
    if r not in ("erasing", "gone"):
        out(f"not entered: {r} (nothing was written)")
        return 1
    t0 = time.time()
    if not wait_for(lambda: bool(dfu_devices(sysfs)), wait):
        out(f"no 0483:df11 after {wait:.0f} s: sector 3 may be blank with the bootloader not in DFU;"
            " power-cycle the radio (it should come up in DFU with no keys), else the usual PTT +"
            " top side key at power-on")
        return 2
    out(f"in DFU after {time.time() - t0:.1f} s: " + " ".join(dfu_devices(sysfs)))
    return 0


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def flash(dev, image, want_sha, radio_tool, key_file, log=None, wait_acm=40.0, out=print, sysfs=SYSFS,
          run=subprocess.run):
    got = sha256(image)
    if got != want_sha.lower():
        out(f"refused: {image} sha256 {got}, not {want_sha}")
        return 1
    r = enter(dev, key_file, log=log, out=out, sysfs=sysfs)
    if r:
        return r
    n = dfu_devices(sysfs)
    if len(n) != 1:
        out(f"refused: {len(n)} DFU devices ({' '.join(n)}); radio_tool -d 0 needs exactly one")
        return 1
    out(f"flashing {image}")
    p = run([radio_tool, "-d", "0", "-f", "-i", image])
    if p.returncode:
        out(f"radio_tool flash failed ({p.returncode}); the radio stays in DFU, run this again")
        return 3
    out("usb-leave")
    run([radio_tool, "usb-leave"])
    if not wait_for(lambda: os.path.exists(dev) and not dfu_devices(sysfs), wait_acm):
        out(f"{dev} not back after {wait_acm:.0f} s (still DFU: {' '.join(dfu_devices(sysfs)) or 'no'});"
            " try radio_tool usb-leave again, else power-cycle")
        return 4
    time.sleep(1.0)
    bad = probe.command(dev, ["mod info"], log, out=out)
    return 0 if not bad else 5


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("info", "enter", "flash"):
        q = sub.add_parser(name)
        q.add_argument("dev")
        q.add_argument("--key-file", default="~/.config/sotto/k6-mod.key")
        q.add_argument("--log")
        if name == "enter":
            q.add_argument("--dry", action="store_true", help="checks and handshake, no erase")
            q.add_argument("--wait", type=float, default=20.0)
        if name == "flash":
            q.add_argument("image", help="the keyed Plus _wrap.bin")
            q.add_argument("--sha256", required=True)
            q.add_argument("--radio-tool", required=True)
            q.add_argument("--wait-acm", type=float, default=40.0)
    a = ap.parse_args(argv)
    if a.cmd == "info":
        img = probe.module_image(os.path.join(MOD, "bootinfo.c"), a.key_file)
        return 1 if probe.command(a.dev, probe.mod_lines(img), a.log, out=probe.quiet(),
                                  stop_on_err=True) else 0
    if a.cmd == "enter":
        return enter(a.dev, a.key_file, a.dry, a.log, a.wait)
    return flash(a.dev, a.image, a.sha256, a.radio_tool, a.key_file, a.log, a.wait_acm)


if __name__ == "__main__":
    sys.exit(main())
