/* blowfang's K6 installer in the browser: tools/k6_install.py ported to JavaScript over Web Serial.

   The same steps, frames and checks as the Python tool (docs/manual/install.md):
   - the core image goes in through the stock bootloader (AA cmd arg len data CRC EF, 1 KB
     packages), caught in its ~200 ms window after a restart: the cable session's end command on a
     radio that runs blowfang (no hands), or the owner switching the radio on;
   - EXPLORE's pages, the help deck and the modules go into the SPI module library through the
     image's cable session (A6 cmd addr len data CRC; handshake PROGRAMBF-K6BLWF), as signed L
     chunks checked by the radio's own loader;
   - a new radio first gets the two codebook installer images (cbi, vlri), each caught at power-on,
     and its 2 MB SPI flash is backed up at Aura's FORMAT FLASH? screen before anything is erased.

   Keys: the page carries no key. The owner makes one here (or loads the k6-mod.key file the Python
   tool made: same 32-hex-digit format), and it is written into each image and signs each library
   image in this browser, as the Python tool does on the owner's machine. Nothing is uploaded.

   No DOM in this file: it runs in the page (window.K6Web) and under Node (require) for the
   emulator tests (tests/test_k6_web_install.py). SPDX-License-Identifier: MIT */
(function (root) {
  "use strict";

  // ---- bytes ----

  const enc = new TextEncoder();
  const dec = new TextDecoder("latin1");
  const ascii = (s) => enc.encode(s);

  function concat(...parts) {
    let n = 0;
    for (const p of parts) n += p.length;
    const out = new Uint8Array(n);
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  }

  function indexOf(hay, needle, from = 0) {
    if (typeof needle === "number") return hay.indexOf(needle, from);
    outer: for (let i = from; i <= hay.length - needle.length; i++) {
      for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
      return i;
    }
    return -1;
  }

  function count(hay, needle) {
    let n = 0, i = indexOf(hay, needle);
    while (i >= 0) { n++; i = indexOf(hay, needle, i + needle.length); }
    return n;
  }

  function equal(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }

  const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");

  function unhex(s) {
    s = s.trim();
    if (!/^([0-9a-fA-F]{2})*$/.test(s)) throw new InstallError("not hex: " + s.slice(0, 20));
    const out = new Uint8Array(s.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(2 * i, 2), 16);
    return out;
  }

  const u16be = (b, o) => (b[o] << 8) | b[o + 1];
  const u16le = (b, o) => b[o] | (b[o + 1] << 8);
  const u32le = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
  const i32le = (b, o) => b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24);
  const i8 = (x) => (x << 24) >> 24;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

  class InstallError extends Error {}

  // ---- CRCs, whitening, SipHash (modem/pager.py, modem/k6mod.py) ----

  function crc16(data, init) {
    let crc = init;
    for (const b of data) {
      crc ^= b << 8;
      for (let i = 0; i < 8; i++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
    return crc;
  }
  const crc16Xmodem = (d) => crc16(d, 0);          // the CPS frames and the bootloader's
  const crc16Ccitt = (d) => crc16(d, 0xffff);      // the module packets'

  const WHITEN = (() => {                           // PN9 (TI DN509 / CC1101)
    const out = new Uint8Array(128);
    let s = 0x1ff;
    for (let i = 0; i < 128; i++) {
      out[i] = s & 0xff;
      for (let j = 0; j < 8; j++) s = (s >> 1) | (((s ^ (s >> 5)) & 1) << 8);
    }
    return out;
  })();
  const whiten = (p) => p.map((b, i) => b ^ WHITEN[i]);

  const M64 = (1n << 64n) - 1n;
  const rotl = (x, b) => ((x << b) | (x >> (64n - b))) & M64;
  function u64le(b, o) {
    let x = 0n;
    for (let i = 7; i >= 0; i--) x = (x << 8n) | BigInt(b[o + i]);
    return x;
  }

  function siphash24(key, m) {
    const k0 = u64le(key, 0), k1 = u64le(key, 8);
    const v = [k0 ^ 0x736f6d6570736575n, k1 ^ 0x646f72616e646f6dn, k0 ^ 0x6c7967656e657261n, k1 ^ 0x7465646279746573n];
    const rounds = (n) => {
      for (let i = 0; i < n; i++) {
        v[0] = (v[0] + v[1]) & M64; v[1] = rotl(v[1], 13n) ^ v[0]; v[0] = rotl(v[0], 32n);
        v[2] = (v[2] + v[3]) & M64; v[3] = rotl(v[3], 16n) ^ v[2];
        v[0] = (v[0] + v[3]) & M64; v[3] = rotl(v[3], 21n) ^ v[0];
        v[2] = (v[2] + v[1]) & M64; v[1] = rotl(v[1], 17n) ^ v[2]; v[2] = rotl(v[2], 32n);
      }
    };
    const n = m.length - (m.length % 8);
    for (let i = 0; i < n; i += 8) {
      const mi = u64le(m, i);
      v[3] ^= mi; rounds(2); v[0] ^= mi;
    }
    let b = BigInt(m.length & 0xff) << 56n;
    for (let i = m.length - 1; i >= n; i--) b |= BigInt(m[i]) << BigInt(8 * (i - n));
    v[3] ^= b; rounds(2); v[0] ^= b;
    v[2] ^= 0xffn; rounds(4);
    const t = v[0] ^ v[1] ^ v[2] ^ v[3];
    const out = new Uint8Array(8);
    for (let i = 0; i < 8; i++) out[i] = Number((t >> BigInt(8 * i)) & 0xffn);
    return out;                                     // the tag, little-endian as the K6 compares it
  }

  // ---- module images and packets (c/k6/k6_mod.h, modem/k6mod.py) ----

  const ABI = 1;
  const LIB_FLAG = 0x01, PAGE_FLAG = 0x02, CARDS_FLAG = 0x04;
  const LIB_SLOTS = 32, LIB_SLOT_BYTES = 0x4000;
  const NAME_MAGIC = ascii("K6NM"), NAME_LEN = 12;
  const PLACEHOLDER = ascii("sotto-K6MOD-key!");   // k6_mod.c's key in an image built without one

  const binEntry = (code) => (equal(code.subarray(0, 4), NAME_MAGIC) ? 4 + NAME_LEN : 0);

  function moduleName(code) {
    if (code.length < 4 + NAME_LEN || !equal(code.subarray(0, 4), NAME_MAGIC)) return null;
    const n = code.subarray(4, 4 + NAME_LEN);
    const z = n.indexOf(0);
    return dec.decode(z >= 0 ? n.subarray(0, z) : n);
  }

  function moduleImage(code, entry, serial, key, { level = 1, lib = null, page = false, cards = false } = {}) {
    if (entry >= code.length || entry & 1 || code.length > 0xffff) throw new InstallError("bad entry offset or code length");
    if (lib !== null && !(lib >= 0 && lib < LIB_SLOTS)) throw new InstallError(`library slot 0..${LIB_SLOTS - 1}`);
    if (lib !== null && 16 + code.length + 8 > LIB_SLOT_BYTES)
      throw new InstallError(`${16 + code.length + 8} bytes is more than a library slot (${LIB_SLOT_BYTES})`);
    if ((page || cards) && lib === null) throw new InstallError("a page or a deck goes into a library slot");
    const flags = lib === null ? 0 : LIB_FLAG | (page ? PAGE_FLAG : 0) | (cards ? CARDS_FLAG : 0);
    const hdr = new Uint8Array(16);
    hdr.set(ascii("K6M"), 0);
    hdr[3] = ABI;
    hdr[4] = code.length & 0xff; hdr[5] = code.length >> 8;
    hdr[6] = entry & 0xff; hdr[7] = entry >> 8;
    const s = serial >>> 0;
    hdr[8] = s & 0xff; hdr[9] = (s >>> 8) & 0xff; hdr[10] = (s >>> 16) & 0xff; hdr[11] = s >>> 24;
    hdr[12] = level; hdr[13] = flags; hdr[14] = 0; hdr[15] = lib === null ? 0 : lib;
    const body = concat(hdr, code);
    return concat(body, siphash24(key, body));
  }

  function packets(img, xfer, length = 64) {
    const dl = length - 6;
    const n = Math.ceil(img.length / dl);
    if (n > 255) throw new InstallError(`${img.length} bytes is ${n} chunks; 255 at most`);
    const out = [];
    for (let i = 0; i < n; i++) {
      const d = new Uint8Array(length);
      d[0] = 0xab; d[1] = xfer & 0xff; d[2] = i; d[3] = n;
      d.set(img.subarray(i * dl, (i + 1) * dl), 4);
      const c = crc16Ccitt(d.subarray(0, length - 2));
      d[length - 2] = c >> 8; d[length - 1] = c & 0xff;
      out.push(whiten(d));
    }
    return out;
  }

  function keyedImage(data, key) {
    // the image with the owner's key over the placeholder; one without it takes no modules (or was
    // built keyed already) and goes as it is, and the caller says so
    const n = count(data, PLACEHOLDER);
    if (n === 1) {
      const out = data.slice();
      out.set(key, indexOf(data, PLACEHOLDER));
      return { data: out, keyed: true };
    }
    if (n === 0) return { data, keyed: false };
    throw new InstallError(`the key placeholder is in the image ${n} times`);
  }

  // ---- keys: the owner's own, never shipped (same file format as tools/k6_install.py key) ----

  function newKey() {
    const k = new Uint8Array(16);
    crypto.getRandomValues(k);
    return k;
  }

  function parseKey(text) {
    const k = unhex(String(text).trim());
    if (k.length !== 16) throw new InstallError(`a key file holds 32 hex digits (16 bytes), this one ${k.length} bytes`);
    if (equal(k, PLACEHOLDER)) throw new InstallError("that is the placeholder, not a key");
    return k;
  }

  const keyFile = (k) => hex(k) + "\n";

  async function sha256(b) {
    return new Uint8Array(await crypto.subtle.digest("SHA-256", b));
  }

  async function keyFingerprint(k) {
    // safe to show: names the key without revealing it
    return hex((await sha256(k)).subarray(0, 4));
  }

  // ---- release bundles (tools/k6_install.py bundle): a folder, or its zip from CI ----

  async function unzip(data) {
    // a zip's files -> {name: bytes}: stored or deflated entries (the GitHub artifact zips),
    // found through the central directory; folders inside are flattened to base names
    let eocd = -1;
    for (let i = data.length - 22; i >= Math.max(0, data.length - 65557); i--) {
      if (data[i] === 0x50 && data[i + 1] === 0x4b && data[i + 2] === 5 && data[i + 3] === 6) { eocd = i; break; }
    }
    if (eocd < 0) throw new InstallError("not a zip file");
    const entries = u16le(data, eocd + 10);
    let p = u32le(data, eocd + 16);
    const out = {};
    for (let e = 0; e < entries; e++) {
      if (u32le(data, p) !== 0x02014b50) throw new InstallError("damaged zip (central directory)");
      const method = u16le(data, p + 10), csize = u32le(data, p + 20), usize = u32le(data, p + 24);
      const nlen = u16le(data, p + 28), xlen = u16le(data, p + 30), clen = u16le(data, p + 32);
      const local = u32le(data, p + 42);
      const name = dec.decode(data.subarray(p + 46, p + 46 + nlen));
      p += 46 + nlen + xlen + clen;
      if (name.endsWith("/")) continue;
      if (u32le(data, local) !== 0x04034b50) throw new InstallError("damaged zip (local header)");
      const start = local + 30 + u16le(data, local + 26) + u16le(data, local + 28);
      const raw = data.subarray(start, start + csize);
      let body;
      if (method === 0) body = raw.slice();
      else if (method === 8) {
        const ds = new DecompressionStream("deflate-raw");
        body = new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(ds)).arrayBuffer());
      } else throw new InstallError(`${name}: zip method ${method} isn't supported`);
      if (body.length !== usize) throw new InstallError(`${name}: damaged in the zip`);
      out[name.split("/").pop()] = body;
    }
    return out;
  }

  async function loadBundle(files) {
    // files: {name: bytes} of a release folder -> the manifest, its files checked, entries with data
    const mf = files["manifest.json"];
    if (!mf) throw new InstallError("no manifest.json: choose the release folder blowfang-k6-... (or its zip)");
    const m = JSON.parse(new TextDecoder().decode(mf));
    if (m.format !== 1) throw new InstallError(`manifest format ${m.format}; this page reads 1`);
    for (const [name, want] of Object.entries(m.sha256)) {
      if (!files[name]) throw new InstallError(`${name} is missing from the release folder`);
      if (hex(await sha256(files[name])) !== want) throw new InstallError(`${name} doesn't match the manifest (damaged download?)`);
    }
    m.files = files;
    for (const e of m.library) e.data = files[e.file];
    return m;
  }

  // ---- the cable session's and Aura's CPS frames: A6 cmd addr(2) len(2) data CRC-16/XMODEM(2) ----

  const CPS_HEADER = 0xa6, ACK = 0x06;
  const CABLE_HANDSHAKE = ascii("PROGRAMBF-K6BLWF");   // blowfang's cable session
  const AURA_HANDSHAKE = ascii("PROGRAMBF-K6AURA");    // Aura's CPS session (its first-boot dump)
  const CMD_INFO = 0x56, CMD_CHUNK = 0x4d, CMD_READ = 0x44, CMD_END = 0x45, CMD_ERROR = 0xee;
  const ERR_BAD_CRC = 0x01, READ_BLOCK = 64, CHUNK = 64;
  const LIB_BASE = 0x110000, LIB_SLOT = 0x4000;
  const CODEBOOKS = [[0x100000, ascii("K6CB"), "sotto's codebook (v3.7, sotto 1200)", "cbi"],
                     [0x108000, ascii("K6VL"), "sotto 600 / 400's codebooks", "vlri"]];
  const KM = { 1: "chunk", 2: "repeat", 3: "checked", "-1": "bad CRC", "-2": "bad chunk", "-3": "bad header",
               "-4": "wrong key", "-5": "no key in the image", "-6": "old serial" };
  const KL = { "-1": "slot empty", "-2": "bad header", "-3": "bad tag", "-4": "flash refused",
               "-5": "read-back differs", "-6": "no such slot" };

  function cpsFrame(cmd, addr = 0, data = new Uint8Array(0)) {
    const body = new Uint8Array(5 + data.length);
    body[0] = cmd; body[1] = addr >> 8; body[2] = addr & 0xff; body[3] = data.length >> 8; body[4] = data.length & 0xff;
    body.set(data, 5);
    const c = crc16Xmodem(body);
    return concat([CPS_HEADER], body, [c >> 8, c & 0xff]);
  }

  class CpsLink {
    // One CPS session on the cable. port: {write(bytes), read(ms) -> bytes (maybe empty), clear()}
    constructor(port, log = () => {}, opts = {}) {
      this.port = port; this.log = log;
      this.timeout = opts.timeout || 3000;            // ms for an answer before a frame goes again
      this.buf = new Uint8Array(0);
      this.text = new Uint8Array(0);
      this.lines = [];                                 // the radio's report lines so far
    }

    async _fill() {
      const b = await this.port.read(5);
      if (b && b.length) this.buf = concat(this.buf, b);
      return !!(b && b.length);
    }

    _text(b) {
      this.text = concat(this.text, b);
      let i;
      while ((i = this.text.indexOf(10)) >= 0) {
        const line = dec.decode(this.text.subarray(0, i)).trim();
        this.text = this.text.slice(i + 1);
        if (line) { this.lines.push(line); this.log("  K6: " + line.slice(0, 120)); }
      }
    }

    async handshake(key, { wait = 20000, gap = 500, fresh = true } = {}) {
      // key every `gap` ms until the radio answers ACK, at most `wait` ms (16 bytes take 1.4 ms:
      // PTT's debouncer never sees them)
      const deadline = now() + wait;
      if (fresh) { await this.port.clear(); this.buf = new Uint8Array(0); }
      while (now() < deadline) {
        await this.port.write(key);
        const t = now() + gap;
        while (now() < t) {
          await this._fill();
          const i = this.buf.indexOf(ACK);
          if (i >= 0) {
            this._text(this.buf.subarray(0, i));
            this.buf = this.buf.slice(i + 1);
            return true;
          }
          const j = this.buf.lastIndexOf(10);
          if (j >= 0) { this._text(this.buf.subarray(0, j + 1)); this.buf = this.buf.slice(j + 1); }
        }
      }
      return false;
    }

    async _frame(deadline) {
      for (;;) {
        const i = this.buf.indexOf(CPS_HEADER);
        if (i < 0) { this._text(this.buf); this.buf = new Uint8Array(0); }
        else {
          if (i) { this._text(this.buf.subarray(0, i)); this.buf = this.buf.slice(i); }
          if (this.buf.length >= 6) {
            const n = u16be(this.buf, 4);
            if (this.buf.length >= 8 + n) {
              const f = this.buf.slice(0, 8 + n);
              this.buf = this.buf.slice(8 + n);
              if (crc16Xmodem(f.subarray(1, 6 + n)) === u16be(f, 6 + n)) return [f[1], u16be(f, 2), f.slice(6, 6 + n)];
              this.buf = concat(f.subarray(1), this.buf);   // not a frame after all: resync
              continue;
            }
          }
        }
        if (now() > deadline) return null;
        await this._fill();
      }
    }

    async calls(reqs, { window = 1, retries = 4 } = {}) {
      // reqs: [[cmd, addr, data]] -> their answers' data, in order; up to `window` in flight; an
      // answer that doesn't come, or a CRC error, sends that frame again
      const out = new Array(reqs.length).fill(null);
      let todo = reqs.map((_, i) => i);
      const tries = {};
      while (todo.length) {
        const batch = todo.slice(0, window);
        todo = todo.slice(window);
        for (const i of batch) await this.port.write(cpsFrame(...reqs[i]));
        const pending = batch.slice();
        const deadline = now() + this.timeout;
        while (pending.length) {
          const f = await this._frame(deadline);
          if (!f) break;
          const [cmd, addr, data] = f;
          const k = pending.findIndex((i) => reqs[i][1] === addr);
          if (k < 0) continue;                          // a late answer to an earlier try
          const hit = pending[k];
          if (cmd === CMD_ERROR) {
            if (data[0] !== ERR_BAD_CRC)
              throw new InstallError(`the radio refused command 0x${reqs[hit][0].toString(16)} at ${addr}: error ${hex(data)}`);
            continue;                                   // sent again below
          }
          if (cmd !== reqs[hit][0]) continue;
          out[hit] = data;
          pending.splice(k, 1);
        }
        const again = batch.filter((i) => out[i] === null);
        for (const i of again) {
          tries[i] = (tries[i] || 0) + 1;
          if (tries[i] > retries) throw new InstallError(`no answer from the radio to command 0x${reqs[i][0].toString(16)} at ${reqs[i][1]}`);
        }
        todo = again.concat(todo);
        if (again.length) {
          await sleep(50);
          await this.port.clear();
          this.buf = new Uint8Array(0);
        }
      }
      return out;
    }

    async call(cmd, addr = 0, data = new Uint8Array(0)) {
      return (await this.calls([[cmd, addr, data]]))[0];
    }

    async read(addr, n, { window = 8, progress = null } = {}) {
      // n bytes of the SPI flash from addr (64-byte aligned)
      if (addr % READ_BLOCK) throw new InstallError("reads start on a 64-byte block");
      const first = addr / READ_BLOCK, last = Math.ceil((addr + n) / READ_BLOCK);
      const out = new Uint8Array((last - first) * READ_BLOCK);
      let o = 0;
      for (let s = first; s < last; s += 256) {
        const reqs = [];
        for (let b = s; b < Math.min(s + 256, last); b++) reqs.push([CMD_READ, b, new Uint8Array(0)]);
        for (const d of await this.calls(reqs, { window })) {
          if (d.length !== READ_BLOCK) throw new InstallError(`a read came back ${d.length} bytes, not ${READ_BLOCK}`);
          out.set(d, o); o += READ_BLOCK;
        }
        if (progress) progress(o, out.length);
      }
      return out.subarray(0, n);
    }

    async end() {
      // the cable session restarts the radio; Aura's first-boot one returns to FORMAT FLASH?
      try { await this.call(CMD_END); } catch (e) { if (!(e instanceof InstallError)) throw e; }
    }
  }

  class Cable extends CpsLink {
    async info() {
      const d = await this.call(CMD_INFO);
      if (!equal(d.subarray(0, 4), ascii("BLWF"))) throw new InstallError(`the radio's info doesn't look like blowfang's: ${hex(d)}`);
      const id = d.slice(8, 16);
      return { protocol: d[4], slots: d[5], arena: u16le(d, 6), buildId: id.some((x) => x) ? id : null };
    }

    async sendImage(img, xfer, window = 3) {
      // one signed image as L chunks; the last alone (its install erases and programs SPI, and the
      // radio's 320-byte receive ring must not overflow meanwhile)
      const reqs = packets(img, xfer, CHUNK).map((p, i) => [CMD_CHUNK, i, p]);
      const ans = reqs.length > 1 ? await this.calls(reqs.slice(0, -1), { window }) : [];
      ans.forEach((d, i) => {
        const r = i8(d[0]);
        if (r < 0) throw new InstallError(`chunk ${i}: ${KM[r] || r}`);
      });
      const d = (await this.calls(reqs.slice(-1)))[0];
      return { r: i8(d[0]), have: d[1], count: d[2], put: i32le(d, 4) };
    }

    async slotHead(slot) {
      const h = await this.read(LIB_BASE + slot * LIB_SLOT, 64);
      if (!equal(h.subarray(0, 3), ascii("K6M"))) return null;
      const flags = h[13];
      return { codeBytes: u16le(h, 4), serial: u32le(h, 8), name: moduleName(h.subarray(16)),
               kind: flags & 2 ? "page" : flags & 4 ? "cards" : "module" };
    }
  }

  // ---- the stock bootloader: catch its window, then flash the MCU (AA cmd arg len data CRC EF) ----

  const BOOT_HELLO = 0x01, BOOT_DATA = 0x03, BOOT_COUNT = 0x04, BOOT_END = 0x45, BOOT_HANDSHAKE = 0x42;
  const BOOT_PACKAGE = 1024;
  const BOOT_ERRORS = { 0xe1: "bad handshake", 0xe2: "CRC mismatch", 0xe3: "bad address", 0xe4: "flash write failed", 0xe5: "bad command" };

  function bootFrame(cmd, arg = 0, data = new Uint8Array(0)) {
    const body = concat([cmd, arg, data.length >> 8, data.length & 0xff], data);
    const c = crc16Xmodem(body);
    return concat([0xaa], body, [c >> 8, c & 0xff, 0xef]);
  }

  async function bootAnswer(port, timeout) {
    // the bootloader's answer to a frame: [cmd, arg], or null
    const deadline = now() + timeout;
    let buf = new Uint8Array(0);
    while (now() < deadline) {
      const b = await port.read(5);
      if (b && b.length) buf = concat(buf, b);
      let i = buf.indexOf(0xaa);
      while (i >= 0 && buf.length - i >= 5) {
        const n = (buf[i + 3] << 8) | buf[i + 4];
        if (buf.length - i < 8 + n) break;
        const f = buf.subarray(i, i + 8 + n);
        if (f[f.length - 1] === 0xef && crc16Xmodem(f.subarray(1, 5 + n)) === ((f[5 + n] << 8) | f[6 + n])) return [f[1], f[2]];
        i = buf.indexOf(0xaa, i + 1);
      }
    }
    return null;
  }

  async function bootCall(port, cmd, arg, data, { retries = 5, timeout = 2000, quietOk = false } = {}) {
    for (let t = 0; t < retries; t++) {
      await port.clear();
      await port.write(bootFrame(cmd, arg, data));
      const a = await bootAnswer(port, timeout);
      if (!a) continue;
      if (a[1] === ACK) return;
      if (a[0] === 0xe2) continue;
      throw new InstallError(`the bootloader refused command 0x${cmd.toString(16)}: ${BOOT_ERRORS[a[0]] || "0x" + a[0].toString(16)}`);
    }
    if (!quietOk) throw new InstallError(`no answer from the bootloader to command 0x${cmd.toString(16)}`);
  }

  async function bootFlash(port, image, log = () => {}, progress = null) {
    // flash an image into the MCU; the radio must be in UPDATE mode (catchWindow)
    const n = Math.ceil(image.length / BOOT_PACKAGE);
    if (n > 256) throw new InstallError(`${image.length} bytes is more than the bootloader takes`);
    await bootCall(port, BOOT_HELLO, 0, ascii("BOOTLOADER"));
    await bootCall(port, BOOT_COUNT, 0, new Uint8Array([n & 0xff]));
    for (let seq = 0; seq < n; seq++) {
      const pkg = new Uint8Array(BOOT_PACKAGE);
      pkg.set(image.subarray(seq * BOOT_PACKAGE, (seq + 1) * BOOT_PACKAGE));
      await bootCall(port, BOOT_DATA, seq & 0xff, pkg);
      if (progress) progress(seq + 1, n);
      if (seq % 16 === 15 || seq === n - 1) log(`  flashed ${seq + 1}/${n} KB`);
    }
    await bootCall(port, BOOT_END, 0, new Uint8Array(0), { retries: 2, quietOk: true });   // it may restart first
  }

  function findBootReply(buf) {
    let i = indexOf(buf, [0xaa, BOOT_HANDSHAKE]);
    while (i >= 0) {
      const f = buf.subarray(i, i + 8);
      if (f.length === 8 && f[3] === 0 && f[4] === 0 && f[7] === 0xef && crc16Xmodem(f.subarray(1, 5)) === ((f[5] << 8) | f[6])) return true;
      i = indexOf(buf, [0xaa, BOOT_HANDSHAKE], i + 1);
    }
    return false;
  }

  async function catchWindow(port, window, period = 18) {
    // the UPDATE handshake every `period` ms (the bootloader ends a frame after 10 ms idle) until
    // it answers (tools/k6_update.py)
    const hello = bootFrame(BOOT_HANDSHAKE);
    const deadline = now() + window;
    let buf = new Uint8Array(0);
    while (now() < deadline) {
      await port.write(hello);
      const t = now() + period;
      while (now() < t) {
        const b = await port.read(3);
        if (b && b.length) buf = concat(buf, b);
        if (findBootReply(buf)) return true;
      }
      buf = buf.slice(-16);
    }
    return false;
  }

  // ---- the steps (tools/k6_install.py) ----

  class Serials {
    // module serials, each newer than the last (the radio refuses an old one since power-on)
    constructor(start) { this.n = start === undefined ? Math.floor(Date.now() / 1000) : start; }
    next() { return ++this.n; }
  }

  const probeImage = (serial, key) => moduleImage(new Uint8Array([0x70, 0x47]), 0, serial, key);

  function sign(entry, serial, key) {
    const code = entry.data;
    return moduleImage(code, entry.kind === "module" ? binEntry(code) : 0, serial, key,
                       { level: entry.level || 1, lib: entry.slot, page: entry.kind === "page", cards: entry.kind === "cards" });
  }

  class Installer {
    /* port: {write, read, clear}; ui: {log(text), ask(kind, text) -> answer, save(name, bytes),
       progress(what, done, total)}; opts: timeout (ms), chipBytes, pause (ms after a flash) */
    constructor(port, ui = {}, opts = {}) {
      this.port = port;
      this.log = ui.log || (() => {});
      this.ask = ui.ask || (async () => "");
      this.save = ui.save || (async () => {});
      this.progress = ui.progress || (() => {});
      this.timeout = opts.timeout || 3000;
      this.chipBytes = opts.chipBytes || 2 << 20;
      this.pause = opts.pause === undefined ? 3000 : opts.pause;
      this.serials = new Serials(opts.serial);
    }

    cable() { return new Cable(this.port, this.log, { timeout: this.timeout }); }

    async openCable(wait) {
      const c = this.cable();
      return (await c.handshake(CABLE_HANDSHAKE, { wait })) ? c : null;
    }

    async backup(link) {
      const t = now();
      const data = (await link.read(0, this.chipBytes, { progress: (n, tot) => this.progress("backup", n, tot) })).slice();
      const cal = await link.read(0xf000, 0x1000);
      if (!equal(cal, data.subarray(0xf000, 0x10000)))
        throw new InstallError("the calibration sector read differently twice: the cable is unreliable, try again");
      const stamp = new Date().toISOString().replace(/[-:]/g, "").slice(0, 13) + "Z";
      const name = `k6-spiflash-${stamp}.bin`;
      const digest = hex(await sha256(data)).slice(0, 16);
      await this.save(name, data);
      this.log(`saved ${name}: ${data.length >> 10} KB in ${Math.round((now() - t) / 1000)} s, sha256 ${digest}; ` +
               "keep it: it holds your radio's factory calibration");
      return { name, data, sha256: digest };
    }

    async flash(image, key, how) {
      // key the image and flash it. how: "cable" (a blowfang radio: end the session, catch the
      // window after its restart, no hands) or "power" (the owner switches the radio on)
      const { data, keyed } = keyedImage(image, key);
      if (!keyed) this.log("  (this image takes no modules: it is flashed as it is)");
      let ok;
      if (how === "cable") {
        await this.cable().call(CMD_END);
        await sleep(30);
        ok = await catchWindow(this.port, 2000);
      } else {
        await this.ask("poweron", "Switch the radio OFF. Then click Continue and switch it ON (no keys held) within 10 seconds.");
        ok = await catchWindow(this.port, 12000);
      }
      if (!ok) throw new InstallError("the bootloader didn't answer: switch the radio off and try again " +
                                      "(or hold Side Keys 1 and 2 while switching it on)");
      this.log("  the radio is in UPDATE mode");
      await bootFlash(this.port, data, this.log, (n, tot) => this.progress("flash", n, tot));
      this.log("  flashed; the radio restarts (if its screen stays on UPDATE, switch it off and on)");
    }

    async checkKey(c, key) {
      const { r } = await c.sendImage(probeImage(this.serials.next(), key), this.serials.n & 0xff);
      if (r === 3) return;
      if (r === -4) throw new InstallError("the radio's image holds another key than yours: flash the image again " +
                                           "from this page (it keys it with yours), or load the key it was keyed with");
      if (r === -5) throw new InstallError("the radio's image holds no key: flash it from this page, which keys it");
      throw new InstallError(`the key check came back ${KM[r] || r}`);
    }

    async load(c, bundle, key, { only = null, verify = false, force = false } = {}) {
      const info = await c.info();
      this.log(`  the radio: cable session ${info.protocol}, ${info.slots} slots, build ID ` +
               (info.buildId ? hex(info.buildId) : "(none: not the voice + EXPLORE image)"));
      await this.checkKey(c, key);
      const want = unhex(bundle.build_id);
      let entries = bundle.library.filter((e) => !only || only.includes(e.slot));
      if (!equal(info.buildId, want) && !force && entries.some((e) => e.kind === "page")) {
        this.log(`  skipping EXPLORE's pages: they are for another image than the radio's (bundle ${bundle.build_id}); ` +
                 "flash the bundle's image first");
        entries = entries.filter((e) => e.kind !== "page");
      }
      const order = { page: 0, cards: 1, module: 2 };
      entries.sort((a, b) => order[a.kind] - order[b.kind] || a.slot - b.slot);
      const t = now();
      let total = 0;
      for (const [k, e] of entries.entries()) {
        const img = sign(e, this.serials.next(), key);
        const { r, put } = await c.sendImage(img, this.serials.n & 0xff);
        const slot = LIB_BASE + e.slot * LIB_SLOT;
        if (r !== 3 || put !== img.length) {
          // the last chunk's answer may have been lost and the chunk sent again (it then starts a
          // transfer of its own): the slot itself says whether the install happened
          if (r === 3 || !equal(await c.read(slot, img.length), img))
            throw new InstallError(`slot ${e.slot} (${e.name}): ${r !== 3 ? KM[r] || r : KL[put] || put}`);
        } else if (verify && !equal(await c.read(slot, img.length), img)) {
          throw new InstallError(`slot ${e.slot} (${e.name}) reads back differently`);
        }
        total += img.length;
        this.log(`  slot ${String(e.slot).padStart(2)} ${e.kind.padEnd(6)} ${String(e.name).padEnd(12)} ${String(img.length).padStart(6)} B`);
        this.progress("load", k + 1, entries.length);
      }
      this.log(`  ${entries.length} library images, ${total} B, in ${Math.round((now() - t) / 1000)} s`);
    }

    async codebooks(c) {
      const out = [];
      for (const [addr, magic, what, img] of CODEBOOKS)
        if (!equal((await c.read(addr, 64)).subarray(0, 4), magic)) out.push([what, img]);
      return out;
    }

    async waitLine(prefix, timeout) {
      // report lines until one starts with prefix: the line, or null
      const deadline = now() + timeout;
      let buf = new Uint8Array(0);
      while (now() < deadline) {
        const b = await this.port.read(5);
        if (b && b.length) buf = concat(buf, b);
        let i;
        while ((i = buf.indexOf(10)) >= 0) {
          const ln = dec.decode(buf.subarray(0, i)).trim();
          buf = buf.slice(i + 1);
          if (ln) this.log("  K6: " + ln.slice(0, 120));
          if (ln.startsWith(prefix)) return ln;
        }
      }
      return null;
    }

    async firstBoot(timeout = 90000) {
      // after an image's first flash on a radio Aura never formatted: back up at FORMAT FLASH?,
      // then the owner presses MENU; ends at the image's K6 CB line (its codebook check)
      const link = new CpsLink(this.port, this.log, { timeout: this.timeout });
      const deadline = now() + timeout;
      while (now() < deadline) {
        if (await link.handshake(AURA_HANDSHAKE, { wait: 3000, fresh: false })) {
          this.log("  the radio shows FORMAT FLASH?: backing up its SPI flash before anything is erased");
          await this.backup(link);
          await link.end();
          await this.ask("menu", "Now press MENU on the radio to format its flash, then click Continue.");
          break;
        }
        if (link.lines.some((l) => l.startsWith("K6 CB"))) return true;   // formatted already
      }
      return (await this.waitLine("K6 CB", 60000)) !== null;
    }

    async install(bundle, key, { fresh = false, backup = "auto", verify = false, yes = false } = {}) {
      // backup: "auto" (unless one was made before: the caller says so with "have"), "always", "never", "have"
      this.log("1. Looking for the radio on the cable (have it on, in EXPLORE or the arena)...");
      let c = fresh ? null : await this.openCable(6000);
      if (c) {
        this.log("   It runs blowfang already: an update, no hands needed.");
        const need = await this.codebooks(c);
        if (backup === "always" || backup === "auto") {
          this.log("2. Backing up the SPI flash (once per radio is enough)");
          await this.backup(c);
        } else if (backup === "never") this.log("2. No backup (you chose to skip it)");
        else this.log("2. A backup was made before: not backing up again");
        if (need.length) throw new InstallError("the radio lacks " + need.map((n) => n[0]).join(" and ") +
                                                ": install again with \"new radio\" ticked (it flashes the codebook installers)");
        this.log("3. Flashing the core image...");
        await this.flash(bundle.files[bundle.image], key, "cable");
      } else {
        this.log(fresh ? "   New-radio install: codebook installers first, three power-ons."
                       : "   No blowfang cable session: treating it as a new radio (stock, or another firmware).");
        const imgs = bundle.codebook_images || {};
        for (const k of ["cbi", "vlri"])
          if (!imgs[k]) throw new InstallError(`the release has no ${k} image, which a new radio needs for its codebooks`);
        if (!yes) {
          const a = await this.ask("confirm", "This replaces the radio's firmware for good: Baofeng's stock firmware can't be " +
                                   "read out or put back from here. Your SPI flash (factory calibration) is backed up first. Type YES to go on.");
          if (String(a).trim() !== "YES") { this.log("stopped; nothing was written"); return 1; }
        }
        this.log("2. Flashing the codebook installer (sotto's codebook into SPI flash, once)...");
        await this.flash(bundle.files[imgs.cbi], key, "power");
        if (!(await this.firstBoot())) throw new InstallError("no K6 CB line after the first boot: is the radio on, and the cable in?");
        this.log("3. Flashing the sotto 600 / 400 codebook installer...");
        await this.flash(bundle.files[imgs.vlri], key, "power");
        if ((await this.waitLine("K6 VLR", 60000)) === null) throw new InstallError("no K6 VLR line: the second codebook install didn't report");
        this.log("4. Flashing the core image...");
        await this.flash(bundle.files[bundle.image], key, "power");
      }
      this.log("5. Waiting for the new image (it shows PAGES MISSING until its pages are in)...");
      await sleep(this.pause);
      c = await this.openCable(40000);
      if (!c) throw new InstallError("the new image doesn't answer on the cable: switch the radio off and on, then use Load library");
      this.log("6. Loading EXPLORE's pages, the help deck and the modules over the cable...");
      await this.load(c, bundle, key, { verify });
      await c.end();
      this.log("Done. The radio restarts into EXPLORE. Hold MENU on any screen for its help card.");
      return 0;
    }

    async status() {
      const c = await this.openCable(20000);
      if (!c) throw new InstallError("no cable session: have the radio on a blowfang image, in EXPLORE or the arena");
      const info = await c.info();
      this.log(`blowfang cable session ${info.protocol}, build ID ${info.buildId ? hex(info.buildId) : "(not the voice + EXPLORE image)"}`);
      const missing = await this.codebooks(c);
      for (const [what, img] of missing) this.log(`missing: ${what} (the ${img} image installs it)`);
      const slots = [];
      for (let s = 0; s < info.slots && s < LIB_SLOTS; s++) {
        const h = await c.slotHead(s);
        if (h) {
          slots.push({ slot: s, ...h });
          this.log(`slot ${String(s).padStart(2)} ${h.kind.padEnd(6)} ${String(h.name || "").padEnd(12)} ${String(h.codeBytes).padStart(6)} B code, serial ${h.serial}`);
        }
        this.progress("status", s + 1, info.slots);
      }
      await c.end();
      return { info, missing, slots };
    }

    async backupOnly() {
      let link = await this.openCable(4000);
      if (!link) {
        link = new CpsLink(this.port, this.log, { timeout: this.timeout });
        if (!(await link.handshake(AURA_HANDSHAKE, { wait: 4000 })))
          throw new InstallError("the radio answers neither blowfang's cable session nor Aura's: have it on, in EXPLORE " +
                                 "or the arena (or at Aura's FORMAT FLASH? screen)");
      }
      const r = await this.backup(link);
      await link.end();
      return r;
    }

    async flashOnly(image, key, { power = false } = {}) {
      const how = !power && (await this.openCable(3000)) ? "cable" : "power";
      await this.flash(image, key, how);
      return 0;
    }

    async loadOnly(bundle, key, opts = {}) {
      const c = await this.openCable(20000);
      if (!c) throw new InstallError("no cable session: have the radio on a blowfang image, in EXPLORE or the arena");
      await this.load(c, bundle, key, opts);
      await c.end();
      return 0;
    }
  }

  // ---- Web Serial: the port interface the steps use ----

  class WebSerialPort {
    // wraps a SerialPort (navigator.serial.requestPort()) with a background reader
    constructor(sp) {
      this.sp = sp;
      this.chunks = [];
      this.waiters = [];
      this.closed = false;
    }

    async open() {
      await this.sp.open({ baudRate: 115200, dataBits: 8, stopBits: 1, parity: "none", flowControl: "none", bufferSize: 65536 });
      this.writer = this.sp.writable.getWriter();
      this.reader = this.sp.readable.getReader();
      this.loop = (async () => {
        try {
          for (;;) {
            const { value, done } = await this.reader.read();
            if (done) break;
            if (value && value.length) {
              this.chunks.push(value);
              this.waiters.splice(0).forEach((w) => w());
            }
          }
        } catch (e) { this.error = e; }
        this.closed = true;
        this.waiters.splice(0).forEach((w) => w());
      })();
    }

    async write(b) {
      if (this.closed) throw new InstallError("the cable was unplugged" + (this.error ? ` (${this.error.message})` : ""));
      await this.writer.write(b instanceof Uint8Array ? b : new Uint8Array(b));
    }

    async read(ms) {
      if (!this.chunks.length && !this.closed) {
        await new Promise((r) => {
          const t = setTimeout(() => { this.waiters = this.waiters.filter((w) => w !== done); r(); }, ms);
          const done = () => { clearTimeout(t); r(); };
          this.waiters.push(done);
        });
      }
      if (!this.chunks.length && this.closed) throw new InstallError("the cable was unplugged");
      const out = concat(...this.chunks);
      this.chunks = [];
      return out;
    }

    async clear() { this.chunks = []; }

    async close() {
      try { await this.reader.cancel(); } catch (e) { /* closing anyway */ }
      try { this.reader.releaseLock(); } catch (e) { /* closing anyway */ }
      try { this.writer.releaseLock(); } catch (e) { /* closing anyway */ }
      await this.loop;
      await this.sp.close();
    }
  }

  const K6Web = {
    InstallError, Installer, CpsLink, Cable, WebSerialPort, Serials,
    crc16Xmodem, crc16Ccitt, siphash24, moduleImage, packets, keyedImage, binEntry, moduleName, sign, probeImage,
    newKey, parseKey, keyFile, keyFingerprint, sha256, unzip, loadBundle, cpsFrame, bootFrame, catchWindow, bootFlash,
    hex, unhex, PLACEHOLDER, CABLE_HANDSHAKE, AURA_HANDSHAKE, LIB_SLOTS,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = K6Web;
  else root.K6Web = K6Web;
})(typeof self !== "undefined" ? self : this);
