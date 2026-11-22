#!/usr/bin/env python3
"""pluto_fw: the Pluto's boot image and runtime FPGA image, for the earshot channelizer (Jeff, 11-22).

    pluto_fw.py doctor                       what hamlet has (dfu-util, ssh) and what the Pluto runs
    pluto_fw.py info                         firmware version, DMA reservation, PL state, fit_size
    pluto_fw.py backup OUTDIR                every flash partition over DFU, then boot normally
    pluto_fw.py ramboot IMAGE.itb            boot a pinned FIT from RAM (flash untouched; a power
                                             cycle or reboot goes back to the flash image)
    pluto_fw.py fpga BIT CHANCTL ENSURE LOAD load earshot's tested channelizer into the PL at
                                             runtime with earshot's own loader (ENSURE, LOAD =
                                             fpga/sw/ensure_pluto_fpga.sh, pluto_runtime_load.sh)
    pluto_fw.py reboot                       reboot the Pluto (stock PL again), wait for it
    pluto_fw.py flash IMAGE.itb BACKUPDIR    write a pinned FIT to the firmware partition, read it
                                             back and compare, then boot it (bench_runner: the
                                             pluto.flash step, a local submit only, never the inbox)

Only images whose sha256 is pinned below go onto the Pluto: a queued job names the files, but
root on the Pluto can key its transmitter, so only reviewed bytes run there (same rule as
tools/pluto_chan/allowed.sha256). The FITs are ADI's release pluto.dfu files with the DFU suffix
cut off, and the same FITs with earshot's 64 MiB DMA reservation added to the three device trees
(github.com/coryking/earshot fpga/sw/make_reserved_stock_itb.py, which changes nothing else and
checks the kernel, ramdisk and stock PL hashes). earshot is GPL-3.0+; this file only runs its
loader scripts as separate programs. Everything here holds the bench's Pluto lock.

Standard library only (plus the bench's own k6_bench / pluto_bench). SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

# sha256 -> (name, what it may be used for). FIT = the release pluto.dfu minus its 16-byte suffix.
FITS = {
    "3fa622a1725a98a88640d28eaa61e8edb594bfd1534ea6cf184c8c8bd57ab2eb": ("v0.39", "stock"),
    "aacf6e68ecf244ef571f9db9d491e161c6200651255210c0ea70f4e85b4d5279": ("v0.39+dma", "reserved"),
    "29507d125e9739390f106808117b1d7aebe080ee584c854f7d2e4785307e8ce2": ("v0.37", "stock"),
    "b3ae872c10413f654990c7baa2bf74a476ce422973e1632a22ecf6887f377ecc": ("v0.37+dma", "reserved"),
}
FAMILY = {"v0.39": "v0.39", "v0.39+dma": "v0.39", "v0.37": "v0.37", "v0.37+dma": "v0.37"}
# earshot release pluto-chan-8cea8ae, chanctl built from earshot 8a3f459 (fpga/sw/build.sh: zig cc
# 0.15.2, arm-linux-musleabihf, static), and its two loader scripts at 8a3f459
EARSHOT = {
    "bit": "23632b191f56efce8593764c0f8a6858cd273208f30a7dd344888e978b54e7c9",
    "chanctl": "06b725538ed9df147fe4fd7dbd6df8cb6857871544258de310828422845a60e5",
    "ensure": "faa3514271e1ca3371d7b79b830911f0674d517a2e97a67d1052df84191fb8d5",   # fpga/sw/ensure_pluto_fpga.sh
    "load": "e48abd7dabe5a5a5cbdcc45b5f76b0185b5480face9ef67230d7c401c812e017",     # fpga/sw/pluto_runtime_load.sh
}
EARSHOT_SCRIPTS = {
    "ensure": "ensure_pluto_fpga.sh",
    "load": "pluto_runtime_load.sh",
}
DFU_IDS = "0456:b673,0456:b674"
DFU_ALTS = ("boot", "uboot-env", "uboot-extra-env", "spare", "firmware")
HOST = os.environ.get("PLUTO_HOST", "192.168.2.1")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pinned_fit(path, uses):
    h = sha(path)
    if h not in FITS or FITS[h][1] not in uses:
        raise SystemExit(f"{os.path.basename(path)} (sha256 {h[:16]}...) is not a pinned {'/'.join(uses)} FIT")
    return FITS[h][0], h


def pinned(path, key):
    h = sha(path)
    if h != EARSHOT[key]:
        raise SystemExit(f"{os.path.basename(path)}: sha256 {h[:16]}... is not the pinned {key} ({EARSHOT[key][:16]}...)")
    return h


def dfu_file(itb, out):
    """the FIT plus the DFU suffix ADI's pluto.dfu carries (bcdDevice ffff, b673, 0456, DFU 1.0, CRC)"""
    body = open(itb, "rb").read()
    suf = struct.pack("<HHHH3sB", 0xFFFF, 0xB673, 0x0456, 0x0100, b"UFD", 16)
    crc = zlib.crc32(body + suf) ^ 0xFFFFFFFF
    with open(out, "wb") as f:
        f.write(body + suf + struct.pack("<I", crc))
    return out


def ssh(remote, timeout=30, stdin=None):
    import pluto_bench as pb
    r = pb.ssh_run("ip:" + HOST, remote, stdin=stdin, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).decode(errors="replace").strip()


def wait_ssh(seconds=150):
    t = time.time() + seconds
    while time.time() < t:
        try:
            rc, _ = ssh("true", timeout=8)
            if rc == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(3)
    return False


def dfu(*args, timeout=600):
    cmd = ["dfu-util", "-d", DFU_IDS] + list(args)
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out = (r.stdout + r.stderr).decode(errors="replace")
    tail = "\n".join(out.strip().splitlines()[-6:])
    print(tail, flush=True)
    return r.returncode, out


def wait_dfu(alt="firmware.dfu", seconds=40):
    t = time.time() + seconds
    while time.time() < t:
        r = subprocess.run(["dfu-util", "-l", "-d", DFU_IDS], capture_output=True, timeout=20)
        out = (r.stdout + r.stderr).decode(errors="replace")
        if f'name="{alt}"' in out:
            return out
        time.sleep(1)
    raise SystemExit(f"no DFU device with {alt} after {seconds} s (dfu-util -l: {out.strip()[-300:]})")


def need(tool):
    if not shutil.which(tool):
        raise SystemExit(f"{tool} is not installed on this host (Debian/Ubuntu: sudo apt install {tool})")


INFO = ("echo fw=$(cat /opt/VERSIONS 2>/dev/null | grep -m1 -i 'firmware\\|plutosdr' | tr -s ' ');"
        " echo model=$(cat /sys/firmware/devicetree/base/model 2>/dev/null | tr -d '\\0');"
        " echo reserved=$(ls /proc/device-tree/reserved-memory/ 2>/dev/null | tr '\\n' ' ');"
        " echo fpga=$(cat /sys/class/fpga_manager/fpga0/state 2>/dev/null);"
        " echo pl_is_chan=$(test -e /tmp/pl_is_chan && echo yes || echo no);"
        " echo $(fw_printenv fit_size 2>/dev/null); echo $(fw_printenv attr_name attr_val 2>/dev/null | tr '\\n' ' ');"
        " echo uptime=$(cut -d' ' -f1 /proc/uptime); grep -i 'System RAM' /proc/iomem | tr '\\n' ' '")
CLEAN = "killall pluto_chan iio_readdev pluto-fpga-source 2>/dev/null; true"


def info():
    rc, out = ssh(INFO)
    print(out)
    return out


def cmd_doctor(a):
    for t in ("dfu-util", "ssh", "scp", "sshpass", "flock", "python3"):
        print(f"{t:10s} {shutil.which(t) or 'MISSING'}")
    if shutil.which("dfu-util"):
        print(subprocess.run(["dfu-util", "-V"], capture_output=True).stdout.decode(errors="replace").splitlines()[0])
    print("ssh:", "ok" if wait_ssh(15) else "no answer from root@" + HOST)
    info()


def cmd_info(a):
    info()


def reboot_into(mode):
    """device_reboot sf (flash DFU) or ram (RAM DFU); the Pluto drops off USB Ethernet"""
    ssh(CLEAN, timeout=20)
    try:
        ssh(f"sync; /usr/sbin/device_reboot {mode}", timeout=15)
    except subprocess.TimeoutExpired:
        pass
    return wait_dfu()


def cmd_backup(a):
    import k6_bench as kb
    need("dfu-util")
    os.makedirs(a.out, exist_ok=True)
    meta = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "before": None, "parts": {}}
    with kb.ptt_lock():
        meta["before"] = info()
        listing = reboot_into("sf")
        with open(os.path.join(a.out, "dfu-list.txt"), "w") as f:
            f.write(listing)
        try:
            for alt in DFU_ALTS:
                if f'name="{alt}.dfu"' not in listing:
                    continue
                p = os.path.join(a.out, f"{alt}.bin")
                if os.path.exists(p):
                    os.unlink(p)            # dfu-util -U refuses an existing file
                rc, _ = dfu("-a", f"{alt}.dfu", "-U", p)
                if rc or not os.path.exists(p):
                    raise SystemExit(f"backup: reading {alt}.dfu failed (rc {rc})")
                meta["parts"][alt] = {"bytes": os.path.getsize(p), "sha256": sha(p)}
        finally:
            dfu("-a", "firmware.dfu", "-e", timeout=60)
        if not wait_ssh():
            raise SystemExit("backup: the Pluto did not come back on ssh after DFU (replug it)")
    fw = os.path.join(a.out, "firmware.bin")
    d = open(fw, "rb").read(1 << 26)
    if d[:4] == b"\xd0\x0d\xfe\xed":
        n = int.from_bytes(d[4:8], "big")
        itb = os.path.join(a.out, "firmware-original.itb")
        with open(itb, "wb") as f:
            f.write(d[:n])
        h = sha(itb)
        meta["fit"] = {"bytes": n, "sha256": h, "known": FITS.get(h, ("unknown", ""))[0]}
    with open(os.path.join(a.out, "backup.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps({k: v for k, v in meta.items() if k != "before"}, indent=1))
    print("backup: done; keep these private (uboot-env holds the unit's settings)")


def cmd_ramboot(a):
    import k6_bench as kb
    need("dfu-util")
    name, h = pinned_fit(a.image, ("stock", "reserved"))
    with tempfile.TemporaryDirectory(prefix="pluto_fw_") as td, kb.ptt_lock():
        img = dfu_file(a.image, os.path.join(td, "ram.dfu"))
        print(f"ramboot: {name} ({h[:16]}...) into RAM; flash untouched")
        reboot_into("ram")
        rc, _ = dfu("-a", "firmware.dfu", "-D", img)
        dfu("-a", "firmware.dfu", "-e", timeout=60)
        if rc:
            raise SystemExit(f"ramboot: download failed (rc {rc}); power-cycle the Pluto to get the flash image back")
        if not wait_ssh(180):
            raise SystemExit("ramboot: no ssh after the RAM boot; power-cycle the Pluto (flash is unchanged)")
        time.sleep(5)
        out = info()
    if "+dma" in name and "dma_probe@4000000" not in out:
        raise SystemExit("ramboot: booted, but the DMA reservation is missing")
    print(f"ramboot: {name} running from RAM")


def sshpass_shim(td):
    """earshot's scripts call `sshpass -e ssh|scp ...`; without sshpass, hand ssh the password the
    way pluto_bench does (SSH_ASKPASS), from $SSHPASS"""
    if shutil.which("sshpass"):
        return None
    ask = os.path.join(td, "askpass")
    with open(ask, "w") as f:
        f.write('#!/bin/sh\necho "$SSHPASS"\n')
    sp = os.path.join(td, "sshpass")
    with open(sp, "w") as f:
        f.write('#!/bin/sh\n[ "$1" = -e ] && shift\n'
                f'SSH_ASKPASS={ask} SSH_ASKPASS_REQUIRE=force DISPLAY=${{DISPLAY:-:0}} exec setsid -w "$@" < /dev/null\n')
    os.chmod(ask, 0o700)
    os.chmod(sp, 0o755)
    return td


def cmd_fpga(a):
    import k6_bench as kb
    pinned(a.bit, "bit")
    pinned(a.chanctl, "chanctl")
    pinned(a.ensure, "ensure")
    pinned(a.load, "load")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("fpga: stopped (SIGTERM)"))
    with tempfile.TemporaryDirectory(prefix="pluto_fw_") as td, kb.ptt_lock():
        sw = os.path.join(td, "sw")
        os.makedirs(sw)
        for src, name in ((a.ensure, EARSHOT_SCRIPTS["ensure"]), (a.load, EARSHOT_SCRIPTS["load"])):
            shutil.copy(src, os.path.join(sw, name))
            os.chmod(os.path.join(sw, name), 0o755)
        bit = shutil.copy(a.bit, os.path.join(td, "system_top.bit.bin"))
        tool = shutil.copy(a.chanctl, os.path.join(td, "chanctl"))
        env = dict(os.environ, PLUTO=f"root@{HOST}", PLUTO_SSH_PASSWORD=os.environ.get("PLUTO_PASS", "analog"))
        os.makedirs(os.path.join(td, "bin"))
        shim = sshpass_shim(os.path.join(td, "bin"))
        if shim:
            env["PATH"] = shim + os.pathsep + env.get("PATH", "")
        ssh(CLEAN, timeout=20)
        r = subprocess.run(["bash", os.path.join(sw, EARSHOT_SCRIPTS["ensure"]), bit, tool], env=env, timeout=600)
        out = info()
    if r.returncode:
        raise SystemExit(f"fpga: earshot's loader exited {r.returncode} (a reboot gives the stock PL back)")
    print("fpga: channelizer loaded" + (" (pl_is_chan=yes)" if "pl_is_chan=yes" in out else ""))


def cmd_reboot(a):
    import k6_bench as kb
    with kb.ptt_lock():
        ssh(CLEAN, timeout=20)
        try:
            ssh("sync; reboot", timeout=10)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(15)
        if not wait_ssh(180):
            raise SystemExit("reboot: no ssh after 3 min (replug the Pluto)")
        time.sleep(5)
        info()


def cmd_flash(a):
    """write a pinned FIT of the same release as the backed-up flash image (a reserved image of the
    stock release on it, or the stock image back), padded to the backed-up FIT length so U-Boot's
    fit_size still fits; read back and compare before booting it"""
    import k6_bench as kb
    need("dfu-util")
    name, h = pinned_fit(a.image, ("stock", "reserved"))
    meta = json.load(open(os.path.join(a.backup, "backup.json")))
    fit = meta.get("fit") or {}
    have = fit.get("known", "unknown")
    if have not in FAMILY or FAMILY[have] != FAMILY[name]:
        raise SystemExit(f"flash: the backup's flash image is {have}; only a {FAMILY.get(have, '?')} image may go on")
    for alt in ("boot", "uboot-env", "firmware"):
        if alt not in meta.get("parts", {}):
            raise SystemExit(f"flash: the backup has no {alt}.bin")
    body = open(a.image, "rb").read()
    if len(body) > fit["bytes"]:
        raise SystemExit(f"flash: {len(body)} bytes is longer than the backed-up FIT ({fit['bytes']})")
    body += bytes(fit["bytes"] - len(body))
    with tempfile.TemporaryDirectory(prefix="pluto_fw_") as td, kb.ptt_lock():
        itb = os.path.join(td, "flash.itb")
        with open(itb, "wb") as f:
            f.write(body)
        img = dfu_file(itb, os.path.join(td, "flash.dfu"))
        print(f"flash: {name} ({h[:16]}...), {len(body)} bytes, over {have}")
        reboot_into("sf")
        rb = os.path.join(td, "readback.bin")
        rc, _ = dfu("-a", "firmware.dfu", "-D", img)
        if rc:
            raise SystemExit(f"flash: download failed (rc {rc}); the Pluto stays in DFU: rerun flash, or flash "
                             f"{os.path.join(a.backup, 'firmware-original.itb')} back")
        rc, _ = dfu("-a", "firmware.dfu", "-U", rb)
        got = open(rb, "rb").read(len(body)) if os.path.exists(rb) else b""
        if got != body:
            raise SystemExit("flash: READBACK DIFFERS; the Pluto stays in DFU: rerun flash, or flash the "
                             "backup's firmware-original.itb back")
        print("flash: readback matches")
        dfu("-a", "firmware.dfu", "-e", timeout=60)
        if not wait_ssh(180):
            raise SystemExit("flash: written and verified, but no ssh after boot (replug; DFU recovery in RECOVERY)")
        time.sleep(5)
        info()
    print(f"flash: {name} on flash and booted")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tools/pluto_fw.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as", dest="as_who", metavar="WHO", help="the bench user (set by bench_runner)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    p = sub.add_parser("backup")
    p.add_argument("out")
    p.set_defaults(fn=cmd_backup)
    p = sub.add_parser("ramboot")
    p.add_argument("image")
    p.set_defaults(fn=cmd_ramboot)
    p = sub.add_parser("fpga")
    p.add_argument("bit")
    p.add_argument("chanctl")
    p.add_argument("ensure")
    p.add_argument("load")
    p.set_defaults(fn=cmd_fpga)
    sub.add_parser("reboot").set_defaults(fn=cmd_reboot)
    p = sub.add_parser("flash")
    p.add_argument("image")
    p.add_argument("backup")
    p.set_defaults(fn=cmd_flash)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
