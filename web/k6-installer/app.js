/* The installer page: its buttons, files, prompts and log around K6Web (k6web.js).
   SPDX-License-Identifier: MIT */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const K = window.K6Web;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window: fine */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* fine */ } },
  };
  const KEY_STORE = "blowfang-k6-key", BACKUPS_STORE = "blowfang-k6-backups";
  const CHIPS = { 0x1a86: "CH340", 0x10c4: "CP210x", 0x067b: "PL2303", 0x0403: "FTDI" };

  let key = null, bundle = null, port = null, busy = false, stopAsk = null;

  // ---- log, progress, prompts ----

  const logLines = [];
  function log(text) {
    const t = new Date().toISOString().slice(11, 19);
    logLines.push(`${t} ${text}`);
    const el = $("log");
    el.textContent += `${t} ${text}\n`;
    el.scrollTop = el.scrollHeight;
  }

  const WHAT = { backup: "Backing up", flash: "Flashing", load: "Loading the library", status: "Reading the library" };
  function progress(what, done, total) {
    $("bar").max = total; $("bar").value = done;
    $("what").textContent = `${WHAT[what] || what}: ${done} of ${total}` + (what === "flash" ? " KB" : what === "backup" ? " bytes" : "");
  }

  function ask(kind, text) {
    return new Promise((resolve, reject) => {
      $("prompttext").textContent = text;
      $("promptinput").style.display = kind === "confirm" ? "" : "none";
      $("promptinput").value = "";
      $("prompt").classList.add("on");
      $("prompt").scrollIntoView({ behavior: "smooth", block: "center" });
      (kind === "confirm" ? $("promptinput") : $("promptgo")).focus();
      log("waiting for you: " + text);
      const done = (fn, v) => { $("prompt").classList.remove("on"); stopAsk = null; $("promptgo").onclick = null; fn(v); };
      $("promptgo").onclick = () => done(resolve, kind === "confirm" ? $("promptinput").value : "");
      stopAsk = () => done(reject, new K.InstallError("stopped by you"));
    });
  }
  $("promptstop").onclick = () => stopAsk && stopAsk();
  $("promptinput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("promptgo").click(); });

  function download(name, data, type = "application/octet-stream") {
    const url = URL.createObjectURL(new Blob([data], { type }));
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function backups() {
    try { return JSON.parse(store.get(BACKUPS_STORE) || "[]"); } catch (e) { return []; }
  }

  async function save(name, data) {
    download(name, data);
    const list = backups();
    list.push({ name, sha256: K.hex(await K.sha256(data)).slice(0, 16), at: new Date().toISOString() });
    store.set(BACKUPS_STORE, JSON.stringify(list.slice(-20)));
    log(`your browser saved ${name}: keep it with your key file`);
  }

  // ---- state ----

  function refresh() {
    const serial = "serial" in navigator;
    $("nosupport").style.display = serial ? "none" : "block";
    const can = (on) => !busy && on;
    $("connect").disabled = !can(serial && !port);
    $("disconnect").disabled = !can(!!port);
    $("install").disabled = !can(!!(port && key && bundle));
    $("load").disabled = !can(!!(port && key && bundle));
    $("dobackup").disabled = !can(!!port);
    $("status").disabled = !can(!!port);
    $("flashimg").disabled = !can(!!(port && key));
    $("flashbtn").classList.toggle("disabled", $("flashimg").disabled);
    $("newkey").disabled = busy;
    $("keyfile").disabled = busy;
    $("keyfilebtn").classList.toggle("disabled", busy);
  }

  async function setKey(k, how) {
    key = k;
    if (!k) { $("keystate").textContent = "No key yet."; refresh(); return; }
    if ($("rememberkey").checked) store.set(KEY_STORE, K.hex(k));
    $("keystate").innerHTML = `<b class="ok">Key ready</b> (${how}), fingerprint <b>${await K.keyFingerprint(k)}</b>. ` +
      "The fingerprint names the key without showing it.";
    refresh();
  }

  $("newkey").onclick = async () => {
    if (key && !confirm("Replace the key you have? A radio keyed with the old one won't take modules signed with a new one.")) return;
    const k = K.newKey();
    download("k6-mod.key", K.keyFile(k), "text/plain");
    await setKey(k, "made here; your browser saved it as k6-mod.key");
    log("made a new key; the file k6-mod.key is in your downloads: keep it somewhere safe and private");
  };

  $("keyfile").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try { await setKey(K.parseKey(await f.text()), `from ${f.name}`); }
    catch (err) { $("keystate").innerHTML = `<b class="bad">${err.message}</b>`; }
    e.target.value = "";
  };

  $("forgetkey").onclick = () => { store.del(KEY_STORE); setKey(null); log("this browser forgot the key"); };
  $("rememberkey").onchange = () => {
    if (!$("rememberkey").checked) store.del(KEY_STORE);
    else if (key) store.set(KEY_STORE, K.hex(key));
  };

  async function setBundle(files, from) {
    $("bundlestate").textContent = "Checking…";
    try {
      bundle = await K.loadBundle(files);
      const kinds = {};
      for (const e of bundle.library) kinds[e.kind] = (kinds[e.kind] || 0) + 1;
      const cb = bundle.codebook_images || {};
      const newRadio = cb.cbi && cb.vlri;
      $("bundlestate").innerHTML = `<b class="ok">Release ${bundle.build || "(no build name)"}</b> from ${from}: image build ID ` +
        `<b>${bundle.build_id}</b>, ${kinds.page || 0} pages, ${kinds.cards || 0} help deck, ${kinds.module || 0} modules. ` +
        (newRadio ? "Has the codebook installers a new radio needs."
                  : '<span class="warn">No codebook installers: it can update a blowfang radio, not install a new one.</span>');
      log(`release ${bundle.build}: ${Object.keys(bundle.sha256).length} files, all match the manifest`);
    } catch (err) {
      bundle = null;
      $("bundlestate").innerHTML = `<b class="bad">${err.message}</b>`;
    }
    refresh();
  }

  $("zip").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try { await setBundle(await K.unzip(new Uint8Array(await f.arrayBuffer())), f.name); }
    catch (err) { bundle = null; $("bundlestate").innerHTML = `<b class="bad">${err.message}</b>`; refresh(); }
    e.target.value = "";
  };

  $("folder").onchange = async (e) => {
    const files = {};
    let folder = "";
    for (const f of e.target.files) {
      files[f.name] = new Uint8Array(await f.arrayBuffer());
      folder = folder || (f.webkitRelativePath || "").split("/")[0];
    }
    await setBundle(files, folder || "the folder");
    e.target.value = "";
  };

  // ---- the port ----

  $("connect").onclick = async () => {
    try {
      const sp = await navigator.serial.requestPort();
      const p = new K.WebSerialPort(sp);
      await p.open();
      port = p;
      const info = sp.getInfo ? sp.getInfo() : {};
      const chip = CHIPS[info.usbVendorId] || (info.usbVendorId ? `USB ${info.usbVendorId.toString(16)}:${(info.usbProductId || 0).toString(16)}` : "serial port");
      $("portstate").innerHTML = `<b class="ok">Connected</b>: ${chip}, 115200 baud.`;
      log(`connected to the cable (${chip})`);
    } catch (err) {
      if (err.name !== "NotFoundError") $("portstate").innerHTML = `<b class="bad">${err.message}</b> (is another program using the port?)`;
    }
    refresh();
  };

  async function disconnect() {
    if (!port) return;
    try { await port.close(); } catch (e) { /* gone already */ }
    port = null;
    $("portstate").textContent = "Not connected.";
    refresh();
  }
  $("disconnect").onclick = disconnect;
  if ("serial" in navigator) {
    navigator.serial.addEventListener("disconnect", (e) => {
      if (port && e.target === port.sp) { log("the cable was unplugged"); port = null; $("portstate").innerHTML = '<b class="warn">Unplugged.</b>'; refresh(); }
    });
  }

  // ---- the steps ----

  async function run(title, fn) {
    if (busy) return;
    busy = true; refresh();
    $("what").textContent = title + "…";
    $("bar").value = 0;
    log(`---- ${title}`);
    const t = Date.now();
    try {
      const inst = new K.Installer(port, { log, ask, save, progress });
      const r = await fn(inst);
      $("what").innerHTML = r === 1 ? `${title}: stopped` : `<span class="ok">${title}: done</span> in ${Math.round((Date.now() - t) / 1000)} s`;
    } catch (err) {
      log(`stopped: ${err.message}`);
      if (!(err instanceof K.InstallError)) console.error(err);
      $("what").innerHTML = `<span class="bad">${title} stopped:</span> ${err.message}`;
    } finally {
      busy = false;
      $("prompt").classList.remove("on");
      refresh();
    }
  }

  $("install").onclick = () => {
    let b = $("backup").value;
    if (b === "auto" && backups().length) b = "have";
    run("Install", (i) => i.install(bundle, key, { fresh: $("fresh").checked, backup: b, verify: $("verify").checked }));
  };
  $("load").onclick = () => run("Load library", (i) => i.loadOnly(bundle, key, { verify: $("verify").checked }));
  $("dobackup").onclick = () => run("Backup", (i) => i.backupOnly());
  $("status").onclick = () => run("Status", (i) => i.status());
  $("flashimg").onchange = async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    const img = new Uint8Array(await f.arrayBuffer());
    if (!confirm(`Flash ${f.name} (${img.length} bytes) into the radio? This replaces the firmware it runs now.`)) return;
    run(`Flash ${f.name}`, (i) => i.flashOnly(img, key));
  };

  $("savelog").onclick = () => download(`k6-install-log-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.txt`, logLines.join("\n") + "\n", "text/plain");

  window.addEventListener("beforeunload", (e) => { if (busy) { e.preventDefault(); e.returnValue = ""; } });

  // ---- start ----
  const saved = store.get(KEY_STORE);
  if (saved) {
    try { setKey(K.parseKey(saved), "remembered by this browser"); } catch (e) { store.del(KEY_STORE); }
  }
  if (backups().length) log(`this browser saved ${backups().length} backup(s) before; the newest: ${backups().slice(-1)[0].name}`);
  refresh();
})();
