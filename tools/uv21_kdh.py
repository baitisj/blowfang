#!/usr/bin/env python3
"""The Baofeng UV-21 Pro's KDH bootloader and the one-page dumper (radios/uv21/dumper).

  uv21_kdh.py probe --port P --out RUN [--wait-boot-s S [--boot-baud 57600]]
      bootloader handshake only (the radio must be in flash mode: both side keys held at power-on,
      or --wait-boot-s: command 42 every 50 ms while someone switches the radio off and on; the
      UV-21 Pro's key is not the 5RM's two side keys, see radios/uv21/README.md).
      Nothing is erased or written; a power cycle without the keys boots the stock firmware.
      --boot-baud: an app on the radio that resets on command 42 but listens at another speed
      (UV-21 port 2 ran at 57,600): each command 42 goes out at that speed too, then at 115200
      for the bootloader it resets into.

  uv21_kdh.py flash --port P --out RUN --image IMG --sha256 HEX [--listen-after-s S]
      write IMG to the app area (0x08001000 up) through the bootloader: region 1 only, never
      region 2 (0x1FFFE400, Baofeng's own code). The bootloader erases only the pages it is sent,
      so a 1 KB image costs exactly page 0 of the stock app. IMG's sha256 must match --sha256.
      --listen-after-s: keep the port open that long after the end command and save what the new
      image prints as it starts to RUN/after-flash.txt (a logger opened later misses it).

  uv21_kdh.py dump --port P --out RUN --private DIR [--passes 2]
      taste the SPI flash first (dumper 2: its JEDEC ID and first 256 bytes in each SPI mode),
      and only if a chip answered, ask for full passes: the MCU flash, the system area, the SPI
      flash. No answer: stop after the taste (exit 4), so a wrong guess costs seconds, not passes.
      Everything that came off the radio (firmware, unique ID, SPI flash: Baofeng's code and the
      owner's channels) goes to DIR, never to RUN; RUN gets sizes, hashes and agreement only,
      because bench results are published.

  uv21_kdh.py frames FILE
      decode a raw dumper capture (from dump's DIR/raw-N.bin or the emulator) and summarise it.

  uv21_kdh.py console --port P --out RUN [--send LETTERS] [--seconds S] [--baud 57600]
      the bring-up image's serial console (radios/uv21/bringup): send its read-only commands
      (? help, i report, r RF registers, l redraw, p pin census), then listen for S seconds, and
      save the text to RUN/console.txt. It prints no unique ID or memory contents, so the text
      may be published. Never sends O (power off) or R (reset).

The bootloader protocol (from the UV-5RM's open firmware tools/flash.py and our disassembly of
the 5RM bootloader, radios/uv21/README.md): frames AA, cmd, args, len (2, big-endian), data,
CRC-16/XMODEM (2, big-endian) over cmd..data, EF; the reply echoes cmd, then 06 (ACK) or an
error code (E1 bad frame, E2 wrong package number, E3 write failed, E5 not now) in the args byte;
the 5RM tool's reading of the code from the cmd byte doesn't match the disassembly. 01 =
handshake ("BOOTLOADER"), 04 = region 1 with N packages, 03 = one 1 KB package (args = its
number, in order from 0), 45 = end (the radio resets). Packages 2 .. N-3 travel XORed with
"KDHT" (package % 3 == 1) or "RBGI" (% 3 == 2), skipping bytes 00, FF, the key byte and its
complement; the bootloader undoes that before writing. 04 sets the package offset back to 0, so a
flash that stops part way is restarted from the handshake, with the radio still in flash mode.

SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_kplug  # noqa: E402  (Port, and its K6 guard text)

BAUD = 115200
BAUDS = (57600, 115200)                     # --boot-baud / --baud: an app at half speed, or normal
PAGE = 0x400
APP_BASE = 0x08001000
APP_MAX = 0xF000                           # the bootloader refuses offsets from 0xF000
ACK = 0x06
REPLY_S = 2.0                              # the 5RM tool waits as long for each reply
CMD_HANDSHAKE, CMD_REGION1, CMD_PACKAGE, CMD_END = 0x01, 0x04, 0x03, 0x45
CMD_BOOT = 0x42                            # within ~200 ms of power-on: stay in the bootloader
BOOT_EVERY_S = 0.05
ERRORS = {0xE1: "frame or command refused", 0xE2: "package number or length wrong",
          0xE3: "flash write failed", 0xE5: "command not expected now"}
DUMPER_BANNER = b"blowfang uv21 dumper"     # 1 or 2: page 0 is ours either way


def crc16_xmodem(data, crc=0):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


# ---- the bootloader ----

def kdh_frame(cmd, args, data=b""):
    body = bytes([cmd, args & 0xFF]) + struct.pack(">H", len(data)) + data
    return b"\xAA" + body + struct.pack(">H", crc16_xmodem(body)) + b"\xEF"


def kdh_xor(data, key):
    out = bytearray(data)
    for i, b in enumerate(out):
        k = key[i % 4]
        if b not in (0x00, 0xFF, k, k ^ 0xFF):
            out[i] = b ^ k
    return bytes(out)


def packages(image):
    """(declared count, [(number, 1 KB as sent)]) for an app-area image; raises if it can't go"""
    if not image:
        raise ValueError("empty image")
    if len(image) > APP_MAX:
        raise ValueError(f"{len(image)} bytes: the app area is {APP_MAX}")
    n = -(-len(image) // PAGE)
    image = image + b"\xFF" * (n * PAGE - len(image))
    sp, pc = struct.unpack_from("<II", image, 0)
    if not (0x20000000 < sp <= 0x20004000 and APP_BASE < pc < APP_BASE + n * PAGE and pc & 1):
        raise ValueError(f"no app vector table at the start (sp {sp:#x}, reset {pc:#x})")
    count = max(n, 2)                      # the bootloader reads a count under 2 as 5
    out = []
    for i in range(n):
        page = image[i * PAGE:(i + 1) * PAGE]
        if 2 <= i < count - 2 and i % 3:
            page = kdh_xor(page, b"KDHT" if i % 3 == 1 else b"RBGI")
        out.append((i, page))
    return count, out


def read_kdh_reply(port, timeout=None):
    """(cmd, args) of the bootloader's reply, or None"""
    end = time.monotonic() + (timeout or REPLY_S)
    while time.monotonic() < end:
        b = port.read(1, timeout=end - time.monotonic())
        if b == b"\xAA":
            break
    else:
        return None
    head = port.read(4, timeout=1.0)
    if len(head) != 4:
        return None
    n = struct.unpack(">H", head[2:4])[0]
    rest = port.read(n + 3, timeout=1.0)
    if len(rest) != n + 3 or struct.unpack(">H", rest[n:n + 2])[0] != crc16_xmodem(head + rest[:n]):
        return None
    return head[0], head[1]


def kdh_send(port, cmd, args, data, log, tries=5):
    last = "no reply"
    for _ in range(tries):
        port.drain(0.02)
        port.write(kdh_frame(cmd, args, data))
        r = read_kdh_reply(port)
        if r is None:
            continue
        if r[1] == ACK:
            return r
        last = ERRORS.get(r[1], f"error {r[1]:#04x}")   # the code sits where the ACK would
        if r[1] != 0xE2:
            break
    raise RuntimeError(f"command {cmd:#04x}: {last}")


def wait_boot(port, seconds, log, boot_baud=None):
    """send command 42 every BOOT_EVERY_S until the bootloader ACKs it (someone switched the radio
    off and on, or the app on it reset itself) or seconds pass: flash mode with no keys held.
    boot_baud: also send each one at that speed first, for an app listening there"""
    frame = kdh_frame(CMD_BOOT, 0)
    log(f"waiting up to {seconds:.0f} s for a power-on: switch the radio off and on now")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if boot_baud and boot_baud != BAUD:
            port.set_baud(boot_baud)
            port.write(frame)
            port.set_baud(BAUD)
        port.write(frame)
        r = read_kdh_reply(port, timeout=BOOT_EVERY_S)
        if r == (CMD_BOOT, ACK):
            port.drain(0.05)
            log("the bootloader answered command 42: flash mode")
            return True
    return False


def k6_on_cable(port, seconds):
    heard = port.drain(seconds) if seconds else b""
    return b"K6 " in heard


def cmd_probe(a, port=None, log=print):
    port = port or bench_kplug.Port(a.port)
    os.makedirs(a.out, exist_ok=True)
    res = {"step": "probe", "handshake": None}
    try:
        if k6_on_cable(port, a.listen_s):
            log("refusing: a K6 is talking on this cable")
            return 4
        if a.wait_boot_s:
            res["boot"] = wait_boot(port, a.wait_boot_s, log, getattr(a, "boot_baud", None))
            if not res["boot"]:
                res["handshake"] = "no power-on seen"
                return 2
        try:
            kdh_send(port, CMD_HANDSHAKE, 0, b"BOOTLOADER", log)
            res["handshake"] = "ack"
        except RuntimeError as e:
            res["handshake"] = str(e)
    finally:
        port.close()
        json.dump(res, open(os.path.join(a.out, "probe.json"), "w"), indent=1)
        log(f"handshake: {res['handshake']}")
    return 0 if res["handshake"] == "ack" else 2


def cmd_flash(a, port=None, log=print):
    image = open(a.image, "rb").read()
    got = hashlib.sha256(image).hexdigest()
    if got != a.sha256.lower():
        log(f"refusing: {a.image} is sha256 {got}, not the {a.sha256} that was approved")
        return 5
    count, pkgs = packages(image)
    port = port or bench_kplug.Port(a.port)
    os.makedirs(a.out, exist_ok=True)
    res = {"step": "flash", "image_sha256": got, "bytes": len(image), "packages": len(pkgs),
           "declared": count, "sent": 0, "result": None}
    try:
        if k6_on_cable(port, a.listen_s):
            log("refusing: a K6 is talking on this cable")
            return 4
        kdh_send(port, CMD_HANDSHAKE, 0, b"BOOTLOADER", log)
        kdh_send(port, CMD_REGION1, 0, bytes([count]), log)
        for i, page in pkgs:
            kdh_send(port, CMD_PACKAGE, i, page, log)
            res["sent"] = i + 1
            log(f"package {i + 1}/{len(pkgs)}")
        after_s = getattr(a, "listen_after_s", 0.0)
        if after_s:
            # one end command, then everything until after_s: the reply (if the radio sends it
            # before it resets) and the new image's first lines
            port.drain(0.02)
            end = kdh_frame(CMD_END, 0)
            port.write(end)
            raw = port.read(1 << 16, timeout=after_s)
            at = raw.find(b"\xAA" + bytes([CMD_END, ACK, 0, 0]))
            res["end_ack"] = at >= 0
            text = raw[:at] + raw[at + 8:] if at >= 0 else raw    # the 8-byte reply frame out
            open(os.path.join(a.out, "after-flash.txt"), "wb").write(text)
            res["after_bytes"] = len(text)
            log(f"after the end command: {len(text)} bytes in {after_s:.0f} s")
        else:
            try:
                kdh_send(port, CMD_END, 0, b"", log, tries=2)
            except RuntimeError:
                pass                      # the radio may reset before its reply gets out
        res["result"] = "ok"
    except RuntimeError as e:
        res["result"] = str(e)
    finally:
        port.close()
        json.dump(res, open(os.path.join(a.out, "flash.json"), "w"), indent=1)
    log(f"flash: {res['result']} ({res['sent']}/{len(pkgs)} packages)")
    return 0 if res["result"] == "ok" else 3


# ---- the dumper's frames ----

def parse_frames(raw):
    """[(type, addr, data)] for each frame with a good CRC, and the count of bad or cut frames"""
    frames, bad, i = [], 0, 0
    while True:
        i = raw.find(b"\xA5\x5A", i)
        if i < 0 or i + 9 > len(raw):
            break
        t, addr, n = raw[i + 2], *struct.unpack_from("<IH", raw, i + 3)
        end = i + 9 + n + 2
        if n > 256 or end > len(raw):
            bad += n <= 256
            i += 2
            continue
        body = raw[i + 2:i + 9 + n]
        if struct.unpack(">H", raw[end - 2:end])[0] != crc16_xmodem(body):
            bad += 1
            i += 2
            continue
        frames.append((chr(t), addr, raw[i + 9:i + 9 + n]))
        i = end
    return frames, bad


def assemble(frames):
    """{'banner', 'passes': [complete passes], 'partial': n}: a pass is complete when
    it has every piece at full length (a capture that starts mid-pass leaves a partial one)"""
    out = {"banner": None, "passes": [], "partial": 0, "tastes": []}
    cur = None
    for t, addr, data in frames:
        if t == "B":
            out["banner"] = data.decode("latin-1")
            continue
        if cur is None:
            cur = {"mem": {}, "jedec": None, "spi": bytearray(), "holes": 0, "taste": []}
        if t == "F":
            base = next((b for b, m in cur["mem"].items() if b + len(m) == addr), addr)
            cur["mem"][base] = cur["mem"].get(base, b"") + data
        elif t == "T":
            cur["taste"].append((addr & 0xFF, addr >> 8, data))
        elif t == "J":
            cur["jedec"] = addr
        elif t == "S":
            if addr != len(cur["spi"]):
                cur["holes"] += 1
                cur["spi"] += b"\xFF" * max(0, addr - len(cur["spi"]))
            cur["spi"][addr:addr + len(data)] = data
        elif t == "E":
            cur["n"] = addr
            cur["spi"] = bytes(cur["spi"])
            if cur["taste"]:
                out["tastes"].append(cur["taste"])
            if addr == 0 and not cur["mem"] and not cur["spi"]:
                pass                        # a taste on its own
            elif complete(cur):
                out["passes"].append(cur)
            else:
                out["partial"] += 1
            cur = None
    return out


def flash_kb(info):
    """the dumper's reading of the flash size register (16..256, else 64)"""
    kb = struct.unpack_from("<H", info)[0] if len(info) >= 2 else 0
    return kb if 16 <= kb <= 256 else 64


def spi_size(jedec):
    cap = (jedec or 0) >> 16 & 0xFF
    return 1 << (cap if 0x11 <= cap <= 0x18 else 0x15)


def chip_answered(jedec):
    return jedec not in (None, 0, 0xFFFFFF)


def taste_summary(taste):
    """public: mode, JEDEC ID and whether the 256 bytes were all FF or all 00 (no bytes)"""
    return [{"mode": m, "jedec": f"{j & 0xFF:02x} {j >> 8 & 0xFF:02x} {j >> 16 & 0xFF:02x}",
             "answered": chip_answered(j), "all_ff": d == b"\xFF" * len(d), "all_00": not any(d)}
            for m, j, d in taste]


def complete(p):
    m = p["mem"]
    info = m.get(0x1FFFF7E0, b"")
    return (len(info) == 0x14 and len(m.get(0x08000000, b"")) == flash_kb(info) * 1024
            and len(m.get(0x1FFFE400, b"")) == 0x1000 and len(m.get(0x1FFFF800, b"")) == 0x10
            and p["jedec"] is not None and len(p["spi"]) == spi_size(p["jedec"]) and not p["holes"])


def regions(p):
    """the pass's named pieces: flash, sysarea (0x1FFFE400), info (size + UID), usd, spi"""
    m = p["mem"]
    return {"flash": m.get(0x08000000, b""), "sysarea": m.get(0x1FFFE400, b""),
            "info": m.get(0x1FFFF7E0, b""), "usd": m.get(0x1FFFF800, b""), "spi": p["spi"]}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def summary(dump, bad):
    """what may be published: sizes, hashes, agreement; never the bytes or the unique ID"""
    info = dump["passes"][0]["mem"][0x1FFFF7E0] if dump["passes"] else b""
    s = {"banner": dump["banner"], "flash_kb": flash_kb(info) if info else None, "bad_frames": bad,
         "passes": len(dump["passes"]), "partial_passes": dump["partial"], "pieces": {}}
    if dump.get("tastes"):
        s["taste"] = taste_summary(dump["tastes"][-1])
        s["tastes"] = len(dump["tastes"])
    ps = [regions(p) for p in dump["passes"]]
    for k in ("flash", "sysarea", "usd", "spi"):
        vals = [r[k] for r in ps]
        e = {"bytes": [len(v) for v in vals], "agree": len(set(vals)) == 1 and len(vals) > 1}
        if k != "spi":                    # the SPI flash holds the owner's channels
            e["sha256"] = sorted({sha(v) for v in vals})
        s["pieces"][k] = e
    if ps and ps[0]["flash"]:
        f = ps[0]["flash"]
        s["bootloader_sha256"] = sha(f[:0x1000])
        s["page0_is_dumper"] = DUMPER_BANNER in f[0x1000:0x1400]
        s["app_pages_erased"] = sum(f[o:o + PAGE] == b"\xFF" * PAGE for o in range(0x1000, len(f), PAGE))
        if len(ps) > 1:
            s["flash_pages_differ"] = [hex(o) for o in range(0, len(f), PAGE)
                                       if any(r["flash"][o:o + PAGE] != f[o:o + PAGE] for r in ps[1:])]
    if dump["passes"]:
        j = dump["passes"][0]["jedec"]
        s["spi_jedec"] = None if j is None else f"{j & 0xFF:02x} {j >> 8 & 0xFF:02x} {j >> 16 & 0xFF:02x}"
    # bad frames only spoil the passes they fell in, and those are left out above
    s["complete"] = s["passes"] >= 2 and all(e["agree"] for e in s["pieces"].values())
    return s


def save_private(dump, priv):
    """the agreed bytes (pass 1 where all passes agree) into priv, and the last taste's bytes"""
    os.makedirs(priv, exist_ok=True)
    for m, _, d in (dump["tastes"][-1] if dump.get("tastes") else []):
        open(os.path.join(priv, f"taste-mode{m}.bin"), "wb").write(d)
    if not dump["passes"]:
        return
    r = regions(dump["passes"][0])
    for k, v in r.items():
        open(os.path.join(priv, k + ".bin"), "wb").write(v)


def cmd_dump(a, port=None, log=print):
    """read until a.passes complete passes agree-able: whatever the dumper is already sending;
    at each 2 s of quiet, a 'T' if no taste has come in yet (dumper 2), then, once a taste shows a
    chip answering, one more pass for each 'D'. A taste where no mode answered ends the dump."""
    port = port or bench_kplug.Port(a.port)
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.private, exist_ok=True)
    raw = bytearray()
    asked = tasted = 0
    stopped = None
    try:
        end = time.monotonic() + a.timeout_s
        while time.monotonic() < end:
            got = port.read(1 << 14, timeout=2.0, gap=2.0)
            raw += got
            if got:
                continue
            dump = assemble(parse_frames(bytes(raw))[0])      # 2 s quiet: a pass boundary
            if len(dump["passes"]) >= a.passes:
                break
            if asked >= a.passes + 2:
                break
            if dump["tastes"] or dump["banner"] == "blowfang uv21 dumper 1":
                if dump["tastes"] and not any(chip_answered(j) for _, j, _ in dump["tastes"][-1]):
                    stopped = "no SPI flash answered the taste"
                    break
            elif tasted < 2:
                port.write(b"T")
                tasted += 1
                continue
            port.write(b"D")
            asked += 1
    finally:
        port.close()
    open(os.path.join(a.private, "raw.bin"), "wb").write(raw)
    frames, bad = parse_frames(bytes(raw))
    dump = assemble(frames)
    s = summary(dump, bad)
    s["raw_bytes"] = len(raw)
    s["asked"] = asked
    if stopped:
        s["stopped"] = stopped
    json.dump(s, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    save_private(dump, a.private)
    log(json.dumps(s, indent=1))
    if not raw:
        return 2
    if stopped:
        return 4
    return 0 if s["complete"] else 3


def cmd_frames(a, log=print):
    raw = open(a.file, "rb").read()
    frames, bad = parse_frames(raw)
    s = summary(assemble(frames), bad)
    log(json.dumps(s, indent=1))
    return 0 if s["complete"] else 3


CONSOLE_SAFE = b"?irlp"                     # the bring-up image's commands that change nothing
BRINGUP_BANNER = "blowfang uv21 bringup"


def cmd_console(a, port=None, log=print):
    send = a.send.encode()
    bad = sorted(set(send) - set(CONSOLE_SAFE))
    if bad:
        log(f"refusing: {bytes(bad).decode(errors='replace')!r}: only {CONSOLE_SAFE.decode()}")
        return 5
    port = port or bench_kplug.Port(a.port)
    os.makedirs(a.out, exist_ok=True)
    text = bytearray()
    try:
        baud = getattr(a, "baud", BAUD)
        if baud != BAUD:
            port.set_baud(baud)
        text += port.drain(a.listen_s)
        if b"K6 " in text:
            log("refusing: a K6 is talking on this cable")
            return 4
        for c in send:
            port.write(bytes([c]))
            text += port.read(1 << 16, timeout=2.0, gap=0.5)
        end = time.monotonic() + a.seconds
        while time.monotonic() < end:
            text += port.read(1 << 16, timeout=min(1.0, max(0.01, end - time.monotonic())))
    finally:
        port.close()
    open(os.path.join(a.out, "console.txt"), "wb").write(bytes(text))
    lines = bytes(text).decode("latin-1").splitlines()
    banner = next((ln.strip() for ln in lines if ln.startswith(BRINGUP_BANNER)), None)
    res = {"step": "console", "bytes": len(text), "lines": len(lines), "banner": banner,
           "sent": send.decode()}
    json.dump(res, open(os.path.join(a.out, "console.json"), "w"), indent=1)
    log(json.dumps(res))
    return 0 if text else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "flash", "dump"):
        p = sub.add_parser(name)
        p.add_argument("--port", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--listen-s", type=float, default=1.0, dest="listen_s")
        if name == "probe":
            p.add_argument("--wait-boot-s", type=float, default=0.0, dest="wait_boot_s",
                           help="first send command 42 until the radio is switched on (flash mode, no keys)")
            p.add_argument("--boot-baud", type=int, default=None, choices=BAUDS, dest="boot_baud",
                           help="also send each command 42 at this speed, for an app listening there")
        if name == "flash":
            p.add_argument("--image", required=True)
            p.add_argument("--sha256", required=True)
            p.add_argument("--listen-after-s", type=float, default=0.0, dest="listen_after_s",
                           help="save what the new image prints for this long after the end command")
        if name == "dump":
            p.add_argument("--private", required=True)
            p.add_argument("--passes", type=int, default=2, choices=(1, 2, 3))
            p.add_argument("--timeout-s", type=float, default=3000.0, dest="timeout_s")
    p = sub.add_parser("frames")
    p.add_argument("file")
    p = sub.add_parser("console")
    p.add_argument("--port", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--listen-s", type=float, default=1.0, dest="listen_s")
    p.add_argument("--send", default="i", help=f"commands to send first, from {CONSOLE_SAFE.decode()}")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--baud", type=int, default=BAUD, choices=BAUDS)
    a = ap.parse_args(argv)
    return {"probe": cmd_probe, "flash": cmd_flash, "dump": cmd_dump, "frames": cmd_frames,
            "console": cmd_console}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
