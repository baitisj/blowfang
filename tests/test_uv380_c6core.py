"""The UV-380's HR_C6000 core (c/common/c6_core.c, docs/uv380-multimode.md) on a fake chip
(c/common/c6core_host.c): the 4FSK engine must put a whole M17 or YSF sotto transmission on the
air byte for byte after the preamble, then pad; stop at its deadline; and the DMR engine must
send the headers, voice bursts in A-F order with their payloads, and the terminator, one burst
every other slot. Every register a profile touches must be back as it was.
Needs make -C c/common. SPDX-License-Identifier: MIT"""
import os
import re
import subprocess

import numpy as np
import pytest

from modem import m17, ysf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "c", "common", "c6core_host")
pytestmark = pytest.mark.skipif(not os.path.exists(EXE), reason="needs make -C c/common")


def run(*args, data=b""):
    r = subprocess.run([EXE, *args], input=data, capture_output=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout, r.stderr.decode()


def stats(err):
    return {k: int(v) for k, v in re.findall(r"(\w+)=(-?\d+)", err.splitlines()[0])}


def changed(err):
    """registers not as they started, apart from the flags 0x82 (0x83 clears them)"""
    line = [x for x in err.splitlines() if x.startswith("changed")][0]
    return [x for x in line.split()[1:] if not x.startswith("82=")]


def sotto(n, seed=3):
    return bytes(np.random.default_rng(seed).integers(0, 256, 6 * n, dtype=np.uint8))


@pytest.mark.parametrize("n", [1, 20, 75])
def test_m17_stream_goes_out_whole(n):
    s = sotto(n, seed=n)
    air, err = run("fsk4", "m17", "AG7EW", data=s)
    tx = m17.tx_sotto(s)
    st = stats(err)
    assert air[:36] == bytes([0x77]) * 36                   # the preamble fill before slot 1
    assert air[36:36 + len(tx)] == tx
    assert set(air[36 + len(tx):]) <= {0x55, 0x5D}          # then EOT pad
    assert st["r"] == 0 and st["done"] == 1 and st["late"] == 0 and st["bytes"] == len(tx)
    assert re.search(r"chk=36/", err)
    assert changed(err) == []


@pytest.mark.parametrize("n", [1, 37])
def test_ysf_stream_goes_out_whole(n):
    s = sotto(n, seed=50 + n)
    air, err = run("fsk4", "ysf", "AG7EW", data=s)
    tx = ysf.tx_sotto(s)
    assert air[36:36 + len(tx)] == tx and set(air[36 + len(tx):]) == {0x77}
    assert stats(err)["r"] == 0 and changed(err) == []


def test_deadline_stops_early_and_restores():
    s = sotto(40)
    air, err = run("fsk4", "m17", "AG7EW", "500", data=s)
    st = stats(err)
    assert st["r"] == -1 and st["done"] == 0
    assert st["slots"] <= 500 // 30
    assert air[36:36 + 36 * (st["slots"] - 1)] == m17.tx_sotto(s)[:36 * (st["slots"] - 1)]
    assert changed(err) == []


def test_dmr_call_order_and_payloads():
    out, err = run("dmr", "12")
    bursts = [ln.split() for ln in out.decode().splitlines()]
    kinds = [b[2] for b in bursts]
    assert kinds == ["50=10"] * 3 + [f"50={0x08 | (k % 6) << 4:02x}" for k in range(12)] + ["50=20"]
    for k in range(12):
        want = bytes((k * 16 + i) & 0xFF for i in range(27)).hex()
        assert bursts[3 + k][3] == "pay=" + want
    edges = [int(b[1]) for b in bursts]
    assert all(b - a == 2 for a, b in zip(edges, edges[1:]))   # every other slot: direct mode
    st = stats(err)
    assert st["r"] == 0 and st["hdr"] == 3 and st["voice"] == 12 and st["term"] == 1
    assert changed(err) == ["22=40"]                           # encode stop, as c6dvtx leaves it


MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")


def test_sotto_modules_build_and_fit():
    """c6sotto (the core + a framer as a RAM module, keyed by tx_tone) for each mode, dry and on
    air: position independent, level 4, well inside the 16 KB arena"""
    import shutil
    if not shutil.which("clang") or not shutil.which("ld.lld"):
        pytest.skip("needs clang and ld.lld")
    from modem import uv380mod
    for air in ("m17", "ysf", "dmr"):
        for dry in ("_dry", ""):
            src = os.path.join(MOD, f"c6sotto_{air}{dry}.c")
            assert uv380mod.source_level(src) == 4
            code, entry = uv380mod.build(src)
            assert entry == 0 and len(code) < 8192, (src, len(code))


def test_sotto_module_is_dry_unless_a_wrapper_says_otherwise():
    """c6sotto (its body c6air.h) keys only through c6key.h's c6k_key (level 4: ident first,
    transmit guard), only with dry 0, which only the three on-air wrappers set (TX_DRY 0)"""
    src = open(os.path.join(MOD, "c6sotto.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert "c6air_run(api, SOTTO_AIR, SOTTO_SRC, TX_DRY)" in src
    air = open(os.path.join(MOD, "c6air.h")).read()
    assert "api->tx_tone(" not in src + air and air.count("c6k_key(") == 1
    assert "if (!dry && c6k_key(" in air
    key = open(os.path.join(MOD, "c6key.h")).read()
    assert key.index("api->level < 4u") < key.index("api->tx_tone(")
    live = sorted(f for f in os.listdir(MOD) if f.startswith("c6sotto_")
                  and "#define TX_DRY 0" in open(os.path.join(MOD, f)).read())
    assert live == ["c6sotto_dmr.c", "c6sotto_m17.c", "c6sotto_ysf.c"]


HERE = os.path.join(ROOT, "firmware", "uv380-openrtx")
PATCH = os.path.join(HERE, "uv380-blowfang.patch")


def patch_files():
    """the patch's files as they are after it applies: {path: [lines]} for the files it adds"""
    out, cur = {}, None
    for ln in open(PATCH).read().split("\n"):
        if ln.startswith("+++ b/"):
            cur = ln[6:]
            out[cur] = []
        elif ln.startswith("+++ /dev/null"):
            cur = None
        elif cur and ln.startswith("+") and not ln.startswith("+++"):
            out[cur].append(ln[1:])
    return out


def test_patch_hunk_headers_count_their_lines():
    """every hunk says how many lines it holds, and where it lands after the hunks above it"""
    hdr = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    lines = open(PATCH).read().split("\n")
    i, delta = 0, 0
    while i < len(lines):
        if lines[i].startswith("diff --git"):
            delta = 0
        m = hdr.match(lines[i])
        if not m:
            i += 1
            continue
        o0, n0 = int(m.group(1)), int(m.group(3))
        j, o, n = i + 1, 0, 0
        while j < len(lines) and not lines[j].startswith(("@@", "diff --git")):
            c = lines[j][:1]
            if j == len(lines) - 1 and lines[j] == "":
                break
            o += c in " -"
            n += c in " +"
            j += 1
        assert (int(m.group(2) or 1), int(m.group(4) or 1)) == (o, n), (i + 1, lines[i])
        if o0:
            assert n0 == o0 + delta, (i + 1, lines[i])
        delta += n - o
        i = j


def test_image_runs_the_modules_body_from_flash():
    """milestone 4: the bench image's PTT in M17 / YSF / DMR and its "air" command run c6air_run
    (c6air_img.c) with the image's own module table, after PTT is let go (tx_tone keys only then),
    and offset only the keyed tone, never the ident, by the raw modes' 6680 Hz"""
    f = patch_files()
    pager = "\n".join(f["openrtx/src/rtx/OpMode_Pager.cpp"])
    assert "c6air_send(om_api(&omHost), a, src, dry)" in pager
    assert "AIR_TRIM_HZ = -6680" in pager
    ptt = pager[pager.index("if(ptt && armed && (status->txDisable == 0))"):]
    ptt = ptt[:ptt.index("holdProbe();")]
    assert "airPend = phyAir;" in ptt and "send(" not in ptt
    poll = pager[pager.index("static void *probeFunc"):]
    assert "if((airPend != 0) && (pbStop(NULL) == false))" in poll    # pbStop: PTT let go
    tone = pager[pager.index("if(toneTxMs != 0)"):]
    tone = tone[:tone.index("toneTest(status, ms, toneHz);")]
    assert tone.index("send(status);") < tone.index("blowfang_txTrimHz = trimKeep + toneTrimOfs;")
    assert "(listenCommand(pbCmd) == 0) && (airCommand(pbCmd) == 0))" in pager
    ui = "\n".join(f["openrtx/src/ui/default/ui_main.c"] + f["openrtx/src/ui/default/ui_menu.c"])
    assert "pager_phyFsk() ?" not in ui and ui.count("pager_phyName()") == 2
    meson = "\n".join(f["meson.build"])
    assert "'openrtx/src/blowfang/mod/c6air_img.c'," in meson
    img = open(os.path.join(HERE, "c6air_img.c")).read()
    assert "#if defined(BLOWFANG_RANGE) && defined(BLOWFANG_I2SPROBE) && defined(PLATFORM_MDUV3x0)" in img
    assert "#define BLOWFANG_AIR 1" in pager and \
        "#if defined(BLOWFANG_RANGE) && defined(BLOWFANG_I2SPROBE) && defined(PLATFORM_MDUV3x0)\n" \
        "// The bench image's M17" in pager
    assert "const ortx_api_t *om_api(const om_host *h) {" in open(os.path.join(HERE, "ortx_mod.c")).read()


def test_build_copies_what_c6air_includes():
    """build.sh puts each header c6air_img.c reaches (mod/ and c/common/) where its includes look"""
    sh = open(os.path.join(HERE, "build.sh")).read()
    lists = re.findall(r'while read -r f; do\n  cp "([^"]+)/\$f" "([^"]+)"\ndone <<EOF\n(.*?)\nEOF', sh, re.S)
    copied = {}
    for src, dst, names in lists:
        for n in names.split("\n"):
            copied[(src, n)] = dst
    assert 'cp "$HERE/c6air_img.c" "$WORK/openrtx/src/blowfang/mod/"' in sh
    # c6air_img.c sits in mod/ in the image: its includes resolve from there
    seen, todo = set(), [(os.path.join(HERE, "mod", "c6air_img.c"), os.path.join(HERE, "c6air_img.c"))]
    while todo:
        p, real = todo.pop()
        for inc in re.findall(r'^#include "([^"]+)"', open(real).read(), re.M):
            q = os.path.normpath(os.path.join(os.path.dirname(p), inc))
            if inc == "blowfang_config.h" or q in seen:
                continue
            if not os.path.exists(q) and inc == "ortx_mod.h":
                q = os.path.join(HERE, "ortx_mod.h")      # found on openrtx/src/blowfang in the image
            seen.add(q)
            todo.append((q, q))
    for q in seen:
        d, n = os.path.dirname(q), os.path.basename(q)
        if d == os.path.join(HERE, "mod"):
            assert copied.get(("$HERE/mod", n)) == "$WORK/openrtx/src/blowfang/mod/", n
        elif d == os.path.join(ROOT, "c", "common"):
            assert copied.get(("$ROOT/c/common", n)) == "$WORK/openrtx/c/common/", n
        else:
            assert q == os.path.join(HERE, "ortx_mod.h"), q


def test_c6air_img_builds_for_the_radio(tmp_path):
    """c6air_img.c in the image's tree (as build.sh lays it out), for the Cortex-M4F with no -fropi:
    clean under -Wall -Wextra, and the only globals c6air_send and the core's and framers' own
    prefixed names"""
    import shutil
    if not shutil.which("clang"):
        pytest.skip("needs clang")
    mod = tmp_path / "openrtx" / "src" / "blowfang" / "mod"
    com = tmp_path / "openrtx" / "c" / "common"
    mod.mkdir(parents=True)
    com.mkdir(parents=True)
    for n in ("c6air.h", "c6bus_mod.h", "c6key.h", "c6slot.h", "c6spi.h", "c6vspi.h", "w25sec.h"):
        shutil.copy(os.path.join(HERE, "mod", n), mod)
    shutil.copy(os.path.join(HERE, "c6air_img.c"), mod)
    shutil.copy(os.path.join(HERE, "ortx_mod.h"), mod.parent)
    for n in ("c6_core.c", "c6_core.h", "dmr_sotto.h", "dmr_voice.h", "m17_tx.c", "m17_tx.h",
              "ysf_tx.c", "ysf_tx.h"):
        shutil.copy(os.path.join(ROOT, "c", "common", n), com)
    (mod.parent / "blowfang_config.h").write_text("#define BLOWFANG_I2SPROBE 1\n#define BLOWFANG_RANGE 1\n")
    obj = tmp_path / "c6air_img.o"
    r = subprocess.run(["clang", "--target=thumbv7em-none-eabihf", "-mcpu=cortex-m4", "-mfloat-abi=hard",
                        "-ffreestanding", "-c", "-Os", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                        "-DPLATFORM_MDUV3x0", "-I", str(mod.parent), str(mod / "c6air_img.c"),
                        "-o", str(obj)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    nm = shutil.which("llvm-nm") or shutil.which("nm")
    out = subprocess.run([nm, str(obj)], capture_output=True, text=True).stdout
    glob = [ln.split()[-1] for ln in out.splitlines() if re.match(r"^\S+ [TDBR] ", ln)]
    assert "c6air_send" in glob
    assert all(g == "c6air_send" or g.startswith(("c6c_", "C6C_", "m17t_", "ysft_")) for g in glob), glob
    assert not [ln for ln in out.splitlines() if " U " in ln]      # nothing from libc
