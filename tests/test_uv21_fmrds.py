"""The UV-21 Pro's RDS module (radios/uv21/mod/fmrds.c), a cable RAM module, run on the emulated
RDA5807-family chip of tests/test_uv21_fmprobe.py with the 12 KB arena's RAM: the sweep finds
the stations, each one's RDS name comes out in the K6 module's line tags, tools/k6_fmrds.py
check reads it, and the pins and the chip are left as found."""
import inspect
import os
import sys

import pytest

pytest.importorskip("unicorn")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_uv21_fmprobe as t  # noqa: E402
from test_k6_i2s import need_clang  # noqa: E402

SRC = os.path.join(ROOT, "radios", "uv21", "mod", "fmrds.c")
FAST = ["FR_SCAN_MS=1500"]


def go_arena(rda, img):
    """test_uv21_fmprobe.go_img with the module's RAM the rest of the 12 KB arena (umod.rs ROOM)"""
    from modem import uv21mod
    src = inspect.getsource(t.go_img).replace("4096 - ram_at", f"{uv21mod.ROOM} - ram_at")
    ns = dict(vars(t))
    exec(src, ns)
    return ns["go_img"](rda, img)


@need_clang
def test_fmrds_finds_the_stations_and_their_names_in_the_arena():
    from modem import uv21mod
    import k6_fmrds
    img = uv21mod.image(*uv21mod.build(SRC, FAST))
    old = t.go_img(t.Rda21(t.STATIONS, at30=True, tea=False, dead=True), img)   # the 4 KB block
    assert old[0] == -6, old[:2]                                        # (why it waited for #293)
    rda = t.Rda21(t.STATIONS, at30=True, tea=False, dead=True)
    ret, said, _ = go_arena(rda, img)
    assert ret == 3 and said[-1].startswith("E n=3 "), said[-3:]
    assert said[0] == "v 5804 aec0>aeff ok"
    names = [s for s in said if s.startswith("n ")]
    assert any("pi=4321" in s and "ps=KBBB 104" in s for s in names), names
    assert k6_fmrds.main(["check", _log(said)]) == 0
    assert not rda.g[2] & 1 and (rda.pa, rda.pb) == rda.start and rda.contention == 0


@need_clang
def test_fmrds_without_a_chip_leaves_quickly():
    from modem import uv21mod
    rda = t.Rda21(t.STATIONS, at30=True, tea=False, never=True)
    ret, said, _ = go_arena(rda, uv21mod.image(*uv21mod.build(SRC, FAST)))
    assert ret == -5 and said[-1].startswith("E n=-5 "), said
    assert not rda.g[2] & 1 and (rda.pa, rda.pb) == rda.start


def _log(said):
    import tempfile
    f = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
    f.write("\n".join(said) + "\n")
    f.close()
    return f.name
