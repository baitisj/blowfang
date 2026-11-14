#!/usr/bin/env python3
"""Install blowfang on a Baofeng UV-K6 / K61 over the programming cable alone: no second radio.
The instruction book is docs/manual/install.md; this is the tool it walks you through.

  python3 tools/k6_install.py install BUNDLE        everything, step by step (a new radio, or an update)
  python3 tools/k6_install.py backup -o FILE        the radio's whole 2 MB SPI flash (calibration included)
  python3 tools/k6_install.py flash IMAGE           key an image with your key and flash it
  python3 tools/k6_install.py load BUNDLE           EXPLORE's pages, the help deck and the modules
  python3 tools/k6_install.py status                what the radio runs and what its library holds
  python3 tools/k6_install.py key                   make your module key (once)
  python3 tools/k6_install.py bundle OUT -o DIR     a release folder from build.sh's or CI's out/

How the pieces get there:

- The core image goes in through the stock bootloader (MCU flash, 1 KB packages). The bootloader
  listens on the cable for about 200 ms after every reset: this tool catches that window when you
  switch the radio on, or, on a radio already running blowfang, right after the cable session's
  end command restarts it (no hands).
- Everything else (EXPLORE's code pages, the help card deck, the modules) goes into the radio's
  SPI module library through the image's cable session (cps::run_cable_session in the Aura
  patch): the same signed chunks the radio takes over the air, checked by the same loader, written
  by the same library code (slots in 0x110000-0x18FFFF only). The session opens on a handshake
  while the radio is in EXPLORE or the arena, ignores the PTT line (the cable's PC->radio wire),
  never transmits, and ends with a restart.
- Your key: every image is keyed with it on this machine (the release images carry a placeholder)
  and every library image is signed with it as it is sent. It never leaves this machine; nobody
  else's key is needed or shipped. Keep the key file: an image keyed with it takes only modules
  signed with it.

Needs Python 3.8+, pyserial and numpy (pip install pyserial numpy). SPDX-License-Identifier: MIT
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from modem import k6mod  # noqa: E402

# ---- the cable session's and Aura's CPS frames: A6 cmd addr(2) len(2) data CRC-16/XMODEM(2) ----

CPS_HEADER = 0xA6
ACK = 0x06
CABLE_HANDSHAKE = b"PROGRAMBF-K6BLWF"     # blowfang's cable session (cps::run_cable_session)
AURA_HANDSHAKE = b"PROGRAMBF-K6AURA"      # Aura's CPS session: its first-boot dump, or full Aura
CMD_INFO, CMD_CHUNK, CMD_READ, CMD_END, CMD_ERROR = 0x56, 0x4D, 0x44, 0x45, 0xEE
ERR_BAD_CRC = 0x01
READ_BLOCK = 64
CHIP_BYTES = 2 << 20

# the SPI library (c/k6/k6_lib.h) and the codebooks (docs/k6-spi-codebook.md)
LIB_BASE, LIB_SLOT, LIB_SLOTS = 0x110000, 0x4000, 32
CODEBOOKS = ((0x100000, b"K6CB", "sotto's codebook (v3.7, sotto 1200)", "cbi"),
             (0x108000, b"K6VL", "sotto 600 / 400's codebooks", "vlri"))
PAGE_SLOTS = {"table": 9, "sweep": 10, "listen": 11, "edit": 12}
CARD_SLOTS = (13, 14, 15)
BUILD_ID = 8          # an EXPLORE page ends with its image's build ID (tools/k6_pages.py)
CHUNK = 64            # the cable sends L packets: 64 bytes, 58 of image each

# km_packet's results (c/k6/k6_mod.h) and the library's (k6_lib.h)
KM = {1: "chunk", 2: "repeat", 3: "checked", -1: "bad CRC", -2: "bad chunk", -3: "bad header",
      -4: "wrong key", -5: "no key in the image", -6: "old serial"}
KL = {-1: "slot empty", -2: "bad header", -3: "bad tag", -4: "flash refused", -5: "read-back differs",
      -6: "no such slot"}

TIMEOUT = 3.0         # s for an answer before a frame goes again
DEFAULT_KEYS = ("~/.config/blowfang/k6-mod.key", "~/.config/sotto/k6-mod.key")


class InstallError(Exception):
    pass


def crc16_xmodem(data):
    crc = 0
    for b in bytes(data):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


def cps_frame(cmd, addr=0, data=b""):
    body = struct.pack(">BHH", cmd, addr, len(data)) + bytes(data)
    return bytes([CPS_HEADER]) + body + struct.pack(">H", crc16_xmodem(body))


class CpsLink:
    """One CPS session on the cable: the cable session (blowfang) or Aura's. Frames go one at a
    time or a few in flight (window); the radio's report lines between frames (plain text: never
    an 0xA6 byte) go to log."""

    def __init__(self, port, log=print, timeout=None):
        self.port, self.log, self.timeout = port, log, timeout or TIMEOUT
        self.buf = b""
        self.text = b""
        self.lines = []                                      # the radio's report lines so far

    def _fill(self):
        b = self.port.read(256)
        if b:
            self.buf += b
        return bool(b)

    def _text(self, b):
        self.text += b
        while b"\n" in self.text:
            line, self.text = self.text.split(b"\n", 1)
            line = line.decode("ascii", "replace").strip()
            if line:
                self.lines.append(line)
                self.log("  K6: " + line[:120])

    def handshake(self, key, wait=20.0, gap=0.5, fresh=True):
        """Send key until the radio answers ACK (0x06), at most `wait` seconds, one try every
        `gap` s (each 16 bytes take 1.4 ms: PTT's debouncer never sees them). True on ACK.
        fresh: drop what the radio sent before (False keeps its report lines coming)."""
        deadline = time.monotonic() + wait
        if fresh:
            self.port.reset_input_buffer()
            self.buf = b""
        while time.monotonic() < deadline:
            self.port.write(key)
            t = time.monotonic() + gap
            while time.monotonic() < t:
                self._fill()
                i = self.buf.find(bytes([ACK]))
                if i >= 0:
                    self._text(self.buf[:i])
                    self.buf = self.buf[i + 1:]
                    return True
                if b"\n" in self.buf:
                    j = self.buf.rindex(b"\n") + 1
                    self._text(self.buf[:j])
                    self.buf = self.buf[j:]
        return False

    def _frame(self, deadline):
        """The next frame from the radio: (cmd, addr, data), or None at the deadline"""
        while True:
            i = self.buf.find(bytes([CPS_HEADER]))
            if i < 0:
                self._text(self.buf)
                self.buf = b""
            else:
                if i:
                    self._text(self.buf[:i])
                    self.buf = self.buf[i:]
                if len(self.buf) >= 6:
                    n = struct.unpack(">H", self.buf[4:6])[0]
                    if len(self.buf) >= 8 + n:
                        f, self.buf = self.buf[:8 + n], self.buf[8 + n:]
                        if crc16_xmodem(f[1:6 + n]) == struct.unpack(">H", f[6 + n:8 + n])[0]:
                            return f[1], struct.unpack(">H", f[2:4])[0], f[6:6 + n]
                        self.buf = f[1:] + self.buf          # not a frame after all: resync
                        continue
            if time.monotonic() > deadline:
                return None
            self._fill()

    def calls(self, reqs, window=1, retries=4):
        """reqs: [(cmd, addr, data)] -> their answers' data, in order. Up to `window` frames in
        flight; an answer that doesn't come, or a CRC error, sends that frame again."""
        out = [None] * len(reqs)
        todo = list(range(len(reqs)))
        tries = {}
        while todo:
            batch, todo = todo[:window], todo[window:]
            for i in batch:
                self.port.write(cps_frame(*reqs[i]))
            pending = list(batch)
            deadline = time.monotonic() + self.timeout
            while pending:
                f = self._frame(deadline)
                if f is None:
                    break
                cmd, addr, data = f
                hit = next((i for i in pending if reqs[i][1] == addr), None)
                if hit is None:
                    continue                                 # a late answer to an earlier try
                if cmd == CMD_ERROR:
                    if data[:1] != bytes([ERR_BAD_CRC]):
                        raise InstallError(f"the radio refused command {reqs[hit][0]:#04x} at {addr}: error {data.hex()}")
                    continue                                 # sent again below
                if cmd != reqs[hit][0]:
                    continue
                out[hit] = data
                pending.remove(hit)
            again = [i for i in batch if out[i] is None]
            for i in again:
                tries[i] = tries.get(i, 0) + 1
                if tries[i] > retries:
                    raise InstallError(f"no answer from the radio to command {reqs[i][0]:#04x} at {reqs[i][1]}")
            todo = again + todo
            if again:
                time.sleep(0.05)
                self.port.reset_input_buffer()
                self.buf = b""
        return out

    def call(self, cmd, addr=0, data=b""):
        return self.calls([(cmd, addr, data)])[0]

    def read(self, addr, n, window=8, progress=None):
        """n bytes of the SPI flash from addr (64-byte aligned)"""
        if addr % READ_BLOCK:
            raise ValueError("reads start on a 64-byte block")
        blocks = range(addr // READ_BLOCK, (addr + n + READ_BLOCK - 1) // READ_BLOCK)
        out = bytearray()
        step = 256
        for s in range(0, len(blocks), step):
            part = self.calls([(CMD_READ, b, b"") for b in blocks[s:s + step]], window=window)
            for d in part:
                if len(d) != READ_BLOCK:
                    raise InstallError(f"a read came back {len(d)} bytes, not {READ_BLOCK}")
                out += d
            if progress:
                progress(len(out), len(blocks) * READ_BLOCK)
        return bytes(out[:n])

    def end(self):
        """End the session: the cable session restarts the radio; Aura's first-boot one returns
        to the FORMAT FLASH? screen"""
        try:
            self.call(CMD_END)
        except InstallError:
            pass                                             # it restarts all the same after 5 s idle


class Cable(CpsLink):
    """blowfang's cable session"""

    def info(self):
        d = self.call(CMD_INFO)
        if d[:4] != b"BLWF":
            raise InstallError(f"the radio's info doesn't look like blowfang's: {d.hex()}")
        return dict(protocol=d[4], slots=d[5], arena=struct.unpack("<H", d[6:8])[0],
                    build_id=d[8:16] if any(d[8:16]) else None)

    def send_image(self, img, xfer, window=3):
        """One signed image as L chunks; the last chunk alone (its install erases and programs
        SPI, and the radio's 320-byte receive ring must not overflow meanwhile). Returns
        (km_packet's last result, the install's result)."""
        pk = k6mod.packets(img, xfer, CHUNK)
        reqs = [(CMD_CHUNK, i, p) for i, p in enumerate(pk)]
        ans = self.calls(reqs[:-1], window=window) if len(reqs) > 1 else []
        for i, d in enumerate(ans):
            r = struct.unpack("b", d[:1])[0]
            if r < 0:
                raise InstallError(f"chunk {i}: {KM.get(r, r)}")
        d = self.calls(reqs[-1:])[0]
        r, have, count = struct.unpack("b", d[:1])[0], d[1], d[2]
        put = struct.unpack("<i", d[4:8])[0]
        return r, have, count, put

    def slot_head(self, slot):
        """(header, name) of what a library slot holds, or None if it's erased"""
        h = self.read(LIB_BASE + slot * LIB_SLOT, 64)
        if h[:3] != b"K6M":
            return None
        code = h[16:]
        return h[:16], k6mod.module_name(code)


# ---- the stock bootloader: catch its window, then flash the MCU (AA cmd arg len data CRC EF) ----

BOOT_HELLO, BOOT_DATA, BOOT_COUNT, BOOT_END = 0x01, 0x03, 0x04, 0x45
BOOT_PACKAGE = 1024
BOOT_ERRORS = {0xE1: "bad handshake", 0xE2: "CRC mismatch", 0xE3: "bad address",
               0xE4: "flash write failed", 0xE5: "bad command"}


def boot_frame(cmd, arg=0, data=b""):
    body = bytes([cmd, arg, len(data) >> 8, len(data) & 0xFF]) + bytes(data)
    c = crc16_xmodem(body)
    return bytes([0xAA]) + body + bytes([c >> 8, c & 0xFF, 0xEF])


def boot_answer(port, timeout):
    """The bootloader's answer to a frame: (cmd, arg), or None"""
    deadline, buf = time.monotonic() + timeout, b""
    while time.monotonic() < deadline:
        buf += port.read(64)
        i = buf.find(b"\xaa")
        while i >= 0 and len(buf) - i >= 5:
            n = buf[i + 3] << 8 | buf[i + 4]
            if len(buf) - i < 8 + n:
                break
            f = buf[i:i + 8 + n]
            if f[-1] == 0xEF and crc16_xmodem(f[1:5 + n]) == (f[5 + n] << 8 | f[6 + n]):
                return f[1], f[2]
            i = buf.find(b"\xaa", i + 1)
    return None


def boot_call(port, cmd, arg, data, retries=5, timeout=2.0, quiet_ok=False):
    for _ in range(retries):
        port.reset_input_buffer()
        port.write(boot_frame(cmd, arg, data))
        a = boot_answer(port, timeout)
        if a is None:
            continue
        if a[1] == ACK:
            return
        if a[0] == 0xE2:
            continue
        raise InstallError(f"the bootloader refused command {cmd:#04x}: {BOOT_ERRORS.get(a[0], hex(a[0]))}")
    if quiet_ok:
        return
    raise InstallError(f"no answer from the bootloader to command {cmd:#04x}")


def boot_flash(port, image, log=print):
    """Flash an image into the MCU: the radio must be in UPDATE mode (catch_window)"""
    n = -(-len(image) // BOOT_PACKAGE)
    if n > 256:
        raise InstallError(f"{len(image)} bytes is more than the bootloader takes")
    boot_call(port, BOOT_HELLO, 0, b"BOOTLOADER")
    boot_call(port, BOOT_COUNT, 0, bytes([n & 0xFF]))
    for seq in range(n):
        boot_call(port, BOOT_DATA, seq & 0xFF, image[seq * BOOT_PACKAGE:(seq + 1) * BOOT_PACKAGE].ljust(BOOT_PACKAGE, b"\0"))
        if seq % 8 == 7 or seq == n - 1:
            log(f"\r  flashing {seq + 1}/{n} KB", end="")
    log("")
    boot_call(port, BOOT_END, 0, b"", retries=2, quiet_ok=True)   # it may restart before it answers


def catch_window(port, window):
    """Send the bootloader's UPDATE handshake until it answers (tools/k6_update.py)"""
    import k6_update
    return k6_update.catch_window(port, window)


# ---- keys, signing, bundles ----

def find_key(path=None, create=False, log=print):
    if path:
        return os.path.expanduser(path)
    env = os.environ.get("BLOWFANG_KEY")
    if env:
        return os.path.expanduser(env)
    for p in DEFAULT_KEYS:
        if os.path.exists(os.path.expanduser(p)):
            return os.path.expanduser(p)
    p = os.path.expanduser(DEFAULT_KEYS[0])
    if not create:
        raise InstallError(f"no module key yet: run `{sys.argv[0]} key` (it makes {p})")
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    k6mod.new_key(p)
    log(f"made your module key {p} (keep it, and keep it private: it signs everything for your radio)")
    return p


def keyed_image(data, key):
    """The image with your key over the placeholder. An image without the placeholder takes no
    modules (or was built keyed already): it is used as it is, and the caller says so."""
    n = data.count(k6mod.PLACEHOLDER)
    if n == 1:
        return k6mod.key_image(data, key), True
    if n == 0:
        return data, False
    raise InstallError(f"the key placeholder is in the image {n} times")


class Serials:
    """Module serials: each newer than the last (the radio refuses an old one since power-on)"""

    def __init__(self, start=None):
        self.n = int(time.time()) if start is None else start

    def next(self):
        self.n += 1
        return self.n


def probe_image(serial, key):
    """A two-byte module (bx lr) that is never installed or run: the cable session only checks
    its tag, so the answer says whether the radio's key is yours"""
    return k6mod.image(b"\x70\x47", 0, serial, key)


def sign(entry, serial, key):
    """A bundle's library entry, signed for its slot"""
    code = open(entry["path"], "rb").read()
    kind = entry["kind"]
    return k6mod.image(code, k6mod.bin_entry(code) if kind == "module" else 0, serial, key,
                       entry.get("level", 1), lib=entry["slot"], page=kind == "page", cards=kind == "cards")


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def load_bundle(d):
    m = json.load(open(os.path.join(d, "manifest.json")))
    if m.get("format") != 1:
        raise InstallError(f"{d}: manifest format {m.get('format')}, this tool reads 1")
    for name, want in m["sha256"].items():
        if sha256(os.path.join(d, name)) != want:
            raise InstallError(f"{d}/{name} doesn't match the manifest (damaged download?)")
    for e in m["library"]:
        e["path"] = os.path.join(d, e["file"])
    return m


def read_modules_list(path):
    out = []
    for ln in open(path):
        ln = ln.split("#")[0].split()
        if len(ln) == 2:
            out.append((int(ln[0]), ln[1]))
    return out


def cmd_bundle(a, log=print):
    """build.sh's (or a CI artifact's) out/ -> a release folder anyone can install from"""
    src, dst = a.out, a.output
    image = os.path.join(src, "aura-voice-explore.bin")
    data = open(image, "rb").read()
    if data.count(k6mod.PLACEHOLDER) != 1:
        raise InstallError(f"{image} has no key placeholder: it was built with a key "
                           "(K6_MOD_KEY_FILE). Bundle an unkeyed build (CI's), never a keyed one")
    files, lib = ["aura-voice-explore.bin"], []
    ids = set()
    for page, slot in sorted(PAGE_SLOTS.items(), key=lambda x: x[1]):
        f = f"page-{page}.bin"
        b = open(os.path.join(src, f), "rb").read()
        ids.add(b[-BUILD_ID:])
        files.append(f)
        lib.append(dict(slot=slot, kind="page", file=f, name=page.upper()))
    if len(ids) != 1:
        raise InstallError("the pages were linked against different images")
    build_id = ids.pop()
    if data.count(build_id) < 1:
        raise InstallError("the pages weren't linked against this image (its build ID isn't in it)")
    files.append("cards-core.bin")
    lib.append(dict(slot=CARD_SLOTS[0], kind="cards", file="cards-core.bin", name="HELP"))
    for slot, name in read_modules_list(a.modules):
        f = f"mod-{name}.bin"
        code = open(os.path.join(src, f), "rb").read()
        files.append(f)
        lib.append(dict(slot=slot, kind="module", file=f, name=k6mod.module_name(code) or name,
                        level=k6mod.source_level(os.path.join(ROOT, "c", "k6", "mod", name + ".c"))))
    extra = {}
    for key, f in (("cbi", "aura-pager-cbi.bin"), ("vlri", "aura-pager-vlri.bin")):
        if os.path.exists(os.path.join(src, f)):
            extra[key] = f
            files.append(f)
    os.makedirs(dst, exist_ok=True)
    for f in files:
        shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))
    build = open(os.path.join(src, "build-id.txt")).read().strip() if os.path.exists(os.path.join(src, "build-id.txt")) else ""
    with open(os.path.join(dst, "NOTICE.txt"), "w") as f:
        f.write(NOTICE.format(build=build or "(unknown)"))
    m = dict(format=1, build=build, image="aura-voice-explore.bin", build_id=build_id.hex(),
             codebook_images=extra, library=lib, sha256={f: sha256(os.path.join(dst, f)) for f in files})
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(m, f, indent=1)
    missing = [k for k in ("cbi", "vlri") if k not in extra]
    log(f"{dst}: {len(files)} files, image build ID {build_id.hex()}, {len(lib)} library entries"
        + (f"; no {' or '.join(missing)} image (a new radio needs them for the codebooks)" if missing else ""))
    return 0


NOTICE = """blowfang for the Baofeng UV-K6 / K61, release {build}

The .bin images are builds of Aura (https://github.com/sophiel-meow/bf-k61-firmware-aura, GPL-3.0)
with blowfang's patch (firmware/k6-aura/aura-pager.patch, GPL-3.0) and sotto's C library (MIT).
Their complete source is https://github.com/baitisj/blowfang (with its sotto submodule) and Aura
at the commit firmware/k6-aura/build.sh names. The modules (mod-*.bin), EXPLORE's code pages
(page-*.bin) and the help deck (cards-core.bin) are blowfang's own code, MIT.

Nothing here is Baofeng's: no stock firmware, no part of it. Flashing replaces the stock
firmware for good; back up your radio's SPI flash first (docs/manual/install.md).

The images carry a key placeholder, not anybody's key: tools/k6_install.py writes your own key
into the image on your machine and signs every library image with it as it sends it.
"""


# ---- the steps ----

def open_port(a):
    import serial
    if a.port:
        name = a.port
    else:
        from serial.tools import list_ports
        usb = [p.device for p in list_ports.comports() if p.vid is not None]
        if len(usb) != 1:
            raise InstallError("say which serial port is the K6 cable: --port /dev/ttyUSB0 (or COM5); "
                               f"USB serial ports here: {', '.join(usb) or 'none'}")
        name = usb[0]
    return serial.Serial(name, 115200, timeout=0.005)


def open_cable(port, log, wait):
    c = Cable(port, log)
    if not c.handshake(CABLE_HANDSHAKE, wait=wait):
        return None
    return c


def backup(link, path, log=print):
    t = time.monotonic()
    data = link.read(0, CHIP_BYTES, progress=lambda n, tot: log(f"\r  backup {n * 100 // tot}%", end="")
                     if n % (64 * 1024) == 0 or n == tot else None)
    log("")
    cal = link.read(0xF000, 0x1000)
    if cal != data[0xF000:0x10000]:
        raise InstallError("the calibration sector read differently twice: the cable is unreliable, try again")
    with open(path, "wb") as f:
        f.write(data)
    log(f"wrote {path}: {len(data) >> 10} KB in {time.monotonic() - t:.0f} s, sha256 {hashlib.sha256(data).hexdigest()[:16]}; "
        "keep it: it holds your radio's factory calibration")
    return data


def have_backup():
    d = os.path.expanduser("~/blowfang-backups")
    return os.path.isdir(d) and any(f.startswith("k6-spiflash-") for f in os.listdir(d))


def default_backup_path():
    d = os.path.expanduser("~/blowfang-backups")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "k6-spiflash-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%MZ") + ".bin")


def flash(port, image, key, how, log=print, ask=input):
    """Key the image and flash it. how: "cable" (a blowfang radio: end the cable session, catch
    the window after its restart, no hands) or "power" (you switch the radio on)"""
    data, keyed = keyed_image(image, key)
    if not keyed:
        log("  (this image takes no modules: it is flashed as it is)")
    if how == "cable":
        c = Cable(port, log)
        c.call(CMD_END)
        time.sleep(0.03)
        ok = catch_window(port, 2.0)
    else:
        ask("Switch the radio OFF, then press Enter here and switch it ON (no keys held) within 10 s... ")
        ok = catch_window(port, 12.0)
    if not ok:
        raise InstallError("the bootloader didn't answer: switch the radio off, press Enter and switch it on "
                           "again (or hold Side Keys 1 and 2 while switching it on)")
    log("  the radio is in UPDATE mode")
    boot_flash(port, data, log)
    log("  flashed; the radio restarts (if its screen stays on UPDATE, switch it off and on)")


def check_key(c, key, serials, log=print):
    r, _, _, _ = c.send_image(probe_image(serials.next(), key), serials.n & 0xFF)
    if r == 3:
        return
    if r == -4:
        raise InstallError("the radio's image holds another key than yours: flash the image again with "
                           "`flash` (it keys it with yours), or pass the key it was keyed with (--key-file)")
    if r == -5:
        raise InstallError("the radio's image holds no key: flash it with this tool, which keys it")
    raise InstallError(f"the key check came back {KM.get(r, r)}")


def load(c, bundle, key, serials, log=print, only=None, verify=False, force=False):
    info = c.info()
    log(f"  the radio: cable session {info['protocol']}, {info['slots']} slots, "
        f"build ID {info['build_id'].hex() if info['build_id'] else '(none: not the voice + EXPLORE image)'}")
    check_key(c, key, serials, log)
    want = bytes.fromhex(bundle["build_id"])
    entries = [e for e in bundle["library"] if only is None or e["slot"] in only]
    if info["build_id"] != want and not force:
        pages = [e for e in entries if e["kind"] == "page"]
        if pages:
            log("  skipping EXPLORE's pages: they are for another image than the radio's "
                f"(bundle {want.hex()}); flash the bundle's image first")
            entries = [e for e in entries if e["kind"] != "page"]
    order = {"page": 0, "cards": 1, "module": 2}
    entries.sort(key=lambda e: (order[e["kind"]], e["slot"]))
    t, total = time.monotonic(), 0
    for e in entries:
        img = sign(e, serials.next(), key)
        r, have, count, put = c.send_image(img, serials.n & 0xFF)
        slot = LIB_BASE + e["slot"] * LIB_SLOT
        if r != 3 or put != len(img):
            # the last chunk's answer may have been lost and the chunk sent again (it then
            # starts a transfer of its own): the slot itself says whether the install happened
            if r == 3 or c.read(slot, len(img)) != img:
                why = KM.get(r, r) if r != 3 else KL.get(put, put)
                raise InstallError(f"slot {e['slot']} ({e['name']}): {why}")
        elif verify and c.read(slot, len(img)) != img:
            raise InstallError(f"slot {e['slot']} ({e['name']}) reads back differently")
        total += len(img)
        log(f"  slot {e['slot']:2d} {e['kind']:6s} {e['name']:12s} {len(img):6d} B")
    log(f"  {len(entries)} library images, {total} B, in {time.monotonic() - t:.0f} s")


def codebooks(c):
    """Which codebook images are missing from the radio's SPI flash"""
    out = []
    for addr, magic, what, img in CODEBOOKS:
        if c.read(addr, 64)[:4] != magic:
            out.append((what, img))
    return out


def wait_line(port, prefix, timeout, log=print):
    """Read report lines until one starts with prefix; the line, or None"""
    deadline, buf = time.monotonic() + timeout, b""
    while time.monotonic() < deadline:
        buf += port.read(256)
        while b"\n" in buf:
            ln, buf = buf.split(b"\n", 1)
            ln = ln.decode("ascii", "replace").strip()
            if ln:
                log("  K6: " + ln[:120])
            if ln.startswith(prefix):
                return ln
    return None


def first_boot(port, backup_path, log=print, ask=input, timeout=90.0):
    """After an image's first flash on a radio Aura never formatted: back up at FORMAT FLASH?,
    then the owner presses MENU. Ends at the image's K6 CB line (its codebook check)."""
    link = CpsLink(port, log)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if link.handshake(AURA_HANDSHAKE, wait=3.0, fresh=False):
            log("  the radio shows FORMAT FLASH?: backing up its SPI flash before anything is erased")
            backup(link, backup_path, log)
            link.end()
            ask("Now press MENU on the radio to format, then press Enter here... ")
            break
        if any(ln.startswith("K6 CB") for ln in link.lines):
            return True                                       # formatted already: no prompt
    return wait_line(port, "K6 CB", 60.0, log) is not None


def cmd_install(a, bundle, port, key, log=print, ask=input):
    serials = Serials()
    log("1. Looking for the radio on the cable (have it on, in EXPLORE or the arena)...")
    c = None if a.fresh else open_cable(port, log, wait=6.0)
    if c:
        log("   It runs blowfang already: an update, no hands needed.")
        need = codebooks(c)
        if a.backup or not (a.no_backup or have_backup()):
            path = a.backup or default_backup_path()
            log(f"2. Backing up the SPI flash to {path} (once per radio is enough)")
            backup(c, path, log)
        elif a.no_backup:
            log("2. No backup (--no-backup)")
        else:
            log("2. A backup is there already (~/blowfang-backups): not backing up again")
        if need:
            raise InstallError("the radio lacks " + " and ".join(w for w, _ in need) +
                               ": run install with --fresh (it flashes the codebook installers)")
        log("3. Flashing the core image...")
        flash(port, open(os.path.join(a.bundle, bundle["image"]), "rb").read(), key, "cable", log, ask)
    else:
        if a.fresh:
            log("   --fresh: installing as on a new radio (codebook installers first, three power-ons).")
        else:
            log("   No blowfang cable session: treating it as a new radio (stock, or another firmware).")
        imgs = bundle.get("codebook_images", {})
        for k in ("cbi", "vlri"):
            if k not in imgs:
                raise InstallError(f"the bundle has no {k} image, which a new radio needs for its codebooks")
        if not a.yes:
            log("\nThis replaces the radio's firmware for good: Baofeng's stock firmware can't be read out")
            log("or put back from here. Your SPI flash (factory calibration) is backed up first.")
            if ask("Type YES to go on: ").strip() != "YES":
                log("stopped; nothing was written")
                return 1
        log("2. Flashing the codebook installer (sotto's codebook into SPI flash, once)...")
        flash(port, open(os.path.join(a.bundle, imgs["cbi"]), "rb").read(), key, "power", log, ask)
        if not first_boot(port, a.backup or default_backup_path(), log, ask):
            raise InstallError("no K6 CB line after the first boot: is the radio on, and the cable in?")
        log("3. Flashing the sotto 600 / 400 codebook installer...")
        flash(port, open(os.path.join(a.bundle, imgs["vlri"]), "rb").read(), key, "power", log, ask)
        if wait_line(port, "K6 VLR", 60.0, log) is None:
            raise InstallError("no K6 VLR line: the second codebook install didn't report")
        log("4. Flashing the core image...")
        flash(port, open(os.path.join(a.bundle, bundle["image"]), "rb").read(), key, "power", log, ask)
    log("5. Waiting for the new image (it shows PAGES MISSING until its pages are in)...")
    time.sleep(3.0)
    c = open_cable(port, log, wait=40.0)
    if not c:
        raise InstallError("the new image doesn't answer on the cable: switch the radio off and on, then run "
                           f"`{sys.argv[0]} load {a.bundle}`")
    log("6. Loading EXPLORE's pages, the help deck and the modules over the cable...")
    load(c, bundle, key, serials, log, verify=a.verify)
    c.end()
    log("\nDone. The radio restarts into EXPLORE. Hold MENU on any screen for its help card.")
    return 0


def main(argv=None, log=None, ask=input):
    log = log or (lambda *x, **k: print(*x, **k, flush=True))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="the K6 programming cable's serial port (default: the only USB serial port)")
    ap.add_argument("--key-file", help="your module key (default: $BLOWFANG_KEY, ~/.config/blowfang/k6-mod.key, "
                                       "or ~/.config/sotto/k6-mod.key)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install", help="everything, step by step")
    i.add_argument("bundle")
    i.add_argument("--backup", help="where the SPI flash backup goes (default ~/blowfang-backups/...)")
    i.add_argument("--no-backup", action="store_true", help="an update: skip the backup (it is skipped "
                                                           "anyway once ~/blowfang-backups holds one)")
    i.add_argument("--verify", action="store_true", help="read every library slot back after writing it")
    i.add_argument("--yes", action="store_true", help="don't ask before replacing the stock firmware")
    i.add_argument("--fresh", action="store_true", help="install as on a new radio even if it runs blowfang "
                                                       "(flashes the codebook installers again)")
    b = sub.add_parser("backup", help="the whole 2 MB SPI flash to a file")
    b.add_argument("-o", "--output")
    f = sub.add_parser("flash", help="key an image with your key and flash it")
    f.add_argument("image")
    f.add_argument("--power", action="store_true", help="catch the bootloader as you switch the radio on, "
                                                        "even if it runs blowfang")
    ld = sub.add_parser("load", help="the library: EXPLORE's pages, the help deck, the modules")
    ld.add_argument("bundle")
    ld.add_argument("--only", help="these slots only, e.g. 0,13,26")
    ld.add_argument("--verify", action="store_true")
    ld.add_argument("--force", action="store_true", help="pages even if the radio's build ID differs")
    sub.add_parser("status", help="what the radio runs, its codebooks and its library")
    sub.add_parser("key", help="make your module key")
    bd = sub.add_parser("bundle", help="a release folder from build.sh's or CI's out/")
    bd.add_argument("out")
    bd.add_argument("-o", "--output", required=True)
    bd.add_argument("--modules", default=os.path.join(HERE, "k6_release_modules.txt"))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "key":
            p = find_key(a.key_file, create=True, log=log)
            log(f"your key: {p}")
            return 0
        if a.cmd == "bundle":
            return cmd_bundle(a, log)
        key_path = find_key(a.key_file, create=a.cmd == "install", log=log) if a.cmd in ("install", "flash", "load") else None
        key = k6mod.read_key(key_path) if key_path else None
        bundle = load_bundle(a.bundle) if a.cmd in ("install", "load") else None
        port = open_port(a)
        try:
            if a.cmd == "install":
                return cmd_install(a, bundle, port, key, log, ask)
            if a.cmd == "backup":
                path = a.output or default_backup_path()
                c = open_cable(port, log, wait=4.0)
                link = c or CpsLink(port, log)
                if not c and not link.handshake(AURA_HANDSHAKE, wait=4.0):
                    raise InstallError("the radio answers neither blowfang's cable session nor Aura's: have it on, "
                                       "in EXPLORE or the arena (or at Aura's FORMAT FLASH? screen)")
                backup(link, path, log)
                link.end()
                return 0
            if a.cmd == "flash":
                how = "power"
                if not a.power and open_cable(port, log, wait=3.0):
                    how = "cable"
                flash(port, open(a.image, "rb").read(), key, how, log, ask)
                return 0
            c = open_cable(port, log, wait=20.0)
            if not c:
                raise InstallError("no cable session: have the radio on a blowfang image, in EXPLORE or the arena")
            if a.cmd == "load":
                only = {int(s) for s in a.only.split(",")} if a.only else None
                load(c, bundle, key, Serials(), log, only, a.verify, a.force)
            else:
                info = c.info()
                log(f"blowfang cable session {info['protocol']}, build ID "
                    f"{info['build_id'].hex() if info['build_id'] else '(not the voice + EXPLORE image)'}")
                for what, img in codebooks(c):
                    log(f"missing: {what} (the {img} image installs it)")
                for s in range(LIB_SLOTS):
                    h = c.slot_head(s)
                    if h:
                        hdr, name = h
                        code, serial, flags = struct.unpack_from("<H", hdr, 4)[0], struct.unpack_from("<I", hdr, 8)[0], hdr[13]
                        kind = "page" if flags & 2 else "cards" if flags & 4 else "module"
                        log(f"slot {s:2d} {kind:6s} {name or '':12s} {code:6d} B code, serial {serial}")
            c.end()
            return 0
        finally:
            port.close()
    except InstallError as e:
        log(f"\nstopped: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
