// The browser installer's steps (web/k6-installer/k6web.js) under Node, with its serial port and
// its prompts handed to tests/test_k6_web_install.py over stdio: one JSON request a line out, one
// JSON answer a line in. The test answers with its emulated radio (tests/test_k6_install.py Radio).
//   node tests/k6_web_driver.cjs '{"run": "load", "bundle": DIR, "key": HEX, ...}'
// SPDX-License-Identifier: MIT
"use strict";
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const K6Web = require(path.join(__dirname, "..", "web", "k6-installer", "k6web.js"));

const cfg = JSON.parse(process.argv[2]);
const rl = readline.createInterface({ input: process.stdin });
const answers = [];
const waiting = [];
rl.on("line", (l) => (waiting.length ? waiting.shift()(JSON.parse(l)) : answers.push(JSON.parse(l))));

function call(req) {
  process.stdout.write(JSON.stringify(req) + "\n");
  return new Promise((r) => (answers.length ? r(answers.shift()) : waiting.push(r)));
}

const port = {
  async write(b) { await call({ op: "write", hex: K6Web.hex(b) }); },
  async read(ms) {
    // the emulator answers as soon as it is written to: poll it until bytes come or ms pass
    const end = Date.now() + ms;
    for (;;) {
      const a = await call({ op: "read" });
      if (a.hex || Date.now() >= end) return K6Web.unhex(a.hex || "");
      await new Promise((r) => setTimeout(r, 1));
    }
  },
  async clear() { await call({ op: "clear" }); },
};

const ui = {
  log: (text) => call({ op: "log", text }),
  ask: async (kind, text) => (await call({ op: "ask", kind, text })).answer || "",
  save: (name, data) => call({ op: "save", name, hex: K6Web.hex(data) }),
  progress: () => {},
};

function readDir(d) {
  const out = {};
  for (const f of fs.readdirSync(d)) out[f] = new Uint8Array(fs.readFileSync(path.join(d, f)));
  return out;
}

async function main() {
  const inst = new K6Web.Installer(port, ui, cfg.opts || {});
  const key = cfg.key ? K6Web.parseKey(cfg.key) : null;
  if (cfg.run === "vectors") {
    // the codec pieces, for comparison with modem/k6mod.py
    const code = K6Web.unhex(cfg.code);
    const img = K6Web.moduleImage(code, K6Web.binEntry(code), cfg.serial, key, { level: 2, lib: 7 });
    const page = K6Web.moduleImage(code, 0, cfg.serial, key, { lib: 9, page: true });
    const cards = K6Web.moduleImage(code, 0, cfg.serial, key, { lib: 13, cards: true });
    const zip = cfg.zip ? await K6Web.unzip(new Uint8Array(fs.readFileSync(cfg.zip))) : {};
    await call({ op: "result", vectors: {
      siphash: K6Web.hex(K6Web.siphash24(key, code)), image: K6Web.hex(img), page: K6Web.hex(page), cards: K6Web.hex(cards),
      packets: K6Web.packets(img, 0x5a, 64).map(K6Web.hex), name: K6Web.moduleName(code),
      keyed: K6Web.hex(K6Web.keyedImage(K6Web.unhex(cfg.image), key).data),
      zip: Object.fromEntries(Object.entries(zip).map(([k, v]) => [k, K6Web.hex(v)])),
    } });
    return 0;
  }
  let files = cfg.bundle ? readDir(cfg.bundle) : null;
  if (cfg.zip) files = await K6Web.unzip(new Uint8Array(fs.readFileSync(cfg.zip)));
  const bundle = files ? await K6Web.loadBundle(files) : null;
  switch (cfg.run) {
    case "load": return inst.loadOnly(bundle, key, cfg.args || {});
    case "install": return inst.install(bundle, key, cfg.args || {});
    case "flash": return inst.flashOnly(new Uint8Array(fs.readFileSync(cfg.image)), key, cfg.args || {});
    case "backup": await inst.backupOnly(); return 0;
    case "status": await call({ op: "result", status: JSON.parse(JSON.stringify(await inst.status(), (k, v) => (v instanceof Uint8Array ? K6Web.hex(v) : v))) }); return 0;
    case "bundle": return 0;
    default: throw new Error("no such run: " + cfg.run);
  }
}

main().then(
  async (r) => { await call({ op: "done", result: r }); process.exit(0); },
  async (e) => {
    await call({ op: "done", result: 1, error: e.message, installError: e instanceof K6Web.InstallError });
    process.exit(0);
  },
);
